import os
import random
import json
import sqlite3
from urllib.parse import unquote
from flask import Flask, redirect, url_for, jsonify, render_template_string, request, send_file

# --- Configuration (Unchanged) ---
CACHE_FILE = 'media_cache.json'
DB_PATH = "test.db"
PAGE_SIZE = 24
PROJECT_PARENT_DIR = os.path.abspath('..')
PROJECT_DIR_NAME = os.path.basename(os.getcwd())

app = Flask(__name__)

# --- Tag Parsing Helper ---
def parse_tags(query_string):
    """Parses the user's search string into a normalized list of tags."""
    tags = []
    for tag in query_string.strip().split():
        if not tag: continue
        if tag.lower().startswith("rating:"):
            tag = tag[7:]
        tags.append(tag.replace('_', ' '))
    return tags

# --- Database and Backend Logic ---
def get_db_connection():
    if not os.path.exists(DB_PATH): return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def scan_media_files(force_rescan=False):
    if not force_rescan and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("images", []), data.get("videos_and_gifs", [])
        except Exception:
            pass
    image_list, video_and_gif_list = [], []
    image_formats = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')
    video_formats = ('.mp4', '.webm', '.mov', '.mkv', '.avi', '.gif')
    for root, dirs, files in os.walk(PROJECT_PARENT_DIR, topdown=True):
        if PROJECT_DIR_NAME in dirs:
            dirs.remove(PROJECT_DIR_NAME)
        for file in files:
            file_lower = file.lower()
            relative_path = os.path.relpath(os.path.join(root, file), PROJECT_PARENT_DIR).replace('\\', '/')
            if file_lower.endswith(image_formats):
                image_list.append(relative_path)
            elif file_lower.endswith(video_formats):
                video_and_gif_list.append(relative_path)
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({"images": image_list, "videos_and_gifs": video_and_gif_list}, f)
    except IOError:
        pass
    return image_list, video_and_gif_list

image_files, video_and_gif_files = scan_media_files()

# --- Page Routes ---
@app.route('/')
def random_image_page():
    return render_template_string(RANDOM_IMAGE_HTML, image_path=random.choice(image_files) if image_files else None)

@app.route('/slideshow')
def slideshow_page():
    return render_template_string(SLIDESHOW_HTML)

@app.route('/grid')
def grid_page():
    return render_template_string(GRID_HTML)

@app.route('/videos')
def video_grid_page():
    return render_template_string(VIDEO_GRID_HTML)

@app.route('/tags')
def tags_index_page():
    return render_template_string(TAGS_INDEX_HTML)

@app.route('/tags/random/<path:character_name>')
def character_gallery_page(character_name):
    return render_template_string(CHARACTER_GALLERY_HTML, character_name=character_name, PAGE_SIZE=PAGE_SIZE)

@app.route('/search')
def search_page():
    query = request.args.get('q', '').strip()
    return render_template_string(SEARCH_HTML, search_query=query, PAGE_SIZE=PAGE_SIZE)

# --- API Endpoints ---
@app.route('/api/images')
def get_all_images():
    return jsonify(random.sample(image_files, len(image_files)))

@app.route('/api/random-image')
def get_random_image_path():
    if not image_files:
        return jsonify({'error': 'No images found'}), 404
    return jsonify({'path': random.choice(image_files)})

@app.route('/api/videos')
def get_all_videos():
    return jsonify(random.sample(video_and_gif_files, len(video_and_gif_files)))

@app.route('/api/characters')
def get_characters_with_covers():
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": f"Database file '{DB_PATH}' not found."}), 404
    page = request.args.get('page', 1, type=int)
    search_term = request.args.get('search', '', type=str).strip().replace(" ", "_")
    offset = (page - 1) * PAGE_SIZE
    character_data = []
    if page == 1 and not search_term:
        oc_query = "SELECT 'others/oc' as character_name, i.filepath FROM images i JOIN image_tags it ON i.id = it.image_id JOIN tags t ON it.tag_id = t.id WHERE i.character_name = 'others/oc' AND t.name = 'looking at viewer' LIMIT 1"
        oc_cover = conn.execute(oc_query).fetchone()
        if oc_cover:
            character_data.append(dict(oc_cover))
    params = []
    search_clause = ""
    if search_term:
        search_clause = "AND i.character_name LIKE ?"
        params.append(f"%{search_term}%")
    query = f"SELECT T1.character_name, T1.filepath FROM images AS T1 INNER JOIN (SELECT i.character_name, MIN(i.id) as image_id FROM images i JOIN image_tags it ON i.id = it.image_id JOIN tags t ON it.tag_id = t.id WHERE t.name = 'looking at viewer' AND i.character_name != 'others/oc' {search_clause} GROUP BY i.character_name) AS T2 ON T1.character_name = T2.character_name AND T1.id = T2.image_id ORDER BY T1.character_name LIMIT ? OFFSET ?"
    params.extend([PAGE_SIZE, offset])
    other_characters = conn.execute(query, params).fetchall()
    conn.close()
    character_data.extend([dict(row) for row in other_characters])
    return jsonify(character_data)

@app.route('/api/character_images/<path:character_name>')
def get_character_images(character_name):
    conn = get_db_connection()
    if conn is None:
        return jsonify([]), 404
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', PAGE_SIZE, type=int)
    offset = (page - 1) * limit
    params = (character_name,)
    all_ids_query = "SELECT id FROM images WHERE character_name = ?"
    all_ids_rows = conn.execute(all_ids_query, params).fetchall()
    all_ids = [row['id'] for row in all_ids_rows]
    random.shuffle(all_ids)
    paginated_ids = all_ids[offset : offset + limit]
    if not paginated_ids:
        conn.close()
        return jsonify([])
    filepath_query = f"SELECT id, filepath FROM images WHERE id IN ({','.join(['?']*len(paginated_ids))})"
    images = conn.execute(filepath_query, paginated_ids).fetchall()
    conn.close()
    path_map = {row['id']: row['filepath'] for row in images}
    image_paths = [path_map[id] for id in paginated_ids if id in path_map]
    return jsonify(image_paths)

@app.route('/api/search')
def api_search_images_by_tags():
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": f"Database file '{DB_PATH}' not found."}), 404
    query_string = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', PAGE_SIZE, type=int)
    offset = (page - 1) * limit
    tags_to_search = parse_tags(query_string)
    if not tags_to_search:
        return jsonify([])
    tags_count = len(tags_to_search)
    placeholders = ','.join('?' * tags_count)
    sql_query = f"""
        SELECT i.filepath FROM images i
        JOIN image_tags it ON i.id = it.image_id JOIN tags t ON it.tag_id = t.id
        WHERE t.name IN ({placeholders}) GROUP BY i.id, i.filepath
        HAVING COUNT(t.id) = ? ORDER BY i.id DESC LIMIT ? OFFSET ?
    """
    params = tags_to_search + [tags_count, limit, offset]
    try:
        results = conn.execute(sql_query, params).fetchall()
        conn.close()
        return jsonify([row['filepath'] for row in results])
    except sqlite3.Error as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

# --- Media Service and Rescan ---
@app.route('/media/<path:filepath>')
def serve_media(filepath):
    decoded_filepath = unquote(filepath)
    absolute_path = os.path.join(PROJECT_PARENT_DIR, decoded_filepath)
    if not os.path.abspath(absolute_path).startswith(os.path.abspath(PROJECT_PARENT_DIR)):
        return "Forbidden", 403
    if os.path.exists(absolute_path):
        return send_file(absolute_path)
    else:
        return "File not found", 404

@app.route('/rescan')
def rescan_media():
    global image_files, video_and_gif_files
    image_files, video_and_gif_files = scan_media_files(force_rescan=True)
    referrer = request.headers.get("Referer")
    if referrer:
        if any(x in referrer for x in ['/grid', '/videos', '/slideshow', '/tags', '/search']):
            return redirect(referrer)
    return redirect(url_for('random_image_page'))

# --- HTML Templates ---

# --- Template 1: Random Image Page ---
RANDOM_IMAGE_HTML = """
<!DOCTYPE html><html lang="zh-CN"><head><title>随机图片</title><style>
body,html{margin:0;padding:0;height:100%;background-color:#111;color:#fff;font-family:sans-serif}
.nav{position:absolute;top:15px;right:20px;z-index:100;display:flex;align-items:center;gap:10px}
.nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,0.5);border-radius:5px;white-space:nowrap}
#container{width:100vw;height:100vh;display:flex;justify-content:center;align-items:center}
#container a{display:contents}#container img{max-width:100%;max-height:100%;object-fit:contain;cursor:pointer}
</style></head><body><div class="nav">
<a href="/search">搜索</a><a href="/slideshow">幻灯片</a><a href="/grid">图片网格</a><a href="/videos">视频/GIF</a><a href="/tags">角色标签</a><a href="/rescan">重新扫描</a>
</div><div id="container"><a href="/"><img src="/media/{{ image_path }}" alt="随机图片"></a></div></body></html>
"""

# --- Template 2: Slideshow Page ---
SLIDESHOW_HTML = """
<!DOCTYPE html><html lang="zh-CN"><head><title>幻灯片</title><style>
body,html{margin:0;padding:0;height:100%;background-color:#111;color:#fff;overflow:hidden;font-family:sans-serif}
.nav{position:absolute;top:15px;right:20px;z-index:100;display:flex;align-items:center;gap:10px}
.nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,0.5);border-radius:5px;white-space:nowrap}
#slideshow-container{width:100vw;height:100vh;display:flex;justify-content:center;align-items:center;cursor:pointer}
#slideshow-container img{max-width:100%;max-height:100%;object-fit:contain;opacity:0;transition:opacity .8s ease-in-out}
#slideshow-container img.loaded{opacity:1}</style></head><body>
<div class="nav">
<a href="/search">搜索</a><a href="/">随机视图</a><a href="/grid">图片网格</a><a href="/videos">视频/GIF</a><a href="/tags">角色标签</a><a href="/rescan">重新扫描</a>
</div><div id="slideshow-container"><img id="image-display" alt="正在加载..."></div>
<script>{% raw %}const container=document.getElementById("slideshow-container"),imgElement=document.getElementById("image-display"),SLIDESHOW_INTERVAL=5e3;let timer;async function fetchAndShowNextImage(){try{const e=await fetch("/api/random-image");if(!e.ok)throw new Error("无法获取图片");const t=await e.json();imgElement.classList.remove("loaded");const a=new Image;a.src=`/media/${t.path}`,a.onload=()=>{imgElement.src=a.src,imgElement.classList.add("loaded")}}catch(e){console.error("幻灯片错误:",e)}}function startSlideshow(){timer&&clearInterval(timer),fetchAndShowNextImage(),timer=setInterval(fetchAndShowNextImage,SLIDESHOW_INTERVAL)}container.addEventListener("click",startSlideshow),document.addEventListener("DOMContentLoaded",startSlideshow);{% endraw %}</script></body></html>
"""

# --- Template 3: Image Grid Page ---
GRID_HTML="""
<!DOCTYPE html><html lang="zh-CN"><head><title>图片网格</title><style>
body{margin:0;background-color:#222;font-family:sans-serif}
.header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:10px 15px;z-index:100;display:flex;align-items:center;gap:10px}
.header .nav{margin-left:auto;display:flex;align-items:center;gap:10px}
.header .nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;white-space:nowrap}
#grid-container{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;padding:10px}.grid-item{position:relative;border-radius:8px;cursor:pointer;background-color:#333;aspect-ratio:3/4;overflow:hidden}.grid-item img{width:100%;height:100%;display:block;object-fit:cover;opacity:0;transition:opacity .5s}.grid-item img.loaded{opacity:1}.skeleton{position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(90deg,#333 25%,#444 50%,#333 75%);background-size:200% 100%;animation:shimmer 1.5s infinite}@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}#loader{text-align:center;padding:20px;color:#888}.modal{display:none;position:fixed;z-index:1000;left:0;top:0;width:100%;height:100%;overflow:hidden;background-color:rgba(0,0,0,.9);align-items:center;justify-content:center}.modal-content{max-width:95vw;max-height:95vh;object-fit:contain}.modal-close{position:absolute;top:20px;right:35px;color:#f1f1f1;font-size:40px;font-weight:700;cursor:pointer}.modal-nav{position:absolute;top:50%;transform:translateY(-50%);color:#f1f1f1;font-size:60px;font-weight:700;cursor:pointer;user-select:none;padding:16px}.modal-prev{left:0}.modal-next{right:0}
</style></head><body><div class="header"><div class="nav">
<a href="/search">搜索</a><a href="/">随机视图</a><a href="/slideshow">幻灯片</a><a href="/videos">视频/GIF</a><a href="/tags">角色标签</a><a href="/rescan">重新扫描</a>
</div></div><div id="grid-container"></div><div id="loader">正在加载...</div><div id="imageModal" class="modal"><span class="modal-close">&times;</span><span class="modal-nav modal-prev">&#10094;</span><img class="modal-content" id="modalImage"><span class="modal-nav modal-next">&#10095;</span></div>
<script>{% raw %}const grid=document.getElementById("grid-container"),loader=document.getElementById("loader"),imageModal=document.getElementById("imageModal"),modalImage=document.getElementById("modalImage"),closeBtn=document.querySelector(".modal-close"),prevBtn=document.querySelector(".modal-prev"),nextBtn=document.querySelector(".modal-next");let allImages=[],currentIndex=0,currentModalImageIndex=-1;const BATCH_SIZE=30;function loadMoreImages(){if(currentIndex>=allImages.length){loader.textContent="已加载全部";return}const t=allImages.slice(currentIndex,currentIndex+BATCH_SIZE);for(const[e,a]of t.entries()){const t=document.createElement("div");t.className="grid-item";const n=document.createElement("div");n.className="skeleton",t.appendChild(n);const d=document.createElement("img"),o=currentIndex+e;t.dataset.index=o,d.dataset.index=o,d.onload=()=>{t.removeChild(n),d.classList.add("loaded")},d.src=`/media/${a}`,t.appendChild(d),grid.appendChild(t)}currentIndex+=BATCH_SIZE}function openModal(e){currentModalImageIndex=parseInt(e),modalImage.src=`/media/${allImages[currentModalImageIndex]}`,imageModal.style.display="flex",document.body.style.overflow="hidden"}function closeModal(){imageModal.style.display="none",document.body.style.overflow=""}function showNextImage(){currentModalImageIndex=(currentModalImageIndex+1)%allImages.length,openModal(currentModalImageIndex)}function showPrevImage(){currentModalImageIndex=(currentModalImageIndex-1+allImages.length)%allImages.length,openModal(currentModalImageIndex)}async function initializeGrid(){try{const e=await fetch("/api/images");if(allImages=await e.json(),0===allImages.length)return void(loader.textContent="未找到任何图片。");loadMoreImages();new IntersectionObserver(e=>{e[0].isIntersecting&&loadMoreImages()},{rootMargin:"200px"}).observe(loader)}catch(e){console.error("无法初始化网格:",e),loader.textContent="加载图片列表失败。"}}grid.addEventListener("click",e=>{e.target.dataset.index&&openModal(e.target.dataset.index)}),closeBtn.addEventListener("click",closeModal),prevBtn.addEventListener("click",showPrevImage),nextBtn.addEventListener("click",showNextImage),document.addEventListener("keydown",e=>{"flex"===imageModal.style.display&&("Escape"===e.key?closeModal():"ArrowRight"===e.key?showNextImage():"ArrowLeft"===e.key&&showPrevImage())}),document.addEventListener("DOMContentLoaded",initializeGrid);{% endraw %}</script></body></html>
"""

# --- Template 4: Video Grid Page ---
VIDEO_GRID_HTML="""
<!DOCTYPE html><html lang="zh-CN"><head><title>视频/GIF 网格</title><style>
body{margin:0;background-color:#222;font-family:sans-serif}
.header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:10px 15px;z-index:100;display:flex;align-items:center;gap:10px}
.header .nav{margin-left:auto;display:flex;align-items:center;gap:10px}
.header .nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;white-space:nowrap}
#grid-container{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px;padding:10px}.grid-item{position:relative;border-radius:8px;cursor:pointer;background-color:#333;aspect-ratio:9/16;overflow:hidden}.grid-item img,.grid-item video{width:100%;height:100%;display:block;object-fit:cover;opacity:0;transition:opacity .5s}.grid-item img.loaded,.grid-item video.loaded{opacity:1}.skeleton{position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(90deg,#333 25%,#444 50%,#333 75%);background-size:200% 100%;animation:shimmer 1.5s infinite}@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}#loader{text-align:center;padding:20px;color:#888}.modal{display:none;position:fixed;z-index:1000;left:0;top:0;width:100%;height:100%;overflow:hidden;background-color:rgba(0,0,0,.9)}.modal-content-container{width:100%;height:100%;display:flex;justify-content:center;align-items:center}.modal-content-container img,.modal-content-container video{max-width:95vw;max-height:95vh;object-fit:contain}.modal-close{position:absolute;top:15px;right:35px;color:#f1f1f1;font-size:40px;font-weight:700;cursor:pointer}.modal-nav{position:absolute;top:50%;transform:translateY(-50%);font-size:50px;color:#fff;padding:16px;cursor:pointer;user-select:none;z-index:1001}.modal-prev{left:10px}.modal-next{right:10px}
</style></head><body><div class="header"><div class="nav">
<a href="/search">搜索</a><a href="/">随机视图</a><a href="/slideshow">幻灯片</a><a href="/grid">图片网格</a><a href="/tags">角色标签</a><a href="/rescan">重新扫描</a>
</div></div><div id="grid-container"></div><div id="loader">正在加载...</div><div id="mediaModal" class="modal"><span class="modal-close">&times;</span><span class="modal-nav modal-prev">&#10094;</span><div class="modal-content-container" id="modalMediaContainer"></div><span class="modal-nav modal-next">&#10095;</span></div>
<script>{% raw %}const grid=document.getElementById("grid-container"),loader=document.getElementById("loader"),mediaModal=document.getElementById("mediaModal"),modalMediaContainer=document.getElementById("modalMediaContainer");let allMedia=[],currentIndex=0,currentModalIndex=-1;const BATCH_SIZE=20;function createMediaElement(e,t){const a=e.toLowerCase().endsWith(".gif");let l;if(a)l=document.createElement("img");else{l=document.createElement("video"),l.loop=!0,l.playsinline=!0,t?(l.autoplay=!0,l.muted=!0):(l.autoplay=!0,l.controls=!0,l.muted=!1)}return l.src=`/media/${e}`,l}function loadMoreMedia(){if(currentIndex>=allMedia.length){loader.textContent="已加载全部";return}const e=allMedia.slice(currentIndex,currentIndex+BATCH_SIZE);for(const[t,a]of e.entries()){const e=document.createElement("div");e.className="grid-item";const n=document.createElement("div");n.className="skeleton",e.appendChild(n);const d=currentIndex+t;e.dataset.index=d;const i=createMediaElement(a,!0);e.appendChild(i);const o=i.tagName.toLowerCase();"video"===o?i.onloadeddata=()=>{e.contains(n)&&e.removeChild(n),i.classList.add("loaded")}:i.onload=()=>{e.contains(n)&&e.removeChild(n),i.classList.add("loaded")},grid.appendChild(e)}currentIndex+=BATCH_SIZE}function openModal(e){currentModalIndex=parseInt(e);const t=allMedia[currentModalIndex];modalMediaContainer.innerHTML="";const a=createMediaElement(t,!1);modalMediaContainer.appendChild(a),mediaModal.style.display="block",document.body.style.overflow="hidden"}function closeModal(){mediaModal.style.display="none",modalMediaContainer.innerHTML="",document.body.style.overflow=""}function showAdjacentMedia(e){if(-1===currentModalIndex)return;currentModalIndex=(currentModalIndex+e+allMedia.length)%allMedia.length,openModal(currentModalIndex)}async function initializeGrid(){try{const e=await fetch("/api/videos");if(allMedia=await e.json(),0===allMedia.length)return void(loader.textContent="未找到任何视频或GIF。");loadMoreMedia();new IntersectionObserver(e=>{e[0].isIntersecting&&loadMoreMedia()},{rootMargin:"400px"}).observe(loader)}catch(e){console.error("无法初始化网格:",e),loader.textContent="加载列表失败。"}}grid.addEventListener("click",e=>{const t=e.target.closest(".grid-item");t&&t.dataset.index&&openModal(t.dataset.index)}),document.querySelector(".modal-close").addEventListener("click",closeModal),document.querySelector(".modal-prev").addEventListener("click",()=>showAdjacentMedia(-1)),document.querySelector(".modal-next").addEventListener("click",()=>showAdjacentMedia(1)),document.addEventListener("keydown",e=>{"block"===mediaModal.style.display&&("Escape"===e.key?closeModal():"ArrowRight"===e.key?showAdjacentMedia(1):"ArrowLeft"===e.key&&showAdjacentMedia(-1))}),document.addEventListener("DOMContentLoaded",initializeGrid);{% endraw %}</script></body></html>
"""

# --- Template 5: Tags Index Page ---
TAGS_INDEX_HTML="""
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>角色标签</title><style>
body{margin:0;background-color:#222;font-family:sans-serif}
.header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:15px;z-index:100;display:flex;align-items:center;gap:15px}
.header .nav{margin-left:auto;display:flex;align-items:center;gap:10px}
.header a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;margin-left:10px}
#search-box{padding:8px 12px;font-size:1em;border-radius:5px;border:1px solid #555;background-color:#333;color:#fff;width:250px}
#grid-container{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:15px;padding:15px}.character-card{display:block;position:relative;border-radius:8px;overflow:hidden;aspect-ratio:3/4;background-size:cover;background-position:center;text-decoration:none;color:#fff;transition:transform .2s ease-out;background-color:#333}.character-card:hover{transform:scale(1.03)}.character-card::after{content:'';position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(to top,rgba(0,0,0,.8) 0%,rgba(0,0,0,0) 50%)}.character-name{position:absolute;bottom:10px;left:15px;font-size:1.2em;font-weight:700;z-index:1;text-shadow:1px 1px 3px rgba(0,0,0,.7)}#loader{text-align:center;padding:20px;color:#888}
</style></head><body><div class="header"><input type="search" id="search-box" placeholder="搜索角色..."><div class="nav">
<a href="/search">搜索</a><a href="/">随机视图</a><a href="/slideshow">幻灯片</a><a href="/grid">图片网格</a><a href="/videos">视频/GIF</a><a href="/rescan">重新扫描</a>
</div></div><div id="grid-container"></div><div id="loader">正在加载角色列表...</div>
<script>{% raw %}
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
{% endraw %}</script></body></html>
"""

# --- Template 6: Character Gallery Page ---
CHARACTER_GALLERY_HTML="""
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>角色: {{ character_name.replace('_', ' ') }}</title><style>
body{margin:0;background-color:#222;font-family:sans-serif}
.header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:15px;z-index:100;display:flex;align-items:center}
.header .title{font-size:1.2em;color:#fff;margin-right:auto;padding-left:15px}
.header .nav{display:flex;align-items:center;gap:10px;margin-left:auto}
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
</style></head><body data-character-name="{{ character_name | urlencode }}" data-page-size="{{ PAGE_SIZE }}"><div class="header"><span class="title">角色: {{ character_name.replace('_', ' ') }}</span><div class="nav">
<a href="/search">搜索</a><a href="/">随机视图</a><a href="/grid">图片网格</a><a href="/tags">返回角色列表</a><a href="/rescan">重新扫描</a>
</div></div><div id="grid-container"></div><div id="loader">正在加载图片...</div><div id="imageModal" class="modal"><span class="modal-close">&times;</span><span class="modal-nav modal-prev">&#10094;</span><img class="modal-content" id="modalImage"><span class="modal-nav modal-next">&#10095;</span></div>
<script>{% raw %}
document.addEventListener("DOMContentLoaded",()=>{const e=document.body.dataset.characterName,t=parseInt(document.body.dataset.pageSize),a=document.getElementById("grid-container"),n=document.getElementById("loader"),d=document.getElementById("imageModal"),o=document.getElementById("modalImage"),i=document.querySelector(".modal-close"),l=document.querySelector(".modal-prev"),r=document.querySelector(".modal-next");let c=[],s=-1,u=1,m=!1,h=!1;async function p(){if(m||h)return;m=!0,n.textContent="正在加载...";try{const i=await fetch(`/api/character_images/${e}?page=${u}&limit=${t}`),l=await i.json();if(0===l.length){h=!0,n.textContent=0===c.length?"未找到该角色的任何图片。":"已加载全部";g&&g.disconnect();return}c.push(...l);for(const[e,t]of l.entries()){const i=document.createElement("div");i.className="grid-item";const l=document.createElement("div");l.className="skeleton",i.appendChild(l);const r=document.createElement("img"),p=c.indexOf(t);i.dataset.index=p,r.dataset.index=p,r.onload=()=>{i.contains(l)&&i.removeChild(l),r.classList.add("loaded")},r.src=`/media/${t}`,i.appendChild(r),a.appendChild(i)}u++}catch(i){console.error("无法加载更多图片:",i),n.textContent="加载失败。"}finally{m=!1}}function g(e){s=parseInt(e),o.src=`/media/${c[s]}`,d.style.display="flex",document.body.style.overflow="hidden"}function f(){d.style.display="none",document.body.style.overflow=""}function v(){0!==c.length&&(s=(s+1)%c.length,g(s))}function w(){0!==c.length&&(s=(s-1+c.length)%c.length,g(s))}const observer=new IntersectionObserver(e=>{e[0].isIntersecting&&p()},{rootMargin:"200px"});p(),observer.observe(n),a.addEventListener("click",e=>{e.target.dataset.index&&g(e.target.dataset.index)}),i.addEventListener("click",f),l.addEventListener("click",w),r.addEventListener("click",v),document.addEventListener("keydown",e=>{"flex"===d.style.display&&("Escape"===e.key?f():"ArrowRight"===e.key?v():"ArrowLeft"===e.key&&w())})});
{% endraw %}</script></body></html>
"""

# --- Template 7: Search Page ---
SEARCH_HTML = """
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>搜索: {{ search_query }}</title><style>
body{margin:0;background-color:#222;font-family:sans-serif}
.header{position:sticky;top:0;background-color:rgba(20,20,20,.95);padding:10px 15px;z-index:100;display:flex;align-items:center;gap:15px}
.header .search-form{display:flex;align-items:center;background-color:rgba(0,0,0,0.5);border-radius:5px;}
.header .search-form input{background-color:#333;border:1px solid #555;color:#fff;padding:8px 12px;font-size:1em;border-radius:5px 0 0 5px;outline:none;width:300px;}
.header .search-form button{background-color:#444;color:#fff;border:1px solid #555;border-left:none;padding:8px 12px;border-radius:0 5px 5px 0;cursor:pointer;}
.header .nav{margin-left:auto;display:flex;align-items:center;gap:10px}
.header .nav a{color:#fff;text-decoration:none;padding:8px 15px;background-color:rgba(0,0,0,.5);border-radius:5px;white-space:nowrap}
#grid-container{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;padding:10px}.grid-item{position:relative;border-radius:8px;cursor:pointer;background-color:#333;aspect-ratio:3/4;overflow:hidden}.grid-item img{width:100%;height:100%;display:block;object-fit:cover;opacity:0;transition:opacity .5s}.grid-item img.loaded{opacity:1}.skeleton{position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(90deg,#333 25%,#444 50%,#333 75%);background-size:200% 100%;animation:shimmer 1.5s infinite}@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}#loader{text-align:center;padding:20px;color:#888}.modal{display:none;position:fixed;z-index:1000;left:0;top:0;width:100%;height:100%;overflow:hidden;background-color:rgba(0,0,0,.9);align-items:center;justify-content:center}.modal-content{max-width:95vw;max-height:95vh;object-fit:contain}.modal-close{position:absolute;top:20px;right:35px;color:#f1f1f1;font-size:40px;font-weight:700;cursor:pointer}.modal-nav{position:absolute;top:50%;transform:translateY(-50%);color:#f1f1f1;font-size:60px;font-weight:700;cursor:pointer;user-select:none;padding:16px}.modal-prev{left:0}.modal-next{right:0}
</style></head><body data-query="{{ search_query | urlencode }}" data-page-size="{{ PAGE_SIZE }}">
<div class="header">
<form action="/search" method="get" class="search-form">
    <input type="search" name="q" placeholder="输入标签搜索 (e.g. 1girl looking_at_viewer)..." value="{{ search_query | default('') }}" autofocus>
    <button type="submit">搜索</button>
</form>
<div class="nav">
<a href="/">随机视图</a><a href="/grid">图片网格</a><a href="/tags">角色列表</a><a href="/rescan">重新扫描</a>
</div></div><div id="grid-container"></div><div id="loader">输入标签以开始搜索。</div>
<div id="imageModal" class="modal"><span class="modal-close">&times;</span><span class="modal-nav modal-prev">&#10094;</span><img class="modal-content" id="modalImage"><span class="modal-nav modal-next">&#10095;</span></div>
<script>{% raw %}
document.addEventListener("DOMContentLoaded", () => {
    const query = document.body.dataset.query;
    const pageSize = parseInt(document.body.dataset.pageSize);
    const grid = document.getElementById("grid-container");
    const loader = document.getElementById("loader");
    const imageModal = document.getElementById("imageModal"), modalImage = document.getElementById("modalImage");
    const closeBtn = document.querySelector(".modal-close"), prevBtn = document.querySelector(".modal-prev"), nextBtn = document.querySelector(".modal-next");
    let foundImages = [], currentModalIndex = -1, currentPage = 1, isLoading = false, noMoreData = false;
    async function loadMoreResults() {
        if (isLoading || noMoreData || !query) return;
        isLoading = true; loader.textContent = "正在加载..."; loader.style.display = 'block';
        try {
            const response = await fetch(`/api/search?q=${query}&page=${currentPage}&limit=${pageSize}`);
            if (!response.ok) throw new Error('网络响应错误');
            const newImages = await response.json();
            if (newImages.length === 0) {
                noMoreData = true;
                loader.textContent = foundImages.length === 0 ? "未找到任何匹配的图片。" : "已加载全部结果";
                if (observer) observer.disconnect();
                return;
            }
            newImages.forEach(imagePath => {
                const item = document.createElement("div"); item.className = "grid-item";
                const skeleton = document.createElement("div"); skeleton.className = "skeleton"; item.appendChild(skeleton);
                const img = document.createElement("img");
                const index = foundImages.length;
                item.dataset.index = index; img.dataset.index = index;
                img.onload = () => { if(item.contains(skeleton)) item.removeChild(skeleton); img.classList.add("loaded"); };
                img.src = `/media/${imagePath}`;
                item.appendChild(img); grid.appendChild(item); foundImages.push(imagePath);
            });
            currentPage++;
        } catch (error) { console.error("无法加载搜索结果:", error); loader.textContent = "加载失败。"; } finally { isLoading = false; }
    }
    function openModal(index) { currentModalIndex = parseInt(index); modalImage.src = `/media/${foundImages[currentModalIndex]}`; imageModal.style.display = "flex"; document.body.style.overflow = "hidden"; }
    function closeModal() { imageModal.style.display = "none"; document.body.style.overflow = ""; }
    function showNextImage() { if (foundImages.length === 0) return; currentModalIndex = (currentModalIndex + 1) % foundImages.length; openModal(currentModalIndex); }
    function showPrevImage() { if (foundImages.length === 0) return; currentModalIndex = (currentModalIndex - 1 + foundImages.length) % foundImages.length; openModal(currentModalIndex); }
    if (!query) { return; }
    const observer = new IntersectionObserver((entries) => { if (entries[0].isIntersecting) { loadMoreResults(); } }, { rootMargin: "200px" });
    loadMoreResults(); observer.observe(loader);
    grid.addEventListener("click", e => { if (e.target.dataset.index) openModal(e.target.dataset.index); });
    closeBtn.addEventListener("click", closeModal); prevBtn.addEventListener("click", showPrevImage); nextBtn.addEventListener("click", showNextImage);
    document.addEventListener("keydown", e => { if (imageModal.style.display === "flex") { if (e.key === "Escape") closeModal(); if (e.key === "ArrowRight") showNextImage(); if (e.key === "ArrowLeft") showPrevImage(); } });
});
{% endraw %}</script></body></html>
"""

# --- Start Server ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)