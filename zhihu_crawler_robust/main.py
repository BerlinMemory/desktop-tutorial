"""
知乎爬虫主程序
程序运行入口
"""
import os
import sys
import argparse
import yaml
from crawler import ZhihuCrawler
from database import Database
from export import DataExporter


def load_config(config_path: str = "config.yaml") -> dict:
    """
    加载配置文件
    :param config_path: 配置文件路径
    :return: 配置字典
    """
    if not os.path.exists(config_path):
        print(f"错误：配置文件不存在: {config_path}")
        print("请先创建配置文件 config.yaml")
        sys.exit(1)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except Exception as e:
        print(f"错误：配置文件解析失败: {e}")
        sys.exit(1)


def validate_config(config: dict) -> bool:
    """
    验证配置文件
    :param config: 配置字典
    :return: 验证是否通过
    """
    # 检查 Cookie（支持单个或多个）
    cookies = config.get('cookies', [])
    single_cookie = config.get('cookie', '')
    has_cookies = cookies and any(c and c != 'your_cookie_here' for c in cookies)
    has_single = single_cookie and single_cookie != 'your_cookie_here'

    if not has_cookies and not has_single:
        print("错误：配置文件中未设置 Cookie")
        print("请在配置文件中填入知乎登录后的 Cookie")
        print("支持单个 cookie 或多个 cookies 列表")
        return False

    if not config.get('keywords'):
        print("错误：配置文件中未设置搜索关键词")
        print("请在配置文件中添加至少一个搜索关键词")
        return False

    return True


def show_stats(db: Database):
    """
    显示数据库统计信息
    :param db: 数据库实例
    """
    stats = db.get_overall_stats()
    print("\n" + "="*60)
    print("数据库统计信息")
    print("="*60)

    # 问题统计
    q_stats = stats.get('questions', {})
    print(f"\n问题:")
    print(f"  总计: {q_stats.get('total', 0)}")
    print(f"  待处理: {q_stats.get('pending', 0)}")
    print(f"  已完成: {q_stats.get('done', 0)}")
    print(f"  失败: {q_stats.get('failed', 0)}")

    # 回答统计
    a_stats = stats.get('answers', {})
    print(f"\n回答:")
    print(f"  总计: {a_stats.get('total', 0)}")
    print(f"  待处理: {a_stats.get('pending', 0)}")
    print(f"  已完成: {a_stats.get('done', 0)}")
    print(f"  失败: {a_stats.get('failed', 0)}")

    # 评论统计
    c_stats = stats.get('comments', {})
    print(f"\n评论:")
    print(f"  总计: {c_stats.get('total', 0)}")
    print(f"  主评论: {c_stats.get('root_comments', 0)}")
    print(f"  子评论: {c_stats.get('child_comments', 0)}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='知乎爬虫 - 按关键词搜索并爬取问题、回答、评论',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py                    # 运行完整爬取流程
  python main.py --preview          # 预览搜索结果（只看数量，不爬取）
  python main.py --retry-failed     # 重试失败项
  python main.py --stats            # 查看统计信息
  python main.py --export           # 导出数据到 CSV
  python main.py --config custom.yaml  # 使用自定义配置文件
        """
    )

    parser.add_argument('--config', '-c', default='config.yaml',
                       help='配置文件路径 (默认: config.yaml)')
    parser.add_argument('--retry-failed', action='store_true',
                       help='重置失败项为待处理状态并重试')
    parser.add_argument('--stats', action='store_true',
                       help='仅显示统计信息')
    parser.add_argument('--export', action='store_true',
                       help='导出数据到 CSV')
    parser.add_argument('--preview', action='store_true',
                       help='预览模式：只搜索并显示问题/回答数量，不爬取内容')
    parser.add_argument('--search-only', action='store_true',
                       help='仅执行搜索阶段')
    parser.add_argument('--answers-only', action='store_true',
                       help='仅执行回答爬取阶段')
    parser.add_argument('--comments-only', action='store_true',
                       help='仅执行评论爬取阶段')

    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    # 获取数据库路径（从配置或使用默认值）
    db_path = config.get('database_path', 'data/zhihu.db')
    config['database_path'] = db_path

    # 仅查看统计信息
    if args.stats:
        if not os.path.exists(db_path):
            print(f"数据库文件不存在: {db_path}")
            print("请先运行爬虫")
            return
        with Database(db_path) as db:
            show_stats(db)
        return

    # 仅导出数据
    if args.export:
        if not os.path.exists(db_path):
            print(f"数据库文件不存在: {db_path}")
            print("请先运行爬虫")
            return
        output_dir = config.get('output', {}).get('directory', 'data/exports')
        with Database(db_path) as db:
            exporter = DataExporter(db, output_dir)
            exporter.export_all()
        return

    # 验证配置
    if not validate_config(config):
        return

    # 重试失败项
    if args.retry_failed:
        with Database(db_path) as db:
            db.reset_failed_to_pending()

    # 创建爬虫实例
    crawler = ZhihuCrawler(config)

    try:
        # 根据参数选择执行模式
        if args.preview:
            print("\n运行模式: 预览搜索结果")
            keywords = config.get('keywords', [])
            crawler.preview_search(keywords)

        elif args.search_only:
            print("\n运行模式: 仅搜索")
            keywords = config.get('keywords', [])
            crawler.crawl_search(keywords)

        elif args.answers_only:
            print("\n运行模式: 仅爬取回答")
            crawler.crawl_answers()

        elif args.comments_only:
            print("\n运行模式: 仅爬取评论")
            crawler.crawl_comments()

        else:
            # 完整流程
            print("\n运行模式: 完整爬取")
            # 显示 Cookie 信息
            cookie_count = len(crawler.cookies)
            if cookie_count > 1:
                print(f"Cookie 数量: {cookie_count}（并行模式）")
            else:
                print(f"Cookie 数量: 1（单线程模式）")
            crawler.run_full_crawl()

        # 显示统计信息
        show_stats(crawler.db)

        # 询问是否导出
        print("\n" + "="*60)
        print("爬取完成！")
        print("="*60)
        print("\n提示：使用以下命令导出数据到 CSV:")
        print("  python main.py --export")
        print("  或者")
        print("  python export.py")

    except KeyboardInterrupt:
        print("\n\n用户中断，程序退出")
        print("提示：下次运行会自动从断点继续")

    except Exception as e:
        print(f"\n发生错误: {e}")
        import traceback
        traceback.print_exc()

    finally:
        crawler.close()


if __name__ == "__main__":
    main()
