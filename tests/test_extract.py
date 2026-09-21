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
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lanzou_direct import (                       # noqa: E402
    LanzouFile, NotLanzouUrl, PasswordRequired, ParseFailed,
    is_lanzou_url, parse, resolve_real_url,
    extract_sign, extract_signs, extract_ves, extract_ajax_path,
    extract_fid, extract_iframe, looks_password_protected,
    _abs_url, _base_of,
)

SIGN = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
FID = '1234567'


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

    def test_full_flow_with_iframe(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=SHARE_PAGE, url=SHARE_URL),
             'content.lanzou.com': FakeResponse(text=IFRAME_PAGE,
                                                url='https://content.lanzou.com/abc123')},
            post_json=AJAX_OK)

        f = parse(SHARE_URL, session=sess)

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
        f = parse(SHARE_URL, session=sess)
        self.assertTrue(f.ok)
        self.assertEqual(f.direct_url, 'https://vip.lanzou.com/file/i9abc123')
        # 没有 iframe 就只 GET 一次
        self.assertEqual([c[0] for c in sess.calls].count('GET'), 1)

    def test_ajax_url_is_absolute_when_dom_absent(self):
        """dom 缺失时不能拼出 'None/file/...' 这种垃圾"""
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=DIRECT_PAGE, url=SHARE_URL)},
            post_json={"zt": 1, "dom": "", "url": "https://cdn.example.com/a.zip"})
        f = parse(SHARE_URL, session=sess)
        self.assertEqual(f.direct_url, 'https://cdn.example.com/a.zip')

    def test_zt_not_1_raises(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=DIRECT_PAGE, url=SHARE_URL)},
            post_json={"zt": 0, "inf": "文件不存在"})
        with self.assertRaises(ParseFailed):
            parse(SHARE_URL, session=sess)

    def test_no_sign_raises_parse_failed(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text='<html>nothing</html>', url=SHARE_URL)},
            post_json=AJAX_OK)
        with self.assertRaises(ParseFailed):
            parse(SHARE_URL, session=sess)

    def test_password_required(self):
        page = '<html><body><input name="pwd"> 请输入提取码</body></html>'
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=page, url=SHARE_URL)},
            post_json=AJAX_OK)
        with self.assertRaises(PasswordRequired):
            parse(SHARE_URL, session=sess)

    def test_password_is_sent_to_ajax(self):
        sess = FakeSession(
            {'www.lanzou.com': FakeResponse(text=DIRECT_PAGE, url=SHARE_URL)},
            post_json=AJAX_OK)
        parse(SHARE_URL, pwd='9527', session=sess)
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
        parse(SHARE_URL, session=sess)
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
