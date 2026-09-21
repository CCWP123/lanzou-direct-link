#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lanzou-direct-link / examples/quickstart.py
===========================================
最小可用示例：三行拿到浏览器下载直链。
Minimal example: get a browser-ready direct link in three lines.

运行 / Run:
    python examples/quickstart.py "https://www.lanzou.com/xxxxx"
    python examples/quickstart.py "https://www.lanzou.com/xxxxx" 1234
    python examples/quickstart.py "https://www.lanzou.com/xxxxx" "" .    # 顺带真下载
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lanzou_direct import (                        # noqa: E402
    parse, resolve_real_url, download, probe_direct_url, LanzouFile,
)


def demo_simple(url: str, pwd: str = ''):
    """最简用法 / the simplest possible usage."""
    print('=== 最简用法 / simple ===')
    f = parse(url, pwd=pwd)
    print('文件名 :', f.name)
    print('大小   :', f.size)
    print('直链   :', f.direct_url)
    print('引擎   :', f.engine)
    return f


def demo_resolve(url: str, pwd: str = ''):
    """再跟一层重定向拿最终 CDN 地址 / follow redirects to the final URL."""
    print()
    print('=== 跟随重定向 / resolve ===')
    f = parse(url, pwd=pwd, resolve=True)
    print('中间页 :', f.middle_url)
    print('最终页 :', f.real_url or '(未解析到，可能本身已是最终地址)')
    return f


def demo_json(url: str, pwd: str = ''):
    """结构化输出（方便喂给别的程序）/ structured output."""
    print()
    print('=== JSON 输出 / as JSON ===')
    f = parse(url, pwd=pwd)
    print(f.to_json())
    return f


def demo_download(f: LanzouFile, dest: str = '.'):
    """
    下载 —— 注意 `cookies`。

    蓝奏云的下载节点有 JS 反爬挑战，`parse()` 已经顺手过掉并把 cookie 存在
    `f.cookies` 里；把它传给 `download()` 就不用再过一次。
    其实不传也行：`download()` 自己嗅到挑战页会补过一遍，只是多花几秒。
    """
    print()
    print('=== 下载 / download ===')
    print('已备好的反爬 cookie:', ', '.join(sorted(f.cookies)) or '(无)')

    info = probe_direct_url(f.direct_url, cookies=f.cookies)
    if info['ok']:
        print('探测结果: %s · %s' % (info['filename'], info['total']))
    else:
        print('探测未通过:', info['message'])

    def on_progress(done, total):
        if total:
            sys.stderr.write('\r  %5.1f%%  %.1f/%.1f MB'
                             % (done * 100.0 / total,
                                done / 1048576.0, total / 1048576.0))
            sys.stderr.flush()

    try:
        path = download(f.direct_url, dest, cookies=f.cookies,
                        progress=on_progress)
        sys.stderr.write('\n')
        print('已保存:', path)
    except Exception as e:                          # noqa: BLE001
        sys.stderr.write('\n')
        print('下载失败:', e)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print('这里给一个离线演示（不走网络）/ offline demo below:')
        demo_offline()
        return 0

    url = sys.argv[1]
    pwd = sys.argv[2] if len(sys.argv) > 2 else ''

    try:
        f = demo_simple(url, pwd)
        demo_resolve(url, pwd)
        demo_json(url, pwd)
        if len(sys.argv) > 3:
            # 第四个参数给了目录才真的下载，免得默认示例就拉几十 MB
            demo_download(f, sys.argv[3])
        else:
            print()
            print('（加一个目录参数就会真的下载，例如：')
            print('  python examples/quickstart.py "%s" "" . ）' % url)
    except Exception as e:                          # noqa: BLE001
        print('解析失败 / failed: %s' % e)
        return 1
    return 0


def demo_offline():
    """不联网也能看的演示：展示提取函数在真实 HTML 片段上的行为。"""
    from lanzou_direct import (
        extract_sign, extract_ves, extract_ajax_path, extract_fid, is_lanzou_url,
    )
    html = """
    <html><body><script>
      var sign = 'a1b2c3d4e5f60718293a4b5c6d7e8f90';
      var ves = 2;
      url: '/ajaxm.php',
    </script><a href="/file/1234567">下载</a></body></html>
    """
    print('  extract_sign      ->', extract_sign(html))
    print('  extract_ves       ->', extract_ves(html))
    print('  extract_ajax_path ->', extract_ajax_path(html))
    print('  extract_fid       ->', extract_fid(html))
    print('  is_lanzou_url     ->', is_lanzou_url('https://wwx.lanzoux.com/abc'))
    print()
    print('  直链拼装规则: dom + "/file/" + url')
    print('  例如: {"zt":1,"dom":"https://vip.lanzou.com","url":"i9abc"}')
    print('        => https://vip.lanzou.com/file/i9abc')


if __name__ == '__main__':
    sys.exit(main())
