import sqlite3
import os

def final_audit():
    db_path = 'data/zhihu.db'
    if not os.path.exists(db_path):
        print("Database not found")
        return

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Correct Top 15 sorted by theoretical comments
    query = """
    SELECT q.id, q.title, SUM(a.comment_count) as total_theory 
    FROM questions q 
    JOIN answers a ON q.id = a.question_id 
    GROUP BY q.id 
    ORDER BY total_theory DESC 
    LIMIT 15
    """
    c.execute(query)
    rows = c.fetchall()
    
    print("\n--- ACTUAL TOP 15 FROM DATABASE ---")
    for i, row in enumerate(rows, 1):
        print(f"{i}. [{row[2]} comments] {row[1]}")
    print("----------------------------------\n")

    conn.close()

if __name__ == "__main__":
    final_audit()
