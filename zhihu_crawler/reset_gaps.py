import sqlite3
import os

def reset_gaps(threshold=0.9):
    db_path = 'data/zhihu.db'
    if not os.path.exists(db_path):
        print("Database not found.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("\n" + "="*50)
    print("Zhihu Crawler - Smart Data Audit & Repair")
    print("="*50)

    # Find answers that are marked 'done' but have significant gaps
    # Logic: SELECT answers where collected_comments < theoretical_comments * threshold
    c.execute("""
        SELECT a.id, a.author_name, a.comment_count, 
               (SELECT COUNT(*) FROM comments WHERE answer_id = a.id) as actual_count
        FROM answers a
        WHERE a.status = 'done' AND a.comment_count > 0
    """)
    
    rows = c.fetchall()
    to_reset = []
    
    print(f"Audit: Checking {len(rows)} scanned answers...")
    
    for row in rows:
        theory = row['comment_count']
        actual = row['actual_count']
        
        # Zhihu API often misses some, but if gap is > 10% we retry
        if actual < theory * threshold:
            to_reset.append(row['id'])

    if not to_reset:
        print("Audit Result: No significant gaps found (all > 90% coverage).")
        conn.close()
        return

    print(f"Audit Result: Found {len(to_reset)} answers with data gaps.")
    print(f"Action: Clearing partial data and resetting status to 'pending'...")

    # Start a transaction
    try:
        # 1. Delete existing comments for these answers to avoid primary key conflicts on retry
        # (Though INSERT OR IGNORE handles it, cleaning is safer for a fresh start)
        placeholder = ','.join(['?'] * len(to_reset))
        c.execute(f"DELETE FROM comments WHERE answer_id IN ({placeholder})", to_reset)
        deleted_comments = c.rowcount
        
        # 2. Reset status to pending
        c.execute(f"UPDATE answers SET status = 'pending' WHERE id IN ({placeholder})", to_reset)
        reset_answers = c.rowcount
        
        conn.commit()
        print(f"Success: Reset {reset_answers} answers and cleared {deleted_comments} partial comments.")
        print("You can now restart the crawler to fill the gaps.")
    except Exception as e:
        conn.rollback()
        print(f"Error during repair: {e}")
    finally:
        conn.close()
    print("="*50 + "\n")

if __name__ == "__main__":
    # Threshold 0.9 means if we have < 90% of theoretical comments, we re-crawl.
    reset_gaps(threshold=0.9)
