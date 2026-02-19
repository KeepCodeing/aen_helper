# config.py
import os

BASE_DIR = os.getcwd()

# 专属数据文件夹名称
DATA_DIR_NAME = ".aen_data"

# 这些变量将在 app.py 启动时被动态赋值，不要在这里写死了
PROJECT_PARENT_DIR = ""
DB_PATH = ""
CACHE_FILE = ""
DB_CACHE_FILE = ""

# 分页大小
PAGE_SIZE = 24