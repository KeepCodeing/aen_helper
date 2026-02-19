# app.py
import argparse
import sys
import os
import config

# 1. 优先解析命令行参数
parser = argparse.ArgumentParser(description="AEN Helper Web Server (Portable Mode)")
parser.add_argument("--path", type=str, required=True, help="要挂载的本地图库目录绝对路径 (例如 E:/Photos)")
parser.add_argument("--port", type=int, default=5000, help="Web服务端口 (默认 5000)")
args = parser.parse_args()

target_dir = os.path.abspath(args.path)
if not os.path.exists(target_dir):
    print(f"❌ 错误: 找不到目标目录 '{target_dir}'")
    sys.exit(1)

# 2. 动态注入全局配置
config.PROJECT_PARENT_DIR = target_dir
data_dir = os.path.join(target_dir, config.DATA_DIR_NAME)
config.DB_PATH = os.path.join(data_dir, 'image_tags.db')
config.CACHE_FILE = os.path.join(data_dir, 'media_cache.json')
config.DB_CACHE_FILE = os.path.join(data_dir, 'db_cache.json')

# 确保隐藏数据目录存在
os.makedirs(data_dir, exist_ok=True)

print(f"=====================================")
print(f"🚀 挂载图库: {config.PROJECT_PARENT_DIR}")
print(f"📂 数据文件: {data_dir}")
print(f"=====================================")

# 3. 注入配置完成后，再导入路由和工具类
from flask import Flask
from routes import main_bp
from utils import scan_media_files

app = Flask(__name__)

# 注册蓝图并执行初始扫描
app.register_blueprint(main_bp)

# 在应用上下文中执行扫描，确保所有配置已就绪
with app.app_context():
    scan_media_files()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=args.port, debug=True, use_reloader=False)