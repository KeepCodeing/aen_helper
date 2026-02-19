import os
import random
import sqlite3
import json
import urllib.parse # 确保顶部有这个导入
from urllib.parse import unquote
from flask import Blueprint, render_template, jsonify, request, send_file, redirect, url_for

# 引入本地配置和工具
from config import PROJECT_PARENT_DIR, DB_PATH, PAGE_SIZE
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
    files, has_more = search_files_paged(query_str, page, PAGE_SIZE, folder_filter=folder)
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
    items, has_more = get_paginated_list(MediaState.image_files, page, PAGE_SIZE, seed=seed)
    return jsonify({'files': items, 'has_more': has_more})

@main_bp.route('/api/videos')
def api_get_videos():
    page = request.args.get('page', 1, type=int)
    seed = request.args.get('seed', None)
    items, has_more = get_paginated_list(MediaState.video_and_gif_files, page, PAGE_SIZE, seed=seed)
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
    items, has_more = get_paginated_list(filtered_files, page, PAGE_SIZE)
    return jsonify({'files': items, 'has_more': has_more})

@main_bp.route('/api/character_images/<path:character_name>')
def api_character_images(character_name):
    """获取特定角色的图片 (查库)"""
    conn = get_db_connection()
    if conn is None: return jsonify([]), 404
        
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * PAGE_SIZE
    db_char_name = character_name.replace('_', ' ')

    query = "SELECT filepath FROM images WHERE character_name = ? ORDER BY id DESC LIMIT ? OFFSET ?"
    try:
        cursor = conn.execute(query, (db_char_name, PAGE_SIZE, offset))
        images = cursor.fetchall()
        # [修复] 同样记得转换路径
        results = [to_web_path(row['filepath']) for row in images]
        has_more = len(results) == PAGE_SIZE
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
    chars = get_character_groups(page, PAGE_SIZE, search_query=search)
    has_more = len(chars) == PAGE_SIZE
    return jsonify({'data': chars, 'has_more': has_more})

# ==========================================
#  静态文件服务 (Static Files)
# ==========================================

@main_bp.route('/media/<path:filepath>')
def serve_media(filepath):
    decoded_filepath = unquote(filepath)
    normalized_filepath = os.path.normpath(decoded_filepath)
    absolute_path = os.path.join(PROJECT_PARENT_DIR, normalized_filepath)
    
    if not os.path.abspath(absolute_path).startswith(os.path.abspath(PROJECT_PARENT_DIR)):
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