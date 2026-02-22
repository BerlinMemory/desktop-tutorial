import sqlite3
import os

def audit_titles():
    db_path = 'data/zhihu.db'
    if not os.path.exists(db_path):
        print("Database not found")
        return

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Get all titles
    c.execute("SELECT id, title, keyword FROM questions")
    rows = c.fetchall()
    
    with open('audit_log.txt', 'w', encoding='utf-8') as f:
        f.write(f"Total Questions: {len(rows)}\n\n")
        f.write(f"{'ID':<15} | {'Keyword':<10} | {'Title'}\n")
        f.write("-" * 80 + "\n")
        
        invalid_count = 0
        for q_id, title, keyword in rows:
            f.write(f"{q_id:<15} | {keyword:<10} | {title}\n")
            if '穷养' not in title and '富养' not in title:
                invalid_count += 1
        
        f.write(f"\nInvalid Questions (missing keywords): {invalid_count}\n")

    conn.close()

if __name__ == "__main__":
    audit_titles()
