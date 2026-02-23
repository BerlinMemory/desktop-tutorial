import sqlite3
import os
import shutil
from datetime import datetime

def backup_database(db_path):
    """备份数据库文件"""
    if not os.path.exists(db_path):
        return None
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = db_path.replace('.db', f'_backup_{timestamp}.db')
    shutil.copy2(db_path, backup_path)
    return backup_path

def reset_gaps(threshold=0.9):
    db_path = 'data/zhihu.db'
    if not os.path.exists(db_path):
        print("Database not found.")
        return

    print("\n" + "="*50)
    print("Zhihu Crawler - Smart Data Audit & Repair")
    print("="*50)

    # 1. 自动备份数据库
    print("Step 1: Backing up database...")
    backup_path = backup_database(db_path)
    if backup_path:
        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        print(f"  Backup created: {backup_path} ({size_mb:.1f} MB)")
    else:
        print("  Warning: Backup failed, proceeding anyway...")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 2. 查找有缺口的回答
    c.execute("""
        SELECT a.id, a.author_name, a.comment_count, 
               (SELECT COUNT(*) FROM comments WHERE answer_id = a.id) as actual_count
        FROM answers a
        WHERE a.status = 'done' AND a.comment_count > 0
    """)
    
    rows = c.fetchall()
    to_reset = []
    
    print(f"\nStep 2: Auditing {len(rows)} scanned answers...")
    
    for row in rows:
        theory = row['comment_count']
        actual = row['actual_count']
        
        if actual < theory * threshold:
            to_reset.append(row['id'])

    if not to_reset:
        print("Audit Result: No significant gaps found (all > 90% coverage).")
        conn.close()
        return

    print(f"Audit Result: Found {len(to_reset)} answers with data gaps.")

    # 3. 只重置状态，不删除旧评论
    #    因为数据库用 INSERT OR IGNORE，旧评论会被自动跳过
    #    双排序策略的第二轮会补充新评论
    print(f"\nStep 3: Resetting status to 'pending' (keeping existing comments)...")

    try:
        placeholder = ','.join(['?'] * len(to_reset))
        c.execute(f"UPDATE answers SET status = 'pending' WHERE id IN ({placeholder})", to_reset)
        reset_answers = c.rowcount
        
        conn.commit()
        print(f"  Reset {reset_answers} answers to 'pending'.")
        print(f"  Existing comments preserved (INSERT OR IGNORE handles dedup).")
        print("\nYou can now restart the crawler to fill the gaps.")
    except Exception as e:
        conn.rollback()
        print(f"Error during repair: {e}")
    finally:
        conn.close()
    print("="*50 + "\n")

if __name__ == "__main__":
    # Threshold 0.9 means if we have < 90% of theoretical comments, we re-crawl.
    reset_gaps(threshold=0.9)
