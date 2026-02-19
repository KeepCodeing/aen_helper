# config.py
import os

# 获取当前工作目录
BASE_DIR = os.getcwd()
# 项目父目录 (根据原逻辑)
PROJECT_PARENT_DIR = r"E:\rise and shine\2D\XP\Splus\AI"
# 媒体缓存文件
CACHE_FILE = 'media_cache.json'
# --- [新增] 数据库聚合查询缓存文件 ---
DB_CACHE_FILE = 'db_cache.json'

# 数据库路径
DB_PATH = "image_tags.db"
# 分页大小
PAGE_SIZE = 24