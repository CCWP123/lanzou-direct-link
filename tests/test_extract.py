#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lanzou-direct-link / tests/test_extract.py
==========================================
离线测试：**不发任何网络请求**。
Offline tests: no network requests are made.

覆盖两类：
  1. 纯函数 —— URL 校验、HTML 提取、相对地址补全
  2. 端到端 —— 用 mock session 完整跑通
     「分享页 → iframe → 提取 sign → 调 ajaxm.php → 拼出直链」

运行 / Run:
    python tests/test_extract.py
    python -m unittest discover -s tests -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lanzou_direct                              # noqa: E402
from lanzou_direct import (                       # noqa: E402
    LanzouFile, LanzouError, NotLanzouUrl, PasswordRequired, ParseFailed,
    is_lanzou_url, parse, resolve_real_url,
    extract_sign, extract_signs, extract_ves, extract_ajax_path,
    extract_fid, extract_iframe, looks_password_protected, looks_js_challenge,
    probe_direct_url, guess_filename, download, _human_size,
    _abs_url, _base_of,
)

SIGN = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
FID = '1234567'
DIRECT_URL = 'https://developer4.lanrar.com/file/?UzUFOwo7AzID'

# 实测的 CDN 反爬挑战页特征
CHALLENGE_HTML = ("<html><script>var arg1='6A8408035F417F52C0708BC196A0B23D"
                  "32AF1234';eval('x')</script></html>")


# ---------------------------------------------------------------------------
#  mock 网络层 / mock network layer
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text='', url='', json_data=None, status=200, headers=None):
        self.text = text
        self.url = url
        self._json = json_data
        self.status_code = status
        self.headers = headers or {}
        self.encoding = 'utf-8'

    def json(self):
        if self._json is None:
            raise ValueError('no json')
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError('HTTP %s' % self.status_code)


class FakeSession:
    """
    假的 requests.Session：按 URL 返回预置内容，并记录所有请求以便断言。
    A fake requests.Session that serves canned pages and records every call.
    """

    def __init__(self, routes, post_json):
        self.routes = routes          # url -> FakeResponse
        self.post_json = post_json    # ajax 接口返回的 JSON
        self.headers = {}
        self.calls = []

    def get(self, url, timeout=None, allow_redirects=True, headers=None):
        self.calls.append(('GET', url, headers))
        for key, resp in self.routes.items():
            if key in url:
                return resp
        return FakeResponse(text='<html></html>', url=url, status=404)

    def post(self, url, data=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(('POST', url, data))
        if self.post_json is None:
            raise RuntimeError('connection refused')
        return FakeResponse(url=url, json_data=self.post_json)


SHARE_PAGE = """<html><head><title>蓝奏云</title></head>
<body>
  <iframe src="//content.lanzou.com/abc123"></iframe>
</body></html>
"""

IFRAME_PAGE = """<html><body>
<script>
  var sign = '%s';
  var ves = 2;
  url: '/ajaxm.php',
</script>
<a href="/file/%s">下载</a>
</body></html>
""" % (SIGN, FID)

DIRECT_PAGE = """<html><body>
<script>
  var sign = '%s';
  var signs = 'sg-1';
</script>
</body></html>
""" % SIGN

AJAX_OK = {"zt": 1, "dom": "https://vip.lanzou.com", "url": "i9abc123",
           "inf": "示例文件.zip", "size": "12.3 M"}

SHARE_URL = 'https://www.lanzou.com/iabcdefg'


# ---------------------------------------------------------------------------
#  1. URL 校验 / URL validation
# ---------------------------------------------------------------------------

class TestIsLanzouUrl(unittest.TestCase):

    def test_common_domains(self):
        for u in ('https://www.lanzou.com/iabcdef',
                  'https://wwx.lanzoux.com/abcdef',
                  'https://lanzoui.com/abcdef',
                  'https://lanzoub.com/abcdef',
                  'https://lanzouw.com/abcdef',
                  'http://lanzn.com/abcdef',
                  'https://pan.lanzou.com/abcdef'):
            self.assertTrue(is_lanzou_url(u), u)

    def test_rejects_others(self):
        for u in ('https://example.com/x',
                  'https://lanzou.example.com.evil.com/x',
                  'ftp://lanzou.com/x',
                  '', None, 'not a url', 123):
            self.assertFalse(is_lanzou_url(u), repr(u))

    def test_strips_userinfo(self):
        self.assertTrue(is_lanzou_url('https://user:pass@www.lanzou.com/x'))


# ---------------------------------------------------------------------------
#  2. HTML 提取 / HTML extraction
# ---------------------------------------------------------------------------

class TestExtractSign(unittest.TestCase):

    def test_var_single_quotes(self):
        self.assertEqual(extract_sign("var sign = '%s';" % SIGN), SIGN)

    def test_var_double_quotes(self):
        self.assertEqual(extract_sign('var sign = "%s";' % SIGN), SIGN)

    def test_object_literal(self):
        self.assertEqual(extract_sign('{"sign":"%s"}' % SIGN), SIGN)

    def test_signn_variant(self):
        self.assertEqual(extract_sign("var signn = '%s';" % SIGN), SIGN)

    def test_fallback_hex_token(self):
        tok = 'f' * 32
        self.assertEqual(extract_sign("var whatever = '%s';" % tok), tok)

    def test_missing_returns_empty(self):
        self.assertEqual(extract_sign('<html>no sign here</html>'), '')
        self.assertEqual(extract_sign(''), '')

    def test_does_not_confuse_signs(self):
        """var signs=... 不能被当成 sign"""
        html = "var signs = 'sg-1';\nvar sign = '%s';" % SIGN
        self.assertEqual(extract_sign(html), SIGN)


class TestExtractSignsVesFid(unittest.TestCase):

    def test_signs(self):
        self.assertEqual(extract_signs("var signs = 'sg-1';"), 'sg-1')
        self.assertEqual(extract_signs('<html></html>'), '')

    def test_ves_quoted(self):
        self.assertEqual(extract_ves("var ves = '2';"), '2')

    def test_ves_unquoted(self):
        """
        回归：原脚本的 [^'"]+ 贪婪匹配会把 'var ves = 2;' 后面整段 JS 都吞进去。
        这里必须只拿到 '2'。
        """
        html = "var ves = 2;\nvar other = 'zzz';"
        self.assertEqual(extract_ves(html), '2')

    def test_ves_default(self):
        self.assertEqual(extract_ves('<html></html>'), '1')
        self.assertEqual(extract_ves(''), '1')

    def test_ajax_path(self):
        self.assertEqual(extract_ajax_path("url: '/ajaxm.php',"), '/ajaxm.php')
        self.assertEqual(extract_ajax_path("ajaxurl = '/file/ajax.php'"), '/file/ajax.php')
        self.assertEqual(extract_ajax_path('<html></html>'), '')

    def test_fid_from_input(self):
        self.assertEqual(extract_fid('file = "%s"' % FID), FID)

    def test_fid_from_anchor(self):
        self.assertEqual(extract_fid('<a href="/file/%s">x</a>' % FID), FID)

    def test_fid_missing(self):
        self.assertEqual(extract_fid('<html></html>'), '')


class TestIframeAndUrl(unittest.TestCase):

    def test_protocol_relative(self):
        self.assertEqual(
            extract_iframe('<iframe src="//c.lanzou.com/x"></iframe>', 'https://a.b'),
            'https://c.lanzou.com/x')

    def test_absolute_path(self):
        self.assertEqual(
            extract_iframe('<iframe src="/p/x"></iframe>', 'https://a.b'),
            'https://a.b/p/x')

    def test_none(self):
        self.assertEqual(extract_iframe('<html></html>', 'https://a.b'), '')

    def test_abs_url(self):
        self.assertEqual(_abs_url('//c/x', 'https://a.b'), 'https://c/x')
        self.assertEqual(_abs_url('/p', 'https://a.b'), 'https://a.b/p')
        self.assertEqual(_abs_url('https://z/y', 'https://a.b'), 'https://z/y')
        self.assertEqual(_abs_url('', 'https://a.b'), '')

    def test_base_of(self):
        self.assertEqual(_base_of('https://a.b/c/d?e=1'), 'https://a.b')


class TestPasswordHint(unittest.TestCase):

    def test_detects(self):
        self.assertTrue(looks_password_protected('<input name="pwd">'))
        self.assertTrue(looks_password_protected('请输入提取码'))
        self.assertTrue(looks_password_protected('<input id="pwd" type="password">'))

    def test_not_detected(self):
        self.assertFalse(looks_password_protected('<html>正常页面</html>'))


# ---------------------------------------------------------------------------
#  3. 端到端（mock）/ end-to-end with a mocked session
# ---------------------------------------------------------------------------

class TestParseEndToEnd(unittest.TestCase):
    """
    全部走 mock session，所以一律 `engine='http'` 锁死纯 HTTP 那条路 ——
    否则 engine='auto' 在 HTTP 失败时会去启动真浏览器，单测就既慢又依赖环境。
    All mocked, so pin engine='http': otherwise engine='auto' would launch a
    real browser whenever the fake HTTP page fails, which is slow and flaky.
    """

    def test_full_flow_with_iframe(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=SHARE_PAGE, url=SHARE_URL),
             'content.lanzou.com': FakeResponse(text=IFRAME_PAGE,
                                                url='https://content.lanzou.com/abc123')},
            post_json=AJAX_OK)

        f = parse(SHARE_URL, session=sess, engine='http')

        self.assertTrue(f.ok)
        self.assertEqual(f.direct_url, 'https://vip.lanzou.com/file/i9abc123')
        self.assertEqual(f.name, '示例文件.zip')
        self.assertEqual(f.size, '12.3 M')
        self.assertEqual(f.share_url, SHARE_URL)

        # 应当先 GET 分享页、再 GET iframe、最后 POST ajax
        kinds = [c[0] for c in sess.calls]
        self.assertEqual(kinds[:2], ['GET', 'GET'])
        self.assertIn('POST', kinds)
        self.assertTrue(any('content.lanzou.com' in c[1] for c in sess.calls if c[0] == 'GET'))

    def test_full_flow_without_iframe(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=DIRECT_PAGE, url=SHARE_URL)},
            post_json=AJAX_OK)
        f = parse(SHARE_URL, session=sess, engine='http')
        self.assertTrue(f.ok)
        self.assertEqual(f.direct_url, 'https://vip.lanzou.com/file/i9abc123')
        # 没有 iframe 就只 GET 一次
        self.assertEqual([c[0] for c in sess.calls].count('GET'), 1)

    def test_ajax_url_is_absolute_when_dom_absent(self):
        """dom 缺失时不能拼出 'None/file/...' 这种垃圾"""
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=DIRECT_PAGE, url=SHARE_URL)},
            post_json={"zt": 1, "dom": "", "url": "https://cdn.example.com/a.zip"})
        f = parse(SHARE_URL, session=sess, engine='http')
        self.assertEqual(f.direct_url, 'https://cdn.example.com/a.zip')

    def test_zt_not_1_raises(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=DIRECT_PAGE, url=SHARE_URL)},
            post_json={"zt": 0, "inf": "文件不存在"})
        with self.assertRaises(ParseFailed):
            parse(SHARE_URL, session=sess, engine='http')

    def test_no_sign_raises_parse_failed(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text='<html>nothing</html>', url=SHARE_URL)},
            post_json=AJAX_OK)
        with self.assertRaises(ParseFailed):
            parse(SHARE_URL, session=sess, engine='http')

    def test_password_required(self):
        page = '<html><body><input name="pwd"> 请输入提取码</body></html>'
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=page, url=SHARE_URL)},
            post_json=AJAX_OK)
        with self.assertRaises(PasswordRequired):
            parse(SHARE_URL, session=sess, engine='http')

    def test_password_is_sent_to_ajax(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=DIRECT_PAGE, url=SHARE_URL)},
            post_json=AJAX_OK)
        parse(SHARE_URL, pwd='9527', session=sess, engine='http')
        post = [c for c in sess.calls if c[0] == 'POST'][0]
        self.assertEqual(post[2].get('p'), '9527')
        self.assertEqual(post[2].get('action'), 'downprocess')
        self.assertEqual(post[2].get('sign'), SIGN)

    def test_non_lanzou_url_raises(self):
        with self.assertRaises(NotLanzouUrl):
            parse('https://example.com/x')

    def test_fid_is_appended_to_ajax_url(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=IFRAME_PAGE, url=SHARE_URL),
             'content.lanzou.com': FakeResponse(text=IFRAME_PAGE,
                                                url='https://content.lanzou.com/x')},
            post_json=AJAX_OK)
        parse(SHARE_URL, session=sess, engine='http')
        post_url = [c[1] for c in sess.calls if c[0] == 'POST'][0]
        self.assertIn('file=%s' % FID, post_url)


class TestLanzouFile(unittest.TestCase):

    def test_ok_property(self):
        self.assertFalse(LanzouFile().ok)
        self.assertTrue(LanzouFile(direct_url='https://x/y').ok)

    def test_to_dict_and_json(self):
        f = LanzouFile(direct_url='https://x/y', name='a.zip', size='1M')
        d = f.to_dict()
        self.assertEqual(d['direct_url'], 'https://x/y')
        self.assertEqual(d['name'], 'a.zip')
        self.assertIn('a.zip', f.to_json())


class TestResolveRealUrl(unittest.TestCase):

    def test_none_on_empty(self):
        self.assertIsNone(resolve_real_url(''))

    def test_none_on_failure(self):
        class Boom:
            headers = {}
            def get(self, *a, **k):
                raise RuntimeError('boom')
        self.assertIsNone(resolve_real_url('https://x/y', session=Boom()))


class TestCdnChallenge(unittest.TestCase):
    """
    CDN 反爬（``var arg1='...'`` 挑战页）相关行为。

    这是原脚本 `蓝奏云网盘.py` 缺失的一环：直链节点自己也有挑战页，
    纯 HTTP 下到的其实是 4KB 的脚本，不是文件。
    """

    # 真实抓到的挑战页开头（ESA / 阿里云「5 秒盾」）
    CHALLENGE = ("<html><script>var arg1='B47A77C762D5455494977B81D74ABF74"
                 "32A62F67';function setCookie(){}eval('x')</script></html>")

    def test_looks_js_challenge_detects(self):
        self.assertTrue(looks_js_challenge(self.CHALLENGE))

    def test_looks_js_challenge_bytes(self):
        self.assertTrue(looks_js_challenge(self.CHALLENGE.encode('utf-8')))

    def test_looks_js_challenge_rejects_normal(self):
        self.assertFalse(looks_js_challenge('<html><body>普通页面</body></html>'))
        self.assertFalse(looks_js_challenge(''))
        self.assertFalse(looks_js_challenge(None))
        # 短随机串不算挑战（长度不够 20）
        self.assertFalse(looks_js_challenge("var arg1='ABCD'"))

    def test_browser_module_agrees(self):
        """lanzou_browser.is_cdn_challenge 与 lanzou_direct.looks_js_challenge 同源。"""
        import lanzou_browser
        self.assertTrue(lanzou_browser.is_cdn_challenge(self.CHALLENGE))
        self.assertFalse(lanzou_browser.is_cdn_challenge('<html>hi</html>'))
        self.assertEqual(lanzou_browser.CHALLENGE_COOKIE, 'acw_sc__v2')

    def test_human_size(self):
        self.assertEqual(_human_size(0), '0 B')
        self.assertEqual(_human_size(512), '512 B')
        self.assertEqual(_human_size(1024), '1.00 KB')
        self.assertEqual(_human_size(30127389), '28.73 MB')
        self.assertEqual(_human_size(None), '0 B')      # None 视作 0

    def test_probe_omits_total_when_length_unknown(self):
        """Content-Length 缺失时 total 要是空串，而不是骗人的 '0 B'。"""
        class R:
            status_code = 200
            url = DIRECT_URL
            headers = {'Content-Type': 'application/octet-stream'}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Sess:
            def head(self, *a, **k):
                return R()

        out = probe_direct_url(DIRECT_URL, session=Sess())
        self.assertTrue(out['ok'])
        self.assertEqual(out['size'], 0)
        self.assertEqual(out['total'], '')


class TestGuessFilename(unittest.TestCase):

    def _resp(self, cd):
        class R:
            headers = {'Content-Disposition': cd} if cd else {}
        return R()

    def test_rfc5987(self):
        """实测蓝奏云直链返回的就是这种：filename*=UTF-8''%E8%B6%85..."""
        cd = ("attachment; filename*=UTF-8''%E8%B6%85%E8%8B%B1%E6%B1%89%E5%8C%96"
              "%E6%8A%80%E8%83%BD%E5%9B%BE%E7%94%9F4.37.exe")
        self.assertEqual(guess_filename(self._resp(cd)),
                         '超英汉化技能图生4.37.exe')

    def test_plain_quoted(self):
        self.assertEqual(guess_filename(self._resp('attachment; filename="a.zip"')),
                         'a.zip')

    def test_falls_back_to_url_tail(self):
        self.assertEqual(guess_filename(None, 'https://x/y/名字.7z'), '名字.7z')

    def test_empty_fallback_allowed(self):
        """probe 里用 fallback='' 表示「没名字就别编」——不要退回 download.bin。"""
        self.assertEqual(guess_filename(None, 'https://x/y/file/?abc', fallback=''), '')


class TestProbeDirectUrl(unittest.TestCase):
    """
    探测直链：第一次碰到挑战页 → 过反爬 → 第二次拿到真文件头。
    """

    def _head_resp(self, ct, cd=None, length=None, err=None):
        headers = {'Content-Type': ct}
        if cd:
            headers['Content-Disposition'] = cd
        if length is not None:
            headers['Content-Length'] = str(length)
        if err:
            headers['X-Tengine-Error'] = err

        class R:
            status_code = 200

            def __init__(self):
                self.headers = headers
                self.url = DIRECT_URL

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()

    def test_challenge_then_success(self):
        """第 1 次 HEAD → 挑战页；过完反爬第 2 次 HEAD → 真文件。"""
        calls = {'n': 0}

        class Sess:
            def head(self, url, headers=None, allow_redirects=True, timeout=None):
                calls['n'] += 1
                if calls['n'] == 1:
                    self.last = headers
                    return outer._head_resp('text/html; charset=utf-8')
                outer.seen_cookie = (headers or {}).get('Cookie', '')
                return outer._head_resp(
                    'application/octet-stream',
                    "attachment; filename*=UTF-8''%E6%B5%8B%E8%AF%95.zip",
                    length=30127389)

        outer = self
        solved = []

        def fake_solve(url, browser=None, log=None):
            solved.append(url)
            return {'acw_sc__v2': 'deadbeef'}

        with mock.patch.object(lanzou_direct, 'solve_cdn_cookies', fake_solve):
            out = probe_direct_url(DIRECT_URL, session=Sess())

        self.assertTrue(out['ok'])
        self.assertEqual(out['filename'], '测试.zip')
        self.assertEqual(out['size'], 30127389)
        self.assertEqual(out['total'], '28.73 MB')
        self.assertEqual(out['cookies'], {'acw_sc__v2': 'deadbeef'})
        self.assertEqual(len(solved), 1, '反爬只应尝试一次')
        self.assertIn('acw_sc__v2=deadbeef', outer.seen_cookie)

    def test_solve_disabled_reports_failure(self):
        class Sess:
            def head(self, url, headers=None, allow_redirects=True, timeout=None):
                return self_inner._head_resp('text/html', err='denied by http_custom')

        self_inner = self
        out = probe_direct_url(DIRECT_URL, session=Sess(), solve=False)
        self.assertFalse(out['ok'])
        self.assertEqual(out['message'], 'CDN 反爬未通过')

    def test_empty_url(self):
        self.assertFalse(probe_direct_url('')['ok'])


class TestDownloadChallenge(unittest.TestCase):
    """download() 嗅到挑战页时，应当自动过反爬再下一次，并且不丢头一个字节。"""

    def _stream(self, body, ct='application/octet-stream', cd=None, length=None):
        """
        模拟 requests 的流式响应 —— 关键点是 `iter_content` 必须**可恢复**：
        真实 requests 第二次调用是从上次读完的位置继续，而不是从头再来。
        如果这里从头再 yield，就会掩盖「首块被写两遍」这类 bug。
        """
        class R:
            status_code = 200

            def __init__(self):
                self.headers = {'Content-Type': ct}
                if cd:
                    self.headers['Content-Disposition'] = cd
                if length is not None:
                    self.headers['Content-Length'] = str(length)
                self.url = DIRECT_URL
                self.closed = False
                self._pos = 0

            def iter_content(self, chunk_size=65536):
                while self._pos < len(body):
                    chunk = body[self._pos:self._pos + chunk_size]
                    self._pos += len(chunk)
                    yield chunk

            def close(self):
                self.closed = True

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()
                return False
        return R()

    def test_auto_solves_then_writes_full_file(self):
        payload = b'MZ' + b'\x00' * 5000
        calls = {'n': 0}

        class Sess:
            def get(self, url, headers=None, stream=False,
                    timeout=None, allow_redirects=True):
                calls['n'] += 1
                if calls['n'] == 1:
                    return outer._stream(CHALLENGE_HTML.encode('utf-8'),
                                         ct='text/html; charset=utf-8')
                return outer._stream(
                    payload, cd="attachment; filename*=UTF-8''%E6%B5%8B%E8%AF%95.bin",
                    length=len(payload))

        outer = self

        def fake_solve(url, browser=None, log=None):
            return {'acw_sc__v2': 'x'}

        tmp = tempfile.mkdtemp()
        try:
            with mock.patch.object(lanzou_direct, 'solve_cdn_cookies', fake_solve):
                path = download(DIRECT_URL, tmp, session=Sess(), solve_challenge=True)
            self.assertEqual(os.path.basename(path), '测试.bin')
            with open(path, 'rb') as fh:
                self.assertEqual(fh.read(), payload, '不能丢掉被嗅探走的第一块')
            self.assertEqual(calls['n'], 2, '应当重下一次')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_solve_disabled_raises_clear_error(self):
        class Sess:
            def get(self, url, headers=None, stream=False,
                    timeout=None, allow_redirects=True):
                return self_inner._stream(CHALLENGE_HTML.encode('utf-8'),
                                          ct='text/html; charset=utf-8')

        self_inner = self
        tmp = tempfile.mkdtemp()
        try:
            with self.assertRaises(LanzouError) as cm:
                download(DIRECT_URL, tmp, session=Sess(), solve_challenge=False)
            self.assertIn('反爬', str(cm.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cookies_are_sent(self):
        """parse() 存下来的 cookie 必须真的发出去。"""
        sent = {}

        class Sess:
            def get(self, url, headers=None, stream=False,
                    timeout=None, allow_redirects=True):
                sent['cookie'] = (headers or {}).get('Cookie', '')
                return outer._stream(b'PK\x03\x04hello', cd='attachment; filename="a.zip"')

        outer = self
        tmp = tempfile.mkdtemp()
        try:
            path = download(DIRECT_URL, tmp, session=Sess(),
                            cookies={'acw_sc__v2': 'abc', 'down_ip': '1'})
            self.assertEqual(os.path.basename(path), 'a.zip')
            self.assertIn('acw_sc__v2=abc', sent['cookie'])
            self.assertIn('down_ip=1', sent['cookie'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
