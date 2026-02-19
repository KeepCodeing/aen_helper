# db_manager.py
import argparse
import sqlite3
import os

DATA_DIR_NAME = ".aen_data"
DB_NAME = "image_tags.db"

def get_db_path(target_dir):
    return os.path.join(os.path.abspath(target_dir), DATA_DIR_NAME, DB_NAME)

def list_characters(db_path):
    print(f"\nScanning database for characters in '{db_path}'...\n")
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        query = "SELECT character_name, COUNT(*) as count FROM images GROUP BY character_name ORDER BY count DESC"
        results = cursor.execute(query).fetchall()

    if results:
        print(f"{'Character Name':<40} | {'Count':<10}")
        print("-" * 55)
        for name, count in results:
            display_name = name if name else "Unknown"
            print(f"{display_name:<40} | {count:<10}")
        print("-" * 55)
        print(f"Total unique characters: {len(results)}\n")
    else:
        print("No characters found in the database.")

def list_tags(db_path):
    print(f"\nListing all supported tags from '{db_path}'...\n")
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        results = cursor.execute("SELECT name FROM tags ORDER BY name ASC").fetchall()

    if results:
        tags = [row[0] for row in results]
        print(f"Found {len(tags)} tags:\n")
        print(", ".join(tags)) 
        print(f"\n\nTotal tags: {len(tags)}\n")
    else:
        print("No tags found in the database.")

def handle_cleanup(target_dir, db_path):
    print(f"Starting database cleanup and self-check for '{target_dir}'...")
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()
        cursor.execute("SELECT id, filepath FROM images")
        rows = cursor.fetchall()
        
        invalid_ids = []
        for img_id, rel_path in rows:
            # 将数据库里的相对路径拼上目标目录，检查物理文件是否还在
            abs_path = os.path.join(target_dir, rel_path)
            if not os.path.exists(abs_path):
                invalid_ids.append(img_id)
        
        if invalid_ids:
            print(f"Found {len(invalid_ids)} missing files (broken links). Cleaning up database...")
            for i in range(0, len(invalid_ids), 999):
                batch = invalid_ids[i:i+999]
                cursor.execute(f"DELETE FROM images WHERE id IN ({','.join(['?']*len(batch))})", batch)
            conn.commit()
            print("Cleanup complete.")
        else:
            print("Database is healthy. No broken links found.")

def search_images(db_path, query_string: str):
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
    
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(final_query, params)
        results = cursor.fetchall()

    if results:
        print(f"Found {len(results)} matching image(s):"); [print(row[0]) for row in results]
    else:
        print("No images found matching all the specified criteria.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Database Manager for AEN Helper (Portable Library Mode).")
    
    # 全局参数：必须指定维护哪一个目录的数据库
    parser.add_argument("--db-dir", type=str, required=True, help="The target directory containing the .aen_data folder.")
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")
    
    parser_query = subparsers.add_parser("query", help="Search images by tags.")
    parser_query.add_argument("tags", type=str, help="Comma-separated tags. E.g., '1girl, rating:safe'")
    
    subparsers.add_parser("list-chars", help="List all characters.")
    subparsers.add_parser("list-tags", help="List all available tags.")
    subparsers.add_parser("cleanup", help="Remove DB records for files that no longer exist.")

    args = parser.parse_args()
    
    target_dir = os.path.abspath(args.db_dir)
    db_path = get_db_path(target_dir)
    
    if not os.path.exists(db_path):
        print(f"Error: Database not found at '{db_path}'. Please run ai_tagger.py on this directory first.")
        exit(1)

    if args.command == "query":
        search_images(db_path, args.tags)
    elif args.command == "list-chars":
        list_characters(db_path)
    elif args.command == "list-tags":
        list_tags(db_path)
    elif args.command == "cleanup":
        handle_cleanup(target_dir, db_path)