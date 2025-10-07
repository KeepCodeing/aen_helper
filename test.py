# image_database_v4.py (修正了角色类别ID)

import argparse
import os
import sqlite3
import torch
from transformers import AutoModelForImageClassification, AutoImageProcessor
import huggingface_hub
import pandas as pd
from PIL import Image
from tqdm import tqdm

# --- 配置 ---
MODEL_REPO = "SmilingWolf/wd-eva02-large-tagger-v3"
LABEL_FILENAME = "selected_tags.csv"
DB_PATH = "image_tags.db"
CHARACTER_CONFIDENCE_THRESHOLD = 0.85

# --- 数据库操作 (不变) ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL UNIQUE, character_name TEXT)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_character_name ON images (character_name)')
        cursor.execute('CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, category INTEGER)')
        cursor.execute('CREATE TABLE IF NOT EXISTS image_tags (image_id INTEGER, tag_id INTEGER, confidence REAL, FOREIGN KEY (image_id) REFERENCES images (id) ON DELETE CASCADE, FOREIGN KEY (tag_id) REFERENCES tags (id), PRIMARY KEY (image_id, tag_id))')
        conn.commit()

# --- AI Tagger 核心类 (不变) ---
class Predictor:
    def __init__(self):
        self.model, self.processor, self.device, self.tags_df = None, None, None, None

    def load_model(self):
        if self.model: return
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"Using device: {self.device}")
        csv_path = huggingface_hub.hf_hub_download(MODEL_REPO, LABEL_FILENAME)
        self.tags_df = pd.read_csv(csv_path)
        self.processor = AutoImageProcessor.from_pretrained(MODEL_REPO)
        self.model = AutoModelForImageClassification.from_pretrained(
            MODEL_REPO, trust_remote_code=True
        ).to(self.device).eval()
        print("Model loaded successfully.")

    def predict(self, image: Image.Image):
        self.load_model()
        inputs = self.processor(images=image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            logits = self.model(**inputs).logits
        return torch.sigmoid(logits.squeeze()).cpu().numpy()

# --- 命令行处理函数 (已修正) ---

def handle_index(args):
    # (index 命令处理逻辑已修正)
    print("Starting indexing process...")
    init_db()
    predictor = Predictor()
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT filepath FROM images")
        indexed_files = {row[0] for row in cursor.fetchall()}
    print(f"Found {len(indexed_files)} images already in the database.")

    image_paths = []
    supported_exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
    project_dir_name = os.path.basename(os.getcwd())
    scan_path = os.path.abspath('..')
    print(f"Scanning for images in '{scan_path}'...")
    for root, dirs, files in os.walk(scan_path, topdown=True):
        if project_dir_name in dirs: dirs.remove(project_dir_name)
        for file in files:
            if file.lower().endswith(supported_exts):
                image_paths.append(os.path.abspath(os.path.join(root, file)))
    
    new_files = [p for p in image_paths if p not in indexed_files]
    if not new_files:
        print("No new images found. Database is up to date."); return
    
    print(f"Found {len(new_files)} new images to index. Starting tagging process...")
    
    predictor.load_model()
    tags_df = predictor.tags_df
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        for _, row in tags_df.iterrows():
            cursor.execute("INSERT OR IGNORE INTO tags (name, category) VALUES (?, ?)", (row['name'], row['category']))
        conn.commit()
        
        cursor.execute("SELECT name, id FROM tags")
        tag_to_id = {name: id for name, id in cursor.fetchall()}

        # ==========================================================
        #  ↓↓↓ 核心修正：使用正确的 category ID (4) 来查找角色 ↓↓↓
        # ==========================================================
        character_indices = tags_df[tags_df['category'] == 4].index.tolist()

        for filepath in tqdm(new_files, desc="Tagging images"):
            try:
                image = Image.open(filepath).convert("RGB")
                probabilities = predictor.predict(image)
                best_character_name = "others/oc"
                best_character_score = 0.0
                for i in character_indices:
                    prob = probabilities[i]
                    if prob > CHARACTER_CONFIDENCE_THRESHOLD and prob > best_character_score:
                        best_character_name = tags_df.loc[i, 'name']
                        best_character_score = prob
                cursor.execute("INSERT INTO images (filepath, character_name) VALUES (?, ?)", (filepath, best_character_name))
                image_id = cursor.lastrowid
                tags_to_insert = []
                for i, prob in enumerate(probabilities):
                    if prob > args.min_store_confidence:
                        tag_name = tags_df.loc[i, 'name']
                        tag_id = tag_to_id[tag_name]
                        tags_to_insert.append((image_id, tag_id, float(prob)))
                cursor.executemany("INSERT INTO image_tags (image_id, tag_id, confidence) VALUES (?, ?, ?)", tags_to_insert)
            except Exception as e:
                print(f"\nCould not process {filepath}: {e}")
            if len(new_files) > 100 and new_files.index(filepath) % 100 == 0:
                conn.commit()
        conn.commit()
    print("Indexing complete!")


def handle_search(args):
    # (search 命令无需修改)
    if not os.path.exists(DB_PATH):
        print("Database not found. Please run the 'index' command first."); return
    tags_to_search = [tag.strip().replace(" ", "_") for tag in args.tags.split(',')]
    num_tags = len(tags_to_search)
    query = f"SELECT i.filepath FROM images i JOIN image_tags it ON i.id = it.image_id JOIN tags t ON it.tag_id = t.id WHERE t.name IN ({','.join(['?']*num_tags)}) AND it.confidence >= ? GROUP BY i.id HAVING COUNT(t.id) = ?"
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        params = tags_to_search + [args.min_confidence, num_tags]
        cursor.execute(query, params)
        results = cursor.fetchall()
    if results:
        print(f"Found {len(results)} image(s) matching all tags:"); [print(row[0]) for row in results]
    else:
        print("No images found matching all the specified tags.")

def handle_test(args):
    # (test 命令处理逻辑已修正)
    if not os.path.exists(args.image_path):
        print(f"Error: Image file not found at '{args.image_path}'"); return

    print("Initializing model for testing...")
    predictor = Predictor()
    predictor.load_model()
    tags_df = predictor.tags_df

    print(f"\nProcessing image: {args.image_path}")
    try:
        image = Image.open(args.image_path).convert("RGB")
    except Exception as e:
        print(f"Error opening image: {e}"); return
    
    probabilities = predictor.predict(image)

    print("\n" + "="*40)
    print("        DETAILED TAGGING ANALYSIS")
    print("="*40)

    # ==========================================================
    #  ↓↓↓ 核心修正：使用正确的 category ID (4) 来分析角色 ↓↓↓
    # ==========================================================
    character_indices = tags_df[tags_df['category'] == 4].index.tolist()
    char_candidates = []
    for i in character_indices:
        if probabilities[i] > 0.1:
            char_candidates.append((tags_df.loc[i, 'name'], probabilities[i]))
    char_candidates.sort(key=lambda x: x[1], reverse=True)

    final_char_name = "others/oc"
    if char_candidates and char_candidates[0][1] > CHARACTER_CONFIDENCE_THRESHOLD:
        final_char_name = char_candidates[0][0]

    print(f"\n--- Character Recognition (Threshold: {CHARACTER_CONFIDENCE_THRESHOLD}) ---")
    print(f"-> Final Determined Character: {final_char_name.replace('_', ' ')}")
    
    print("\nTop Character Candidates:")
    if not char_candidates:
        print("No character tags found with confidence > 0.1")
    else:
        for name, prob in char_candidates[:5]:
            print(f"- {name.replace('_', ' '):<25} (Confidence: {prob:.4f})")

    # (评级和通用标签分析部分无需修改)
    rating_indices = tags_df[tags_df['category'] == 9].index.tolist()
    rating_probs = probabilities[rating_indices]
    best_rating_index = rating_probs.argmax().item()
    best_rating_tag = tags_df.loc[rating_indices[best_rating_index], 'name']
    best_rating_prob = rating_probs[best_rating_index].item()
    print("\n--- Rating ---")
    print(f"- {best_rating_tag.replace('_', ' '):<25} (Confidence: {best_rating_prob:.4f})")

    general_indices = tags_df[~tags_df['category'].isin([1, 4, 9])].index.tolist() # 排除版权(1), 角色(4), 评级(9)
    general_tags = []
    for i in general_indices:
        if probabilities[i] > 0.35:
            general_tags.append((tags_df.loc[i, 'name'], probabilities[i]))
    general_tags.sort(key=lambda x: x[1], reverse=True)

    print("\n--- Top 10 General Tags (Threshold > 0.35) ---")
    if not general_tags:
        print("No general tags found.")
    else:
        for name, prob in general_tags[:10]:
            print(f"- {name.replace('_', ' '):<25} (Confidence: {prob:.4f})")


def main():
    # (命令行解析部分不变)
    parser = argparse.ArgumentParser(description="A command-line tool to tag and search a local image library.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    parser_index = subparsers.add_parser("index", help="Scan for new images and add them to the database.")
    parser_index.add_argument("--min-store-confidence", type=float, default=0.1, help="Minimum confidence to store a tag in the DB.")
    parser_index.set_defaults(func=handle_index)
    parser_search = subparsers.add_parser("search", help="Search for images by tags (comma-separated).")
    parser_search.add_argument("tags", type=str, help="e.g., '1girl,rating:safe,blue_eyes'")
    parser_search.add_argument("--min-confidence", type=float, default=0.35, help="Minimum confidence for a tag to be considered in the search.")
    parser_search.set_defaults(func=handle_search)
    parser_test = subparsers.add_parser("test", help="Run tagger on a single image and print detailed results for verification.")
    parser_test.add_argument("image_path", type=str, help="The path to the image file to test.")
    parser_test.set_defaults(func=handle_test)
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()