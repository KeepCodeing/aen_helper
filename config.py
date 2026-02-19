# config.py
import os

BASE_DIR = os.getcwd()
DATA_DIR_NAME = ".aen_data"

# 相册注册表路径
ALBUMS_FILE = os.path.join(BASE_DIR, "albums.json")

# 动态挂载变量
PROJECT_PARENT_DIR = ""
DB_PATH = ""
CACHE_FILE = ""
DB_CACHE_FILE = ""

PAGE_SIZE = 24

# --- 新增：打标任务的全局状态 ---
CURRENT_TASK = {
    "is_running": False,
    "target_dir": "",
    "output": "等待启动...",
    "process": None
}