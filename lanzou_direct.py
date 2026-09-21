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
    requests      —— 必需 / required
    playwright    —— 可选，见 browser_fallback.py / optional, see that module

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
    'parse',
    'resolve_real_url',
    'download',
    'guess_filename',
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
    :ivar raw:        蓝奏云 ajax 接口的原始 JSON 响应
    """
    direct_url: str = ''
    middle_url: str = ''
    name: str = ''
    size: str = ''
    share_url: str = ''
    real_url: str = ''
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.direct_url)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


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
    """下载中间页用的头（必须带 Referer，否则蓝奏云拒绝）/ headers for the middle page."""
    return {
        "User-Agent": _ua(),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": url or "https://www.lanzou.com/",
    }


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


def parse(share_url: str,
          pwd: str = '',
          *,
          resolve: bool = False,
          timeout: int = DEFAULT_TIMEOUT,
          session: Optional[requests.Session] = None,
          allow_browser_fallback: bool = False) -> LanzouFile:
    """
    把蓝奏云分享链接解析成下载直链。
    Resolve a Lanzou share link into a direct download URL.

    :param share_url: 蓝奏云分享链接，如 ``https://www.lanzou.com/xxxxx``
    :param pwd:       提取码（没有就留空）/ extraction code, if any
    :param resolve:   是否再跟随重定向拿最终 CDN 地址（多一次请求）
                      also follow redirects to obtain the final CDN URL
    :param timeout:   单次请求超时秒数 / per-request timeout
    :param session:   复用已有 Session（想自己控制 cookie 时用）
    :param allow_browser_fallback:
                      纯 requests 失败时，是否尝试 Playwright 无头浏览器兜底
                      （需要额外装 playwright）/ fall back to a headless browser
    :return:          :class:`LanzouFile`
    :raises NotLanzouUrl:     传进来的不是蓝奏云链接
    :raises PasswordRequired: 该分享需要提取码但没提供
    :raises ParseFailed:      所有方式都失败
    """
    share_url = (share_url or '').strip()
    if not is_lanzou_url(share_url):
        raise NotLanzouUrl('这不是蓝奏云分享链接 / not a Lanzou share URL: %r' % share_url)

    sess = session or requests.Session()
    sess.headers.update(_page_headers())

    # ---- ① 取分享页 ----
    r = sess.get(share_url, timeout=timeout, allow_redirects=True)
    r.encoding = 'utf-8'
    html = r.text
    referer = r.url
    base = _base_of(referer)

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
        if allow_browser_fallback:
            try:
                from browser_fallback import parse_with_browser
                return parse_with_browser(share_url, pwd=pwd, resolve=resolve,
                                          timeout=timeout)
            except Exception:                        # noqa: BLE001
                pass
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

    result = LanzouFile(
        direct_url=out['direct_url'],
        middle_url=out['direct_url'],
        name=out['name'],
        size=out['size'],
        share_url=share_url,
        raw=out['raw'],
    )

    if resolve:
        real = resolve_real_url(result.direct_url, timeout=timeout, session=sess)
        if real:
            result.real_url = real

    return result


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
             session: Optional[requests.Session] = None) -> str:
    """
    下载直链到本地，自动带上蓝奏云 CDN 需要的 Referer。
    Download a direct URL, automatically sending the Referer Lanzou's CDN wants.

    直接用浏览器打开 ``dom/file/xxx`` 有时会 403，就是因为缺 Referer；
    走这个函数则一定带上正确的请求头。
    Opening ``dom/file/xxx`` in a browser can 403 because the CDN checks the
    Referer. This function always sends the right headers.

    :param url:      ``parse()`` 返回的 ``direct_url`` 或 ``real_url``
    :param dest:     目标文件路径；若传的是**已存在的目录**，自动推断文件名
    :param progress: 进度回调 ``progress(done_bytes, total_bytes)``，
                     ``total_bytes`` 未知时为 0 / progress callback
    :param cancel:   取消回调 ``cancel() -> bool``，返回 True 即中止
    :return:         实际写入的文件路径 / the path actually written
    :raises DownloadCancelled: 被 cancel 中止
    :raises LanzouError:       网络或写入失败
    """
    sess = session or requests.Session()
    try:
        r = sess.get(url, headers=_dl_headers(url), stream=True,
                     timeout=timeout, allow_redirects=True)
    except Exception as e:                           # noqa: BLE001
        raise LanzouError('请求下载地址失败 / request failed: %s' % e) from None

    with r:
        if r.status_code >= 400:
            raise LanzouError('下载地址返回 HTTP %s（可能需要 Referer 或链接已过期）'
                              % r.status_code)

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
