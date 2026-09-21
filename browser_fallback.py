#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lanzou-direct-link / browser_fallback.py
========================================
**可选**兜底方案：当纯 `requests` 拿不到 `sign` 时（页面要靠 JS 才生成），
用 Playwright 无头浏览器把页面真正渲染一遍，再从渲染结果里取信号。

Optional fallback: when the pure-`requests` path cannot find a `sign` (because
the page generates it in JavaScript), render the page with a real headless
browser via Playwright and extract the signals from the rendered result.

为什么要这个 / Why this exists
-----------------------------
蓝奏云对分享页做过好几轮前端改动，某些页面把 `sign` 放在 JS 运行时变量里，
静态 HTML 里根本搜不到。原脚本里 28 种方法有一大半都是在用 Playwright
反复试 —— 这里把它收敛成一个干净的兜底模块。
Lanzou has reworked its share pages several times; some pages only produce
`sign` at runtime, so it never appears in the static HTML.

安装 / Install
--------------
    pip install playwright
    playwright install chromium      # 若用 --channel msedge/chrome 可跳过这步

用法 / Usage
------------
    from lanzou_direct import parse
    f = parse(url, allow_browser_fallback=True)     # 自动在需要时兜底

    # 或直接调用
    from browser_fallback import parse_with_browser
    f = parse_with_browser(url, pwd='1234')
"""

import random
import re
import time
from typing import Optional

__all__ = ['parse_with_browser', 'PlaywrightUnavailable', 'render_page']

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
window.chrome = { runtime: {} };
"""

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-first-run",
    "--disable-background-timer-throttling",
    "--disable-popup-blocking",
]


class PlaywrightUnavailable(RuntimeError):
    """没装 playwright，或系统里找不到任何可用的浏览器内核。"""


def _launch(p, headless: bool = True):
    """
    依次尝试：系统 Edge → 系统 Chrome → Playwright 自带 Chromium。
    这样大多数 Windows 用户不用额外 `playwright install` 就能用。
    """
    last = None
    for kwargs in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(headless=headless, args=_LAUNCH_ARGS, **kwargs)
        except Exception as e:                     # noqa: BLE001
            last = e
    raise PlaywrightUnavailable(
        "找不到可用的浏览器内核 / no usable browser engine. "
        "请先执行: pip install playwright && playwright install chromium  "
        "(最后错误 / last error: %s)" % last)


def render_page(url: str,
                *,
                pwd: str = '',
                timeout: int = 45,
                headless: bool = True,
                wait_seconds: float = 1.2,
                extra_wait_selector: Optional[str] = None):
    """
    用无头浏览器打开 `url`，返回一个 dict：
        {html, final_url, sign, signs, ves, fid, file_link, api}

    其中 ``file_link`` 是页面上 ``a[href*="/file/?"]`` 的地址（蓝奏云的中间页），
    ``sign`` 等是从页面 JS 运行时变量里抠出来的。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:                       # noqa: F841
        raise PlaywrightUnavailable(
            "未安装 playwright / playwright is not installed. "
            "请执行: pip install playwright") from None

    from lanzou_direct import USER_AGENTS, extract_ajax_path, extract_fid

    out = {"html": '', "final_url": url, "sign": '', "signs": '',
           "ves": '', "fid": '', "file_link": '', "api": ''}

    with sync_playwright() as p:
        browser = _launch(p, headless=headless)
        try:
            ctx = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": random.randint(1440, 1920),
                          "height": random.randint(900, 1080)},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = ctx.new_page()
            page.add_init_script(_STEALTH_JS)
            page.goto(url, wait_until="domcontentloaded",
                      timeout=timeout * 1000)

            # 有提取码就填进去并提交
            if pwd:
                for sel in ('input[name="pwd"]', 'input#pwd', 'input[type="password"]'):
                    try:
                        page.fill(sel, pwd, timeout=1500)
                        for btn in ('button:has-text("确认")', 'button:has-text("提交")',
                                    'input[type="submit"]', 'button[type="submit"]'):
                            try:
                                page.click(btn, timeout=1200)
                                break
                            except Exception:      # noqa: BLE001
                                continue
                        page.wait_for_load_state("domcontentloaded", timeout=8000)
                        break
                    except Exception:              # noqa: BLE001
                        continue

            if extra_wait_selector:
                try:
                    page.wait_for_selector(extra_wait_selector, timeout=8000)
                except Exception:                  # noqa: BLE001
                    pass
            time.sleep(wait_seconds)

            # 进 iframe（如果有），蓝奏云常把正文塞在 iframe 里
            frame = page
            try:
                el = page.query_selector('iframe[src]')
                if el:
                    fr = el.content_frame()
                    if fr:
                        frame = fr
            except Exception:                      # noqa: BLE001
                pass

            out["final_url"] = page.url
            out["html"] = frame.content()

            # 页面上直接的中间页链接
            try:
                out["file_link"] = frame.evaluate(
                    """() => {
                        const a = document.querySelector('a[href*="/file/?"]')
                               || document.querySelector('a[href*="/file/"]');
                        return a ? a.href : '';
                    }""") or ''
            except Exception:                      # noqa: BLE001
                pass

            # JS 运行时变量（静态 HTML 里搜不到的那些）
            try:
                vars_ = frame.evaluate(
                    """() => ({
                        sign:  (typeof sign  !== 'undefined' && sign)  || '',
                        signs: (typeof signs !== 'undefined' && signs) || '',
                        ves:   (typeof ves   !== 'undefined' && String(ves)) || '',
                        fid:   (typeof file  !== 'undefined' && String(file)) || ''
                    })""") or {}
                for k in ('sign', 'signs', 'ves', 'fid'):
                    v = vars_.get(k) or ''
                    out[k] = str(v)
            except Exception:                      # noqa: BLE001
                pass

            out["api"] = extract_ajax_path(out["html"]) or ''
            if not out["fid"]:
                out["fid"] = extract_fid(out["html"])
        finally:
            try:
                browser.close()
            except Exception:                      # noqa: BLE001
                pass

    return out


def parse_with_browser(share_url: str,
                       pwd: str = '',
                       *,
                       resolve: bool = False,
                       timeout: int = 45,
                       headless: bool = True):
    """
    用无头浏览器解析蓝奏云分享链接，返回 :class:`lanzou_direct.LanzouFile`。
    Resolve a Lanzou share link with a headless browser.

    流程：渲染页面 → 取回 sign / signs / ves / fid 与已渲染的 HTML →
    若拿到 sign，就复用主模块的 ajax 兑换逻辑；否则退回页面上的
    ``/file/?`` 中间页链接。
    """
    from lanzou_direct import (
        LanzouFile, ParseFailed, resolve_real_url, _base_of,
        _exchange_direct_url, extract_sign, extract_signs, extract_ves,
        extract_fid, extract_ajax_path,
    )
    import requests as _requests

    info = render_page(share_url, pwd=pwd, timeout=timeout, headless=headless)

    sign = info.get("sign") or extract_sign(info.get("html", ""))
    fid = info.get("fid") or extract_fid(info.get("html", ""))
    base = _base_of(info.get("final_url") or share_url)

    result = None
    if sign:
        sess = _requests.Session()
        try:
            out = _exchange_direct_url(
                sess, base, info.get("final_url") or share_url,
                sign=sign,
                signs=info.get("signs") or extract_signs(info.get("html", "")),
                ves=info.get("ves") or extract_ves(info.get("html", "")),
                fid=fid,
                pwd=pwd,
                timeout=timeout,
                ajax_path=info.get("api") or extract_ajax_path(info.get("html", "")),
            )
            result = LanzouFile(
                direct_url=out['direct_url'],
                middle_url=out['direct_url'],
                name=out['name'],
                size=out['size'],
                share_url=share_url,
                raw=out['raw'],
            )
        except Exception:                          # noqa: BLE001
            result = None

    if result is None:
        link = info.get("file_link") or ''
        if not link:
            raise ParseFailed('无头浏览器也没能拿到下载链接 / headless browser got no link')
        result = LanzouFile(
            direct_url=link,
            middle_url=link,
            name='',
            size='',
            share_url=share_url,
            raw={},
        )

    if resolve:
        real = resolve_real_url(result.direct_url, timeout=timeout)
        if real:
            result.real_url = real

    return result


if __name__ == '__main__':
    print(__doc__)
    print('本模块是可选兜底，正常用法请见 cli.py --browser')
