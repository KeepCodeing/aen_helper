# search.py (新版逻辑)
import argparse
import sqlite3
import os

DB_PATH = "image_tags.db"

def search_images(query_string: str):
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file '{DB_PATH}' not found."); return

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
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(final_query, params)
        results = cursor.fetchall()

    if results:
        print(f"Found {len(results)} matching image(s):"); [print(row[0]) for row in results]
    else:
        print("No images found matching all the specified criteria.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A standalone tool to search the image tag database.")
    parser.add_argument("query", type=str, help="Comma-separated tags. E.g., '1girl, rating: safe, char: gawr gura'")
    args = parser.parse_args()
    search_images(args.query)