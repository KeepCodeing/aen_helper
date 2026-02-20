import os
import json
import sqlite3
import re
import random
import traceback
import binascii
import config

# --- 全局状态 ---
class MediaState:
    image_files = []
    video_and_gif_files = []

# --- 数据库统一缓存类 ---
class DBCache:
    all_tags = []
    characters = []
    _loaded = False

    @classmethod
    def load(cls, force_rescan=False):
        # 【修复1：防空拦截】
        if not getattr(config, 'DB_CACHE_FILE', None):
            return
            
        # 1. 尝试从本地文件加载
        if not force_rescan and os.path.exists(config.DB_CACHE_FILE):
            try:
                with open(config.DB_CACHE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    cls.all_tags = data.get("all_tags", [])
                    cls.characters = data.get("characters", [])
                    cls._loaded = True
                    print(f"成功加载数据库缓存: 标签 {len(cls.all_tags)} 个, 角色 {len(cls.characters)} 个")
                    return
            except Exception as e:
                print(f"读取数据库缓存失败，将重新生成: {e}")
        
        # 2. 如果无缓存或强制刷新，执行全量 SQL 查询
        print("开始全量查询数据库并构建缓存 (这可能需要几秒钟)...")
        cls._refresh_from_db()
        
        # 3. 写入本地文件
        try:
            # 【修复2：确保写入前，父级目录 .aen_data 已经被创建】
            os.makedirs(os.path.dirname(config.DB_CACHE_FILE), exist_ok=True)
            with open(config.DB_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump({"all_tags": cls.all_tags, "characters": cls.characters}, f, ensure_ascii=False)
            print("数据库缓存已保存到本地。")
        except Exception as e:
            print(f"保存数据库缓存失败: {e}")


    @classmethod
    def _refresh_from_db(cls):
        conn = get_db_connection()
        if not conn: return
        try:
            # === A. 构建 Tag 自动补全数据 ===
            tag_query = """
                SELECT t.name, COUNT(it.image_id) as cnt
                FROM tags t JOIN image_tags it ON t.id = it.tag_id GROUP BY t.name
            """
            tags = [{'name': row['name'], 'count': row['cnt'], 'type': 'tag'} for row in conn.execute(tag_query).fetchall()]

            char_query = "SELECT character_name, COUNT(*) as cnt FROM images GROUP BY character_name"
            chars_for_tags = []
            for row in conn.execute(char_query).fetchall():
                raw_name = row['character_name']
                display_name = f"char:{raw_name}" if raw_name else "Others / OC"
                chars_for_tags.append({'name': display_name, 'count': row['cnt'], 'type': 'char'})
            
            cls.all_tags = sorted(chars_for_tags + tags, key=lambda x: x['count'], reverse=True)

            # === B. 构建全量角色封面数据 (去掉 LIMIT 和 OFFSET) ===
            deep_pattern = os.path.join(config.PROJECT_PARENT_DIR, '%', '%')
            sql = f"""
                SELECT 
                    CASE WHEN character_name IS NULL OR character_name = '' THEN 'Others / OC' ELSE character_name END as name,
                    COUNT(*) as count,
                    (SELECT filepath FROM images img2 JOIN image_tags it ON img2.id = it.image_id JOIN tags t ON it.tag_id = t.id WHERE (img2.character_name = images.character_name OR (images.character_name IS NULL AND img2.character_name IS NULL)) AND t.name = 'looking at viewer' LIMIT 1) as cover_preferred,
                    (SELECT filepath FROM images img3 WHERE (img3.character_name = images.character_name OR (images.character_name IS NULL AND img3.character_name IS NULL)) LIMIT 1) as cover_fallback
                FROM images 
                GROUP BY character_name 
                HAVING MAX(CASE WHEN filepath LIKE ? THEN 1 ELSE 0 END) = 1
                ORDER BY name ASC 
            """
            cls.characters = []
            for row in conn.execute(sql, [deep_pattern]).fetchall():
                raw_cover = row['cover_preferred'] or row['cover_fallback']
                cls.characters.append({
                    'name': row['name'], 
                    'count': row['count'], 
                    'cover': to_web_path(raw_cover)
                })
            
            cls._loaded = True
        except Exception as e:
            print(f"数据库查询失败: {e}")
        finally:
            conn.close()

    @classmethod
    def get_all_tags(cls):
        if not cls._loaded: cls.load()
        return cls.all_tags

# --- 辅助函数 ---

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', s)]

def get_db_connection():
    if not os.path.exists(config.DB_PATH):
        return None
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def to_web_path(local_path):
    """【核心兼容补丁】：把所有路径洗成相对路径"""
    if not local_path: return ""
    norm_path = os.path.normpath(local_path)
    # 如果传来的是老数据库里的绝对路径，动态剥离成相对路径
    if os.path.isabs(norm_path):
        try:
            rel = os.path.relpath(norm_path, config.PROJECT_PARENT_DIR)
            if not rel.startswith(".."):
                norm_path = rel
        except ValueError: pass
    return norm_path.replace('\\', '/')

def scan_media_files(force_rescan=False):
    if not force_rescan and os.path.exists(config.CACHE_FILE):
        try:
            with open(config.CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                MediaState.image_files = data.get("images", [])
                MediaState.video_and_gif_files = data.get("videos_and_gifs", [])
                return
        except Exception: pass
    
    image_list, video_and_gif_list = [], []
    image_formats = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')
    video_formats = ('.mp4', '.webm', '.mov', '.mkv', '.avi', '.gif')

    # 扫描指定的挂载目录
    for root, dirs, files in os.walk(config.PROJECT_PARENT_DIR, topdown=True):
        # 忽略专属的数据文件夹
        if config.DATA_DIR_NAME in dirs: dirs.remove(config.DATA_DIR_NAME)
        for file in files:
            abs_path = os.path.join(root, file)
            # 计算并存入相对路径
            rel_path = os.path.relpath(abs_path, config.PROJECT_PARENT_DIR)
            web_path = rel_path.replace('\\', '/')
            
            file_lower = file.lower()
            if file_lower.endswith(image_formats): image_list.append(web_path)
            elif file_lower.endswith(video_formats): video_and_gif_list.append(web_path)

    image_list.sort(key=natural_sort_key)
    video_and_gif_list.sort(key=natural_sort_key)

    try:
        os.makedirs(os.path.dirname(config.CACHE_FILE), exist_ok=True)
        with open(config.CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({"images": image_list, "videos_and_gifs": video_and_gif_list}, f)
    except IOError: pass

    MediaState.image_files = image_list
    MediaState.video_and_gif_files = video_and_gif_list

def get_paginated_list(full_list, page, page_size, seed=None):
    total = len(full_list)
    if seed is not None:
        r = random.Random(seed)
        target_list = list(full_list)
        r.shuffle(target_list)
    else:
        target_list = full_list
    start = (page - 1) * page_size
    end = start + page_size
    items = target_list[start:end]
    return items, end < total

# --- Tag 缓存类 (自动补全用) ---
class TagCache:
    _tags = []
    _loaded = False

    @classmethod
    def refresh(cls):
        conn = get_db_connection()
        if not conn: return
        try:
            # 获取普通标签
            tag_query = """
                SELECT t.name, COUNT(it.image_id) as cnt
                FROM tags t
                JOIN image_tags it ON t.id = it.tag_id
                GROUP BY t.name
            """
            cursor = conn.execute(tag_query)
            tags = [{'name': row['name'], 'count': row['cnt'], 'type': 'tag'} 
                    for row in cursor.fetchall()]

            # 获取角色 (自动加上 char: 前缀)
            char_query = """
                SELECT character_name, COUNT(*) as cnt
                FROM images
                GROUP BY character_name
            """
            chars = []
            for row in conn.execute(char_query).fetchall():
                raw_name = row['character_name']
                if not raw_name:
                    display_name = "Others / OC" # 显示用，实际上这类会被下面的逻辑过滤掉
                else:
                    display_name = f"char:{raw_name}"
                
                chars.append({'name': display_name, 'count': row['cnt'], 'type': 'char'})
            
            cls._tags = sorted(chars + tags, key=lambda x: x['count'], reverse=True)
            cls._loaded = True
            print(f"[TagCache] Loaded {len(cls._tags)} items.")
        except Exception as e:
            print(f"TagCache refresh failed: {e}")
        finally:
            conn.close()

    @classmethod
    def get_all(cls):
        if not cls._loaded:
            cls.refresh()
        return cls._tags

# --- 核心查询功能 ---

def get_character_groups(page, page_size, search_query=None):
    """从内存中过滤和分页，性能极高"""
    if not DBCache._loaded:
        DBCache.load()
    
    # 1. 获取全量缓存列表
    chars = DBCache.characters
    
    # 2. 内存过滤 (替代 SQL 的 LIKE)
    if search_query:
        search_lower = search_query.lower()
        chars = [c for c in chars if search_lower in c['name'].lower()]
    
    # 3. 内存分页
    start = (page - 1) * page_size
    end = start + page_size
    
    return chars[start:end]

def get_folders_by_character(character_name):
    conn = get_db_connection()
    if not conn: return []
    try:
        # 核心修复：除了 strip 空格，还要 strip 掉多余的引号
        # 这样不管传入的是 "aegir" 还是 aegir，都能正确匹配
        clean_name = character_name.strip().strip('"').strip("'")
        
        # 使用清洗后的 clean_name 进行查询
        rows = conn.execute(
            "SELECT filepath FROM images WHERE character_name = ? COLLATE NOCASE", 
            (clean_name,)
        ).fetchall()
        
        return _aggregate_files_to_folders([row['filepath'] for row in rows])
    except Exception as e:
        print(f"Error: {e}")
        return []
    finally:
        conn.close()

def _aggregate_files_to_folders(filepath_list):
    """聚合文件到文件夹，带路径转换调试"""
    folder_map = {}
    
    for i, abs_path in enumerate(filepath_list):
        # 2. 检查路径转换逻辑
        web_file_path = to_web_path(abs_path)
        
        if not web_file_path:
            continue
        
        # 3. 检查文件夹切分
        if '/' in web_file_path: 
            web_folder_path = web_file_path.rsplit('/', 1)[0]
        else: 
            web_folder_path = "" # 根目录标识
        
        if web_folder_path not in folder_map:
            display_name = os.path.basename(web_folder_path) if web_folder_path else "根目录"
            folder_map[web_folder_path] = {
                'name': web_folder_path,
                'display_name': display_name,
                'count': 0,
                'cover': web_file_path
            }
        folder_map[web_folder_path]['count'] += 1
    
    result = list(folder_map.values())
    result.sort(key=lambda x: x['count'], reverse=True)
    return result
    
# --- 搜索逻辑 ---

def _build_search_sql(query_str):
    if not query_str: return None, None
    raw_terms = [t.strip() for t in query_str.split(',') if t.strip()]
    rating_filters = set()
    char_filters = set()
    tag_filters = set()

    for term in raw_terms:
        term_lower = term.lower()
        if term_lower.startswith("rating:"):
            val = term[7:].strip()
            if val: rating_filters.add(val)
        elif term_lower.startswith("char:"):
            val = term[5:].strip().replace('_', ' ')
            if val: char_filters.add(val)
        else:
            val = term.replace('_', ' ')
            if val: tag_filters.add(val)

    base_query = "SELECT T0.filepath FROM images AS T0"
    where_conditions = []
    params = []

    if tag_filters:
        tags_list = list(tag_filters)
        placeholders = ','.join(['?'] * len(tags_list))
        sub_query = f"""
            T0.id IN (
                SELECT it.image_id FROM image_tags it JOIN tags t ON it.tag_id = t.id 
                WHERE t.name IN ({placeholders}) GROUP BY it.image_id HAVING COUNT(DISTINCT t.name) = ?
            )
        """
        params.extend(tags_list)
        params.append(len(tags_list))
        where_conditions.append(sub_query)

    for char_name in char_filters:
        where_conditions.append("T0.character_name = ?"); params.append(char_name)
    for rating in rating_filters:
        where_conditions.append("T0.rating = ?"); params.append(rating)

    if not where_conditions: return None, None
    return f"{base_query} WHERE {' AND '.join(where_conditions)}", params

# utils.py
def search_files_paged(query_str, page, page_size, folder_filter=None):
    conn = get_db_connection()
    if not conn: return [], False
    try:
        sql_base, params = _build_search_sql(query_str)
        if not sql_base: return [], False
        
        rows = conn.execute(sql_base, params).fetchall()
        abs_filepaths = [row['filepath'] for row in rows]
        
        if not abs_filepaths:
            return [], False

        ordered_folders = _aggregate_files_to_folders(abs_filepaths)
        
        folder_to_files = {}
        for abs_path in abs_filepaths:
            web_path = to_web_path(abs_path)
            if not web_path: continue
            
            if '/' in web_path:
                folder_name = web_path.rsplit('/', 1)[0]
            else:
                folder_name = "" 
                
            # --- 【核心修改】：支持递归子目录过滤 ---
            if folder_filter is not None:
                # 检查 folder_name 是否等于 folder_filter，或者是 folder_filter 的子路径
                is_match = (folder_name == folder_filter) or folder_name.startswith(folder_filter + "/")
                if not is_match:
                    continue
            # ----------------------------------------
                
            if folder_name not in folder_to_files:
                folder_to_files[folder_name] = []
            folder_to_files[folder_name].append(web_path)
        
        # 4. 根据排好序的文件夹依次取出图片
        final_ordered_files = []
        for folder in ordered_folders:
            folder_name = folder['name']
            files_in_folder = folder_to_files.get(folder_name, [])
            if files_in_folder:
                files_in_folder.sort(key=natural_sort_key) # 局部自然排序
                final_ordered_files.extend(files_in_folder)
            
        # 5. 在内存中进行分页切片
        start = (page - 1) * page_size
        end = start + page_size
        results = final_ordered_files[start:end]
        
        return results, end < len(final_ordered_files)
    except Exception as e:
        print(f"Search error: {e}")
        return [], False
    finally: 
        conn.close()

def search_folders(query_str, folder_filter=None):
    conn = get_db_connection()
    if not conn: return []
    try:
        sql_base, params = _build_search_sql(query_str)
        if not sql_base: return []
        rows = conn.execute(sql_base, params).fetchall()
        
        abs_filepaths = [row['filepath'] for row in rows]
        if not abs_filepaths: return []
        
        # --- 【核心修复：在聚合之前，过滤掉不属于该目录的图片】 ---
        if folder_filter is not None:
            filtered_paths = []
            for abs_path in abs_filepaths:
                web_path = to_web_path(abs_path)
                if not web_path: continue
                
                # 提取图片所在的相对文件夹路径
                if '/' in web_path:
                    folder_name = web_path.rsplit('/', 1)[0]
                else:
                    folder_name = ""
                    
                # 逻辑判断：要求图片必须是在这个文件夹下，或者是它的子文件夹下
                # 例如 folder_filter="tsm2330"，那么 "tsm2330" 和 "tsm2330/album1" 都算匹配
                is_match = (folder_name == folder_filter) or folder_name.startswith(folder_filter + "/")
                if is_match:
                    filtered_paths.append(abs_path)
                    
            # 用过滤后的路径替换掉原本的所有路径
            abs_filepaths = filtered_paths
            if not abs_filepaths: return []
        # -----------------------------------------------------

        return _aggregate_files_to_folders(abs_filepaths)
    except Exception as e:
        print(f"Search folders error: {e}")
        return []
    finally: 
        conn.close()
    
def get_directory_tree(current_path=""):
    """获取指定相对路径下的子文件夹列表与文件状态 (增强版：优先图片封面)"""
    current_path = current_path.replace('\\', '/').strip("/")
    folders = {}
    immediate_files = []
    prefix = current_path + "/" if current_path else ""
    
    # 定义图片扩展名用于判断
    IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
    
    all_media_files = MediaState.image_files + MediaState.video_and_gif_files
    
    for f in all_media_files:
        f_normalized = f.replace("\\", "/")
        if current_path == "" or f_normalized.startswith(prefix):
            remainder = f_normalized[len(prefix):] if prefix else f_normalized
            parts = remainder.split('/')
            
            if len(parts) == 1:
                # 直属文件
                immediate_files.append(remainder)
            else:
                # 子文件夹内容
                folder_name = parts[0]
                is_image_file = f_normalized.lower().endswith(IMG_EXTS)

                if folder_name not in folders:
                    # 初始化文件夹数据结构
                    folders[folder_name] = {
                        'name': folder_name,
                        'path': prefix + folder_name,
                        'cover': None,         # 稍后决定
                        'has_sub': False,      # 是否还有深层子文件夹
                        'has_img': False,      # 这个子文件夹里是否有直接的图片
                        'count': 0
                    }
                
                f_data = folders[folder_name]
                f_data['count'] += 1
                
                # --- [核心修改] 智能封面选择逻辑 ---
                if is_image_file:
                    f_data['has_img'] = True
                    # 如果当前没有封面，或者当前的封面不是图片，则替换为这个图片
                    current_cover = f_data['cover']
                    if current_cover is None or not current_cover.lower().endswith(IMG_EXTS):
                         f_data['cover'] = f_normalized
                else:
                    # 如果是视频，且当前没有任何封面，暂时用视频顶替
                    if f_data['cover'] is None:
                        f_data['cover'] = f_normalized

                # 深度判断 (保持不变)
                if len(parts) > 2:
                    f_data['has_sub'] = True

    # 按自然拼写顺序排序
    folder_list = sorted(list(folders.values()), key=lambda x: natural_sort_key(x['name']))
    return folder_list, immediate_files