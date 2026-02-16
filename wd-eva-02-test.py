# image_database_onnx_optimized.py
import argparse
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
import sqlite3
import huggingface_hub
import numpy as np
import torch  # 导入torch以帮助onnxruntime找到CUDA依赖
import onnxruntime as rt
import pandas as pd
from PIL import Image
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

# --- 配置 ---
MODEL_REPO = "SmilingWolf/wd-eva02-large-tagger-v3"
MODEL_FILENAME = "model.onnx"
LABEL_FILENAME = "selected_tags.csv"
DB_PATH = "image_tags.db"
CHARACTER_CONFIDENCE_THRESHOLD = 0.85

kaomojis = [
    "0_0", "(o)_(o)", "+_+", "+_-", "._.", "<o>_<o>", "<|>_<|>", "=_=", ">_<", "3_3",
    "6_9", ">_o", "@_@", "^_^", "o_o", "u_u", "x_x", "|_|", "||_||",
]

# --- 数据库操作 (不变) ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL UNIQUE, rating TEXT, character_name TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS image_tags (image_id INTEGER, tag_id INTEGER, confidence REAL, FOREIGN KEY (image_id) REFERENCES images (id) ON DELETE CASCADE, FOREIGN KEY (tag_id) REFERENCES tags (id), PRIMARY KEY (image_id, tag_id))')
        conn.commit()

# --- 核心预测器类 (已修改为支持批处理) ---
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
        return image_array[:, :, ::-1] # RGB to BGR, but without the batch dimension

    def predict_batch(self, image_arrays: list[np.ndarray]):
        # 将多个numpy数组堆叠成一个批次
        batch_array = np.stack(image_arrays, axis=0)
        
        input_name = self.model.get_inputs()[0].name
        label_name = self.model.get_outputs()[0].name
        
        preds = self.model.run([label_name], {input_name: batch_array})[0]
        # preds 的形状现在是 (batch_size, num_tags)
        
        results = []
        for p in preds:
            labels = list(zip(self.tag_names, p.astype(float)))
            ratings = dict([labels[i] for i in self.rating_indexes])
            general_names = [labels[i] for i in self.general_indexes]
            character_names = [labels[i] for i in self.character_indexes]
            results.append((ratings, general_names, character_names))
        return results

# --- 命令行处理函数 (已重构) ---
def _prepare_single_image(filepath, predictor):
    """辅助函数，用于在子线程中加载和预处理单张图片"""
    try:
        image = Image.open(filepath).convert("RGBA")
        return predictor.prepare_image(image), filepath
    except Exception:
        # 忽略损坏的图片
        return None, filepath

def handle_index(args):
    """处理 index 子命令: 使用批处理和并行加载"""
    print("Initializing database...")
    init_db()
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT filepath FROM images")
        indexed_files = {row[0] for row in cursor.fetchall()}
    print(f"Found {len(indexed_files)} images already in the database.")

    # (扫描文件部分不变)
    image_paths = []
    supported_exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
    project_dir_name = os.path.basename(os.getcwd())
    scan_path = os.path.abspath('..')
    for root, dirs, files in os.walk(scan_path, topdown=True):
        if project_dir_name in dirs: dirs.remove(project_dir_name)
        for file in files:
            if file.lower().endswith(supported_exts):
                image_paths.append(os.path.abspath(os.path.join(root, file)))
    
    new_files = sorted(list(set(image_paths) - indexed_files))
    if not new_files:
        print("No new images to index."); return
    
    print(f"Found {len(new_files)} new images. Starting optimized tagging process...")
    predictor = Predictor()
    predictor.load_model() # 提前加载模型

    # 创建数据库连接和标签查找字典
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, id FROM tags")
    tag_to_id_base = {name: id for name, id in cursor.fetchall()}

    processed_count = 0
    # 使用线程池进行并行预处理
    with ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        # 创建一个迭代器，它会在后台加载和预处理图片
        image_iterator = executor.map(lambda p: _prepare_single_image(p, predictor), new_files)
        
        pbar = tqdm(total=len(new_files), desc="Tagging Images")
        
        while True:
            batch_arrays = []
            batch_paths = []
            
            # 从迭代器中收集一个批次的预处理数据
            try:
                for _ in range(args.batch_size):
                    prepared_array, filepath = next(image_iterator)
                    if prepared_array is not None:
                        batch_arrays.append(prepared_array)
                        batch_paths.append(filepath)
                    pbar.update(1)
            except StopIteration:
                # 所有图片都已处理
                pass

            if not batch_arrays:
                break # 结束循环

            # 对收集到的批次进行GPU推理
            batch_results = predictor.predict_batch(batch_arrays)
            
            # 将批次结果存入数据库
            for i, filepath in enumerate(batch_paths):
                ratings, general_names, character_names = batch_results[i]
                
                general_res = {name: prob for name, prob in general_names if prob > args.general_thresh}
                character_res = {name: prob for name, prob in character_names if prob > CHARACTER_CONFIDENCE_THRESHOLD}

                best_rating = max(ratings, key=ratings.get) if ratings else "unknown"
                sorted_chars = sorted(character_res.items(), key=lambda x: x[1], reverse=True)
                best_char = sorted_chars[0][0] if sorted_chars else "others/oc"

                cursor.execute("INSERT INTO images (filepath, rating, character_name) VALUES (?, ?, ?)",(filepath, best_rating, best_char))
                image_id = cursor.lastrowid
                
                tags_to_insert = []
                for tag_name, confidence in general_res.items():
                    tag_id = tag_to_id_base.get(tag_name)
                    if tag_id is None: # 如果是新标签
                        cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
                        tag_id = cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()[0]
                        tag_to_id_base[tag_name] = tag_id
                    tags_to_insert.append((image_id, tag_id, confidence))
                
                if tags_to_insert:
                    cursor.executemany("INSERT OR IGNORE INTO image_tags (image_id, tag_id, confidence) VALUES (?, ?, ?)", tags_to_insert)
            
            processed_count += len(batch_paths)
            conn.commit() # 每个批次提交一次
            
            # ============================================
            #  ↓↓↓ 在此处添加短暂休眠 ↓↓↓
            # ============================================
            #time.sleep(0.05) # 休眠50毫秒

    pbar.close()
    conn.close()
    print(f"Indexing complete! {processed_count} new images were tagged.")

# ==========================================================
#  ↓↓↓ 新增的 search 命令处理函数 ↓↓↓
# ==========================================================
def handle_search(args):
    """处理 search 子命令：根据复合标签搜索图片"""
    if not os.path.exists(DB_PATH):
        print("Database not found. Please run the 'index' command first."); return
    
    # 1. 解析和分类用户输入的标签
    raw_tags = [tag.strip() for tag in args.tags.split(',')]
    rating_filters = []
    char_filters = []
    general_tags = []
    
    for tag in raw_tags:
        if tag.startswith("rating:"):
            rating_filters.append(tag)
        elif tag.startswith("char:"):
            # 去掉前缀 "char:" 并将空格替换回下划线以匹配数据库
            char_filters.append(tag[5:].strip().replace(" ", "_"))
        else:
            # 通用标签，空格也替换为下划线
            general_tags.append(tag.strip().replace(" ", "_"))

    # 2. 动态构建SQL查询
    base_query = "SELECT DISTINCT T0.filepath FROM images AS T0"
    params = []
    where_clauses = []
    
    # 2a. 构建通用标签的子查询
    if general_tags:
        # 这个子查询找出包含所有通用标签的 image_id
        general_subquery = f"""
            SELECT it.image_id
            FROM image_tags it
            JOIN tags t ON it.tag_id = t.id
            WHERE t.name IN ({','.join(['?']*len(general_tags))})
            GROUP BY it.image_id
            HAVING COUNT(it.image_id) = ?
        """
        # 将子查询作为主查询的一部分
        where_clauses.append(f"T0.id IN ({general_subquery})")
        params.extend(general_tags)
        params.append(len(general_tags))

    # 2b. 添加对 rating 和 character 的直接过滤
    for rating in rating_filters:
        where_clauses.append("T0.rating = ?")
        params.append(rating)
        
    for char in char_filters:
        where_clauses.append("T0.character_name = ?")
        params.append(char)
        
    # 3. 组合并执行查询
    if where_clauses:
        final_query = f"{base_query} WHERE {' AND '.join(where_clauses)}"
    else:
        print("Please provide tags to search for.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(final_query, params)
        results = cursor.fetchall()

    if results:
        print(f"\nFound {len(results)} image(s) matching all criteria:")
        for row in results:
            print(row[0])
    else:
        print("\nNo images found matching all the specified criteria.")


# --- main 函数 (已更新) ---
def main():
    parser = argparse.ArgumentParser(description="A tool to tag and search a local image library.")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")
    
    # index 命令
    parser_index = subparsers.add_parser("index", help="Scan and tag new images.")
    parser_index.add_argument("--general-thresh", type=float, default=0.35, help="Threshold for general tags.")
    parser_index.add_argument("--batch-size", type=int, default=32, help="Images per GPU batch.")
    parser_index.add_argument("--num-workers", type=int, default=8, help="CPU cores for preprocessing.")
    
    # search 命令
    parser_search = subparsers.add_parser("search", help="Search for images by tags.")
    parser_search.add_argument("tags", type=str, help="Comma-separated tags. Use 'rating:' and 'char:' prefixes. E.g., '1girl,rating:safe,char:tokoyami towa'")

    args = parser.parse_args()
    if args.command == "index":
        handle_index(args)
    elif args.command == "search":
        handle_search(args)

if __name__ == "__main__":
    main()