"""诊断：打开模态框后，检查模态框的完整 DOM 结构"""
import asyncio, yaml, sys
sys.stdout.reconfigure(encoding='utf-8')

async def main():
    from playwright.async_api import async_playwright
    try:
        from playwright_stealth import Stealth
        stealth_obj = Stealth(navigator_webdriver=True, navigator_user_agent=True,
            navigator_plugins=True, navigator_vendor=True, navigator_languages=True,
            webgl_vendor=True, chrome_runtime=False)
    except ImportError:
        stealth_obj = None

    with open('../zhihu_crawler/config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    cookie_str = next((c for c in config.get('cookies', []) if c and c != 'your_cookie_here'), '')

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    ctx = await browser.new_context(viewport={'width': 1280, 'height': 900},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    if stealth_obj:
        await stealth_obj.apply_stealth_async(ctx)
    if cookie_str:
        cookies = [{'name': p.split('=', 1)[0].strip(), 'value': p.split('=', 1)[1].strip(),
                     'domain': '.zhihu.com', 'path': '/'} for p in cookie_str.split(';') if '=' in p]
        await ctx.add_cookies(cookies)

    page = await ctx.new_page()
    url = 'https://www.zhihu.com/question/44196985/answer/131529927'

    # 预热
    await page.goto('https://www.zhihu.com', wait_until='domcontentloaded', timeout=15000)
    await page.wait_for_timeout(2000)

    # 导航
    print('[1] 导航')
    try:
        await page.goto(url, wait_until='networkidle', timeout=45000)
    except:
        await page.goto(url, wait_until='load', timeout=30000)
    await page.wait_for_timeout(3000)

    # 点评论
    print('[2] 点击评论')
    await page.evaluate(r'''() => {
        const btns = [...document.querySelectorAll('button')];
        const b = btns.find(b => b.className.includes('ContentItem-action') && b.textContent.includes('评论'));
        if (b) b.click();
    }''')
    await page.wait_for_timeout(3000)

    # 查看全部评论
    print('[3] 查看全部评论')
    for _ in range(5):
        await page.evaluate('() => window.scrollBy(0, 600)')
        await page.wait_for_timeout(1500)
        clicked = await page.evaluate(r'''() => {
            for (const el of document.querySelectorAll('div, button, a, span')) {
                const text = (el.textContent || '').trim();
                if (text === '点击查看全部评论' || text === '查看全部评论') {
                    el.click(); return text;
                }
            }
            return null;
        }''')
        if clicked:
            print(f'  已点击: "{clicked}"')
            break
    await page.wait_for_timeout(3000)

    # 点击第一个"查看全部 X 条回复"
    print('[4] 点击"查看全部 X 条回复"')
    await page.evaluate(r'''() => {
        const btns = [...document.querySelectorAll('button')];
        const target = btns.find(b => /查看全部.*条回复/.test(b.textContent));
        if (target) { target.scrollIntoView({block: 'center'}); target.click(); }
    }''')
    await page.wait_for_timeout(3000)

    # 分析模态框结构
    print('\n[5] 分析模态框 DOM 结构')
    modal_info = await page.evaluate(r'''() => {
        const results = {
            modalElements: [],
            closeButtons: [],
            overlayElements: [],
            allClassesWithModal: []
        };
        
        // 找所有包含 Modal/modal 的元素
        const allEls = document.querySelectorAll('*');
        for (const el of allEls) {
            const cls = el.className.toString();
            if (/modal/i.test(cls)) {
                results.allClassesWithModal.push({
                    tag: el.tagName,
                    class: cls.slice(0, 120),
                    id: el.id || '',
                    children: el.children.length,
                    visible: el.offsetParent !== null || el.style.display !== 'none'
                });
            }
        }
        
        // 找关闭按钮
        const closePatterns = ['close', 'Close', '关闭', '×', 'x'];
        for (const el of document.querySelectorAll('button, [role="button"], svg, [class*="close"], [class*="Close"]')) {
            const text = (el.textContent || '').trim();
            const cls = el.className.toString();
            const ariaLabel = el.getAttribute('aria-label') || '';
            if (closePatterns.some(p => text.includes(p) || cls.includes(p) || ariaLabel.includes(p))) {
                results.closeButtons.push({
                    tag: el.tagName,
                    text: text.slice(0, 30),
                    class: cls.slice(0, 80),
                    ariaLabel: ariaLabel,
                    parentClass: (el.parentElement?.className || '').toString().slice(0, 80)
                });
            }
        }
        
        // 找遮罩层
        for (const el of document.querySelectorAll('[class*="mask"], [class*="Mask"], [class*="overlay"], [class*="Overlay"]')) {
            results.overlayElements.push({
                tag: el.tagName,
                class: el.className.toString().slice(0, 100),
                visible: el.offsetParent !== null
            });
        }
        
        return results;
    }''')

    print(f'\n=== 含 "modal" 的元素 ({len(modal_info["allClassesWithModal"])}) ===')
    for m in modal_info['allClassesWithModal']:
        print(f'  <{m["tag"]}> class="{m["class"]}" children={m["children"]} visible={m["visible"]}')

    print(f'\n=== 关闭按钮 ({len(modal_info["closeButtons"])}) ===')
    for b in modal_info['closeButtons']:
        print(f'  <{b["tag"]}> text="{b["text"]}" class="{b["class"]}" aria="{b["ariaLabel"]}"')
        print(f'    parentClass="{b["parentClass"]}"')

    print(f'\n=== 遮罩层 ({len(modal_info["overlayElements"])}) ===')
    for o in modal_info['overlayElements']:
        print(f'  <{o["tag"]}> class="{o["class"]}" visible={o["visible"]}')

    # 尝试各种关闭方式并报告效果
    print('\n[6] 测试关闭方式...')

    # 测试 Escape
    print('  尝试 Escape...')
    await page.keyboard.press('Escape')
    await page.wait_for_timeout(1500)
    still = await page.evaluate('() => { const els = document.querySelectorAll("*"); for (const el of els) { if (/modal/i.test(el.className.toString()) && el.offsetParent !== null) return true; } return false; }')
    print(f'  Escape 后模态框仍在: {still}')

    # 如果还在，尝试 page.go_back
    if still:
        print('  尝试 history.back()...')
        await page.evaluate('() => history.back()')
        await page.wait_for_timeout(2000)
        still2 = await page.evaluate('() => { const els = document.querySelectorAll("*"); for (const el of els) { if (/modal/i.test(el.className.toString()) && el.offsetParent !== null) return true; } return false; }')
        print(f'  history.back() 后模态框仍在: {still2}')

    input('\n按 Enter 关闭...')
    await browser.close()
    await pw.stop()

asyncio.run(main())
