import sqlite3
import os
import csv
import argparse
from datetime import datetime

def generate_report(output_csv=False):
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

    if output_csv:
        # 输出到CSV文件（完整中文支持）
        os.makedirs('data', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_path = f'data/report_top15_{timestamp}.csv'

        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow([
                '问题标题',
                '实际回答数',
                '理论回答数',
                '理论评论数',
                '实际评论数',
                '覆盖率(%)'
            ])

            total_theory = 0
            total_actual = 0

            for row in rows:
                title = row['title']
                theory_ans = row['theory_answers']
                actual_ans = row['actual_answers']
                theory = row['theory_comments'] or 0
                actual = row['actual_comments'] or 0
                coverage = (actual / theory * 100) if theory > 0 else 0

                writer.writerow([
                    title,
                    actual_ans,
                    theory_ans,
                    theory,
                    actual,
                    f"{coverage:.1f}"
                ])

                total_theory += theory
                total_actual += actual

            # 添加汇总行
            overall_coverage = (total_actual / total_theory * 100) if total_theory > 0 else 0
            writer.writerow([])
            writer.writerow([
                '总计 (Top 15)',
                '',
                '',
                total_theory,
                total_actual,
                f"{overall_coverage:.1f}"
            ])

        print(f"\n✅ 报告已生成：{csv_path}")
        print(f"   理论评论数：{total_theory:,}")
        print(f"   实际评论数：{total_actual:,}")
        print(f"   整体覆盖率：{overall_coverage:.1f}%")
        print(f"\n💡 提示：用 Excel 打开 {csv_path} 查看完整中文内容")
    else:
        # 控制台输出（ASCII安全，兼容Windows GBK）
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

        print("\n> *Tip: Use --csv to export full Chinese titles to CSV*")

    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='生成知乎数据分布报告（Top 15问题）')
    parser.add_argument('--csv', action='store_true',
                       help='导出为CSV文件（完整中文支持，可用Excel打开）')
    args = parser.parse_args()

    generate_report(output_csv=args.csv)
