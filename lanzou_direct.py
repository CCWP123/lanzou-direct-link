#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lanzou-direct-link / lanzou_direct.py
=====================================
把蓝奏云（Lanzou Cloud）的**分享链接**解析成**可以直接丢进浏览器的下载直链**。
Turn a Lanzou Cloud **share link** into a **direct download URL** you can paste
straight into a browser.

纯 `requests` 实现，不依赖第三方解析服务、不依赖浏览器内核。
Pure `requests` implementation — no third-party parsing API, no browser engine.

算法 / Algorithm
----------------
蓝奏云的分享页本身不是下载地址，真正的直链要通过「页面里的 sign → ajaxm.php」
两步换出来：

    ① GET  分享页                → 拿 HTML，跟随重定向
    ② （可选）GET <iframe src>   → 蓝奏云常把正文放在 iframe 里
    ③ 从 HTML 里提取：
           sign     —— 形如 var sign = 'xxxx'
           signs    —— 部分页面有
           ves      —— 部分页面有，默认 "1"
           ajax 路径 —— 默认 /ajaxm.php
           fid      —— 文件数字 ID（拼在 ajaxm.php?file= 后面）
    ④ POST <ajax>?file=<fid>
           action=downprocess & sign=<sign> & ves=<ves> [& signs=<signs>] [& p=<密码>]
    ⑤ 返回 JSON： {"zt":1, "dom":"https://xx.lanzou?.com", "url":"xxxx", "inf":"文件名", "size":"大小"}
    ⑥ 直链 = dom + "/file/" + url     （若 url 本身是 http 开头则直接用它）
    ⑦ （可选）跟随重定向，得到最终 CDN 地址

    Lanzou's share page is not itself a download URL. The real link is obtained
    by exchanging an in-page `sign` token against `ajaxm.php`, then joining the
    returned `dom` + `url`. See the numbered steps above.

依赖 / Dependencies
-------------------
    requests       —— 必需 / required
    playwright     —— 浏览器引擎用，见 lanzou_browser.py / see that module
                      （现代蓝奏云有 JS 反爬，纯 HTTP 基本跑不通，所以实际上这个也是必需的）

⚠️ 免责声明 / Disclaimer
------------------------
本模块只做「读取公开分享页 → 换取官方直链」这一件事，不绕过付费、不破解提取码、
不修改蓝奏云任何数据。请遵守蓝奏云的服务条款，仅用于你自己的文件或已获授权的分享。
This module merely reads a public share page and exchanges it for Lanzou's own
direct URL. It does not bypass payment, crack extraction codes, or modify any
Lanzou data. Respect Lanzou's ToS; use it on your own files or authorised shares.
"""

import json
import os
import random
import re
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlparse, urljoin

import requests

__all__ = [
    'LanzouFile',
    'LanzouError',
    'NotLanzouUrl',
    'ParseFailed',
    'PasswordRequired',
    'DownloadCancelled',
    'is_lanzou_url',
    'is_valid_direct_link',
    'looks_js_challenge',
    'parse',
    'resolve_real_url',
    'download',
    'guess_filename',
    'extract_filename_from_url',
    'extract_sign',
    'extract_signs',
    'extract_ves',
    'extract_ajax_path',
    'extract_fid',
    'extract_iframe',
]

# ---------------------------------------------------------------------------
#  常量 / Constants
# ---------------------------------------------------------------------------

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

# 蓝奏云历史上用过一大堆域名/镜像，统一用正则匹配
# Lanzou has rotated through many domains/mirrors over the years.
_LANZOU_HOST_RE = re.compile(
    r'(?:^|\.)(?:'
    r'lanzou[a-z]*|lanzo[a-z]*|lanzn|lanz[a-z]{0,3}'
    r')\.(?:com|cn|net|org|top|xyz|vip|cc)(?::\d+)?$',
    re.I,
)

# ajax 端点候选（按优先级）
_AJAX_ENDPOINTS = ['/ajaxm.php', '/file/ajax.php', '/ajax.php', '/download.php']

DEFAULT_TIMEOUT = 15


# ---------------------------------------------------------------------------
#  数据类与异常 / Data class & exceptions
# ---------------------------------------------------------------------------

@dataclass
class LanzouFile:
    """
    解析结果。
    Parse result.

    :ivar direct_url: **可以直接粘贴进浏览器的下载直链**（核心产物）
    :ivar middle_url: 蓝奏云的中间页链接（dom + /file/ + url），需要带 Referer 才能下载
    :ivar name:       文件名
    :ivar size:       文件大小（字符串，蓝奏云原样返回）
    :ivar share_url:  原始分享链接
    :ivar real_url:   跟随重定向后的最终 CDN 地址（仅在 resolve=True 时填充）
    :ivar engine:     实际生效的解析引擎：'http' 或 'browser'
    :ivar cookies:    过 CDN 反爬后拿到的 cookie（下载时必须带上，见 :func:`download`）
    :ivar raw:        底层引擎的原始响应
    """
    direct_url: str = ''
    middle_url: str = ''
    name: str = ''
    size: str = ''
    share_url: str = ''
    real_url: str = ''
    engine: str = ''
    cookies: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.direct_url)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        d = self.to_dict()
        # cookie 是临时的、跟 IP 绑定，没什么参考价值，导出时省略
        d.pop('cookies', None)
        return json.dumps(d, ensure_ascii=False, indent=indent)


class LanzouError(Exception):
    """本模块所有异常的基类 / base class for all errors raised here."""


class NotLanzouUrl(LanzouError):
    """传入的链接看起来不是蓝奏云链接 / the URL does not look like Lanzou."""


class PasswordRequired(LanzouError):
    """该分享需要提取码，但没提供 / the share is password-protected."""


class ParseFailed(LanzouError):
    """所有方法都没能换出直链 / every strategy failed."""


class DownloadCancelled(LanzouError):
    """下载被调用方主动中止 / the download was cancelled by the caller."""


# ---------------------------------------------------------------------------
#  HTTP 头 / Headers
# ---------------------------------------------------------------------------

def _ua() -> str:
    return random.choice(USER_AGENTS)


def _page_headers(referer: Optional[str] = None) -> dict:
    """访问分享页用的头 / headers for fetching the share page."""
    h = {
        "User-Agent": _ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        h["Referer"] = referer
    return h


def _ajax_headers(referer: str) -> dict:
    """调用 ajaxm.php 用的头 / headers for the ajax call."""
    return {
        "User-Agent": _ua(),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": _origin(referer),
        "Referer": referer,
    }


def _dl_headers(url: str = '') -> dict:
    """
    下载用的请求头 —— 原脚本 `_dl_headers` 的等价实现。

    原脚本按域名给不同 Referer（这是「独立下载器」那套策略），这里照抄：
    蓝奏云系一律 `https://wwa.lanzoui.com/`，其它站点用自身根地址。
    Per-domain Referer, exactly as the original script does it.
    """
    headers = {
        "User-Agent": _ua(),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "DNT": "1",
    }
    if url:
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            if any(k in host for k in ("lanzou", "lanzo", "lanzouv", "lanzn", "lanrar")):
                headers["Referer"] = "https://wwa.lanzoui.com/"
            elif "webgetstore" in host:
                headers["Referer"] = "%s://%s/" % (parsed.scheme, parsed.netloc)
            elif "3dmgame" in host:
                headers["Referer"] = "https://mod.3dmgame.com/"
            elif "baidu" in host:
                headers["Referer"] = "https://pan.baidu.com/"
            else:
                headers["Referer"] = "%s://%s/" % (parsed.scheme, parsed.netloc)
        except Exception:                             # noqa: BLE001
            pass
    else:
        headers["Referer"] = "https://wwa.lanzoui.com/"
    return headers


def _origin(url: str) -> str:
    p = urlparse(url)
    return "%s://%s" % (p.scheme, p.netloc) if p.netloc else ''


def _base_of(url: str) -> str:
    p = urlparse(url)
    return "%s://%s" % (p.scheme, p.netloc) if p.netloc else ''


def _abs_url(src: str, base: str) -> str:
    """把可能的相对地址补成绝对地址 / turn a relative URL into an absolute one."""
    src = (src or '').strip()
    if not src:
        return ''
    if src.startswith('//'):
        return 'https:' + src
    if src.startswith('http'):
        return src
    return urljoin(base + '/', src.lstrip('/'))


# ---------------------------------------------------------------------------
#  URL 校验 / URL validation
# ---------------------------------------------------------------------------

def is_lanzou_url(url: str) -> bool:
    """
    判断一个链接是不是蓝奏云分享链接（只接受 http / https）。
    Check whether a URL looks like a Lanzou share link (http/https only).
    """
    if not url or not isinstance(url, str):
        return False
    try:
        p = urlparse(url.strip())
    except Exception:                       # noqa: BLE001
        return False
    if p.scheme.lower() not in ('http', 'https'):
        return False
    host = p.netloc
    if not host:
        return False
    host = host.split('@')[-1]              # 去掉可能的 user:pass@
    host = host.split(':')[0]               # 去掉端口
    return bool(_LANZOU_HOST_RE.search(host))


def is_valid_direct_link(link: str) -> bool:
    """
    原脚本 `lanzou_parser` 里 `is_valid()` 的等价实现（判定规则一字未改）：
    排除空值、`kdns.js`、以 `/file/` 结尾、长度不足 20 的地址。
    Verbatim from the original script's `is_valid()`.
    """
    if not link:
        return False
    if "kdns.js" in link or link.endswith("/file/") or len(link) < 20:
        return False
    return True


# 蓝奏云 JS 反爬挑战页的特征：var arg1='B47A77C7...' + 一段 eval 循环。
# 纯 HTTP 解析在这种页面上必然失败（没有 iframe / 没有 sign / 没有 ajaxm.php），
# 早一点识别出来就能早点切到浏览器引擎，省掉几次无用请求。
_JS_CHALLENGE_RE = re.compile(r"var\s+arg1\s*=\s*'[0-9A-Fa-f]{20,}'")


def looks_js_challenge(html) -> bool:
    """
    判断拿到的是不是蓝奏云/CDN 的 JS 反爬挑战页。
    Detect Lanzou's obfuscated JS anti-bot challenge page.

    分享页和**直链所在节点**都会下发这种页面：
    Both the share page and the download node serve this:
    ``<html><script>var arg1='B47A77C7...'``
    """
    if isinstance(html, (bytes, bytearray)):
        html = bytes(html[:4096]).decode('utf-8', 'ignore')
    return bool(_JS_CHALLENGE_RE.search((html or '')[:4096]))


def extract_filename_from_url(url: str, *, timeout: int = 5):
    """
    从下载链接里提取真实文件名 —— 原脚本 `_extract_filename_from_url` 的等价实现。

    三种方法依次尝试：
      1. URL 参数里的 fileName / filename / file / name / download
      2. HEAD 请求的 Content-Disposition（优先 RFC 5987 的 ``filename*=UTF-8''``）
      3. URL 路径最后一段

    :return: 文件名，取不到返回 None
    """
    from urllib.parse import parse_qs, unquote

    final_name = None

    # 方法1：URL 参数
    try:
        params = parse_qs(urlparse(url).query)
        for key in ("fileName", "filename", "file", "name", "download"):
            if key in params:
                cand = unquote(params[key][0]).strip()
                if cand and len(cand) > 2:
                    final_name = cand
                    break
    except Exception:                                  # noqa: BLE001
        pass

    # 方法2：HEAD 的 Content-Disposition
    if not final_name:
        try:
            resp = requests.head(url, headers=_dl_headers(url),
                                 allow_redirects=True, timeout=timeout)
            cd = resp.headers.get("Content-Disposition", "") or ""
            try:
                resp.close()
            except Exception:                          # noqa: BLE001
                pass
            if cd:
                m = re.search(r"filename\*=\s*UTF-8''(.+?)(?:;|$)", cd, re.I)
                if m:
                    final_name = unquote(m.group(1)).strip()
                else:
                    m = re.search(r'filename="?([^";\s]+)"?', cd, re.I)
                    if m:
                        final_name = unquote(m.group(1)).strip()
        except Exception:                              # noqa: BLE001
            pass

    # 方法3：URL 路径最后一段
    if not final_name:
        try:
            name = url.strip("/").split("/")[-1]
            name = re.sub(r"\?.*|#.*|&.*", "", name)
            name = unquote(name)
            if len(name) >= 3 and "." in name:
                final_name = name.strip()
        except Exception:                              # noqa: BLE001
            pass

    if not final_name or len(final_name.strip()) < 3:
        return None
    if final_name in ("file", "download", "index", "UTF-8"):
        return None
    # 原脚本这里会强行补 .zip；本库不猜后缀，只把明显没有后缀的当失败
    if "." not in final_name:
        return None
    return final_name


# ---------------------------------------------------------------------------
#  HTML 提取 / HTML extraction
# ---------------------------------------------------------------------------

def extract_iframe(html: str, base: str) -> str:
    """
    从分享页里找出正文 iframe 的绝对地址（找不到返回空串）。
    Find the content iframe's absolute URL, or '' if there is none.
    """
    if not html:
        return ''
    patterns = [
        r'<iframe[^>]+src=["\']([^"\']+)["\']',
        r'<iframe.*?src=[\'"](.*?)[\'"]',
        r'iframe\s+src=[\'"](.*?)[\'"]',
        r'data-src=[\'"](.*?)[\'"]',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I | re.S)
        if m:
            u = _abs_url(m.group(1), base)
            if u:
                return u
    return ''


def extract_sign(html: str) -> str:
    """
    提取 sign 令牌。蓝奏云前后端改过很多次，所以这里排了一串模式。
    Extract the `sign` token. Lanzou has changed this many times, hence the
    long pattern list.
    """
    if not html:
        return ''
    patterns = [
        r"var\s+sign\s*=\s*'([^']+)'",
        r'var\s+sign\s*=\s*"([^"]+)"',
        r"var\s+signn\s*=\s*'([^']+)'",
        r"sign\s*[:=]\s*['\"]([^'\"]+)['\"]",
        r"['\"]sign['\"]\s*:\s*['\"]([^'\"]+)['\"]",
        r"['\"]signn['\"]\s*:\s*['\"]([^'\"]+)['\"]",
        r"skdkds\s*=\s*'([^']+)'",
        r"kd\s*=\s*'([^']+)'",
        r"var\s+[a-zA-Z_]\w*\s*=\s*'([a-f0-9]{16,64})'",
        r"var\s+[a-zA-Z_]\w*\s*=\s*'([a-zA-Z0-9]{16,64})'",
        r"['\"]([a-f0-9]{32})['\"]",
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1).strip()
    return ''


def extract_signs(html: str) -> str:
    """提取 signs（部分页面才有）/ extract `signs` when present."""
    if not html:
        return ''
    for pat in (r"var\s+signs\s*=\s*'([^']+)'",
                r"signs\s*[:=]\s*['\"]([^'\"]+)['\"]"):
        m = re.search(pat, html)
        if m:
            return m.group(1).strip()
    return ''


def extract_ves(html: str) -> str:
    """
    提取 ves 版本号，默认 "1" / extract `ves`, defaulting to "1".

    注意：这里刻意用 ``[\\w.\\-]+`` 而不是原脚本的 ``[^'"]+``。
    后者是"除了引号什么都吃"的贪婪匹配，遇到 ``var ves = 2;`` 这种没有
    结尾引号的写法会把后面整段 JS 都吞进来。
    Deliberately narrow: the naive ``[^'"]+`` used elsewhere is greedy and would
    swallow the rest of the script when the value is unquoted.
    """
    if not html:
        return '1'
    for pat in (
        r"var\s+ves\s*=\s*['\"]?([\w.\-]+)['\"]?",
        r"['\"]ves['\"]\s*[:=]\s*['\"]?([\w.\-]+)['\"]?",
        r"\bves\s*[:=]\s*['\"]?([\w.\-]+)['\"]?",
    ):
        m = re.search(pat, html)
        if m:
            v = m.group(1).strip()
            if v and v not in (';', 'var'):
                return v
    return '1'


def extract_ajax_path(html: str) -> str:
    """从页面 JS 里找出 ajax 端点路径（找不到返回空串）/ find the ajax endpoint."""
    if not html:
        return ''
    patterns = [
        r"url\s*:\s*['\"]([^'\"]*?\.php[^'\"]*?)['\"]",
        r"url\s*:\s*['\"]([^'\"]*?ajax[^'\"]*?)['\"]",
        r"ajaxurl\s*=\s*['\"]([^'\"]+)['\"]",
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1).strip()
    return ''


def extract_fid(html: str) -> str:
    """
    提取文件数字 ID，用于拼 ajaxm.php?file=<fid>。
    Extract the numeric file id used as ajaxm.php?file=<fid>.

    注意：老接口需要这个参数，新接口直接把 fid 编码进了 sign，没有也能用。
    The legacy endpoint needs it; newer ones encode it into `sign`, so '' is fine.
    """
    if not html:
        return ''
    patterns = [
        r"""['"]file['"]\s*[:=]\s*['"]?(\d{5,})""",
        r'file\s*=\s*["\']?(\d{5,})["\']?',
        r'/file/(\d{5,})',
        r"""['"]fid['"]\s*[:=]\s*['"]?(\d{5,})""",
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return ''


def looks_password_protected(html: str) -> bool:
    """
    粗略判断这个分享页是否要求提取码（用于给出更友好的报错）。
    Heuristically decide whether the share page asks for an extraction code.
    """
    if not html:
        return False
    hints = [
        r'name=["\']pwd["\']',
        r'id=["\']pwd["\']',
        r'请输入提取码',
        r'输入密码',
        r'password',
        r'提取码',
    ]
    return any(re.search(h, html, re.I) for h in hints)


# ---------------------------------------------------------------------------
#  核心解析 / Core parsing
# ---------------------------------------------------------------------------

def _exchange_direct_url(session, base, referer, sign, signs, ves, fid,
                         pwd, timeout, ajax_path=''):
    """
    第 ④⑤⑥ 步：拿 sign 去 ajaxm.php 换直链。
    Steps ④⑤⑥: exchange `sign` for the direct URL via ajaxm.php.
    """
    post_data = {"action": "downprocess", "sign": sign, "ves": ves}
    if signs:
        post_data["signs"] = signs
    if pwd:
        post_data["p"] = pwd

    # 首选页面 JS 里声明的端点，其余常见路径作为兜底（蓝奏云换过好几次）
    # Prefer the endpoint declared in the page; fall back to the usual paths.
    candidates = []
    if ajax_path:
        candidates.append(_abs_url(ajax_path, base))
    for ep in _AJAX_ENDPOINTS:
        full = base + ep
        if full not in candidates:
            candidates.append(full)

    last_err = None
    for ajax in candidates:
        url = ajax + (("?file=%s" % fid) if fid else "")
        try:
            r = session.post(url, data=post_data,
                             headers=_ajax_headers(referer), timeout=timeout)
            r.encoding = 'utf-8'
            data = r.json()
        except Exception as e:                      # noqa: BLE001
            last_err = e
            continue

        if str(data.get("zt")) == "1":
            dom = (data.get("dom") or '').rstrip('/')
            path = (data.get("url") or '').strip()
            if not path:
                continue
            if path.startswith('http'):
                direct = path                        # 极少数情况直接给全 URL
            else:
                direct = "%s/file/%s" % (dom, path.lstrip('/'))
            return {
                'direct_url': direct,
                'name': (data.get("inf") or '').strip(),
                'size': str(data.get("size") or '').strip(),
                'raw': data,
            }
        last_err = data.get("inf") or data.get("msg") or 'zt != 1'

    raise ParseFailed('ajaxm.php 未返回直链 / no direct link from ajaxm.php: %s'
                      % last_err)


def _parse_via_http(share_url, pwd, *, resolve, timeout, session, log):
    """
    纯 HTTP 路线（等价于原脚本的 `strategy_direct`：sign → ajaxm.php）。

    ⚠️ 现代蓝奏云对分享页做了 JS 反爬，多数链接用这条路**必然失败** ——
    这时应该走 `_parse_via_browser`。保留它是因为：
      * 老式页面 / 部分镜像仍然有效，且它比浏览器快得多
      * 它是本库「零浏览器依赖」时的兜底
    """
    def logm(s):
        if log:
            log(s)

    sess = session or requests.Session()
    sess.headers.update(_page_headers())

    # ---- ① 取分享页 ----
    r = sess.get(share_url, timeout=timeout, allow_redirects=True)
    r.encoding = 'utf-8'
    html = r.text
    referer = r.url
    base = _base_of(referer)

    # 早退：JS 反爬挑战页，纯 HTTP 不可能解析出来
    if looks_js_challenge(html):
        raise ParseFailed(
            '分享页是 JS 反爬挑战页（没有 iframe/sign/ajaxm.php），纯 HTTP 无法解析，'
            '需要浏览器引擎')

    # ---- ② 进 iframe ----
    iframe = extract_iframe(html, base)
    if iframe:
        try:
            r2 = sess.get(iframe, timeout=timeout, headers=_page_headers(referer))
            r2.encoding = 'utf-8'
            html, referer = r2.text, iframe
            base = _base_of(iframe)
        except Exception:                            # noqa: BLE001
            pass                                     # 进不去就用外层页面继续试

    # ---- ③ 提取参数 ----
    sign = extract_sign(html)
    if not sign:
        if looks_password_protected(html) and not pwd:
            raise PasswordRequired('该分享需要提取码 / extraction code required')
        raise ParseFailed('未能从页面提取 sign / could not extract `sign`')

    fid = extract_fid(html)
    out = _exchange_direct_url(
        sess, base, referer,
        sign=sign,
        signs=extract_signs(html),
        ves=extract_ves(html),
        fid=fid,
        pwd=pwd,
        timeout=timeout,
        ajax_path=extract_ajax_path(html),
    )

    direct = out['direct_url']
    if not is_valid_direct_link(direct):
        raise ParseFailed('HTTP 路线拿到的链接不合法: %r' % direct[:80])

    result = LanzouFile(
        direct_url=direct,
        middle_url=direct,
        name=out['name'],
        size=out['size'],
        share_url=share_url,
        raw=out['raw'],
    )
    result.engine = 'http'

    if resolve:
        real = resolve_real_url(direct, timeout=timeout, session=sess)
        if real:
            result.real_url = real
    return result


def _parse_via_browser(share_url, pwd, *, resolve, timeout, browser, log):
    """
    浏览器引擎路线 —— 忠实移植 `蓝奏云网盘.py` 的
    `lanzou_parse_local` → `get_iframe` → `lanzou_parser`（28 种方法）。

    这条路才是现代蓝奏云真正能用的：真实浏览器渲染 → 取 `a[href*="/file/?"]`。

    相比原脚本多做一步（也是唯一的实质增强）：解析出直链后**顺手把下载节点的
    CDN 反爬过掉**，把 cookie 存进结果 —— 原脚本没有这步，所以它下载时只能
    拿到 4KB 的挑战页，被判成「文件过小」。
    """
    try:
        from lanzou_browser import (parse_local, PlaywrightUnavailable,
                                    _Browser)
    except ImportError as e:
        raise ParseFailed('缺少浏览器引擎 lanzou_browser.py: %s' % e) from None

    def logm(s):
        if log:
            log(s)

    owns = browser is None
    br_cm = None
    try:
        if owns:
            br_cm = _Browser(headless=True, log=logm)
            browser = br_cm.__enter__()

        r = parse_local(share_url, pwd, browser=browser, log=logm)

        if r.get('code') != 200 or not r.get('data'):
            raise ParseFailed('浏览器引擎解析失败: %s' % r.get('msg'))

        data = r['data']
        direct = data.get('downloadurl') or ''
        if not is_valid_direct_link(direct):
            raise ParseFailed('浏览器引擎返回的链接不合法: %r' % direct[:80])

        logm('  ✔ 浏览器引擎拿到直链')

        # ------------------------------------------------------------------
        #  探测直链：顺便就是「过 CDN 反爬」那一步。
        #  纯 HTTP 探不动时会自动借**当前这个浏览器**过挑战，再探一次 ——
        #  整个过程浏览器只启动一次。
        # ------------------------------------------------------------------
        probe = probe_direct_url(direct, timeout=min(timeout, 10),
                                 browser=browser, log=logm)
        cookies = probe.get('cookies') or {}

        name = (probe.get('filename') or '') if probe.get('ok') else ''
        size = (probe.get('total') or '') if probe.get('ok') else ''
        if not name:
            name = extract_filename_from_url(direct, timeout=min(timeout, 6)) or ''

        result = LanzouFile(
            direct_url=direct,
            middle_url=direct,
            name=name,
            size=size,
            share_url=share_url,
            cookies=cookies,
            raw={'host': data.get('host', ''), 'fid': data.get('fid', '0')},
        )
        result.engine = 'browser'

        if resolve:
            real = resolve_real_url(direct, timeout=timeout)
            if real:
                result.real_url = real
        return result
    finally:
        if owns and br_cm is not None:
            br_cm.__exit__(None, None, None)


def parse(share_url: str,
          pwd: str = '',
          *,
          resolve: bool = False,
          timeout: int = DEFAULT_TIMEOUT,
          session: Optional[requests.Session] = None,
          engine: str = 'auto',
          browser=None,
          log=None,
          allow_browser_fallback=None) -> LanzouFile:
    """
    把蓝奏云分享链接解析成下载直链。
    Resolve a Lanzou share link into a direct download URL.

    :param share_url: 蓝奏云分享链接，如 ``https://wwx.lanzoux.com/iAbCdEfGhIj``
    :param pwd:       提取码（没有就留空）/ extraction code, if any
    :param resolve:   是否再跟随重定向拿最终 CDN 地址 / also follow redirects
    :param timeout:   单次请求超时秒数 / per-request timeout
    :param session:   复用已有 Session（只影响 HTTP 路线）/ reuse a requests Session
    :param engine:    解析引擎 / which engine to use

                      * ``'auto'``（默认）—— 先试纯 HTTP 快路，失败**自动切浏览器**。
                        现代蓝奏云基本都会走到浏览器这条路，所以这是推荐值。
                      * ``'http'``  —— 只用纯 HTTP（快，但现代蓝奏云多数会失败）
                      * ``'browser'`` —— 直接用浏览器（最稳，但慢一些；需要 playwright）

    :param browser:   复用已有的 ``lanzou_browser._Browser`` 实例
                      （批量解析多个链接时用，能省掉反复启动浏览器）
    :param log:       日志回调 ``log(str)``
    :param allow_browser_fallback: **已废弃**，等价于 ``engine='auto'``（兼容旧代码）
    :return:          :class:`LanzouFile`（``.engine`` 标明实际用了哪条路线）
    :raises NotLanzouUrl:     传进来的不是蓝奏云链接
    :raises PasswordRequired: 该分享需要提取码但没提供
    :raises ParseFailed:      两条路线都失败
    """
    share_url = (share_url or '').strip()
    if not is_lanzou_url(share_url):
        raise NotLanzouUrl('这不是蓝奏云分享链接 / not a Lanzou share URL: %r' % share_url)

    # 兼容旧参数：allow_browser_fallback=False ↔ engine='http'
    if allow_browser_fallback is False:
        engine = 'http'
    if engine not in ('auto', 'http', 'browser'):
        raise ValueError("engine 只能是 'auto' / 'http' / 'browser'，收到 %r" % engine)

    errors = []

    if engine in ('auto', 'http'):
        try:
            return _parse_via_http(share_url, pwd, resolve=resolve,
                                   timeout=timeout, session=session, log=log)
        except PasswordRequired:
            # 「需要提取码」是**确定性的结论**，不是「这条路走不通」——
            # 换成浏览器引擎只会白等几十秒，然后报一个更莫名其妙的错。
            raise
        except LanzouError as e:
            errors.append('HTTP: %s' % e)
            if engine == 'http':
                raise

    # 走到这里说明 HTTP 没成，或者本来就用浏览器引擎
    try:
        return _parse_via_browser(share_url, pwd, resolve=resolve,
                                  timeout=timeout, browser=browser, log=log)
    except LanzouError:
        raise
    except Exception as e:                           # noqa: BLE001
        errors.append('浏览器: %s' % e)

    raise ParseFailed('解析失败 / parse failed: %s' % ' | '.join(errors))


# ---------------------------------------------------------------------------
#  跟随重定向拿最终地址 / Follow redirects to the final URL
# ---------------------------------------------------------------------------

def resolve_real_url(middle_url: str,
                     *,
                     timeout: int = DEFAULT_TIMEOUT,
                     session: Optional[requests.Session] = None) -> Optional[str]:
    """
    从蓝奏云中间页跟随重定向，拿到最终的 CDN 下载地址。
    Follow the middle-page redirect chain down to the final CDN URL.

    蓝奏云的 ``dom/file/xxx`` 是**中间页**，直接用浏览器打开会被要求带 Referer；
    真正落地的是 ``*.lanzou*.com/.../文件名.zip`` 这类地址。
    Lanzou's ``dom/file/xxx`` is an intermediate page. The real payload lives on a
    CDN host and the filename is visible in the final URL.

    :return: 最终地址；拿不到返回 None / the final URL, or None
    """
    if not middle_url:
        return None
    sess = session or requests.Session()
    try:
        resp = sess.get(middle_url, headers=_dl_headers(middle_url),
                        timeout=timeout, allow_redirects=True)
    except Exception:                                # noqa: BLE001
        return None

    final = resp.url or ''
    # 情况 A：重定向后已经落到真实文件（URL 里有扩展名、且不再是 /file/? 中间页）
    if final and final != middle_url and '/file/?' not in final:
        if any(ext in final.lower() for ext in
               ('.zip', '.rar', '.7z', '.exe', '.apk', '.tar', '.gz', '.pdf',
                '.mp4', '.mp3', '.iso', '.txt', '.doc', '.docx', '.xls', '.xlsx')):
            return final

    # 情况 B：从返回的 HTML 里再捞一次
    html = resp.text or ''
    patterns = [
        r'(https?://[^"\'>\s]+\.(?:zip|rar|7z|exe|apk|tar\.gz|tgz)[^"\'>\s]*)',
        r'(https?://[^"\'>\s]+/download[^"\'>\s]*)',
        r'href=["\']?(https?://[^"\'>\s]+)["\']?',
    ]
    for pat in patterns:
        for m in re.findall(pat, html, re.I):
            m = m.strip()
            if '/file/?' in m or len(m) < 20:
                continue
            if any(k in m.lower() for k in
                   ('.zip', '.rar', '.7z', '.exe', '.apk', 'download')):
                return m
    return None


# ---------------------------------------------------------------------------
#  下载 / Download
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  CDN 反爬 / 直链探测
# ---------------------------------------------------------------------------

def _cookie_header(cookies) -> str:
    """把 cookie dict 拼成 Cookie 请求头 / build a Cookie header from a dict."""
    if not cookies:
        return ''
    if isinstance(cookies, str):
        return cookies
    try:
        return '; '.join('%s=%s' % (k, v) for k, v in dict(cookies).items())
    except Exception:                                 # noqa: BLE001
        return ''


def solve_cdn_cookies(url: str, *, browser=None, log=None) -> dict:
    """
    借真实浏览器过一次 CDN 的 JS 反爬，返回可给 ``requests`` 用的 cookie。

    直链所在节点（``*.lanrar.com`` 这类）自己也有挑战页，纯 HTTP 拿不到文件。
    这一步是原脚本没做的 —— 原脚本因此只能下到 4KB 的挑战页。
    """
    try:
        from lanzou_browser import solve_cdn_challenge
    except ImportError:                               # pragma: no cover
        return {}
    return solve_cdn_challenge(url, browser=browser, log=log) or {}


def probe_direct_url(url: str,
                     *,
                     timeout: int = DEFAULT_TIMEOUT,
                     session: Optional[requests.Session] = None,
                     cookies=None,
                     browser=None,
                     solve: bool = True,
                     log=None) -> dict:
    """
    探测直链的真实文件名、大小，并确认自己**真的拿到了文件而不是挑战页**。

    Probe a direct link for its real filename and size, and make sure we are
    actually talking to the file rather than to the CDN's anti-bot page.

    :param browser: 已经开着的 ``lanzou_browser._Browser``；传进来就能复用，
                    否则第一次碰到挑战页时会临时起一个（慢几秒）
    :param solve:   碰到挑战页是否自动过反爬 / auto-clear the challenge
    :return: ``{'ok': bool, 'filename': str, 'size': int, 'total': str,
               'cookies': dict, 'message': str}``
    """
    def logm(s):
        if log:
            log(s)

    out = {'ok': False, 'filename': '', 'size': 0, 'total': '',
           'cookies': dict(cookies or {}), 'message': ''}
    if not url:
        out['message'] = '空链接'
        return out

    sess = session or requests.Session()
    for attempt in (1, 2):
        headers = dict(_dl_headers(url))
        ch = _cookie_header(out['cookies'])
        if ch:
            headers['Cookie'] = ch
        try:
            resp = sess.head(url, headers=headers, allow_redirects=True,
                             timeout=timeout)
        except Exception as e:                        # noqa: BLE001
            out['message'] = 'HEAD 失败: %s' % e
            return out

        with resp:
            ct = (resp.headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or resp.headers.get('X-Tengine-Error'):
                # 还是挑战页（或 CDN 拒绝）→ 需要浏览器出马
                if attempt == 1 and solve:
                    logm('      ⚠ 直链节点要求过 CDN 反爬，改用浏览器…')
                    jar = solve_cdn_cookies(url, browser=browser, log=logm)
                    if jar:
                        out['cookies'] = jar
                        continue
                out['message'] = 'CDN 反爬未通过'
                return out

            out['ok'] = True
            try:
                out['size'] = int(resp.headers.get('Content-Length') or 0)
            except Exception:                         # noqa: BLE001
                out['size'] = 0
            out['total'] = _human_size(out['size']) if out['size'] else ''
            name = guess_filename(resp, resp.url or url, fallback='')
            if name:
                out['filename'] = name
            logm('      ✔ 直链可用 · %s%s'
                 % (out['filename'] or '(文件名未知)',
                    ' · ' + out['total'] if out['total'] else ''))
            return out

    return out


def _human_size(n) -> str:
    """把字节数变成人能看的大小 / human-readable size."""
    try:
        n = float(n or 0)
    except Exception:                                 # noqa: BLE001
        return ''
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.2f %s' % (n, unit))
        n /= 1024.0
    return ''


def guess_filename(resp=None, url: str = '', fallback: str = 'download.bin') -> str:
    """
    推断保存文件名：优先 Content-Disposition，其次 URL 末段。
    Work out a filename: prefer Content-Disposition, fall back to the URL tail.
    """
    from urllib.parse import unquote

    cd = ''
    try:
        cd = (resp.headers.get('Content-Disposition') or '') if resp is not None else ''
    except Exception:                                # noqa: BLE001
        cd = ''
    for key in ('filename*=', 'filename='):
        if key in cd:
            part = cd.split(key, 1)[1].split(';')[0].strip().strip('"\'')
            if part.lower().startswith("utf-8''"):
                part = part[7:]
            if part:
                return unquote(part)

    name = unquote(os.path.basename((url or '').split('?')[0]))
    if name and '.' in name:
        return name
    return name or fallback


def download(url: str,
             dest: str,
             *,
             timeout: int = DEFAULT_TIMEOUT,
             chunk_size: int = 64 * 1024,
             progress=None,
             cancel=None,
             session: Optional[requests.Session] = None,
             cookies=None,
             solve_challenge: bool = True,
             log=None) -> str:
    """
    下载直链到本地，自动带上蓝奏云 CDN 需要的 Referer / cookie。
    Download a direct URL, automatically sending the headers Lanzou's CDN wants.

    直接用浏览器打开 ``dom/file/xxx`` 有时会 403，就是因为缺 Referer；
    而**直链节点本身还有一层 JS 反爬**（返回 4KB 的 ``var arg1=...`` 页面），
    这时本函数会自动借浏览器过一次挑战、拿到 cookie 再继续下载。

    Opening ``dom/file/xxx`` in a browser can 403 because the CDN checks the
    Referer. On top of that the download node itself is guarded by a JS
    anti-bot challenge; when we hit it we borrow a real browser to clear it,
    then carry on with plain ``requests``.

    :param url:      ``parse()`` 返回的 ``direct_url`` 或 ``real_url``
    :param dest:     目标文件路径；若传的是**已存在的目录**，自动推断文件名
    :param progress: 进度回调 ``progress(done_bytes, total_bytes)``，
                     ``total_bytes`` 未知时为 0 / progress callback
    :param cancel:   取消回调 ``cancel() -> bool``，返回 True 即中止
    :param cookies:  ``parse()`` 结果里的 ``.cookies``（CDN 反爬 cookie）
    :param solve_challenge: 遇到反爬挑战页时是否自动借浏览器过（默认 True）
    :param log:      日志回调 ``log(str)``
    :return:         实际写入的文件路径 / the path actually written
    :raises DownloadCancelled: 被 cancel 中止
    :raises LanzouError:       网络、反爬或写入失败
    """
    def logm(s):
        if log:
            log(s)

    sess = session or requests.Session()
    jar = dict(cookies or {})

    def _open():
        headers = dict(_dl_headers(url))
        ch = _cookie_header(jar)
        if ch:
            headers['Cookie'] = ch
        try:
            resp = sess.get(url, headers=headers, stream=True,
                            timeout=timeout, allow_redirects=True)
        except Exception as e:                       # noqa: BLE001
            raise LanzouError('请求下载地址失败 / request failed: %s' % e) from None
        if resp.status_code >= 400:
            status = resp.status_code
            resp.close()
            raise LanzouError('下载地址返回 HTTP %s（可能需要 Referer 或链接已过期）'
                              % status)
        return resp

    r = _open()

    # ------------------------------------------------------------------
    #  先嗅一下头几个字节：CDN 反爬挑战页长得就是一段小 HTML
    # ------------------------------------------------------------------
    first = b''
    try:
        for chunk in r.iter_content(chunk_size=chunk_size):
            first = chunk or b''
            break
        is_challenge = looks_js_challenge(first) or looks_js_challenge(
            r.headers.get('Content-Type', '') + ' ' + first[:2048].decode('utf-8', 'ignore'))
    except Exception:                                # noqa: BLE001
        is_challenge = False

    if is_challenge:
        r.close()
        if not solve_challenge:
            raise LanzouError(
                '直链节点要求过 CDN 的 JS 反爬，拿到的是挑战页而不是文件。\n'
                'The download node served its JS anti-bot challenge instead of '
                'the file. 把 solve_challenge 设为 True（默认）即可自动处理。')
        logm('  ⚠ 直链节点要求过 CDN 反爬，借用浏览器…')
        jar = solve_cdn_cookies(url, log=logm)
        if not jar:
            raise LanzouError('过 CDN 反爬失败，无法下载 / could not clear the '
                              'CDN anti-bot challenge')
        logm('  ✔ 反爬已过，继续下载')
        r = _open()
        first = b''
        for chunk in r.iter_content(chunk_size=chunk_size):
            first = chunk or b''
            break
        if looks_js_challenge(first):
            r.close()
            raise LanzouError('过了反爬仍然拿到挑战页，链接可能已失效 / still '
                              'getting the challenge page after solving it')

    with r:
        try:
            total = int(r.headers.get('Content-Length') or 0)
        except Exception:                            # noqa: BLE001
            total = 0

        path = dest
        if os.path.isdir(dest):
            path = os.path.join(dest, guess_filename(r, r.url or url))
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

        done = 0
        try:
            with open(path, 'wb') as fh:
                # 前面嗅探挑战页时已经读走了第一块，先补写回去
                if first:
                    fh.write(first)
                    done += len(first)
                    if progress:
                        try:
                            progress(done, total)
                        except Exception:            # noqa: BLE001
                            pass
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if cancel and cancel():
                        raise DownloadCancelled('下载已取消 / cancelled')
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        try:
                            progress(done, total)
                        except Exception:            # noqa: BLE001
                            pass
        except DownloadCancelled:
            try:                                     # 取消时清理半截文件
                if os.path.exists(path):
                    os.remove(path)
            except Exception:                        # noqa: BLE001
                pass
            raise
        except Exception as e:                       # noqa: BLE001
            raise LanzouError('写入文件失败 / write failed: %s' % e) from None

    return path


# ---------------------------------------------------------------------------
#  自测 / `python lanzou_direct.py` self-test (offline)
# ---------------------------------------------------------------------------

_FAKE_HTML = """
<html><body>
<script>
var sign = 'a1b2c3d4e5f60718293a4b5c6d7e8f90';
var signs = 'sig-xyz';
var ves = 2;
url: '/ajaxm.php',
</script>
<input name="pwd" id="pwd">
<a href="/file/1234567">x</a>
</body></html>
"""


def _self_test():
    print('--- 离线提取自测 / offline extraction self-test ---')
    assert extract_sign(_FAKE_HTML) == 'a1b2c3d4e5f60718293a4b5c6d7e8f90', extract_sign(_FAKE_HTML)
    assert extract_signs(_FAKE_HTML) == 'sig-xyz'
    assert extract_ves(_FAKE_HTML) == '2'
    assert extract_ajax_path(_FAKE_HTML) == '/ajaxm.php'
    assert extract_fid(_FAKE_HTML) == '1234567'
    assert looks_password_protected(_FAKE_HTML) is True

    assert is_lanzou_url('https://www.lanzou.com/iabcdef') is True
    assert is_lanzou_url('https://wwx.lanzoux.com/abcdef') is True
    assert is_lanzou_url('https://lanzoui.com/abcdef') is True
    assert is_lanzou_url('https://example.com/x') is False
    assert is_lanzou_url('not a url') is False

    assert _abs_url('//cdn.x/a.js', 'https://a.b') == 'https://cdn.x/a.js'
    assert _abs_url('/file/x', 'https://a.b') == 'https://a.b/file/x'
    assert _abs_url('https://z/y', 'https://a.b') == 'https://z/y'

    print('全部断言通过 / all assertions OK')
    print()
    print('在线用法 / online usage:')
    print('    python cli.py "https://www.lanzou.com/xxxxx" --pwd 1234')


if __name__ == '__main__':
    _self_test()
