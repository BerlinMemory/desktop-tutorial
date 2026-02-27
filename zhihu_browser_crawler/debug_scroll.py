"""
诊断评论区滚动容器 v3 
- 使用 playwright-stealth v2 API
- 写结果到文件避免编码问题
"""
import sys, yaml, asyncio, json

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from playwright.async_api import async_playwright

async def main():
    with open('../zhihu_crawler/config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    cookie_str = config.get('cookies', [''])[0]

    # stealth v2
    stealth_obj = None
    try:
        from playwright_stealth import Stealth
        stealth_obj = Stealth(
            navigator_webdriver=True,
            navigator_user_agent=True,
        )
    except ImportError:
        pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1280, 'height': 900}
        )
        if stealth_obj:
            await stealth_obj.apply_stealth_async(context)
        
        cookies = []
        for part in cookie_str.split(';'):
            part = part.strip()
            if '=' in part:
                k, v = part.split('=', 1)
                cookies.append({'name': k.strip(), 'value': v.strip(), 'domain': '.zhihu.com', 'path': '/'})
        await context.add_cookies(cookies)

        page = await context.new_page()

        results = []
        def log(msg):
            print(msg)
            results.append(msg)

        url = 'https://www.zhihu.com/question/44196985/answer/131529927'
        log('[1] Navigate')
        await page.goto(url, wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)

        # 点击评论
        log('[2] Click comment button')
        await page.evaluate(r'''() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const t = (btn.textContent || '').trim();
                if (/^\d+\s*条评论$/.test(t) || t === '添加评论' || /评论/.test(t)) {
                    btn.click(); return t;
                }
            }
        }''')
        await page.wait_for_timeout(3000)

        # 查看全部评论
        log('[3] Click "view all comments"')
        for i in range(5):
            await page.evaluate('() => window.scrollBy(0, 600)')
            await page.wait_for_timeout(1500)
            clicked = await page.evaluate(r'''() => {
                const allEls = document.querySelectorAll('div, button, a, span');
                for (const el of allEls) {
                    const text = (el.textContent || '').trim();
                    if (text === '点击查看全部评论' || text === '查看全部评论') {
                        el.scrollIntoView({behavior: 'smooth', block: 'center'});
                        el.click();
                        return text;
                    }
                }
                return null;
            }''')
            if clicked:
                log(f'  Clicked: "{clicked}"')
                await page.wait_for_timeout(3000)
                break

        # 分析滚动容器
        log('\n[4] Analyzing scroll containers')
        scroll_info = await page.evaluate(r'''() => {
            const results = [];
            const firstComment = document.querySelector('.CommentContent');
            if (!firstComment) return [JSON.stringify({error: 'No .CommentContent found'})];

            let el = firstComment;
            while (el) {
                const style = window.getComputedStyle(el);
                const overflowY = style.overflowY;
                const hasScroll = el.scrollHeight > el.clientHeight;

                if (overflowY === 'auto' || overflowY === 'scroll' || hasScroll) {
                    results.push(JSON.stringify({
                        tag: el.tagName,
                        id: (el.id || '').substring(0, 50),
                        cls: (el.className || '').substring(0, 120),
                        overflowY: overflowY,
                        scrollH: el.scrollHeight,
                        clientH: el.clientHeight,
                        scrollTop: el.scrollTop,
                        role: el.getAttribute('role') || ''
                    }));
                }
                el = el.parentElement;
            }
            return results;
        }''')
        
        log(f'  Scrollable ancestors: {len(scroll_info)}')
        for item_str in scroll_info:
            info = json.loads(item_str)
            log(f'    <{info.get("tag", "?")}> id="{info.get("id", "")}" class="{info.get("cls", "")}"')
            log(f'      overflow-y={info.get("overflowY")}, '
                  f'scrollHeight={info.get("scrollH")}, clientHeight={info.get("clientH")}, '
                  f'role={info.get("role", "")}')

        # 当前状态
        count_before = await page.evaluate('() => document.querySelectorAll(".CommentContent").length')
        data_id_before = await page.evaluate('() => document.querySelectorAll("[data-id]").length')
        log(f'\n[5] Current: {count_before} comments, {data_id_before} data-id elements')

        # 尝试滚动各容器
        for item_str in scroll_info:
            info = json.loads(item_str)
            cls = info.get('cls', '')
            tag = info.get('tag', '')
            if not cls or tag in ('HTML', 'BODY'):
                continue
            first_cls = cls.split()[0]
            if not first_cls:
                continue
            
            selector = f'.{first_cls}'
            log(f'\n  Testing scroll container: {selector}')
            
            for scroll_try in range(5):
                await page.evaluate(f'''() => {{
                    const el = document.querySelector('{selector}');
                    if (el) {{
                        el.scrollTop = el.scrollHeight;
                    }}
                }}''')
                await page.wait_for_timeout(2000)

            count_after = await page.evaluate('() => document.querySelectorAll(".CommentContent").length')
            data_id_after = await page.evaluate('() => document.querySelectorAll("[data-id]").length')
            log(f'    After scroll: {count_after} comments, {data_id_after} data-id')
            if count_after > count_before or data_id_after > data_id_before:
                log(f'    *** NEW CONTENT! comments +{count_after - count_before}, data-id +{data_id_after - data_id_before} ***')
                count_before = count_after
                data_id_before = data_id_after

        # 也试 window scroll
        log(f'\n[6] window.scrollTo bottom...')
        for i in range(5):
            await page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(2000)
        count_after = await page.evaluate('() => document.querySelectorAll(".CommentContent").length')
        data_id_after = await page.evaluate('() => document.querySelectorAll("[data-id]").length')
        log(f'  After: {count_after} comments, {data_id_after} data-id')

        # 写结果
        with open('scroll_diag_result.txt', 'w', encoding='utf-8') as f:
            f.write('\n'.join(results))
        log('\n[Done] Results saved to scroll_diag_result.txt')

        await page.wait_for_timeout(5000)
        await browser.close()

asyncio.run(main())
