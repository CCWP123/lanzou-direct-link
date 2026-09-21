#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lanzou-direct-link / cli.py
===========================
命令行入口：把蓝奏云分享链接解析成浏览器下载直链。
Command-line front-end: turn a Lanzou share link into a direct download URL.

用法 / Usage
------------
    # 最常用：解析并打印直链
    python cli.py "https://www.lanzou.com/xxxxx"

    # 带提取码
    python cli.py "https://www.lanzou.com/xxxxx" --pwd 1234

    # 只要直链本身（方便脚本/管道使用）
    python cli.py "https://www.lanzou.com/xxxxx" --quiet

    # 直接丢进默认浏览器下载
    python cli.py "https://www.lanzou.com/xxxxx" --open

    # 用本工具下载到当前目录
    python cli.py "https://www.lanzou.com/xxxxx" --download .

    # 再跟一层重定向，拿最终 CDN 地址
    python cli.py "https://www.lanzou.com/xxxxx" --resolve

    # 静态解析失败时用无头浏览器兜底（需 pip install playwright）
    python cli.py "https://www.lanzou.com/xxxxx" --browser

退出码 / Exit codes
-------------------
    0  成功 / success
    1  参数或网络错误 / bad arguments or network error
    2  解析失败 / parsing failed
    3  需要提取码 / password required
"""

import argparse
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lanzou_direct import (                       # noqa: E402
    LanzouFile, LanzouError, NotLanzouUrl, ParseFailed, PasswordRequired,
    DownloadCancelled, is_lanzou_url, parse, resolve_real_url, download,
)


# ---------------------------------------------------------------------------
#  输出 / Output
# ---------------------------------------------------------------------------

def print_result(f: LanzouFile, as_json: bool, quiet: bool, resolved):
    if quiet:
        # 只输出可用的那个直链，方便 $(...) 捕获
        print(resolved or f.direct_url)
        return

    if as_json:
        if resolved:
            f.real_url = resolved
        print(f.to_json())
        return

    line = '─' * 62
    print(line)
    if f.name:
        print('文件名 / name   : %s' % f.name)
    if f.size:
        print('大小   / size   : %s' % f.size)
    print('分享页 / share  : %s' % f.share_url)
    print(line)
    print('直链 / direct URL:')
    print('  %s' % f.direct_url)
    if resolved and resolved != f.direct_url:
        print()
        print('最终地址 / final URL (跟随重定向后):')
        print('  %s' % resolved)
    print(line)
    print('提示：直接把这个地址粘贴进浏览器即可下载；')
    print('      若浏览器提示 403，说明该 CDN 需要 Referer —— 改用 --download。')


# ---------------------------------------------------------------------------
#  下载 / Download
# ---------------------------------------------------------------------------

def do_download(url: str, dest: str, timeout: int, cookies=None) -> bool:
    """复用核心库的 download()，这里只负责画进度条。"""
    last = {'t': 0.0}

    def on_progress(done, total):
        import time as _t
        now = _t.time()
        if now - last['t'] < 0.1 and done != total:
            return                                   # 限流，别把终端刷爆
        last['t'] = now
        if total:
            pct = done * 100.0 / total
            bar = '#' * int(pct / 2.5)
            sys.stderr.write('\r  [%-40s] %5.1f%%  %.1f/%.1f MB'
                             % (bar, pct, done / 1048576.0, total / 1048576.0))
        else:
            sys.stderr.write('\r  已下载 %.1f MB' % (done / 1048576.0))
        sys.stderr.flush()

    try:
        path = download(url, dest, timeout=timeout, cookies=cookies,
                        progress=on_progress,
                        log=lambda m: sys.stderr.write('  %s\n' % m))
        sys.stderr.write('\n')
        size = os.path.getsize(path) / 1048576.0 if os.path.exists(path) else 0
        print('✔ 已保存 / saved: %s  (%.2f MB)' % (path, size))
        return True
    except DownloadCancelled:
        sys.stderr.write('\n')
        print('✘ 下载已取消 / cancelled')
        return False
    except Exception as e:                          # noqa: BLE001
        sys.stderr.write('\n')
        print('✘ 下载失败 / download failed: %s' % e)
        return False


# ---------------------------------------------------------------------------
#  main
# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog='lanzou-direct-link',
        description='把蓝奏云分享链接解析成浏览器可用的下载直链',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument('share_url', help='蓝奏云分享链接 / Lanzou share URL')
    ap.add_argument('--pwd', default='', metavar='CODE',
                    help='提取码 / extraction code')
    ap.add_argument('--resolve', action='store_true',
                    help='再跟随重定向，拿最终 CDN 地址')
    ap.add_argument('--browser', action='store_true',
                    help='直接用浏览器引擎解析（最稳；默认已会在 HTTP 失败后自动使用）')
    ap.add_argument('--http', action='store_true',
                    help='只用纯 HTTP 解析（快，但现代蓝奏云多数会失败）')
    ap.add_argument('--json', action='store_true', help='以 JSON 输出')
    ap.add_argument('--quiet', '-q', action='store_true',
                    help='只输出直链本身（方便脚本使用）')
    ap.add_argument('--open', action='store_true',
                    help='用系统默认浏览器打开直链')
    ap.add_argument('--download', metavar='PATH',
                    help='下载文件到 PATH（可以是目录）')
    ap.add_argument('--timeout', type=int, default=15, metavar='SEC',
                    help='单次请求超时秒数（默认 15）')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    if not is_lanzou_url(args.share_url):
        print('✘ 这不是蓝奏云分享链接 / not a Lanzou share URL: %s' % args.share_url,
              file=sys.stderr)
        return 1

    # ---- 解析 ----
    engine = 'browser' if args.browser else ('http' if args.http else 'auto')
    try:
        f = parse(args.share_url,
                  pwd=args.pwd,
                  resolve=args.resolve,
                  timeout=args.timeout,
                  engine=engine,
                  log=(None if args.quiet else (lambda m: print('  ' + m))))
    except PasswordRequired as e:
        print('✘ %s' % e, file=sys.stderr)
        print('  请加 --pwd <提取码> 重试。', file=sys.stderr)
        return 3
    except NotLanzouUrl as e:
        print('✘ %s' % e, file=sys.stderr)
        return 1
    except ParseFailed as e:
        print('✘ 解析失败 / parse failed: %s' % e, file=sys.stderr)
        return 2
    except LanzouError as e:
        print('✘ %s' % e, file=sys.stderr)
        return 2
    except Exception as e:                          # noqa: BLE001
        print('✘ 网络或未知错误 / network or unknown error: %s' % e, file=sys.stderr)
        return 1

    target = f.real_url or f.direct_url

    # ---- 输出 ----
    print_result(f, args.json, args.quiet, f.real_url or None)

    # ---- 后续动作 ----
    rc = 0
    if args.download and not args.quiet:
        # 解析时顺手拿到的 CDN 反爬 cookie 直接复用，省得再过一次挑战
        ok = do_download(target, args.download, args.timeout, cookies=f.cookies)
        rc = 0 if ok else 2
    if args.open:
        if args.quiet:
            pass
        webbrowser.open(target)
        if not args.quiet:
            print('→ 已尝试用默认浏览器打开 / opened in default browser')
    return rc


if __name__ == '__main__':
    sys.exit(main())
