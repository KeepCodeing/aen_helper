# app.py
import argparse
import sys
import os
import json
import config

parser = argparse.ArgumentParser(description="AEN Helper Web Server (Multi-Album Mode)")
parser.add_argument("--port", type=int, default=5000, help="Web服务端口 (默认 5000)")
args = parser.parse_args()

# 确保 albums.json 存在
if not os.path.exists(config.ALBUMS_FILE):
    with open(config.ALBUMS_FILE, 'w', encoding='utf-8') as f:
        json.dump({"albums": []}, f)

# 初始挂载状态为空
config.PROJECT_PARENT_DIR = ""

from flask import Flask, redirect
from routes import main_bp

app = Flask(__name__)
app.register_blueprint(main_bp)

# 增加一个全局拦截器：如果未挂载相册，访问首页自动跳到设置页
@app.before_request
def check_mount():
    from flask import request
    # 允许访问静态文件、设置页和相关的 API
    allowed_prefixes = ['/settings', '/api/albums', '/api/mount', '/api/tagger', '/static']
    
    if not config.PROJECT_PARENT_DIR:
        if request.path == '/' or not any(request.path.startswith(p) for p in allowed_prefixes):
            return redirect('/settings')

if __name__ == '__main__':
    print(f"=====================================")
    print(f"🚀 AEN Helper 启动成功")
    print(f"👉 请在浏览器访问: http://127.0.0.1:{args.port}/settings")
    print(f"=====================================")
    app.run(host='0.0.0.0', port=args.port, debug=True, use_reloader=False)