import sqlite3
import os

def generate_report():
    db_path = 'data/zhihu.db'
    if not os.path.exists(db_path):
        print("数据库文件未找到")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Query Top 15 questions by theoretical comment count
    query = """
    SELECT 
        q.title, 
        q.answer_count as theory_answers,
        (SELECT COUNT(*) FROM answers WHERE question_id = q.id) as actual_answers,
        SUM(a.comment_count) as theory_comments,
        (SELECT COUNT(*) FROM comments WHERE answer_id IN (SELECT id FROM answers WHERE question_id = q.id)) as actual_comments
    FROM questions q
    LEFT JOIN answers a ON q.id = a.question_id
    GROUP BY q.id
    ORDER BY theory_comments DESC
    LIMIT 15
    """
    
    c.execute(query)
    rows = c.fetchall()

    print("\n### Zhihu Data Distribution Report (Top 15)")
    print("\n| Question Title | Answers (Act/Theory) | Theory Comments | Actual Collected | Coverage |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    
    total_theory = 0
    total_actual = 0

    for row in rows:
        title = row['title'][:30] + "..." if len(row['title']) > 30 else row['title']
        # Clean title to avoid encoding errors for console
        title = title.encode('ascii', 'ignore').decode('ascii')
        ans_stat = f"{row['actual_answers']}/{row['theory_answers']}"
        theory = row['theory_comments'] or 0
        actual = row['actual_comments'] or 0
        coverage = (actual / theory * 100) if theory > 0 else 0
        
        print(f"| {title} | {ans_stat} | {theory:,} | {actual:,} | {coverage:.1f}% |")
        
        total_theory += theory
        total_actual += actual

    # Overall summary from the Top 15 results
    if total_theory > 0:
        print(f"\n[Latest Stats] Total Theoretical: {total_theory:,} | Actual: {total_actual:,} | Progress: {(total_actual/total_theory*100):.1f}%")
    
    print("\n> *Note: Repair crawl is running in background. Numbers update every second.*")

    conn.close()

if __name__ == "__main__":
    generate_report()
