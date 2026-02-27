"""
DOM 结构检测 v5: 针对特定回答的评论区完整结构探索
点击指定回答的评论按钮（ContentItem-action），dump 完整评论组件树
"""
import asyncio
import sys
import yaml
import os

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def safe(text):
    if not text:
        return ''
    return text.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '').strip()


async def inspect_dom(config_path, db_path, answer_id):
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    cookies_list = config.get('cookies', [])
    cookie_str = ''
    for c in cookies_list:
        if c and c != 'your_cookie_here':
            cookie_str = c
            break

    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute('SELECT question_id, comment_count FROM answers WHERE id = ?', (answer_id,)).fetchone()
    conn.close()
    if not row:
        print(f"未找到 answer_id={answer_id}")
        return
    question_id = str(row[0])
    expected_comments = row[1]
    url = f'https://www.zhihu.com/question/{question_id}/answer/{answer_id}'
    print(f"URL: {url}")
    print(f"预期评论数: {expected_comments}")

    from playwright.async_api import async_playwright

    stealth_obj = None
    try:
        from playwright_stealth import Stealth
        stealth_obj = Stealth(navigator_webdriver=True)
        print("[OK] stealth v2 loaded")
    except ImportError:
        print("[WARN] no stealth")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,
        args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
    )
    ctx = await browser.new_context(
        viewport={'width': 1280, 'height': 900},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    )
    if stealth_obj:
        await stealth_obj.apply_stealth_async(ctx)

    if cookie_str:
        for part in cookie_str.split(';'):
            part = part.strip()
            if '=' in part:
                name, value = part.split('=', 1)
                await ctx.add_cookies([{
                    'name': name.strip(), 'value': value.strip(),
                    'domain': '.zhihu.com', 'path': '/'
                }])

    page = await ctx.new_page()
    print("加载页面...")
    try:
        await page.goto(url, wait_until='networkidle', timeout=60000)
    except Exception:
        try:
            await page.goto(url, wait_until='load', timeout=30000)
        except Exception:
            pass
    await page.wait_for_timeout(3000)

    title = await page.title()
    print(f"标题: {title}")

    # 找到目标回答的评论按钮（ContentItem-action 且评论数匹配）
    print(f"\n====== 点击目标回答的评论按钮 ======")
    click_info = await page.evaluate(r'''(expectedCount) => {
        const btns = [...document.querySelectorAll('button')];
        // 寻找 ContentItem-action 类的评论按钮
        const answerBtns = btns.filter(b => {
            const cls = b.className.toString();
            const text = (b.textContent||'').replace(/[\u200b\u200c\u200d\ufeff]/g, '').trim();
            return cls.includes('ContentItem-action') && text.includes('评论');
        });

        // 挑选评论数最接近预期的按钮
        let bestBtn = null;
        let bestDiff = Infinity;
        for (const btn of answerBtns) {
            const text = (btn.textContent||'').replace(/[\u200b\u200c\u200d\ufeff\u00a0]/g, '').trim();
            const match = text.match(/(\d+)/);
            if (match) {
                const count = parseInt(match[1]);
                const diff = Math.abs(count - expectedCount);
                if (diff < bestDiff) {
                    bestDiff = diff;
                    bestBtn = btn;
                }
            }
        }

        if (bestBtn) {
            bestBtn.click();
            return 'clicked: ' + bestBtn.textContent.replace(/[\u200b]/g,'').trim();
        }
        // 降级：点第一个
        if (answerBtns.length) {
            answerBtns[0].click();
            return 'fallback: ' + answerBtns[0].textContent.replace(/[\u200b]/g,'').trim();
        }
        return 'not_found';
    }''', expected_comments)
    print(f"  {click_info}")

    # 等评论区加载
    await page.wait_for_timeout(5000)

    # 全面 dump 评论区 DOM 树
    print(f"\n====== 评论区完整 DOM 结构 ======")
    dom_tree = await page.evaluate(r'''() => {
        // 找到评论面板（弹窗/侧边栏/内联） - 可能的容器
        function dumpTree(el, depth, maxDepth) {
            if (depth > maxDepth) return null;
            const cls = (el.className || '').toString();
            const text = (el.textContent || '').replace(/[\u200b\u200c\u200d\ufeff]/g, '').trim();
            const result = {
                tag: el.tagName,
                cls: cls.slice(0, 120),
                id: el.id || '',
                role: el.getAttribute('role') || '',
                childCount: el.children.length,
                textLen: text.length,
                textPreview: text.slice(0, 80),
                children: [],
            };
            for (let i = 0; i < Math.min(el.children.length, 10); i++) {
                const child = dumpTree(el.children[i], depth + 1, maxDepth);
                if (child) result.children.push(child);
            }
            return result;
        }

        // 查找 Comment 相关的顶层容器
        const all = [...document.querySelectorAll('*')];
        const commentContainers = all.filter(el => {
            const cls = (el.className||'').toString();
            // 寻找最外层的评论面板容器
            return (cls.includes('Comments') || cls.includes('CommentList') ||
                    cls.includes('css-') && el.children.length > 5) &&
                   el.getBoundingClientRect().width > 300 &&
                   el.getBoundingClientRect().height > 200;
        });

        // 也查找 Modal/Dialog/Drawer
        const overlays = document.querySelectorAll('[role="dialog"], .Modal, .Drawer, [class*="drawer"]');

        const results = [];

        // 检查弹窗
        for (const ov of overlays) {
            results.push({
                type: 'overlay',
                tree: dumpTree(ov, 0, 3),
            });
        }

        // 检查评论容器（按子元素数排序）
        commentContainers.sort((a, b) => b.children.length - a.children.length);
        for (let i = 0; i < Math.min(commentContainers.length, 3); i++) {
            results.push({
                type: 'comment_container',
                tree: dumpTree(commentContainers[i], 0, 4),
            });
        }

        return results;
    }''')

    def print_tree(tree, indent=0):
        if not tree:
            return
        prefix = '  ' * indent
        cls_short = safe(tree.get('cls', ''))[:80]
        text_p = safe(tree.get('textPreview', ''))[:50]
        role = tree.get('role', '')
        role_str = f" role='{role}'" if role else ''
        print(f"{prefix}<{tree['tag']}>{role_str} cls='{cls_short}' "
              f"children={tree['childCount']} textLen={tree['textLen']}")
        if text_p and tree['childCount'] == 0:
            print(f"{prefix}  text: '{text_p}'")
        for child in tree.get('children', []):
            print_tree(child, indent + 1)

    for item in dom_tree:
        print(f"\n--- {item['type']} ---")
        print_tree(item['tree'])

    # 也 dump 第一条评论的完整 HTML
    print(f"\n====== 第一条评论 outerHTML ======")
    first_comment_html = await page.evaluate(r'''() => {
        // 找到 CommentContent 的父元素（应该是单条评论）
        const contents = document.querySelectorAll('.CommentContent, [class*="CommentItem"]');
        if (contents.length === 0) return 'no CommentContent found';
        // 往上找一两层到评论项
        let item = contents[0].parentElement;
        if (item && item.parentElement) {
            const parentCls = item.parentElement.className.toString().toLowerCase();
            // 如果父级是列表容器，则 item 就是评论项
        }
        return item ? item.outerHTML.slice(0, 3000) : contents[0].outerHTML.slice(0, 3000);
    }''')
    print(first_comment_html[:3000])

    print("\n\n按 Enter 关闭...")
    input()
    await page.close()
    await ctx.close()
    await browser.close()
    await pw.stop()


if __name__ == '__main__':
    answer_id = sys.argv[1] if len(sys.argv) > 1 else '372763616'
    config_path = os.path.abspath('../zhihu_crawler/config.yaml')
    db_path = os.path.abspath('../zhihu_crawler/data/zhihu.db')
    asyncio.run(inspect_dom(config_path, db_path, answer_id))
