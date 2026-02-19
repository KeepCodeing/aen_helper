import os
import json
import re
from config import (
    CACHE_FILE, PROJECT_PARENT_DIR, PROJECT_DIR_NAME, 
    IMAGE_FORMATS, VIDEO_FORMATS
)

class MediaState:
    def __init__(self):
        self.images = []
        self.videos = []

    def load(self, force_rescan=False):
        if not force_rescan and os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.images = data.get("images", [])
                    self.videos = data.get("videos_and_gifs", [])
                    if self.images or self.videos:
                        print(f"成功加载缓存: 图片 {len(self.images)}, 视频 {len(self.videos)}")
                        return
            except Exception:
                pass
        
        print("开始全盘扫描媒体文件...")
        self.images, self.videos = self._scan_disk()
        
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump({"images": self.images, "videos_and_gifs": self.videos}, f)
            print(f"扫描完成并保存缓存。共找到 {len(self.images)} 张图片。")
        except IOError as e:
            print(f"保存缓存失败: {e}")

    def _scan_disk(self):
        img_list = []
        vid_list = []
        
        # 定义需要排除的文件夹（黑名单）
        EXCLUDE_DIRS = {
            PROJECT_DIR_NAME,  # 本项目文件夹
            '.venv',           # Python 虚拟环境
            'venv',
            '.git',            # Git 仓库
            '__pycache__',     # Python 缓存
            '.idea',           # PyCharm 配置
            '.vscode',         # VSCode 配置
            'node_modules'     # 如果有前端依赖
        }

        # 使用 os.walk 遍历
        for root, dirs, files in os.walk(PROJECT_PARENT_DIR, topdown=True):
            # --- 关键修复：排除黑名单目录 ---
            # 这里的 dirs[:] 修改会直接影响 os.walk 的后续遍历
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('.')]
            
            for file in files:
                file_lower = file.lower()
                if file_lower.endswith(IMAGE_FORMATS) or file_lower.endswith(VIDEO_FORMATS):
                    abs_path = os.path.join(root, file)
                    try:
                        # 计算相对于 PROJECT_PARENT_DIR 的相对路径
                        rel_path = os.path.relpath(abs_path, PROJECT_PARENT_DIR)
                        rel_path_web = rel_path.replace('\\', '/')
                        
                        if file_lower.endswith(IMAGE_FORMATS):
                            img_list.append(rel_path_web)
                        else:
                            vid_list.append(rel_path_web)
                    except ValueError:
                        continue
        
        return img_list, vid_list

# 初始化全局状态
media_state = MediaState()

def natural_sort_key(filepath):
    """文件名自然排序逻辑"""
    filename = os.path.basename(filepath)
    # 提取所有数字，转为整数用于比较
    parts = re.split(r'(\d+)', filename)
    parts = [int(p) if p.isdigit() else p.lower() for p in parts]
    
    # 获取创建时间作为次要排序依据
    full_path = os.path.join(PROJECT_PARENT_DIR, filepath)
    ctime = 0
    try:
        ctime = os.path.getctime(full_path)
    except:
        pass
        
    return (parts, ctime)