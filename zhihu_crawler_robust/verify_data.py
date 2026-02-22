import sqlite3
import pandas as pd

def verify():
    conn = sqlite3.connect('data/zhihu.db')
    
    print("\n" + "="*60)
    print("Zhihu Crawler Data Integrity Audit")
    print("="*60)
    
    # 1. Answer Verification
    print("\n[1/2] Verifying Answer Counts...")
    query_a = """
    SELECT 
        q.id as q_id, 
        q.title, 
        q.answer_count as theory_count,
        COUNT(a.id) as actual_count
    FROM questions q
    LEFT JOIN answers a ON q.id = a.question_id
    GROUP BY q.id
    """
    df_a = pd.read_sql_query(query_a, conn)
    df_a['gap'] = df_a['theory_count'] - df_a['actual_count']
    df_a['coverage_pct'] = (df_a['actual_count'] / df_a['theory_count'] * 100).fillna(100)
    
    total_theory_a = df_a['theory_count'].sum()
    total_actual_a = df_a['actual_count'].sum()
    avg_coverage_a = df_a['coverage_pct'].mean()
    
    print(f"Total Theoretical Answers: {total_theory_a}")
    print(f"Total Actual Answers:      {total_actual_a}")
    print(f"Overall Coverage:          {avg_coverage_a:.2f}%")
    
    significant_gaps_a = df_a[df_a['coverage_pct'] < 80].sort_values('gap', ascending=False)
    if not significant_gaps_a.empty:
        print(f"\nSignificant Answer Gaps (<80% coverage): {len(significant_gaps_a)} questions")
        print(significant_gaps_a[['q_id', 'theory_count', 'actual_count', 'coverage_pct']].head(10))
    else:
        print("\nNo significant gaps in answer collection.")

    # 2. Comment Verification
    print("\n[2/2] Verifying Comment Counts...")
    query_c = """
    SELECT 
        a.id as a_id,
        a.comment_count as theory_count,
        COUNT(c.id) as actual_count
    FROM answers a
    LEFT JOIN comments c ON a.id = c.answer_id
    GROUP BY a.id
    """
    df_c = pd.read_sql_query(query_c, conn)
    df_c['gap'] = df_c['theory_count'] - df_c['actual_count']
    df_c['coverage_pct'] = (df_c['actual_count'] / df_c['theory_count'] * 100).fillna(100)
    
    total_theory_c = df_c['theory_count'].sum()
    total_actual_c = df_c['actual_count'].sum()
    # Handle infinite/zero coverage
    df_c_with_comments = df_c[df_c['theory_count'] > 0]
    avg_coverage_c = df_c_with_comments['coverage_pct'].mean() if not df_c_with_comments.empty else 100
    
    print(f"Total Theoretical Comments (Cache): {total_theory_c}")
    print(f"Total Actual Comments:              {total_actual_c}")
    print(f"Average Comment Coverage:           {avg_coverage_c:.2f}%")
    
    significant_gaps_c = df_c[df_c['coverage_pct'] < 70].sort_values('gap', ascending=False)
    if not significant_gaps_c.empty:
        print(f"\nSignificant Comment Gaps (<70% coverage): {len(significant_gaps_c)} answers")
        # Just show top 5 gaps
        print(significant_gaps_c[['a_id', 'theory_count', 'actual_count', 'coverage_pct']].head(5))

    print("\n" + "="*60)
    print("Verification Complete")
    print("="*60)
    conn.close()

if __name__ == "__main__":
    verify()
