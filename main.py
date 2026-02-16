import os
import random
import json
import sqlite3
import re
from urllib.parse import unquote, urlencode
from flask import Flask, send_from_directory, redirect, url_for, jsonify, render_template_string, request, send_file

# --- 配置 ---
CACHE_FILE = 'media_cache.json'
DB_PATH = "test.db"
PAGE_SIZE = 24
PROJECT_PARENT_DIR = os.path.abspath('..')
PROJECT_DIR_NAME = os.path.basename(os.getcwd())

app = Flask(__name__)

# --- 数据库与后端逻辑 ---
def get_db_connection():
    if not os.path.exists(DB_PATH): return None
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def scan_media_files(force_rescan=False):
    if not force_rescan and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f: data = json.load(f); return data.get("images", []), data.get("videos_and_gifs", [])
        except Exception: pass
    image_list, video_and_gif_list = [], []
    image_formats = ('.png', '.jpg', '.jpeg', '.bmp', '.webp'); video_formats = ('.mp4', '.webm', '.mov', '.mkv', '.avi', '.gif')
    for root, dirs, files in os.walk(PROJECT_PARENT_DIR, topdown=True):
        if PROJECT_DIR_NAME in dirs: dirs.remove(PROJECT_DIR_NAME)
        for file in files:
            file_lower, relative_path = file.lower(), os.path.relpath(os.path.join(root, file), PROJECT_PARENT_DIR).replace('\\', '/')
            if file_lower.endswith(image_formats): image_list.append(relative_path)
            elif file_lower.endswith(video_formats): video_and_gif_list.append(relative_path)
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f: json.dump({"images": image_list, "videos_and_gifs": video_and_gif_list}, f)
    except IOError: pass
    return image_list, video_and_gif_list

image_files, video_and_gif_files = scan_media_files()

def natural_sort_key(filepath):
    filename = os.path.basename(filepath)
    numbers = [int(s) for s in re.findall(r'\d+', filename)]
    if numbers: return (0, numbers, filename)
    else:
        full_path = os.path.join(PROJECT_PARENT_DIR, filepath)
        try: return (1, os.path.getctime(full_path), filename)
        except: return (2, filename)

# --- HTML 页面路由 ---
@app.route('/')
def random_image_page():
    if not image_files: return "没有找到静态图片。", 404
    chosen_image = random.choice(image_files)
    folder_path = os.path.dirname(chosen_image)
    return render_template_string(RANDOM_IMAGE_HTML, image_path=chosen_image, folder_path=folder_path)

@app.route('/slideshow')
def slideshow_page(): return render_template_string(SLIDESHOW_HTML)

@app.route('/grid')
def grid_page(): return render_template_string(GRID_HTML)

@app.route('/videos')
def video_grid_page(): return render_template_string(VIDEO_GRID_HTML)

@app.route('/tags')
def tags_index_page(): return render_template_string(TAGS_INDEX_HTML)

@app.route('/tags/random/<path:character_name>')
def character_gallery_page(character_name):
    return render_template_string(CHARACTER_GALLERY_HTML, character_name=character_name)

@app.route('/search')
def search_page():
    query = request.args.get('q', '')
    return render_template_string(SEARCH_PAGE_HTML, query=query, PAGE_SIZE=PAGE_SIZE)

@app.route('/folder/<path:folder_path>')
def folder_view_page(folder_path):
    decoded_folder_path = unquote(folder_path)
    target_dir = decoded_folder_path.replace('\\', '/')
    folder_images = []
    for img_path in image_files:
        img_dir = os.path.dirname(img_path).replace('\\', '/')
        if img_dir == target_dir: folder_images.append(img_path)
    folder_images.sort(key=natural_sort_key)
    return render_template_string(FOLDER_VIEW_HTML, folder_name=decoded_folder_path, images=folder_images)

# --- API 数据接口 ---
@app.route('/api/images')
def get_all_images(): 
    if not image_files: return jsonify([])
    return jsonify(random.sample(image_files, len(image_files)))

@app.route('/api/videos')
def get_all_videos(): 
    if not video_and_gif_files: return jsonify([])
    return jsonify(random.sample(video_and_gif_files, len(video_and_gif_files)))

@app.route('/api/random-image')
def get_random_image_path():
    if not image_files: return jsonify({'error': 'No images found'}), 404
    return jsonify({'path': random.choice(image_files)})

@app.route('/api/characters')
def get_characters_with_covers():
    conn = get_db_connection();
    if conn is None: return jsonify({"error": f"Database file '{DB_PATH}' not found."}), 404
    page = request.args.get('page', 1, type=int); search_term = request.args.get('search', '', type=str).strip().replace(" ", "_"); offset = (page - 1) * PAGE_SIZE
    character_data = []
    if page == 1 and not search_term:
        oc_query = "SELECT 'others/oc' as character_name, i.filepath FROM images i JOIN image_tags it ON i.id = it.image_id JOIN tags t ON it.tag_id = t.id WHERE i.character_name = 'others/oc' AND t.name = 'looking at viewer' LIMIT 1"
        oc_cover = conn.execute(oc_query).fetchone()
        if oc_cover: character_data.append(dict(oc_cover))
    params = []; search_clause = ""
    if search_term: search_clause = "AND i.character_name LIKE ?"; params.append(f"%{search_term}%")
    query = f"SELECT T1.character_name, T1.filepath FROM images AS T1 INNER JOIN (SELECT i.character_name, MIN(i.id) as image_id FROM images i JOIN image_tags it ON i.id = it.image_id JOIN tags t ON it.tag_id = t.id WHERE t.name = 'looking at viewer' AND i.character_name != 'others/oc' {search_clause} GROUP BY i.character_name) AS T2 ON T1.character_name = T2.character_name AND T1.id = T2.image_id ORDER BY T1.character_name LIMIT ? OFFSET ?"
    params.extend([PAGE_SIZE, offset]); other_characters = conn.execute(query, params).fetchall(); conn.close()
    character_data.extend([dict(row) for row in other_characters])
    return jsonify(character_data)

@app.route('/api/character_images/<path:character_name>')
def get_character_images(character_name):
    conn = get_db_connection();
    if conn is None: return jsonify([]), 404
    query = "SELECT filepath FROM images WHERE character_name = ?"
    params = (character_name,)
    images = conn.execute(query, params).fetchall()
    conn.close()
    image_paths = [row['filepath'] for row in images]
    random.shuffle(image_paths) 
    return jsonify(image_paths)

@app.route('/api/search')
def api_search():
    conn = get_db_connection()
    if conn is None: return jsonify({"error": f"Database file '{DB_PATH}' not found."}), 404
    page = request.args.get('page', 1, type=int); limit = request.args.get('limit', PAGE_SIZE, type=int)
    query_str = request.args.get('q', '', type=str); offset = (page - 1) * limit
    if not query_str: return jsonify([])
    raw_tags = [tag.strip() for tag in query_str.split(' ') if tag.strip()]
    rating_filters, char_filters, general_tags = [], [], []
    for tag in raw_tags:
        if tag.lower().startswith("rating:"): rating_filters.append(f"rating:{tag.split(':', 1)[1].strip()}")
        elif tag.lower().startswith("char:"): char_filters.append(tag.split(':', 1)[1].strip().replace(' ', '_'))
        else: general_tags.append(tag.replace('_', ' '))
    base_query = "SELECT T0.filepath FROM images AS T0"; params = []; where_clauses = []
    if general_tags:
        general_subquery = f"T0.id IN (SELECT it.image_id FROM image_tags it JOIN tags t ON it.tag_id = t.id WHERE t.name IN ({','.join(['?']*len(general_tags))}) GROUP BY it.image_id HAVING COUNT(it.image_id) = ?)"
        where_clauses.append(general_subquery); params.extend(general_tags); params.append(len(general_tags))
    for rating in rating_filters: where_clauses.append("T0.rating = ?"); params.append(rating)
    for char in char_filters: where_clauses.append("T0.character_name = ?"); params.append(char)
    if not where_clauses: return jsonify([])
    final_query = f"{base_query} WHERE {' AND '.join(where_clauses)} ORDER BY T0.id DESC LIMIT ? OFFSET ?"; params.extend([limit, offset])
    cursor = conn.cursor(); cursor.execute(final_query, params); results = [row['filepath'] for row in cursor.fetchall()]; conn.close()
    return jsonify(results)

# --- 文件服务与管理 ---
@app.route('/media/<path:filepath>')
def serve_media(filepath):
    decoded_filepath = unquote(filepath); absolute_path = os.path.join(PROJECT_PARENT_DIR, decoded_filepath)
    if not os.path.abspath(absolute_path).startswith(os.path.abspath(PROJECT_PARENT_DIR)): return "Forbidden", 403
    if os.path.exists(absolute_path): return send_file(absolute_path)
    else: return "File not found", 404
@app.route('/rescan')
def rescan_media():
    global image_files, video_and_gif_files; image_files, video_and_gif_files = scan_media_files(force_rescan=True)
    referrer = request.headers.get("Referer");
    if referrer and any(x in referrer for x in ['/grid', '/videos', '/slideshow', '/tags', '/search', '/folder']): return redirect(referrer)
    return redirect(url_for('random_image_page'))

# --- HTML 模板 ---
RANDOM_IMAGE_HTML = """
<!DOCTYPE html><html lang="zh-CN"><head><title>随机图片</title><style>body,html{margin:0;padding:0;height:100%;background-color:#111;color:#fff;font-family:sans-serif}.nav{position:absolute;top:15px;right:20px;z-index:100}.nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,0.5);border-radius:5px;margin-left:10px}.nav a.folder-btn{background-color:rgba(0,100,200,0.6);font-weight:bold}#container{width:100vw;height:100vh;display:flex;justify-content:center;align-items:center}#container a{display:contents}#container img{max-width:100%;max-height:100%;object-fit:contain;cursor:pointer}</style></head><body><div class="nav"><a href="/folder/{{ folder_path }}" class="folder-btn">查看图集</a><a href="/search">搜索</a><a href="/slideshow">幻灯片</a><a href="/grid">图片网格</a><a href="/videos">视频/GIF</a><a href="/tags">角色</a><a href="/rescan">扫描</a></div><div id="container"><a href="/"><img src="/media/{{ image_path }}" alt="随机图片"></a></div></body></html>
"""
SLIDESHOW_HTML = """
<!DOCTYPE html><html lang="zh-CN"><head><title>幻灯片</title><style>{% raw %}body,html{margin:0;padding:0;height:100%;background-color:#111;color:#fff;overflow:hidden;font-family:sans-serif}.nav{position:absolute;top:15px;right:20px;z-index:100}.nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,0.5);border-radius:5px;margin-left:10px}#slideshow-container{width:100vw;height:100vh;display:flex;justify-content:center;align-items:center;cursor:pointer}#slideshow-container img{max-width:100%;max-height:100%;object-fit:contain;opacity:0;transition:opacity .8s ease-in-out}#slideshow-container img.loaded{opacity:1}{% endraw %}</style></head><body><div class="nav"><a href="/search">搜索</a><a href="/">随机</a><a href="/grid">图片网格</a><a href="/videos">视频/GIF</a><a href="/tags">角色</a><a href="/rescan">扫描</a></div><div id="slideshow-container"><img id="image-display" alt="正在加载..."></div><script>{% raw %}const container=document.getElementById("slideshow-container"),imgElement=document.getElementById("image-display"),SLIDESHOW_INTERVAL=5e3;let timer;async function fetchAndShowNextImage(){try{const e=await fetch("/api/random-image");if(!e.ok)throw new Error("无法获取图片");const t=await e.json();imgElement.classList.remove("loaded");const a=new Image;a.src=`/media/${t.path}`,a.onload=()=>{imgElement.src=a.src,imgElement.classList.add("loaded")}}catch(e){console.error("幻灯片错误:",e)}}function startSlideshow(){timer&&clearInterval(timer),fetchAndShowNextImage(),timer=setInterval(fetchAndShowNextImage,SLIDESHOW_INTERVAL)}container.addEventListener("click",startSlideshow),document.addEventListener("DOMContentLoaded",startSlideshow);{% endraw %}</script></body></html>
"""

# ==========================================================
#  ↓↓↓ 更新：文件夹视图模板 (修改为 1列 垂直Feed流) ↓↓↓
# ==========================================================
FOLDER_VIEW_HTML = """
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>文件夹: {{ folder_name }}</title><style>{% raw %}
    body{margin:0;background-color:#222;font-family:sans-serif}
    .header-nav{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:10px 15px;z-index:100;display:flex;justify-content:flex-end;align-items:center}
    .header-nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;margin-left:10px}
    .page-title{padding:20px;color:#fff;background-color:#333;margin:0;text-align:center;}
    .page-title h1{margin:0;font-size:1.2em;word-break:break-all;font-weight:normal;color:#ddd}
    .page-title span{font-weight:bold;color:#fff;font-size:1.4em;margin-right:10px}
    
    /* 改为单列布局 */
    #grid-container{
        display:grid;
        grid-template-columns: 1fr; /* 强制单列 */
        gap: 30px;
        padding: 20px;
        max-width: 800px; /* 限制最大宽度，防止大屏图片过大 */
        margin: 0 auto;   /* 容器居中 */
    }
    
    .grid-item{
        position:relative;
        border-radius:8px;
        cursor:pointer;
        background-color:#333;
        overflow:hidden;
        min-height: 200px; /* 骨架屏占位高度 */
    }
    
    .grid-item img{
        width:100%;
        height: auto; /* 保持图片原始比例 */
        display:block;
        /* object-fit: cover; 不需要cover，因为我们希望看到完整图片 */
        transition:opacity .5s;
    }
    .grid-item img.loaded{opacity:1}
    
    .skeleton{position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(90deg,#333 25%,#444 50%,#333 75%);background-size:200% 100%;animation:shimmer 1.5s infinite}
    @keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
    
    .modal{display:none;position:fixed;z-index:1000;left:0;top:0;width:100%;height:100%;overflow:hidden;background-color:rgba(0,0,0,.9);align-items:center;justify-content:center}
    .modal-content{max-width:95vw;max-height:95vh;object-fit:contain}
    .modal-close{position:absolute;top:20px;right:35px;color:#f1f1f1;font-size:40px;font-weight:700;cursor:pointer}
    .modal-nav{position:absolute;top:50%;transform:translateY(-50%);color:#f1f1f1;font-size:60px;font-weight:700;cursor:pointer;user-select:none;padding:16px}
    .modal-prev{left:0}.modal-next{right:0}
    .modal-folder-btn{position:absolute;bottom:30px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.6);border:1px solid #fff;color:#fff;padding:8px 16px;border-radius:4px;text-decoration:none;font-size:14px;z-index:1002;transition:background .2s}.modal-folder-btn:hover{background:rgba(255,255,255,0.2)}
{% endraw %}</style></head><body><div class="header-nav"><a href="/">返回随机</a><a href="/search">搜索</a><a href="/grid">图片网格</a><a href="/tags">角色</a></div><div class="page-title"><h1><span>📁</span>{{ folder_name }}</h1></div><div id="grid-container">{% for image in images %}<div class="grid-item" data-index="{{ loop.index0 }}"><div class="skeleton"></div><img data-index="{{ loop.index0 }}" src="/media/{{ image }}" onload="this.previousElementSibling.remove(); this.classList.add('loaded')"></div>{% endfor %}</div><div id="imageModal" class="modal"><span class="modal-close">&times;</span><a id="modalFolderBtn" class="modal-folder-btn" target="_blank">查看所属图集</a><span class="modal-nav modal-prev">&#10094;</span><img class="modal-content" id="modalImage"><span class="modal-nav modal-next">&#10095;</span></div><script>{% raw %}
    document.addEventListener("DOMContentLoaded",()=>{const grid=document.getElementById("grid-container"),mod=document.getElementById("imageModal"),modImg=document.getElementById("modalImage"),closeBtn=document.querySelector(".modal-close"),prevBtn=document.querySelector(".modal-prev"),nextBtn=document.querySelector(".modal-next"),modFolderBtn=document.getElementById("modalFolderBtn");const allImages = Array.from(document.querySelectorAll('.grid-item img')).map(img => img.getAttribute('src'));let currIdx = -1;function openMod(idx){currIdx=parseInt(idx);const path=allImages[currIdx];modImg.src=path;mod.style.display="flex";document.body.style.overflow="hidden";const lastSlash=path.lastIndexOf('/');if(lastSlash>-1){const f=path.substring(0,lastSlash);modFolderBtn.href=`/folder/${encodeURIComponent(f.replace('/media/',''))}`;modFolderBtn.style.display="block"}else{modFolderBtn.style.display="none"}}function closeMod(){mod.style.display="none";document.body.style.overflow=""}function nextMod(){if(allImages.length){currIdx=(currIdx+1)%allImages.length;openMod(currIdx)}}function prevMod(){if(allImages.length){currIdx=(currIdx-1+allImages.length)%allImages.length;openMod(currIdx)}}grid.addEventListener("click",e=>{e.target.dataset.index&&openMod(e.target.dataset.index)});closeBtn.addEventListener("click",closeMod);prevBtn.addEventListener("click",prevMod);nextBtn.addEventListener("click",nextMod);mod.addEventListener("click",e=>{if(e.target===mod)closeMod()});document.addEventListener("keydown",e=>{if(mod.style.display==="flex"){if(e.key==="Escape")closeMod();else if(e.key==="ArrowRight")nextMod();else if(e.key==="ArrowLeft")prevMod()}});});
{% endraw %}</script></body></html>
"""

GRID_HTML="""
<!DOCTYPE html><html lang="zh-CN"><head><title>图片网格</title><style>{% raw %}body{margin:0;background-color:#222;font-family:sans-serif}.header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:15px;text-align:right;z-index:100}.header a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;margin-left:10px}#grid-container{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;padding:10px}.grid-item{position:relative;border-radius:8px;cursor:pointer;background-color:#333;aspect-ratio:3/4;overflow:hidden}.grid-item img{width:100%;height:100%;display:block;object-fit:cover;opacity:0;transition:opacity .5s}.grid-item img.loaded{opacity:1}.skeleton{position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(90deg,#333 25%,#444 50%,#333 75%);background-size:200% 100%;animation:shimmer 1.5s infinite}@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}#loader{text-align:center;padding:20px;color:#888}.modal{display:none;position:fixed;z-index:1000;left:0;top:0;width:100%;height:100%;overflow:hidden;background-color:rgba(0,0,0,.9);align-items:center;justify-content:center}.modal-content{max-width:95vw;max-height:95vh;object-fit:contain}.modal-close{position:absolute;top:20px;right:35px;color:#f1f1f1;font-size:40px;font-weight:700;cursor:pointer}.modal-nav{position:absolute;top:50%;transform:translateY(-50%);color:#f1f1f1;font-size:60px;font-weight:700;cursor:pointer;user-select:none;padding:16px}.modal-prev{left:0}.modal-next{right:0}
.modal-folder-btn{position:absolute;bottom:30px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.6);border:1px solid #fff;color:#fff;padding:8px 16px;border-radius:4px;text-decoration:none;font-size:14px;z-index:1002;transition:background .2s}.modal-folder-btn:hover{background:rgba(255,255,255,0.2)}
{% endraw %}</style></head><body><div class="header"><a href="/search">搜索</a><a href="/">随机</a><a href="/slideshow">幻灯片</a><a href="/videos">视频/GIF</a><a href="/tags">角色</a><a href="/rescan">扫描</a></div><div id="grid-container"></div><div id="loader">正在加载...</div><div id="imageModal" class="modal"><span class="modal-close">&times;</span>
<a id="modalFolderBtn" class="modal-folder-btn" target="_blank">查看所属图集</a>
<span class="modal-nav modal-prev">&#10094;</span><img class="modal-content" id="modalImage"><span class="modal-nav modal-next">&#10095;</span></div><script>{% raw %}const grid=document.getElementById("grid-container"),loader=document.getElementById("loader"),imageModal=document.getElementById("imageModal"),modalImage=document.getElementById("modalImage"),closeBtn=document.querySelector(".modal-close"),prevBtn=document.querySelector(".modal-prev"),nextBtn=document.querySelector(".modal-next"),modFolderBtn=document.getElementById("modalFolderBtn");let allImages=[],currentIndex=0,currentModalImageIndex=-1;const BATCH_SIZE=30;function loadMoreImages(){if(currentIndex>=allImages.length){loader.textContent="已加载全部";return}const t=allImages.slice(currentIndex,currentIndex+BATCH_SIZE);for(const[e,a]of t.entries()){const t=document.createElement("div");t.className="grid-item";const n=document.createElement("div");n.className="skeleton",t.appendChild(n);const d=document.createElement("img"),o=currentIndex+e;t.dataset.index=o,d.dataset.index=o,d.onload=()=>{t.removeChild(n),d.classList.add("loaded")},d.src=`/media/${a}`,t.appendChild(d),grid.appendChild(t)}currentIndex+=BATCH_SIZE}function openModal(e){currentModalImageIndex=parseInt(e);const path=allImages[currentModalImageIndex];modalImage.src=`/media/${path}`;imageModal.style.display="flex";document.body.style.overflow="hidden";
const lastSlash=path.lastIndexOf('/');if(lastSlash>-1){const f=path.substring(0,lastSlash);modFolderBtn.href=`/folder/${encodeURIComponent(f)}`;modFolderBtn.style.display="block"}else{modFolderBtn.style.display="none"}
}function closeModal(){imageModal.style.display="none",document.body.style.overflow=""}function showNextImage(){currentModalImageIndex=(currentModalImageIndex+1)%allImages.length,openModal(currentModalImageIndex)}function showPrevImage(){currentModalImageIndex=(currentModalImageIndex-1+allImages.length)%allImages.length,openModal(currentModalImageIndex)}async function initializeGrid(){try{const e=await fetch("/api/images");if(allImages=await e.json(),0===allImages.length)return void(loader.textContent="未找到任何图片。");loadMoreImages();new IntersectionObserver(e=>{e[0].isIntersecting&&loadMoreImages()},{rootMargin:"200px"}).observe(loader)}catch(e){console.error("无法初始化网格:",e),loader.textContent="加载图片列表失败。"}}grid.addEventListener("click",e=>{e.target.dataset.index&&openModal(e.target.dataset.index)}),closeBtn.addEventListener("click",closeModal),prevBtn.addEventListener("click",showPrevImage),nextBtn.addEventListener("click",showNextImage),document.addEventListener("keydown",e=>{"flex"===imageModal.style.display&&("Escape"===e.key?closeModal():"ArrowRight"===e.key?showNextImage():"ArrowLeft"===e.key&&showPrevImage())}),imageModal.addEventListener("click",e=>{e.target===imageModal&&closeModal()}),document.addEventListener("DOMContentLoaded",initializeGrid);{% endraw %}</script></body></html>
"""
VIDEO_GRID_HTML="""<!DOCTYPE html><html lang="zh-CN"><head><title>视频/GIF 网格</title><style>{% raw %}body{margin:0;background-color:#222;font-family:sans-serif}.header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:15px;text-align:right;z-index:100}.header a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;margin-left:10px}#grid-container{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px;padding:10px}.grid-item{position:relative;border-radius:8px;cursor:pointer;background-color:#333;aspect-ratio:9/16;overflow:hidden}.grid-item img,.grid-item video{width:100%;height:100%;display:block;object-fit:cover;opacity:0;transition:opacity .5s}.grid-item img.loaded,.grid-item video.loaded{opacity:1}.skeleton{position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(90deg,#333 25%,#444 50%,#333 75%);background-size:200% 100%;animation:shimmer 1.5s infinite}@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}#loader{text-align:center;padding:20px;color:#888}.modal{display:none;position:fixed;z-index:1000;left:0;top:0;width:100%;height:100%;overflow:hidden;background-color:rgba(0,0,0,.9)}.modal-content-container{width:100%;height:100%;display:flex;justify-content:center;align-items:center}.modal-content-container img,.modal-content-container video{max-width:95vw;max-height:95vh;object-fit:contain}.modal-close{position:absolute;top:15px;right:35px;color:#f1f1f1;font-size:40px;font-weight:700;cursor:pointer}.modal-nav{position:absolute;top:50%;transform:translateY(-50%);font-size:50px;color:#fff;padding:16px;cursor:pointer;user-select:none;z-index:1001}.modal-prev{left:10px}.modal-next{right:10px}
.modal-folder-btn{position:absolute;bottom:30px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.6);border:1px solid #fff;color:#fff;padding:8px 16px;border-radius:4px;text-decoration:none;font-size:14px;z-index:1002;transition:background .2s}.modal-folder-btn:hover{background:rgba(255,255,255,0.2)}
{% endraw %}</style></head><body><div class="header"><a href="/search">搜索</a><a href="/">随机</a><a href="/slideshow">幻灯片</a><a href="/grid">图片网格</a><a href="/tags">角色</a><a href="/rescan">扫描</a></div><div id="grid-container"></div><div id="loader">正在加载...</div><div id="mediaModal" class="modal"><span class="modal-close">&times;</span>
<a id="modalFolderBtn" class="modal-folder-btn" target="_blank">查看所属图集</a>
<span class="modal-nav modal-prev">&#10094;</span><div class="modal-content-container" id="modalMediaContainer"></div><span class="modal-nav modal-next">&#10095;</span></div><script>{% raw %}const grid=document.getElementById("grid-container"),loader=document.getElementById("loader"),mediaModal=document.getElementById("mediaModal"),modalMediaContainer=document.getElementById("modalMediaContainer"),modFolderBtn=document.getElementById("modalFolderBtn");let allMedia=[],currentIndex=0,currentModalIndex=-1;const BATCH_SIZE=20;function createMediaElement(e,t){const a=e.toLowerCase().endsWith(".gif");let l;if(a)l=document.createElement("img");else{l=document.createElement("video"),l.loop=!0,l.playsinline=!0,t?(l.autoplay=!0,l.muted=!0):(l.autoplay=!0,l.controls=!0,l.muted=!0)}return l.src=`/media/${e}`,l}function loadMoreMedia(){if(currentIndex>=allMedia.length){loader.textContent="已加载全部";return}const e=allMedia.slice(currentIndex,currentIndex+BATCH_SIZE);for(const[t,a]of e.entries()){const e=document.createElement("div");e.className="grid-item";const n=document.createElement("div");n.className="skeleton",e.appendChild(n);const d=currentIndex+t;e.dataset.index=d;const i=createMediaElement(a,!0);e.appendChild(i);const o=i.tagName.toLowerCase();"video"===o?i.onloadeddata=()=>{e.contains(n)&&e.removeChild(n),i.classList.add("loaded")}:i.onload=()=>{e.contains(n)&&e.removeChild(n),i.classList.add("loaded")},grid.appendChild(e)}currentIndex+=BATCH_SIZE}function openModal(e){currentModalIndex=parseInt(e);const t=allMedia[currentModalIndex];modalMediaContainer.innerHTML="";const a=createMediaElement(t,!1);modalMediaContainer.appendChild(a),mediaModal.style.display="block",document.body.style.overflow="hidden";
const lastSlash=t.lastIndexOf('/');if(lastSlash>-1){const f=t.substring(0,lastSlash);modFolderBtn.href=`/folder/${encodeURIComponent(f)}`;modFolderBtn.style.display="block"}else{modFolderBtn.style.display="none"}
}function closeModal(){mediaModal.style.display="none",modalMediaContainer.innerHTML="",document.body.style.overflow=""}function showAdjacentMedia(e){if(-1===currentModalIndex)return;currentModalIndex=(currentModalIndex+e+allMedia.length)%allMedia.length,openModal(currentModalIndex)}async function initializeGrid(){try{const e=await fetch("/api/videos");if(allMedia=await e.json(),0===allMedia.length)return void(loader.textContent="未找到任何视频或GIF。");loadMoreMedia();new IntersectionObserver(e=>{e[0].isIntersecting&&loadMoreMedia()},{rootMargin:"400px"}).observe(loader)}catch(e){console.error("无法初始化网格:",e),loader.textContent="加载列表失败。"}}grid.addEventListener("click",e=>{const t=e.target.closest(".grid-item");t&&t.dataset.index&&openModal(t.dataset.index)}),document.querySelector(".modal-close").addEventListener("click",closeModal),document.querySelector(".modal-prev").addEventListener("click",()=>showAdjacentMedia(-1)),document.querySelector(".modal-next").addEventListener("click",()=>showAdjacentMedia(1)),document.addEventListener("keydown",e=>{"block"===mediaModal.style.display&&("Escape"===e.key?closeModal():"ArrowRight"===e.key?showAdjacentMedia(1):"ArrowLeft"===e.key&&showAdjacentMedia(-1))}),mediaModal.addEventListener("click",e=>{if(e.target===mediaModal||e.target===modalMediaContainer)closeModal()}),document.addEventListener("DOMContentLoaded",initializeGrid);{% endraw %}</script></body></html>"""
TAGS_INDEX_HTML="""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>角色标签</title><style>{% raw %}body{margin:0;background-color:#222;font-family:sans-serif}.header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:15px;z-index:100;display:flex;align-items:center;gap:15px}.header .nav{margin-left:auto}.header a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;margin-left:10px}#search-box{padding:8px 12px;font-size:1em;border-radius:5px;border:1px solid #555;background-color:#333;color:#fff;width:250px}#grid-container{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:15px;padding:15px}.character-card{display:block;position:relative;border-radius:8px;overflow:hidden;aspect-ratio:3/4;background-size:cover;background-position:center;text-decoration:none;color:#fff;transition:transform .2s ease-out;background-color:#333}.character-card:hover{transform:scale(1.03)}.character-card::after{content:'';position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(to top,rgba(0,0,0,.8) 0%,rgba(0,0,0,0) 50%)}.character-name{position:absolute;bottom:10px;left:15px;font-size:1.2em;font-weight:700;z-index:1;text-shadow:1px 1px 3px rgba(0,0,0,.7)}#loader{text-align:center;padding:20px;color:#888}{% endraw %}</style></head><body><div class="header"><input type="search" id="search-box" placeholder="搜索角色..."><div class="nav"><a href="/search">搜索</a><a href="/">随机</a><a href="/slideshow">幻灯片</a><a href="/grid">图片网格</a><a href="/videos">视频/GIF</a><a href="/rescan">扫描</a></div></div><div id="grid-container"></div><div id="loader">正在加载角色列表...</div><script>{% raw %}
        const grid = document.getElementById('grid-container');
        const loader = document.getElementById('loader');
        const searchBox = document.getElementById('search-box');
        let currentPage = 1; let isLoading = false; let noMoreData = false;
        let currentSearchTerm = ""; let debounceTimer = null; const BATCH_SIZE = 24;
        async function loadMoreCharacters() {
            if (isLoading || noMoreData) return;
            isLoading = true; loader.textContent = '正在加载...'; loader.style.display = 'block';
            try {
                const searchTerm = encodeURIComponent(currentSearchTerm);
                const response = await fetch(`/api/characters?page=${currentPage}&limit=${BATCH_SIZE}&search=${searchTerm}`);
                if (!response.ok) { const err = await response.json(); throw new Error(err.error || 'Failed to fetch characters.'); }
                const characters = await response.json();
                if (characters.length === 0) {
                    noMoreData = true; loader.textContent = grid.children.length === 0 ? '未找到匹配的角色。' : '已加载所有角色'; return;
                }
                for (const charData of characters) {
                    const cardLink = document.createElement('a'); cardLink.className = 'character-card';
                    cardLink.href = `/tags/random/${encodeURIComponent(charData.character_name)}`;
                    cardLink.style.backgroundImage = `url('/media/${encodeURIComponent(charData.filepath)}')`;
                    const nameSpan = document.createElement('span'); nameSpan.className = 'character-name';
                    if (charData.character_name === 'others/oc') { nameSpan.textContent = 'Others / OC'; } 
                    else { nameSpan.textContent = charData.character_name.replace(/_/g, " "); }
                    cardLink.appendChild(nameSpan); grid.appendChild(cardLink);
                }
                currentPage++;
            } catch (error) { console.error('无法加载更多角色:', error); loader.textContent = `加载失败: ${error.message}`; } 
            finally { isLoading = false; }
        }
        const observer = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && !isLoading) { loadMoreCharacters(); }
        }, { rootMargin: "200px" });
        function resetAndLoad() {
            grid.innerHTML = ''; currentPage = 1; isLoading = false; noMoreData = false;
            if(observer) observer.disconnect();
            loadMoreCharacters(); observer.observe(loader);
        }
        searchBox.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            const searchTerm = e.target.value.trim();
            debounceTimer = setTimeout(() => {
                if (searchTerm !== currentSearchTerm) { currentSearchTerm = searchTerm; resetAndLoad(); }
            }, 300);
        });
        document.addEventListener('DOMContentLoaded', resetAndLoad);
    {% endraw %}</script></body></html>"""
CHARACTER_GALLERY_HTML="""
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>角色: {{ character_name.replace('_', ' ') }}</title><style>{% raw %}
        /* --- 核心修复：从图片网格页完整复制所有样式 --- */
        body{margin:0;background-color:#222;font-family:sans-serif}
        .header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:15px;z-index:100;display:flex;justify-content:flex-end;align-items:center}
        .header .title{font-size:1.2em;color:#fff;margin-right:auto;padding-left:15px}
        .header .nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;margin-left:10px}
        #grid-container{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;padding:10px}
        .grid-item{position:relative;border-radius:8px;cursor:pointer;background-color:#333;aspect-ratio:3/4;overflow:hidden}
        .grid-item img{width:100%;height:100%;display:block;object-fit:cover;opacity:0;transition:opacity .5s}
        .grid-item img.loaded{opacity:1}
        .skeleton{position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(90deg,#333 25%,#444 50%,#333 75%);background-size:200% 100%;animation:shimmer 1.5s infinite}
        @keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
        #loader{text-align:center;padding:20px;color:#888}
        .modal{display:none;position:fixed;z-index:1000;left:0;top:0;width:100%;height:100%;overflow:hidden;background-color:rgba(0,0,0,.9);align-items:center;justify-content:center}
        .modal-content{max-width:95vw;max-height:95vh;object-fit:contain}
        .modal-close{position:absolute;top:20px;right:35px;color:#f1f1f1;font-size:40px;font-weight:700;cursor:pointer}
        .modal-nav{position:absolute;top:50%;transform:translateY(-50%);color:#f1f1f1;font-size:60px;font-weight:700;cursor:pointer;user-select:none;padding:16px}
        .modal-prev{left:0}.modal-next{right:0}
        .modal-folder-btn{position:absolute;bottom:30px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.6);border:1px solid #fff;color:#fff;padding:8px 16px;border-radius:4px;text-decoration:none;font-size:14px;z-index:1002;transition:background .2s}.modal-folder-btn:hover{background:rgba(255,255,255,0.2)}
    {% endraw %}</style></head><body data-character-name="{{ character_name | urlencode }}"><div class="header"><span class="title">角色: {{ character_name.replace('_', ' ') }}</span><div class="nav"><a href="/search">搜索</a><a href="/">随机</a><a href="/grid">图片网格</a><a href="/tags">返回角色列表</a><a href="/rescan">重新扫描</a></div></div><div id="grid-container"></div><div id="loader">正在加载图片...</div><div id="imageModal" class="modal"><span class="modal-close">&times;</span>
    <a id="modalFolderBtn" class="modal-folder-btn" target="_blank">查看所属图集</a>
    <span class="modal-nav modal-prev">&#10094;</span><img class="modal-content" id="modalImage"><span class="modal-nav modal-next">&#10095;</span></div><script>{% raw %}
    document.addEventListener("DOMContentLoaded",()=>{const e=document.body.dataset.characterName;const grid=document.getElementById("grid-container"),loader=document.getElementById("loader"),imageModal=document.getElementById("imageModal"),modalImage=document.getElementById("modalImage"),closeBtn=document.querySelector(".modal-close"),prevBtn=document.querySelector(".modal-prev"),nextBtn=document.querySelector(".modal-next"),modFolderBtn=document.getElementById("modalFolderBtn");let allImages=[],currentModalImageIndex=-1;async function initialize(){try{const r=await fetch(`/api/character_images/${e}`);if(allImages=await r.json(),0===allImages.length)return void(loader.textContent="未找到该角色的任何图片。");loader.style.display="none";for(const[t,a]of allImages.entries()){const n=document.createElement("div");n.className="grid-item";const d=document.createElement("div");d.className="skeleton",n.appendChild(d);const i=document.createElement("img");n.dataset.index=t,i.dataset.index=t,i.onload=()=>{n.contains(d)&&n.removeChild(d),i.classList.add("loaded")},i.src=`/media/${a}`,n.appendChild(i),grid.appendChild(n)}}catch(r){console.error("无法加载图片:",r),loader.textContent="加载失败。"}}function openModal(e){currentModalImageIndex=parseInt(e);const path=allImages[currentModalImageIndex];modalImage.src=`/media/${path}`;imageModal.style.display="flex";document.body.style.overflow="hidden";const lastSlash=path.lastIndexOf('/');if(lastSlash>-1){const f=path.substring(0,lastSlash);modFolderBtn.href=`/folder/${encodeURIComponent(f)}`;modFolderBtn.style.display="block"}else{modFolderBtn.style.display="none"}}function closeModal(){imageModal.style.display="none";document.body.style.overflow=""}function showNextImage(){if(allImages.length)currentModalImageIndex=(currentModalImageIndex+1)%allImages.length,openModal(currentModalImageIndex)}function showPrevImage(){if(allImages.length)currentModalImageIndex=(currentModalImageIndex-1+allImages.length)%allImages.length,openModal(currentModalImageIndex)}initialize();grid.addEventListener("click",e=>{e.target.dataset.index&&openModal(e.target.dataset.index)}),closeBtn.addEventListener("click",closeModal),prevBtn.addEventListener("click",showPrevImage),nextBtn.addEventListener("click",showNextImage),document.addEventListener("keydown",e=>{"flex"===imageModal.style.display&&("Escape"===e.key?closeModal():"ArrowRight"===e.key?showNextImage():"ArrowLeft"===e.key&&showPrevImage())}),imageModal.addEventListener("click",e=>{e.target===imageModal&&closeModal()})});{% endraw %}</script></body></html>"""
SEARCH_PAGE_HTML = """
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>标签搜索</title><style>{% raw %}body{margin:0;background-color:#222;font-family:sans-serif}.header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:10px 15px;z-index:100;display:flex;align-items:center;gap:15px}.header .search-form{display:flex;flex-grow:1}.header #search-box{flex-grow:1;padding:10px 15px;font-size:1.1em;border-radius:5px 0 0 5px;border:1px solid #555;background-color:#333;color:#fff;border-right:none}.header #search-button{padding:10px 20px;font-size:1.1em;border-radius:0 5px 5px 0;border:1px solid #555;background-color:#444;color:#fff;cursor:pointer}.header .nav{margin-left:auto;white-space:nowrap}.header .nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;margin-left:10px}#grid-container{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;padding:10px}.grid-item{position:relative;border-radius:8px;cursor:pointer;background-color:#333;aspect-ratio:3/4;overflow:hidden}.grid-item img{width:100%;height:100%;display:block;object-fit:cover;opacity:0;transition:opacity .5s}.grid-item img.loaded{opacity:1}.skeleton{position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(90deg,#333 25%,#444 50%,#333 75%);background-size:200% 100%;animation:shimmer 1.5s infinite}@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}#loader{text-align:center;padding:20px;color:#888}.modal{display:none;position:fixed;z-index:1000;left:0;top:0;width:100%;height:100%;overflow:hidden;background-color:rgba(0,0,0,.9);align-items:center;justify-content:center}.modal-content{max-width:95vw;max-height:95vh;object-fit:contain}.modal-close{position:absolute;top:20px;right:35px;color:#f1f1f1;font-size:40px;font-weight:700;cursor:pointer}.modal-nav{position:absolute;top:50%;transform:translateY(-50%);color:#f1f1f1;font-size:60px;font-weight:700;cursor:pointer;user-select:none;padding:16px}.modal-prev{left:0}.modal-next{right:0}
.modal-folder-btn{position:absolute;bottom:30px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.6);border:1px solid #fff;color:#fff;padding:8px 16px;border-radius:4px;text-decoration:none;font-size:14px;z-index:1002;transition:background .2s}.modal-folder-btn:hover{background:rgba(255,255,255,0.2)}
{% endraw %}</style></head><body data-query="{{ query }}" data-page-size="{{ PAGE_SIZE }}"><div class="header"><form class="search-form" id="search-form"><input type="search" id="search-box" placeholder="输入标签, 以空格分隔 (可用 rating: 和 char: 前缀)..." value="{{ query }}"><button type="submit" id="search-button">搜索</button></form><div class="nav"><a href="/">随机</a><a href="/tags">角色</a><a href="/grid">图网</a><a href="/videos">视频</a><a href="/rescan">扫描</a></div></div><div id="grid-container"></div><div id="loader">输入标签以开始搜索</div><div id="imageModal" class="modal"><span class="modal-close">&times;</span>
<a id="modalFolderBtn" class="modal-folder-btn" target="_blank">查看所属图集</a>
<span class="modal-nav modal-prev">&#10094;</span><img class="modal-content" id="modalImage"><span class="modal-nav modal-next">&#10095;</span></div><script>{% raw %}
        document.addEventListener("DOMContentLoaded",()=>{const e=document.getElementById("grid-container"),t=document.getElementById("loader"),a=document.getElementById("search-form"),n=document.getElementById("search-box"),d=parseInt(document.body.dataset.pageSize),mod=document.getElementById("imageModal"),modImg=document.getElementById("modalImage"),closeBtn=document.querySelector(".modal-close"),prevBtn=document.querySelector(".modal-prev"),nextBtn=document.querySelector(".modal-next"),modFolderBtn=document.getElementById("modalFolderBtn");let o=[],i=1,l=!1,r=!1,s="",currIdx=-1;async function c(){if(l||r||!s)return;l=!0,t.textContent="正在加载...";try{const a=new URLSearchParams({q:s,page:i,limit:d}),n=await fetch(`/api/search?${a.toString()}`),u=await n.json();if(0===u.length){r=!0,t.textContent=0===o.length?"未找到匹配的图片。":"已加载全部结果",p&&p.disconnect();return}const m=o.length;o.push(...u);for(const[t,a]of u.entries()){const n=document.createElement("div");n.className="grid-item";const d=document.createElement("div");d.className="skeleton",n.appendChild(d);const i=document.createElement("img"),l=m+t;n.dataset.index=l,i.dataset.index=l,i.onload=()=>{n.contains(d)&&n.removeChild(d),i.classList.add("loaded")},i.src=`/media/${a}`,n.appendChild(i),e.appendChild(n)}i++}catch(a){console.error("加载结果失败:",a),t.textContent="加载失败。"}finally{l=!1}}const p=new IntersectionObserver(e=>{e[0].isIntersecting&&c()},{rootMargin:"400px"});function u(t){(t=t.trim())===s&&o.length>0||(s=t,history.pushState(null,"",`/search?q=${encodeURIComponent(s)}`),e.innerHTML="",o=[],i=1,l=!1,r=!1,p&&p.disconnect(),s?(c(),p.observe(t)):t.textContent="输入标签以开始搜索")}
        function openMod(idx){currIdx=parseInt(idx);const path=o[currIdx];modImg.src=`/media/${path}`;mod.style.display="flex";document.body.style.overflow="hidden";
        const lastSlash=path.lastIndexOf('/');if(lastSlash>-1){const f=path.substring(0,lastSlash);modFolderBtn.href=`/folder/${encodeURIComponent(f)}`;modFolderBtn.style.display="block"}else{modFolderBtn.style.display="none"}
        }
        function closeMod(){mod.style.display="none";document.body.style.overflow=""}
        function nextMod(){if(o.length) {currIdx=(currIdx+1)%o.length; openMod(currIdx)}}
        function prevMod(){if(o.length) {currIdx=(currIdx-1+o.length)%o.length; openMod(currIdx)}}
        a.addEventListener("submit",e=>{e.preventDefault(),u(n.value)});e.addEventListener("click",evt=>{evt.target.dataset.index&&openMod(evt.target.dataset.index)});
        closeBtn.addEventListener("click",closeMod);prevBtn.addEventListener("click",prevMod);nextBtn.addEventListener("click",nextMod);mod.addEventListener("click",e=>{if(e.target===mod)closeMod()});
        document.addEventListener("keydown",e=>{if(mod.style.display==="flex"){if(e.key==="Escape")closeMod();else if(e.key==="ArrowRight")nextMod();else if(e.key==="ArrowLeft")prevMod()}});
        const m=document.body.dataset.query;m&&(n.value=m,u(m))});
    {% endraw %}</script></body></html>"""

# --- 启动服务器 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)