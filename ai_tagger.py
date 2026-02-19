# ai_tagger.py
import argparse
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
import sqlite3
import huggingface_hub
import numpy as np
import torch
import onnxruntime as rt
import pandas as pd
from PIL import Image
from tqdm import tqdm
import queue
import threading

# --- 配置 ---
MODEL_REPO = "SmilingWolf/wd-eva02-large-tagger-v3"
MODEL_FILENAME = "model.onnx"
LABEL_FILENAME = "selected_tags.csv"
CHARACTER_CONFIDENCE_THRESHOLD = 0.85
DATA_DIR_NAME = ".aen_data"
DB_NAME = "image_tags.db"

kaomojis = [
    "0_0", "(o)_(o)", "+_+", "+_-", "._.", "<o>_<o>", "<|>_<|>", "=_=", ">_<", "3_3",
    "6_9", ">_o", "@_@", "^_^", "o_o", "u_u", "x_x", "|_|", "||_||",
]

# --- 数据库操作 ---
def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL UNIQUE, rating TEXT, character_name TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS image_tags (image_id INTEGER, tag_id INTEGER, confidence REAL, FOREIGN KEY (image_id) REFERENCES images (id) ON DELETE CASCADE, FOREIGN KEY (tag_id) REFERENCES tags (id), PRIMARY KEY (image_id, tag_id))')
        conn.commit()

# --- 核心预测器类 ---
def load_labels(dataframe) -> tuple[list[str], list[int], list[int], list[int]]:
    name_series = dataframe["name"]
    name_series = name_series.map(lambda x: x.replace("_", " ") if x not in kaomojis else x)
    tag_names = name_series.tolist()
    rating_indexes = list(np.where(dataframe["category"] == 9)[0])
    general_indexes = list(np.where(dataframe["category"] == 0)[0])
    character_indexes = list(np.where(dataframe["category"] == 4)[0])
    return tag_names, rating_indexes, general_indexes, character_indexes

class Predictor:
    def __init__(self):
        self.model = None
        self.tag_names, self.rating_indexes, self.general_indexes, self.character_indexes = [], [], [], []
        self.model_target_size = None

    def load_model(self):
        if self.model: return
        print(f"Downloading and loading model '{MODEL_REPO}'...")
        csv_path = huggingface_hub.hf_hub_download(MODEL_REPO, LABEL_FILENAME)
        model_path = huggingface_hub.hf_hub_download(MODEL_REPO, MODEL_FILENAME)
        tags_df = pd.read_csv(csv_path)
        self.tag_names, self.rating_indexes, self.general_indexes, self.character_indexes = load_labels(tags_df)
        providers = ['CUDAExecutionProvider'] if 'CUDAExecutionProvider' in rt.get_available_providers() else ['CPUExecutionProvider']
        print(f"Using ONNX provider: {providers[0]}")
        self.model = rt.InferenceSession(model_path, providers=providers)
        _, height, _, _ = self.model.get_inputs()[0].shape
        self.model_target_size = height
        print(f"Model loaded. Target image size: {self.model_target_size}x{self.model_target_size}")

    def prepare_image(self, image: Image.Image) -> np.ndarray:
        target_size = self.model_target_size
        canvas = Image.new("RGBA", image.size, (255, 255, 255))
        canvas.alpha_composite(image)
        image = canvas.convert("RGB")
        max_dim = max(image.size)
        pad_left = (max_dim - image.size[0]) // 2
        pad_top = (max_dim - image.size[1]) // 2
        padded_image = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
        padded_image.paste(image, (pad_left, pad_top))
        if max_dim != target_size:
            padded_image = padded_image.resize((target_size, target_size), Image.BICUBIC)
        image_array = np.asarray(padded_image, dtype=np.float32)
        return image_array[:, :, ::-1]

    def predict_batch(self, image_arrays: list[np.ndarray]):
        batch_array = np.stack(image_arrays, axis=0)
        input_name = self.model.get_inputs()[0].name
        label_name = self.model.get_outputs()[0].name
        preds = self.model.run([label_name], {input_name: batch_array})[0]
        
        results = []
        for p in preds:
            labels = list(zip(self.tag_names, p.astype(float)))
            ratings = dict([labels[i] for i in self.rating_indexes])
            general_names = [labels[i] for i in self.general_indexes]
            character_names = [labels[i] for i in self.character_indexes]
            results.append((ratings, general_names, character_names))
        return results

# --- 多线程工作函数 ---
def _worker_process(path_queue, result_queue, predictor):
    while True:
        try:
            # 获取 绝对路径 和 相对路径
            abs_path, rel_path = path_queue.get_nowait()
        except queue.Empty:
            break

        try:
            with Image.open(abs_path) as img:
                image = img.convert("RGBA")
            arr = predictor.prepare_image(image)
            # 存入队列的是 numpy 数组和 相对路径
            result_queue.put((arr, rel_path)) 
        except Exception:
            result_queue.put((None, rel_path))
        finally:
            path_queue.task_done()

# --- 主逻辑 ---
def handle_index(args):
    target_dir = os.path.abspath(args.target_dir)
    if not os.path.exists(target_dir):
        print(f"Error: Target directory '{target_dir}' does not exist.")
        return

    db_path = os.path.join(target_dir, DATA_DIR_NAME, DB_NAME)
    print(f"Target Directory: {target_dir}")
    print(f"Database Path: {db_path}")
    
    init_db(db_path)
    
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT filepath FROM images")
        indexed_files = {row[0] for row in cursor.fetchall()}
    print(f"Found {len(indexed_files)} images already in the database.")

    # 扫描文件 (转换为相对路径)
    new_files_data = [] # 存储 (abs_path, rel_path)
    supported_exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
    
    for root, dirs, files in os.walk(target_dir, topdown=True):
        # 忽略专属数据文件夹
        if DATA_DIR_NAME in dirs: dirs.remove(DATA_DIR_NAME)
        
        for file in files:
            if file.lower().endswith(supported_exts):
                abs_path = os.path.join(root, file)
                # 计算相对路径，并统一为正斜杠存储
                rel_path = os.path.relpath(abs_path, target_dir).replace('\\', '/')
                
                if rel_path not in indexed_files:
                    new_files_data.append((abs_path, rel_path))
    
    if not new_files_data:
        print("No new images to index.")
        return
    
    print(f"Found {len(new_files_data)} new images. Starting AI tagging...")
    predictor = Predictor()
    predictor.load_model()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name, id FROM tags")
    tag_to_id_base = {name: id for name, id in cursor.fetchall()}

    path_queue = queue.Queue()
    result_queue = queue.Queue(maxsize=args.batch_size * 4)

    for item in new_files_data:
        path_queue.put(item)

    threads = []
    for _ in range(args.num_workers):
        t = threading.Thread(target=_worker_process, args=(path_queue, result_queue, predictor))
        t.start()
        threads.append(t)

    pbar = tqdm(total=len(new_files_data), desc="Tagging Images")
    processed_count = 0
    total_files = len(new_files_data)

    while processed_count < total_files:
        batch_arrays = []
        batch_paths = [] # 这里装的是 rel_path
        
        while len(batch_paths) < args.batch_size and processed_count < total_files:
            arr, rel_path = result_queue.get()
            processed_count += 1
            pbar.update(1)
            
            if arr is not None:
                batch_arrays.append(arr)
                batch_paths.append(rel_path)

        if not batch_arrays:
            continue

        batch_results = predictor.predict_batch(batch_arrays)
        
        for i, rel_path in enumerate(batch_paths):
            ratings, general_names, character_names = batch_results[i]
            
            general_res = {name: prob for name, prob in general_names if prob > args.general_thresh}
            character_res = {name: prob for name, prob in character_names if prob > CHARACTER_CONFIDENCE_THRESHOLD}

            best_rating = max(ratings, key=ratings.get) if ratings else "unknown"
            sorted_chars = sorted(character_res.items(), key=lambda x: x[1], reverse=True)
            best_char = sorted_chars[0][0] if sorted_chars else "others/oc"

            cursor.execute("INSERT INTO images (filepath, rating, character_name) VALUES (?, ?, ?)",(rel_path, best_rating, best_char))
            image_id = cursor.lastrowid
            
            tags_to_insert = []
            for tag_name, confidence in general_res.items():
                tag_id = tag_to_id_base.get(tag_name)
                if tag_id is None:
                    cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
                    tag_id = cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()[0]
                    tag_to_id_base[tag_name] = tag_id
                tags_to_insert.append((image_id, tag_id, confidence))
            
            if tags_to_insert:
                cursor.executemany("INSERT OR IGNORE INTO image_tags (image_id, tag_id, confidence) VALUES (?, ?, ?)", tags_to_insert)
        
        conn.commit() 
        del batch_arrays
        del batch_results

    for t in threads:
        t.join()

    pbar.close()
    conn.close()
    print(f"Indexing complete! {processed_count} new images were tagged.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Image Tagger for AEN Helper (Portable Library Mode).")
    
    # 强制要求输入目标目录
    parser.add_argument("--target-dir", type=str, required=True, help="Target directory to scan and create/update the database.")
    
    parser.add_argument("--general-thresh", type=float, default=0.35, help="Threshold for general tags.")
    parser.add_argument("--batch-size", type=int, default=32, help="Images per GPU batch.")
    parser.add_argument("--num-workers", type=int, default=8, help="CPU cores for preprocessing.")
    
    args = parser.parse_args()
    handle_index(args)