import os
import random
import sqlite3
import json
import sys # 【新增】用于获取当前 Python 虚拟环境的绝对路径
import subprocess
import threading
import urllib.parse # 确保顶部有这个导入
from urllib.parse import unquote
from flask import Blueprint, render_template, jsonify, request, send_file, redirect, url_for

# 引入本地配置和工具
import config
# [核心修改] 引入 to_web_path
from utils import MediaState, scan_media_files, get_paginated_list, get_db_connection, natural_sort_key, to_web_path, get_character_groups, get_folders_by_character, DBCache, search_files_paged, search_folders, get_directory_tree

# 创建蓝图
main_bp = Blueprint('main', __name__)

# --- [新增] 应用启动时的钩子 ---
@main_bp.record
def on_blueprint_setup(setup_state):
    """蓝图注册时初始化缓存"""
    print("Initializing Database Cache...")
    DBCache.load()

# ==========================================
#  页面路由 (Page Routes)
# ==========================================

@main_bp.route('/')
def index():
    """首页：随机图片"""
    return render_template('index.html')

@main_bp.route('/slideshow')
def slideshow_page():
    return render_template('index.html', mode='slideshow') 

@main_bp.route('/grid')
def grid_page():
    """所有图片网格"""
    return render_template('grid.html', 
                         page_title="所有图片", 
                         api_url="/api/images")

@main_bp.route('/videos')
def video_grid_page():
    """所有视频网格"""
    return render_template('grid.html', 
                         page_title="所有视频", 
                         api_url="/api/videos")

@main_bp.route('/folder/<path:folder_path>')
def folder_view_page(folder_path):
    """文件夹视图"""
    # 1. 明确使用 unquote 处理路径，确保 #, %20 等字符恢复原貌
    decoded_path = unquote(folder_path)
    clean_path = to_web_path(decoded_path)
    
    return render_template('grid.html', 
                         page_title=f"📁 {clean_path}", 
                         # 2. 传给前端 API 的 URL 也要再次编码
                         api_url=f"/api/folder_data?path={clean_path}")

# --- 页面路由 ---

@main_bp.route('/search')
def search_page():
    """搜索结果页 (图片流模式)"""
    query = request.args.get('q', '')
    folder = request.args.get('folder', None) # [新增] 接收文件夹参数
    
    if query:
        api_url = f"/api/search?q={urllib.parse.quote(query)}"
        # [新增] 如果存在文件夹限制，拼接到 API URL 并修改页面标题
        if folder is not None:
            api_url += f"&folder={urllib.parse.quote(folder)}"
            display_folder = folder if folder else "根目录"
            title = f"搜索: {query} ({display_folder})"
        else:
            title = f"搜索: {query}"
    else:
        api_url = "" 
        title = "搜索"
        
    return render_template('grid.html', page_title=title, api_url=api_url, is_search=True, search_query=query)

@main_bp.route('/search/folders')
def search_folders_page():
    """[新增] 搜索结果页 (文件夹模式)"""
    query = request.args.get('q', '')
    return render_template('search_folders.html', 
                         search_query=query,
                         page_title=f"搜索文件夹: {query}")

# --- API 接口 ---

@main_bp.route('/api/search')
def api_search():
    """搜索文件 API"""
    page = request.args.get('page', 1, type=int)
    query_str = request.args.get('q', '', type=str)
    folder = request.args.get('folder', None) # [新增] 接收文件夹参数
    
    if not query_str: return jsonify({'files': [], 'has_more': False})

    # [修改] 传递 folder 变量给查询函数
    files, has_more = search_files_paged(query_str, page, config.PAGE_SIZE, folder_filter=folder)
    return jsonify({'files': files, 'has_more': has_more})

@main_bp.route('/api/search/folders')
def api_search_folders():
    """[新增] 搜索文件夹 API"""
    query_str = request.args.get('q', '', type=str)
    if not query_str: return jsonify([])
    
    folders = search_folders(query_str)
    return jsonify(folders)
    
# --- 角色相关页面 ---

@main_bp.route('/tags')
def tags_index_page():
    return render_template('tags.html', page_title="角色索引")

@main_bp.route('/tags/random/<path:character_name>')
def character_gallery_page(character_name):
    decoded_name = unquote(character_name)
    # 新增传入 character_name
    return render_template('grid.html', 
                         page_title=f"角色: {decoded_name}", 
                         api_url=f"/api/character_images/{character_name}",
                         character_name=decoded_name)

@main_bp.route('/tags/folders/<path:character_name>')
def character_folders_page(character_name):
    decoded_name = unquote(character_name)
    return render_template('character_folders.html', 
                         character_name=decoded_name,
                         page_title=f"{decoded_name} 的图集")

@main_bp.route('/rescan')
def rescan():
    scan_media_files(force_rescan=True)
    DBCache.load(force_rescan=True) # 强制刷新数据库查询缓存并覆盖本地文件
    referrer = request.headers.get("Referer")
    if referrer:
        return redirect(referrer)
    return redirect(url_for('main.index'))

# ==========================================
#  API 接口 (JSON Endpoints)
# ==========================================

@main_bp.route('/api/random-image')
def api_random_image():
    if not MediaState.image_files:
        return jsonify({'error': 'No images found'}), 404
    chosen_path = random.choice(MediaState.image_files)
    folder_path = os.path.dirname(chosen_path)
    return jsonify({'path': chosen_path, 'folder': folder_path.replace('\\', '/')})

@main_bp.route('/api/images')
def api_get_images():
    page = request.args.get('page', 1, type=int)
    seed = request.args.get('seed', None)
    items, has_more = get_paginated_list(MediaState.image_files, page, config.PAGE_SIZE, seed=seed)
    return jsonify({'files': items, 'has_more': has_more})

@main_bp.route('/api/videos')
def api_get_videos():
    page = request.args.get('page', 1, type=int)
    seed = request.args.get('seed', None)
    items, has_more = get_paginated_list(MediaState.video_and_gif_files, page, config.PAGE_SIZE, seed=seed)
    return jsonify({'files': items, 'has_more': has_more})

@main_bp.route('/api/folder_data')
def api_folder_data():
    """获取指定文件夹内容 (混合图片和视频)"""
    raw_folder_path = request.args.get('path', '')
    page = request.args.get('page', 1, type=int)
    
    if not raw_folder_path:
        return jsonify({'files': [], 'has_more': False})

    # [核心修复] 关键步骤：
    # 无论前端传给我们的是 'E:/rise/NFFA' (绝对) 还是 'NFFA' (相对)
    # to_web_path 都会把它统一变成 'NFFA' (相对)
    clean_target_dir = to_web_path(unquote(raw_folder_path))

    # 简单的筛选逻辑
    all_media = MediaState.image_files + MediaState.video_and_gif_files
    filtered_files = []
    
    # 遍历所有缓存的文件 (这些已经是相对路径了)
    for file_path in all_media:
        # 获取该文件的相对目录
        file_dir = os.path.dirname(file_path).replace('\\', '/')
        
        # 对比：现在两边都是相对路径了，可以成功匹配
        if file_dir == clean_target_dir:
            filtered_files.append(file_path)
            
    filtered_files.sort(key=natural_sort_key)
    items, has_more = get_paginated_list(filtered_files, page, config.PAGE_SIZE)
    return jsonify({'files': items, 'has_more': has_more})

@main_bp.route('/api/character_images/<path:character_name>')
def api_character_images(character_name):
    """获取特定角色的图片 (查库)"""
    conn = get_db_connection()
    if conn is None: return jsonify([]), 404
        
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * config.PAGE_SIZE
    db_char_name = character_name.replace('_', ' ')

    query = "SELECT filepath FROM images WHERE character_name = ? ORDER BY id DESC LIMIT ? OFFSET ?"
    try:
        cursor = conn.execute(query, (db_char_name, config.PAGE_SIZE, offset))
        images = cursor.fetchall()
        # [修复] 同样记得转换路径
        results = [to_web_path(row['filepath']) for row in images]
        has_more = len(results) == config.PAGE_SIZE
        return jsonify({'files': results, 'has_more': has_more})
    except Exception as e:
        print(f"Char image error: {e}")
        return jsonify({'files': [], 'has_more': False})
    finally:
        conn.close()

@main_bp.route('/api/character_folders/<path:character_name>')
def api_character_folders(character_name):
    decoded_name = character_name
    data = get_folders_by_character(decoded_name)
    return jsonify(data)

@main_bp.route('/api/tags/all')
def api_tags_all():
    return jsonify(DBCache.get_all_tags())

@main_bp.route('/api/characters')
def api_characters():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '', type=str)
    chars = get_character_groups(page, config.PAGE_SIZE, search_query=search)
    has_more = len(chars) == config.PAGE_SIZE
    return jsonify({'data': chars, 'has_more': has_more})

# ==========================================
#  静态文件服务 (Static Files)
# ==========================================

@main_bp.route('/media/<path:filepath>')
def serve_media(filepath):
    decoded_filepath = unquote(filepath)
    # 将前端传来的相对路径，与当前挂载的根目录拼合成绝对路径读取
    absolute_path = os.path.abspath(os.path.join(config.PROJECT_PARENT_DIR, decoded_filepath))
    
    # 安全校验：确保请求的文件在挂载的目录下，防止路径穿越攻击 (../../)
    if not absolute_path.startswith(os.path.abspath(config.PROJECT_PARENT_DIR)):
        return "Forbidden", 403
    
    if os.path.exists(absolute_path):
        return send_file(absolute_path)
    else:
        return "File not found", 404
        
@main_bp.route('/explore')
@main_bp.route('/explore/<path:subpath>')
def explore_page(subpath=""):
    folders, immediate_files = get_directory_tree(subpath)
    
    # 【核心逻辑】：如果当前目录下全是文件，没有子文件夹了（倒数第二级）
    # 自动重定向到现有的图片网格页进行浏览
    if not folders and immediate_files:
        # 直接跳转到该文件夹的图片流 (调用已有的 /folder/ 路由)
        return redirect(f"/folder/{urllib.parse.quote(subpath)}")
        
    # 计算用于“返回上一级”的父路径
    parent_path = ""
    if subpath:
        parts = subpath.rstrip('/').split('/')
        if len(parts) > 1:
            parent_path = '/'.join(parts[:-1])
        else:
            parent_path = ""
            
    return render_template('explore.html', 
                           folders=folders, 
                           has_immediate_files=len(immediate_files) > 0,
                           current_path=subpath,
                           parent_path=parent_path,
                           page_title=f"目录: {subpath}" if subpath else "本地目录")
                           
# ==========================================
#  系统设置与任务控制 API (新增)
# ==========================================

@main_bp.route('/settings')
def settings_page():
    """配置与相册管理页面"""
    return render_template('settings.html', page_title="设置与相册管理")

@main_bp.route('/api/albums', methods=['GET'])
def api_get_albums():
    """读取已保存的相册列表"""
    if os.path.exists(config.ALBUMS_FILE):
        with open(config.ALBUMS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return jsonify(data)
    return jsonify({"albums": []})

@main_bp.route('/api/mount', methods=['POST'])
def api_mount_album():
    """挂载指定的相册目录"""
    data = request.json
    target_dir = data.get('path', '').strip()
    
    if not os.path.exists(target_dir):
        return jsonify({"success": False, "message": "目录不存在"}), 400
        
    db_path = os.path.join(target_dir, config.DATA_DIR_NAME, 'image_tags.db')
    if not os.path.exists(db_path):
        return jsonify({"success": False, "message": "该目录下没有找到数据库，请先执行打标"}), 400

    # 动态修改全局配置
    config.PROJECT_PARENT_DIR = target_dir
    data_dir = os.path.join(target_dir, config.DATA_DIR_NAME)
    config.DB_PATH = db_path
    config.CACHE_FILE = os.path.join(data_dir, 'media_cache.json')
    config.DB_CACHE_FILE = os.path.join(data_dir, 'db_cache.json')
    
    # 执行一次扫描刷新缓存
    from utils import scan_media_files
    scan_media_files()
    
    return jsonify({"success": True, "message": f"成功挂载: {target_dir}"})

# --- 后台打标进程控制 ---

def _read_subprocess_output(process):
    """后台线程：实时读取打标程序的输出日志 (修复版)"""
    for line in iter(process.stdout.readline, ''):
        if line.strip(): # 过滤掉空行
            config.CURRENT_TASK["output"] = line.strip()
            
    process.stdout.close()
    return_code = process.wait() # 获取进程退出码
    
    config.CURRENT_TASK["is_running"] = False
    
    # 【修复】根据退出码判断是真完成还是崩溃了
    if return_code == 0:
        config.CURRENT_TASK["output"] = "✅ 打标任务已成功完成！"
    else:
        config.CURRENT_TASK["output"] = f"❌ 任务异常终止 (Exit Code: {return_code})。请检查依赖或路径。"

@main_bp.route('/api/tagger/start', methods=['POST'])
def api_start_tagger():
    """启动本地 AI 打标程序 (修复版)"""
    if config.CURRENT_TASK["is_running"]:
        return jsonify({"success": False, "message": "当前已有打标任务正在运行"}), 400
        
    data = request.json
    target_dir = data.get('path', '').strip()
    
    if not target_dir or not os.path.exists(target_dir):
        return jsonify({"success": False, "message": "无效的目录"}), 400

    try:
        # 【核心修复】：使用 sys.executable 确保子进程使用与 Flask 完全相同的 Python 虚拟环境
        process = subprocess.Popen(
            [sys.executable, "-u", "ai_tagger.py", "--target-dir", target_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, 
            text=True,
            bufsize=1
        )
        
        config.CURRENT_TASK["is_running"] = True
        config.CURRENT_TASK["target_dir"] = target_dir
        config.CURRENT_TASK["process"] = process
        config.CURRENT_TASK["output"] = "正在初始化 AI 模型，请稍候..."
        
        threading.Thread(target=_read_subprocess_output, args=(process,), daemon=True).start()
        
        with open(config.ALBUMS_FILE, 'r+', encoding='utf-8') as f:
            db_data = json.load(f)
            if target_dir not in db_data.get("albums", []):
                db_data.setdefault("albums", []).append(target_dir)
                f.seek(0)
                json.dump(db_data, f, ensure_ascii=False, indent=4)
                f.truncate()
        
        return jsonify({"success": True, "message": "打标任务已启动"})
    except Exception as e:
        return jsonify({"success": False, "message": f"启动失败: {str(e)}"}), 500

@main_bp.route('/api/tagger/status', methods=['GET'])
def api_tagger_status():
    """前端轮询获取当前打标进度的接口"""
    return jsonify({
        "is_running": config.CURRENT_TASK["is_running"],
        "target_dir": config.CURRENT_TASK["target_dir"],
        "output": config.CURRENT_TASK["output"]
    })

@main_bp.route('/api/albums', methods=['DELETE'])
def api_delete_album():
    """从配置中移除指定的相册路径"""
    data = request.json
    target_dir = data.get('path', '').strip()
    
    if os.path.exists(config.ALBUMS_FILE):
        with open(config.ALBUMS_FILE, 'r+', encoding='utf-8') as f:
            db_data = json.load(f)
            albums = db_data.get("albums", [])
            if target_dir in albums:
                albums.remove(target_dir)
                f.seek(0)
                json.dump({"albums": albums}, f, ensure_ascii=False, indent=4)
                f.truncate()
                return jsonify({"success": True, "message": "移除成功"})
                
    return jsonify({"success": False, "message": "相册不存在于记录中"}), 400