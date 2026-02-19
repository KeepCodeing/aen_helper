# search.py (新版逻辑 - 已添加列表功能)
import argparse
import sqlite3
import os

DB_PATH = "image_tags.db"

def list_characters():
    """列出数据库中所有已记录的角色及其图片数量"""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file '{DB_PATH}' not found.")
        return

    print(f"\nScanning database for characters in '{DB_PATH}'...\n")
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # 查询角色名并统计每个角色的图片数量，按数量降序排列
        query = """
            SELECT character_name, COUNT(*) as count 
            FROM images 
            GROUP BY character_name 
            ORDER BY count DESC
        """
        cursor.execute(query)
        results = cursor.fetchall()

    if results:
        print(f"{'Character Name':<40} | {'Count':<10}")
        print("-" * 55)
        for name, count in results:
            # 处理可能的 None 情况
            display_name = name if name else "Unknown"
            print(f"{display_name:<40} | {count:<10}")
        print("-" * 55)
        print(f"Total unique characters: {len(results)}\n")
    else:
        print("No characters found in the database.")

def list_tags():
    """列出数据库中支持的所有 Tag"""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file '{DB_PATH}' not found.")
        return

    print(f"\nListing all supported tags from '{DB_PATH}'...\n")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM tags ORDER BY name ASC")
        results = cursor.fetchall()

    if results:
        # 将结果展平为列表
        tags = [row[0] for row in results]
        
        # 简单打印，或者你可以选择每行打印多个
        # 这里为了清晰，每行打印一个，如果太长可以考虑用逗号分隔
        print(f"Found {len(tags)} tags:\n")
        
        # 这种方式每行打印几个，节省屏幕空间
        print(", ".join(tags)) 
        print(f"\n\nTotal tags: {len(tags)}\n")
    else:
        print("No tags found in the database.")

def search_images(query_string: str):
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file '{DB_PATH}' not found."); return

    if not query_string:
        print("Error: No query provided."); return

    raw_tags = [tag.strip() for tag in query_string.split(',') if tag.strip()]
    rating_filters, char_filters, general_tags = [], [], []
    
    for tag in raw_tags:
        if tag.lower().startswith("rating:"):
            rating_filters.append(tag.split(':', 1)[1].strip())
        elif tag.lower().startswith("char:"):
            char_filters.append(tag.split(':', 1)[1].strip())
        else:
            general_tags.append(tag.strip())

    base_query = "SELECT DISTINCT T0.filepath FROM images AS T0"
    params, where_clauses = [], []
    
    if general_tags:
        # 注意：这里假设数据库中的 tag.name 已经是空格分隔的格式（如 "1girl"），而不是下划线
        general_subquery = f"T0.id IN (SELECT it.image_id FROM image_tags it JOIN tags t ON it.tag_id = t.id WHERE t.name IN ({','.join(['?']*len(general_tags))}) GROUP BY it.image_id HAVING COUNT(it.image_id) = ?)"
        where_clauses.append(general_subquery)
        params.extend(general_tags); params.append(len(general_tags))

    for rating in rating_filters:
        where_clauses.append("T0.rating = ?"); params.append(rating)
        
    for char in char_filters:
        where_clauses.append("T0.character_name = ?"); params.append(char)
        
    if not where_clauses:
        print("Please provide tags to search for."); return

    final_query = f"{base_query} WHERE {' AND '.join(where_clauses)}"
    
    print("\n" + "="*20 + " EXECUTING SQL " + "="*20)
    print("Query:", final_query)
    print("Params:", params)
    print("="*55 + "\n")
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(final_query, params)
        results = cursor.fetchall()

    if results:
        print(f"Found {len(results)} matching image(s):"); [print(row[0]) for row in results]
    else:
        print("No images found matching all the specified criteria.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A standalone tool to search or inspect the image tag database.")
    
    # 将 query 设为可选 (nargs='?')，这样我们就可以只运行 --list-chars 而不输入查询
    parser.add_argument("query", type=str, nargs='?', help="Comma-separated tags. E.g., '1girl, rating: safe'")
    
    # 添加两个互斥的参数
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list-chars", action="store_true", help="List all character names found in the database.")
    group.add_argument("--list-tags", action="store_true", help="List all available tags in the database.")

    args = parser.parse_args()

    # 逻辑判断
    if args.list_chars:
        list_characters()
    elif args.list_tags:
        list_tags()
    elif args.query:
        search_images(args.query)
    else:
        parser.print_help()