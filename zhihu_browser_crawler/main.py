"""
知乎浏览器评论补爬工具
主入口：从数据库找评论缺口，用 Playwright 浏览器补爬
"""
import os
import sys
import asyncio
import argparse
import yaml

# Windows 控制台 UTF-8 兼容
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from gap_finder import GapFinder
from browser_crawler import BrowserCrawler

import sqlite3


def load_config(config_path: str) -> dict:
    """加载配置文件"""
    if not os.path.exists(config_path):
        print(f"错误：配置文件不存在: {config_path}")
        print("请指定配置文件路径，例如:")
        print("  python main.py --config ../zhihu_crawler_enhanced/config.yaml")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def show_stats(db_path: str):
    """显示缺口统计"""
    gf = GapFinder(db_path)
    summary = gf.get_summary()

    print("\n" + "=" * 60)
    print("评论缺口统计")
    print("=" * 60)
    print(f"  总回答数:    {summary['total_answers']}")
    print(f"  已采集评论:  {summary['total_comments_collected']}")
    print(f"  期望评论:    {summary['total_comments_expected']}")
    print(f"  缺失评论:    {summary['missing']}")

    print("\n  缺口分布:")
    print(f"  {'范围':<10} {'回答数':>8} {'缺失评论':>10}")
    for d in summary['distribution']:
        print(f"  {d['range']:<10} {d['answer_count']:>8} {d['total_gap']:>10}")

    gf.close()


def show_gaps(db_path: str, min_gap: int, limit: int = 30):
    """显示具体哪些回答有缺口"""
    gf = GapFinder(db_path)
    gaps = gf.find_gaps(min_gap=min_gap, limit=limit)

    print(f"\n缺口 > {min_gap} 的回答 (最多显示 {limit} 条):")
    print(f"{'Answer ID':<15} {'Question ID':<15} {'期望':>8} {'实际':>8} {'缺口':>8}")
    for g in gaps:
        print(f"{g['answer_id']:<15} {g['question_id']:<15} "
              f"{g['expected']:>8} {g['actual']:>8} {g['gap']:>8}")

    print(f"\n共 {len(gaps)} 条")
    gf.close()


def show_progress(db_path: str):
    """显示断点续爬进度"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('SELECT 1 FROM browser_crawl_progress LIMIT 1')
    except sqlite3.OperationalError:
        print("\n还未开始过浏览器爬取，无进度记录")
        conn.close()
        return

    rows = conn.execute('''
        SELECT status, COUNT(*), SUM(comments_found)
        FROM browser_crawl_progress GROUP BY status
    ''').fetchall()
    conn.close()

    print("\n" + "=" * 60)
    print("浏览器爬取进度")
    print("=" * 60)
    for status, count, comments in rows:
        comments = comments or 0
        print(f"  {status:<10} {count:>5} 个回答, {comments:>8} 条评论")


def reset_progress(db_path: str):
    """清除断点续爬进度，强制重新爬取"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('DELETE FROM browser_crawl_progress')
        conn.commit()
        count = conn.total_changes
        print(f"\n[已重置] 清除了 {count} 条进度记录，下次爬取将从头开始")
    except sqlite3.OperationalError:
        print("\n无进度记录可清除")
    conn.close()


async def run_crawl(config: dict, db_path: str, min_gap: int,
                    max_answers: int = None):
    """执行爬取"""
    # 查找缺口
    gf = GapFinder(db_path)
    gaps = gf.find_gaps(min_gap=min_gap)
    gf.close()

    if not gaps:
        print(f"没有缺口 > {min_gap} 的回答")
        return

    print(f"找到 {len(gaps)} 个缺口 > {min_gap} 的回答")

    # 创建爬虫
    config['database_path'] = db_path
    crawler = BrowserCrawler(config)

    try:
        await crawler.setup()
        await crawler.run(gaps, max_answers=max_answers)
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await crawler.close()


async def run_discover(config: dict, db_path: str, answer_id: str):
    """运行选择器发现模式"""
    # 查找 answer 对应的 question_id
    gf = GapFinder(db_path)
    gap = gf.find_gap_for_answer(answer_id)
    gf.close()

    if not gap:
        print(f"错误：数据库中未找到 Answer ID: {answer_id}")
        return

    question_id = str(gap['question_id'])
    print(f"Answer {answer_id} → Question {question_id}")
    print(f"评论: 期望 {gap['expected']}, 实际 {gap['actual']}, 缺口 {gap['gap']}")

    config['database_path'] = db_path
    config['headless'] = False  # 发现模式强制 headed
    crawler = BrowserCrawler(config)

    try:
        await crawler.setup()
        await crawler.discover(answer_id, question_id)
        # 暂停让用户观察页面
        print("\n按 Enter 关闭浏览器...")
        await asyncio.get_event_loop().run_in_executor(None, input)
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await crawler.close()


async def run_single(config: dict, db_path: str, answer_id: str):
    """爬取单个回答的评论"""
    gf = GapFinder(db_path)
    gap = gf.find_gap_for_answer(answer_id)
    gf.close()

    if not gap:
        print(f"错误：数据库中未找到 Answer ID: {answer_id}")
        return

    question_id = str(gap['question_id'])
    print(f"Answer {answer_id} → Question {question_id}")
    print(f"评论: 期望 {gap['expected']}, 实际 {gap['actual']}, 缺口 {gap['gap']}")

    config['database_path'] = db_path
    crawler = BrowserCrawler(config)

    try:
        await crawler.setup()
        new_count = await crawler.crawl_answer_comments(answer_id, question_id)
        print(f"\n爬取完成，新增 {new_count} 条评论")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await crawler.close()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='知乎浏览器评论补爬工具 - 用 Playwright 补爬 API 遗漏的评论',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py --stats                        # 查看缺口统计
  python main.py --list --min-gap 100            # 列出缺口>100的回答
  python main.py --min-gap 50                    # 补爬缺口>50的评论
  python main.py --min-gap 100 --max 10          # 先跑 10 个试试
  python main.py --answer-id 2835057077          # 爬取指定回答的评论
  python main.py --discover --answer-id 245054626  # 选择器发现模式
  python main.py --show-progress                 # 查看断点续爬进度
  python main.py --reset-progress                # 清除进度，强制重新爬
  python main.py --min-gap 50 --headless         # headless 模式
        """
    )

    parser.add_argument('--config', '-c',
                        default='../zhihu_crawler_enhanced/config.yaml',
                        help='配置文件路径 (默认: ../zhihu_crawler_enhanced/config.yaml)')
    parser.add_argument('--db',
                        default='../zhihu_crawler/data/zhihu.db',
                        help='数据库路径 (默认: ../zhihu_crawler/data/zhihu.db)')

    # 模式选择
    parser.add_argument('--stats', action='store_true',
                        help='显示缺口统计信息')
    parser.add_argument('--list', action='store_true',
                        help='列出有缺口的回答')
    parser.add_argument('--discover', action='store_true',
                        help='选择器发现模式（调试用）')

    # 爬取参数
    parser.add_argument('--min-gap', type=int, default=50,
                        help='最小评论缺口阈值 (默认: 50)')
    parser.add_argument('--max', type=int, default=None,
                        help='最多处理多少个回答')
    parser.add_argument('--answer-id', type=str, default=None,
                        help='指定爬取某个回答的评论')

    # 浏览器选项
    parser.add_argument('--headless', action='store_true',
                        help='使用无头模式（不弹出浏览器窗口）')
    parser.add_argument('--delay', type=int, default=15,
                        help='页面间最小延迟秒数 (默认: 15，范围为 delay ~ delay+10)')

    # 断点续爬
    parser.add_argument('--show-progress', action='store_true',
                        help='显示浏览器爬取进度')
    parser.add_argument('--reset-progress', action='store_true',
                        help='清除断点续爬进度，强制重新爬取所有回答')

    args = parser.parse_args()

    # 解析数据库路径（支持相对路径）
    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print(f"错误：数据库文件不存在: {db_path}")
        print("请指定正确的数据库路径")
        return

    # 纯查看模式
    if args.stats:
        show_stats(db_path)
        return

    if args.list:
        show_gaps(db_path, args.min_gap)
        return

    if args.show_progress:
        show_progress(db_path)
        return

    if args.reset_progress:
        reset_progress(db_path)
        return

    # 需要加载配置的模式
    config_path = os.path.abspath(args.config)
    config = load_config(config_path)
    config['headless'] = args.headless
    config['delay_range'] = [args.delay, args.delay + 10]

    # 发现模式
    if args.discover:
        if not args.answer_id:
            print("错误：--discover 模式需要指定 --answer-id")
            return
        asyncio.run(run_discover(config, db_path, args.answer_id))
        return

    # 单回答模式
    if args.answer_id:
        asyncio.run(run_single(config, db_path, args.answer_id))
        return

    # 批量补爬模式
    asyncio.run(run_crawl(config, db_path, args.min_gap, args.max))


if __name__ == '__main__':
    main()
