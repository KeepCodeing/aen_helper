from flask import Flask
from utils import scan_media_files
from routes import main_bp

def create_app():
    app = Flask(__name__)
    
    # 启动时扫描文件
    scan_media_files()
    
    # 注册路由
    app.register_blueprint(main_bp)
    
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)