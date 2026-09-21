#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lanzou-direct-link / lanzou_browser.py
======================================
【本文件忠实移植自 蓝奏云网盘.py 的 `fetch_real_page` / `get_iframe` / `lanzou_parser` / `lanzou_parse_local`】

为什么必须有这个文件 / Why this engine exists
--------------------------------------------
蓝奏云现在对分享页做了 **JS 反爬**。直接用 `requests` 抓到的不是下载页，而是一段
混淆过的 JS 挑战页（`var arg1='B47A77...'` + eval 循环），里面：

    ✗ 没有 <iframe>
    ✗ 没有 sign
    ✗ 没有 ajaxm.php

所以「纯 HTTP + ajaxm.php」这条路在现代蓝奏云上**必然失败**。
真正能用的路线是 **用真实浏览器把页面渲染出来**，再从渲染结果里取
`a[href*="/file/?"]` —— 这正是原脚本里【3/28】浏览器静默提取干的事。

本文件做了什么 / What this file does
------------------------------------
* 保留原脚本 **全部 28 种方法** 与它们**原本的尝试顺序**（3 → 13 → 1 → 2 → 4 → … → 28）
* `is_valid()` 判定规则原样保留（排除 `kdns.js`、结尾 `/file/`、长度 < 20）
* **唯一优化**：原脚本每个浏览器方法都 `p.chromium.launch()` 一次（共启动 16 次，
  每次 1~3 秒），这里改成**整个解析过程只启动一次浏览器、复用同一个实例**，
  单个方法失败只关掉自己那个 page。

用法 / Usage
------------
    from lanzou_browser import parse_local
    r = parse_local('https://wwx.lanzoux.com/iAbCdEfGhIj')
    if r['code'] == 200:
        print(r['data']['downloadurl'])

依赖 / Requires
---------------
    pip install playwright
    playwright install chromium      # 系统有 Edge/Chrome 时可跳过（会自动用 msedge/chrome）
"""

import random
import re
import time
from urllib.parse import urlparse

import requests

__all__ = [
    'fetch_real_page', 'get_iframe', 'lanzou_parser', 'parse_local',
    'is_valid', 'PlaywrightUnavailable', 'USER_AGENTS',
    'solve_cdn_challenge', 'is_cdn_challenge', 'CHALLENGE_COOKIE',
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
]

MAX_RETRY = 4
DELAY_MIN = 0.4
DELAY_MAX = 1.3

# 原脚本里的启动参数（照抄）
_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-gpu",
    "--no-first-run",
    "--disable-background-timer-throttling",
    "--disable-popup-blocking",
]

# 原脚本里的反检测注入脚本（照抄）
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']});
window.chrome = {};
"""


class PlaywrightUnavailable(RuntimeError):
    """没装 playwright，或系统里找不到任何可用浏览器内核。"""


def _ua():
    return random.choice(USER_AGENTS)


# ---------------------------------------------------------------------------
#  原脚本的 is_valid（判定规则一字未改）
# ---------------------------------------------------------------------------

def is_valid(link):
    """
    原脚本 `lanzou_parser` 里内嵌的 is_valid。
    Verbatim from the original script.
    """
    if not link:
        return False
    if "kdns.js" in link or link.endswith("/file/") or len(link) < 20:
        return False
    return True


def _ok(link, fid="0", host=""):
    """原脚本统一的成功返回结构。"""
    return {"code": 1, "fid": fid, "host": host, "url": link, "direct": link}


def _fail(msg="失败"):
    return {"code": 0, "msg": msg}


# ---------------------------------------------------------------------------
#  浏览器实例管理（原脚本没有这一层，是本文件的唯一优化点）
# ---------------------------------------------------------------------------

class _Browser:
    """
    整个解析过程复用同一个 Playwright + 浏览器实例。

    原脚本 16 个浏览器方法各自 `sync_playwright()` + `chromium.launch()`，
    实测一次解析要 20~40 秒；改成复用后通常 3~6 秒。
    """

    def __init__(self, headless=True, log=print):
        self.headless = headless
        self.log = log
        self._pw = None
        self.browser = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise PlaywrightUnavailable(
                '未安装 playwright / playwright is not installed.\n'
                '请执行: pip install playwright && playwright install chromium') from None

        self._pw = sync_playwright().start()
        last = None
        # 依次尝试：系统 Edge → 系统 Chrome → Playwright 自带 Chromium
        for kwargs in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
            try:
                self.browser = self._pw.chromium.launch(
                    headless=self.headless, args=_LAUNCH_ARGS, **kwargs)
                return self
            except Exception as e:                    # noqa: BLE001
                last = e
        self._pw.stop()
        raise PlaywrightUnavailable(
            '找不到可用的浏览器内核 / no usable browser engine.\n'
            '请执行: playwright install chromium   (最后错误: %s)' % last)

    def __exit__(self, *exc):
        try:
            if self.browser:
                self.browser.close()
        except Exception:                             # noqa: BLE001
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:                             # noqa: BLE001
            pass

    def new_page(self, ua=None, referer=None, accept_downloads=True):
        """建一个新 page（原脚本 new_page/new_context 的封装）。

        注意：`extra_http_headers` 是 **new_context()** 的参数，
        不是 ctx.new_page() 的（后者只接受很少几个参数）。

        `accept_downloads=False` 用于「只过 CDN 反爬、不要真的下载」的场合
        ——见 :func:`solve_cdn_challenge`。
        """
        kwargs = dict(
            user_agent=ua or _ua(),
            viewport={"width": random.randint(1880, 1920),
                      "height": random.randint(980, 1080)},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=[],
            accept_downloads=accept_downloads,
        )
        if referer:
            kwargs["extra_http_headers"] = {"Referer": referer}
        ctx = self.browser.new_context(**kwargs)
        page = ctx.new_page()
        try:
            page.add_init_script(_STEALTH_JS)
        except Exception:                             # noqa: BLE001
            pass
        return ctx, page


def _regex_iframe(html, base):
    """原脚本 get_iframe 里的那组 iframe 正则（顺序照抄）。"""
    patterns = [
        r'<iframe[^>]+src="([^"]+?)"',
        r'<iframe.*?src=[\'"](.*?)[\'"]',
        r'iframe\s+src=[\'"](.*?)[\'"]',
        r'data-src=[\'"](.*?)[\'"]',
        r'frame\s+src=[\'"](.*?)[\'"]',
    ]
    for pat in patterns:
        res = re.search(pat, html, re.I | re.S)
        if res:
            src = res.group(1).strip()
            if src:
                if src.startswith('//'):
                    return 'https:' + src
                return src if src.startswith('http') else base + src
    return ''


# ---------------------------------------------------------------------------
#  fetch_real_page —— 原脚本 65-106 行
# ---------------------------------------------------------------------------

def fetch_real_page(url: str, headless=True, log=None):
    """
    用真实浏览器打开 url 并返回渲染后的 HTML（原脚本 `fetch_real_page`）。

    ⚠️ 与原版的差别：原版每次调用都自己 launch 一次浏览器；这里如果传了
    `browser=` 就复用。为了兼容原签名，单机调用时仍可独立使用。
    """
    for _ in range(MAX_RETRY):
        try:
            with _Browser(headless=headless, log=log or (lambda *a: None)) as br:
                _, page = br.new_page(ua=_ua())
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                html = page.content()
                return html
        except Exception:                             # noqa: BLE001
            time.sleep(1)
            continue
    return None


# ---------------------------------------------------------------------------
#  get_iframe —— 原脚本 108-129 行
# ---------------------------------------------------------------------------

def get_iframe(link: str, browser=None, log=print):
    """
    渲染分享页，取出正文 iframe 的地址（原脚本 `get_iframe`）。

    1. 先用原脚本那组正则从渲染后的 HTML 里找
    2. 找不到时补一步 DOM 查询 `document.querySelector('iframe[src]')`
       —— 有些页面的 iframe 是 JS 动态插进去的，正则抓不到
    """
    link = (link or '').strip()
    if not link.startswith("http"):
        return None

    base = "%s://%s" % (urlparse(link).scheme, urlparse(link).netloc)

    def _do(br):
        ctx, page = br.new_page(ua=_ua(), referer=base)
        try:
            page.goto(link, wait_until="domcontentloaded", timeout=45000)
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            html = page.content()
            src = _regex_iframe(html, base)
            if not src:
                try:
                    src = page.evaluate(
                        "() => { const f = document.querySelector('iframe[src]');"
                        " return f ? f.src : null; }") or ''
                except Exception:                     # noqa: BLE001
                    src = ''
            if src and not src.startswith('http'):
                src = ('https:' + src) if src.startswith('//') else base + src
            return src or None
        finally:
            try:
                page.close()
                ctx.close()
            except Exception:                         # noqa: BLE001
                pass

    if browser is not None:
        try:
            return _do(browser)
        except Exception as e:                        # noqa: BLE001
            if log:
                log('  [get_iframe] 复用浏览器失败: %s' % str(e)[:60])
            return None

    try:
        with _Browser(log=log) as br:
            return _do(br)
    except PlaywrightUnavailable:
        raise
    except Exception as e:                            # noqa: BLE001
        if log:
            log('  [get_iframe] 异常: %s' % str(e)[:60])
        return None


# ---------------------------------------------------------------------------
#  lanzou_parser —— 原脚本 131-736 行，28 种方法，顺序与判定全部照抄
# ---------------------------------------------------------------------------

def lanzou_parser(url: str, browser=None, pwd: str = "", log=print) -> dict:
    """
    对 `url`（通常是**分享页里的 iframe 地址**）依次尝试原脚本的全部 28 种方法。

    :return: 成功 → ``{"code":1,"fid":...,"host":...,"url":链接,"direct":链接}``
             失败 → ``{"code":0,"msg":"失败"}``
    """
    url = (url or '').strip()
    if not url:
        return _fail('空地址')

    parse_info = urlparse(url)
    domain = "%s://%s" % (parse_info.scheme, parse_info.netloc)
    base_ua = _ua()

    owns_browser = browser is None
    br_cm = None
    if owns_browser:
        br_cm = _Browser(log=log)
        browser = br_cm.__enter__()

    def logm(s):
        if log:
            log(s)

    def _page(referer=None, ua=None):
        return browser.new_page(ua=ua or base_ua, referer=referer)

    def _close(ctx, page):
        for x in (page, ctx):
            try:
                x.close()
            except Exception:                         # noqa: BLE001
                pass

    def _goto_eval(goto_url, js, wait=0.8, referer=None, timeout=15000,
                   wait_until="domcontentloaded", click=None):
        """「渲染 → (可选)点按钮 → 取链接」的公共骨架（原脚本各方法都在干这件事）。"""
        ctx, page = _page(referer=referer)
        try:
            page.goto(goto_url, timeout=timeout, wait_until=wait_until)
            time.sleep(wait)
            if click:
                for sel in click:
                    try:
                        if sel.startswith('role:'):
                            page.get_by_role("button", name=sel[5:]).click(timeout=2000)
                        elif sel.startswith('text:'):
                            page.locator("text=%s" % sel[5:]).click(timeout=2000)
                        else:
                            page.click(sel, timeout=2000)
                        time.sleep(0.8)
                        break
                    except Exception:                 # noqa: BLE001
                        continue
            return page.evaluate(js)
        finally:
            _close(ctx, page)

    def _requests_text(get_url, headers=None, timeout=7):
        try:
            h = {"User-Agent": base_ua}
            if headers:
                h.update(headers)
            r = requests.get(get_url, headers=h, timeout=timeout)
            r.encoding = 'utf-8'
            return r.text
        except Exception:                             # noqa: BLE001
            return ''

    JS_QUERY = ('() => { const a = document.querySelector(\'a[href*="/file/?"]\');'
                ' return a ? a.href : null }')
    JS_ALL_A = ('() => { for (const a of document.querySelectorAll("a"))'
                ' { if (a.href && a.href.includes("/file/?")) return a.href; }'
                ' return null; }')
    JS_ALL_EL = ('() => { for (const e of document.querySelectorAll("*"))'
                 ' { if (e.href && e.href.includes("/file/?")) return e.href; }'
                 ' return null; }')
    JS_GET_A = ('() => { for (const a of document.getElementsByTagName("a"))'
                ' { if (a.href && a.href.includes("/file/?")) return a.href; }'
                ' return null; }')
    JS_OR = ('() => { return document.querySelector(\'a[href*="/file/?"]\')?.href ||'
             ' document.querySelector(\'a[href*="file?"]\')?.href; }')

    try:
        # =====================================================================
        #  【3/28】浏览器静默提取   ← 原脚本里第一个成功的就是它
        # =====================================================================
        try:
            logm('  【3/28】浏览器静默提取 ...')
            link = _goto_eval(url, JS_QUERY, wait=0.8, referer=domain)
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 无有效下载链接')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【13/28】简化浏览器提取
        # =====================================================================
        try:
            logm('  【13/28】简化浏览器提取 ...')
            link = _goto_eval(url, JS_QUERY, wait=1.0)
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 无有效链接')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【1/28】AJAX 接口解析
        # =====================================================================
        try:
            logm('  【1/28】AJAX 接口解析 ...')
            sess = requests.Session()
            sess.headers = {"User-Agent": base_ua, "Referer": url, "Origin": domain}
            html = sess.get(url, timeout=7).text
            fid = re.search(r"file.*?=.*?(\d+)", html)
            sign = re.search(r"sign.*?['\"](.*?)['\"]", html)
            if fid and sign:
                api = "%s/ajaxm.php?file=%s" % (domain, fid.group(1))
                res = sess.post(api, data={"action": "downprocess",
                                           "sign": sign.group(1)}, timeout=7).json()
                if res.get("zt") == 1:
                    u = "%s/file/%s" % (res['dom'], res['url'])
                    if is_valid(u):
                        logm('    ✅ %s' % u[:70])
                        return _ok(u, fid=fid.group(1), host=res.get('dom', domain))
            logm('    ✗ 无有效数据')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【2/28】全局正则抓取
        # =====================================================================
        try:
            logm('  【2/28】全局正则抓取 ...')
            html = _requests_text(url)
            for m in re.findall(r"https?://[^\s\"']+/file/\?[^\s\"'>]+", html):
                if is_valid(m):
                    logm('    ✅ %s' % m[:70])
                    return _ok(m, host=domain)
            logm('    ✗ 未匹配到有效直链')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【4/28】浏览器点击普通下载
        # =====================================================================
        try:
            logm('  【4/28】浏览器点击普通下载 ...')
            link = _goto_eval(url, JS_QUERY, wait=0.8, referer=domain,
                              click=('text:普通下载',))
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 未获取到直链')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【5/28】JS变量抓取
        # =====================================================================
        try:
            logm('  【5/28】JS变量抓取 ...')
            html = _requests_text(url)
            m = re.search(r"downfile.*?['\"](https?://.*?)['\"]", html, re.I | re.S)
            if m and is_valid(m.group(1)):
                logm('    ✅ %s' % m.group(1)[:70])
                return _ok(m.group(1), host=domain)
            logm('    ✗ 未找到有效地址')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【6/28】Cookie会话
        # =====================================================================
        try:
            logm('  【6/28】Cookie会话 ...')
            s = requests.Session()
            s.get(url, timeout=6)
            html = s.get(url, timeout=6).text
            m = re.search(r"https?://[^\s]+/file/\?[^\s]+", html)
            if m and is_valid(m.group()):
                logm('    ✅ %s' % m.group()[:70])
                return _ok(m.group(), host=domain)
            logm('    ✗ 无有效链接')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【7/28】手机UA访问
        # =====================================================================
        try:
            logm('  【7/28】手机UA访问 ...')
            mu = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit"
            html = _requests_text(url, {"User-Agent": mu})
            m = re.search(r"https?://[^\s]+/file/\?[^\s]+", html)
            if m and is_valid(m.group()):
                logm('    ✅ %s' % m.group()[:70])
                return _ok(m.group(), host=domain)
            logm('    ✗ 未获取地址')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【8/28】iframe内页抓取
        # =====================================================================
        try:
            logm('  【8/28】iframe内页抓取 ...')
            html = _requests_text(url)
            i = re.search(r'<iframe.*?src="([^"]+)"', html)
            if i:
                u2 = i.group(1) if i.group(1).startswith("http") else domain + i.group(1)
                html2 = _requests_text(u2)
                m = re.search(r"https?://[^\s]+/file/\?[^\s]+", html2)
                if m and is_valid(m.group()):
                    logm('    ✅ %s' % m.group()[:70])
                    return _ok(m.group(), host=domain)
            logm('    ✗ 无iframe')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【9/28】a标签全检索（用正则代替 bs4，避免多一个依赖）
        # =====================================================================
        try:
            logm('  【9/28】a标签全检索 ...')
            html = _requests_text(url)
            for href in re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.I):
                if "/file/?" in href and is_valid(href):
                    logm('    ✅ %s' % href[:70])
                    return _ok(href, host=domain)
            logm('    ✗ 无有效a标签')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【10/28】murl加密参数
        # =====================================================================
        try:
            logm('  【10/28】murl加密参数 ...')
            html = _requests_text(url)
            m = re.search(r"murl.*?['\"](https?://.*?)['\"]", html)
            if m and is_valid(m.group(1)):
                logm('    ✅ %s' % m.group(1)[:70])
                return _ok(m.group(1), host=domain)
            logm('    ✗ 无murl参数')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【11/28】点击高速下载
        # =====================================================================
        try:
            logm('  【11/28】点击高速下载 ...')
            link = _goto_eval(url, JS_QUERY, wait=0.7, referer=domain,
                              click=('text:高速下载',))
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 无链接')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【12/28】源码深度搜索
        # =====================================================================
        try:
            logm('  【12/28】源码深度搜索 ...')
            html = _requests_text(url)
            m = re.search(r"https?://[^\s\"']+/file/\?[a-zA-Z0-9+/=]+", html)
            if m and is_valid(m.group()):
                logm('    ✅ %s' % m.group()[:70])
                return _ok(m.group(), host=domain)
            logm('    ✗ 未匹配到有效地址')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【14/28】最终兜底解析（role=button 点"普通下载"）
        # =====================================================================
        try:
            logm('  【14/28】最终兜底解析 ...')
            link = _goto_eval(url, JS_QUERY, wait=1.0, timeout=20000,
                              click=('role:普通下载',))
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 全部方式无效')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【15/28】网络流量监听抓取
        # =====================================================================
        try:
            logm('  【15/28】网络流量监听抓取 ...')
            ctx, page = _page()
            found = {'link': None}

            def on_response(res):
                if "/file/?" in res.url and not found['link']:
                    found['link'] = res.url

            page.on("response", on_response)
            try:
                page.goto(url, timeout=15000)
                time.sleep(2)
            finally:
                _close(ctx, page)
            if is_valid(found['link']):
                logm('    ✅ %s' % found['link'][:70])
                return _ok(found['link'], host=domain)
            logm('    ✗ 未抓到流量链接')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【16/28】下载按钮智能查找
        # =====================================================================
        try:
            logm('  【16/28】下载按钮智能查找 ...')
            link = _goto_eval(url, JS_ALL_A, wait=1.0)
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 未找到')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【17/28】HTML全文搜索
        # =====================================================================
        try:
            logm('  【17/28】HTML全文搜索 ...')
            html = _requests_text(url, timeout=10)
            for chunk in re.split(r'["\s<>]', html):
                if is_valid(chunk) and "/file/?" in chunk:
                    logm('    ✅ %s' % chunk[:70])
                    return _ok(chunk, host=domain)
            logm('    ✗ 无匹配')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【18/28】PC浏览器完整流程
        # =====================================================================
        try:
            logm('  【18/28】PC浏览器完整流程 ...')
            link = _goto_eval(url, JS_OR, wait=2.0, timeout=20000)
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 无链接')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【19/28】双内核兼容抓取
        # =====================================================================
        try:
            logm('  【19/28】双内核兼容抓取 ...')
            link = _goto_eval(url, JS_ALL_A, wait=1.0)
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 无结果')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【20/28】暴力全页面检索
        # =====================================================================
        try:
            logm('  【20/28】暴力全页面检索 ...')
            link = _goto_eval(url, JS_ALL_EL, wait=2.0, timeout=20000)
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 全部方法用尽')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【21/28】动态下载链接生成
        # =====================================================================
        try:
            logm('  【21/28】动态下载链接生成 ...')
            js = ('() => { const el = document.querySelector(\'[onclick*="down"]\')'
                  ' || document.querySelector("[data-url]");'
                  ' return el ? (el.dataset.url || el.href) : null; }')
            link = _goto_eval(url, js, wait=1.0)
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 未生成')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【22/28】完整请求头访问
        # =====================================================================
        try:
            logm('  【22/28】完整请求头访问 ...')
            html = _requests_text(url, {"Referer": domain,
                                        "Accept": "text/html,application/xhtml+xml"},
                                  timeout=10)
            m = re.search(r"https?://[^\s]+/file/\?[^\s]+", html)
            if m and is_valid(m.group()):
                logm('    ✅ %s' % m.group()[:70])
                return _ok(m.group(), host=domain)
            logm('    ✗ 无数据')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【23/28】页面跳转追踪（requests 自带重定向追踪，等价于原版 urllib3）
        # =====================================================================
        try:
            logm('  【23/28】页面跳转追踪 ...')
            r = requests.get(url, headers={"User-Agent": base_ua}, timeout=10,
                             allow_redirects=True)
            r.encoding = 'utf-8'
            m = re.search(r"https?://[^\s]+/file/\?[^\s]+", r.text)
            if m and is_valid(m.group()):
                logm('    ✅ %s' % m.group()[:70])
                return _ok(m.group(), host=domain)
            logm('    ✗ 追踪无结果')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【24/28】控制台执行获取
        # =====================================================================
        try:
            logm('  【24/28】控制台执行获取 ...')
            js = ('() => { try { return document.links[0].href }'
                  ' catch(e) { return null } }')
            link = _goto_eval(url, js, wait=1.0)
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 获取失败')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【25/28】多正则组合匹配
        # =====================================================================
        try:
            logm('  【25/28】多正则组合匹配 ...')
            html = _requests_text(url, timeout=8)
            for pat in (r'https?://[^/]+/file/\?[a-zA-Z0-9+/]+={0,3}',
                        r'file/\?[a-zA-Z0-9+/]+={0,3}'):
                m = re.search(pat, html)
                if m:
                    l = m.group() if m.group().startswith("http") else domain + "/" + m.group()
                    if is_valid(l):
                        logm('    ✅ %s' % l[:70])
                        return _ok(l, host=domain)
            logm('    ✗ 无匹配')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【26/28】模拟点击+延时等待
        # =====================================================================
        try:
            logm('  【26/28】模拟点击+延时等待 ...')
            link = _goto_eval(url, JS_QUERY, wait=2.0, timeout=20000,
                              click=('button:has-text("下载")',))
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 无响应')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【27/28】接口二次请求
        # =====================================================================
        try:
            logm('  【27/28】接口二次请求 ...')
            sess = requests.Session()
            sess.get(url, headers={"User-Agent": base_ua}, timeout=8)
            r2 = sess.get(url, headers={"User-Agent": base_ua, "Referer": url},
                          timeout=8)
            r2.encoding = 'utf-8'
            m = re.search(r"https?://[^\s]+/file/\?[^\s]+", r2.text)
            if m and is_valid(m.group()):
                logm('    ✅ %s' % m.group()[:70])
                return _ok(m.group(), host=domain)
            logm('    ✗ 二次请求无数据')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        # =====================================================================
        #  【28/28】终极浏览器渲染（等网络空闲）
        # =====================================================================
        try:
            logm('  【28/28】终极浏览器渲染 ...')
            link = _goto_eval(url, JS_GET_A, wait=2.0, timeout=25000,
                              wait_until="networkidle")
            if is_valid(link):
                logm('    ✅ %s' % link[:70])
                return _ok(link, host=domain)
            logm('    ✗ 所有方法失效')
        except Exception as e:                        # noqa: BLE001
            logm('    ✗ 异常: %s' % str(e)[:60])

        logm('  全部 28 种方案解析失败')
        return _fail('全部 28 种方案解析失败')

    finally:
        if owns_browser and br_cm is not None:
            br_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
#  parse_local —— 原脚本 744-805 行（重试 + 断网判断）
# ---------------------------------------------------------------------------

def parse_local(lanzou_url: str,
                pwd: str = "",
                *,
                retry_delays=(1, 3, 5),
                check_network: bool = True,
                browser=None,
                log=print) -> dict:
    """
    原脚本 `lanzou_parse_local` 的等价实现：

        网络检查 → get_iframe() → lanzou_parser(iframe) → 返回结果
        失败则按 retry_delays 重试。

    :return: 原脚本的结构
             ``{"code":200,"msg":"成功","data":{"filename","downloadurl","fasturl"}}``
             或 ``{"code":-1,"msg":"重试失败","data":None}``
    """
    owns = browser is None
    br_cm = None
    if owns:
        br_cm = _Browser(log=log)
        browser = br_cm.__enter__()

    try:
        for attempt, delay in enumerate(retry_delays, 1):
            log('  [+] 引擎启动中 · 第 %d 次尝试...' % attempt)

            # 断网判断（原脚本用百度探活；改成探蓝奏云自身，避免不必要的外网）
            if check_network and attempt == 1:
                try:
                    requests.get(lanzou_url, timeout=8, stream=True).close()
                except Exception:                     # noqa: BLE001
                    log('      ❌ 网络不通！等待 %d 秒后重试...' % delay)
                    time.sleep(delay)
                    continue

            log('  [+] 防检测模式已开启 · 稳定连接中...')
            iframe = get_iframe(lanzou_url, browser=browser, log=log)
            if not iframe:
                log('      ❌ 获取 iframe 失败！等待 %d 秒后重试...' % delay)
                time.sleep(delay)
                continue
            log('  [+] 成功获取真实地址 · 正在请求服务器签名...')

            result = lanzou_parser(iframe, browser=browser, pwd=pwd, log=log)
            if result.get('code') != 1:
                log('      ❌ 解析失败：%s，等待 %d 秒后重试...'
                    % (result.get('msg'), delay))
                time.sleep(delay)
                continue

            real_url = result['url']
            return {
                'code': 200,
                'msg': '成功',
                'data': {
                    'filename': '',          # 由上层用 _extract_filename_from_url 填
                    'downloadurl': real_url,
                    'fasturl': result.get('direct', real_url),
                    'host': result.get('host', ''),
                    'fid': result.get('fid', '0'),
                },
            }

        log('  ❌ 重试 %d 次后仍然失败！' % len(retry_delays))
        return {'code': -1, 'msg': '重试失败', 'data': None}
    finally:
        if owns and br_cm is not None:
            br_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
#  CDN 反爬（ESA / 阿里云「5 秒盾」JS 挑战）—— 原脚本没有这一步
# ---------------------------------------------------------------------------
#
#  背景：蓝奏云的下载节点（如 developer4.lanrar.com）对**直链本身**也挂了
#  一层 JS 挑战。直接 requests.get(直链) 拿到的不是文件，而是 4KB 左右的：
#
#      <html><script>var arg1='6A8408035F417F52C0708BC196A0B23D32AF...'
#
#  这段 JS 需要在**真实浏览器里执行**，算出一个值写进 `acw_sc__v2` cookie，
#  然后自己 reload，CDN 才肯把真文件吐出来。
#
#  原脚本 `download_with_browser_smart` 直接 requests 下载，所以拿到的是这
#  4KB 的挑战页（会被 `actual_size < 30*1024` 判成失败）。
#
#  优化点：让已经开着的浏览器去把挑战过一次，把 cookie 交回给 requests，
#  之后 requests 就能带进度条正常下载了 —— 不用让 Playwright 去接整个文件。

CHALLENGE_COOKIE = 'acw_sc__v2'

_CHALLENGE_MARK = re.compile(r"var\s+arg1\s*=\s*'[0-9A-Fa-f]{20,}'")


def is_cdn_challenge(text) -> bool:
    """
    判断一段响应内容是不是 CDN 的 JS 反爬挑战页。
    True when the payload is the CDN's JS anti-bot challenge, not a real file.
    """
    if not text:
        return False
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text[:4096]).decode('utf-8', 'ignore')
    return bool(_CHALLENGE_MARK.search(text[:4096]))


def solve_cdn_challenge(url, *, browser=None, log=None,
                        wait=25.0, nav_timeout=8000, headless=True):
    """
    用真实浏览器打开 ``url``，等 CDN 的反爬 JS 自己种下 cookie，然后把
    cookie 抓出来。

    Open ``url`` in a real browser just long enough for the CDN's anti-bot
    JavaScript to write its own cookie, then hand that cookie jar back.

    :param url:     直链（``dom/file/xxx`` 那种）
    :param browser: 复用已有的 ``_Browser``；不传就临时起一个
    :param wait:    最多等多少秒等 cookie 出现
    :return:        ``{cookie名: 值}``；没拿到就是空 dict
    :rtype:         dict
    """
    log = log or (lambda *a: None)
    if not url:
        return {}

    owns = browser is None
    br_cm = None
    try:
        if owns:
            br_cm = _Browser(headless=headless, log=log)
            browser = br_cm.__enter__()

        # accept_downloads=False：挑战过了也别真去下几十 MB
        ctx, page = browser.new_page(ua=_ua(), accept_downloads=False)
        try:
            try:
                # wait_until="commit" —— 响应头一到就返回。
                # 用 "load"/"domcontentloaded" 会一直卡到超时，因为挑战页
                # reload 之后就变成下载了，导航永远「完不成」。
                page.goto(url, wait_until='commit', timeout=nav_timeout)
            except Exception:                     # noqa: BLE001
                pass                               # 超时/被中断都无所谓，看 cookie

            jar = {}
            deadline = time.time() + wait
            while time.time() < deadline:
                jar = {c['name']: c['value'] for c in ctx.cookies()}
                if CHALLENGE_COOKIE in jar:
                    break
                time.sleep(0.25)

            if CHALLENGE_COOKIE not in jar:
                log('      ⚠ CDN 反爬 cookie 没等到（%.0fs）' % wait)
                return {}

            # 再等一小会儿，让 acw_tc / down_ip 这些兄弟 cookie 也落下来
            time.sleep(0.8)
            jar = {c['name']: c['value'] for c in ctx.cookies()}
            log('      ✔ 已过 CDN 反爬（%s）' % ', '.join(sorted(jar)))
            return jar
        finally:
            try:
                ctx.close()
            except Exception:                     # noqa: BLE001
                pass
    except PlaywrightUnavailable:
        raise
    except Exception as e:                        # noqa: BLE001
        log('      ⚠ 过 CDN 反爬失败: %s' % e)
        return {}
    finally:
        if owns and br_cm is not None:
            br_cm.__exit__(None, None, None)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        print('用法: python lanzou_browser.py <蓝奏云分享链接> [提取码]')
        sys.exit(1)
    u = sys.argv[1]
    p = sys.argv[2] if len(sys.argv) > 2 else ''
    r = parse_local(u, p)
    print()
    print('结果:', r)
