#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI 无头功能测试：用 offscreen 平台驱动真实的 LanzouGui，检查：
  1. 控件是否都建出来了
  2. 粘贴链接 → 自动解析 是否触发
  3. 解析失败路径是否正确落到日志/状态标签
  4. 下载流程（用一个本地 HTTP 服务模拟 CDN，验证进度回调与取消）

不依赖任何外部网络（第 4 项用本地 127.0.0.1 服务）。
"""
import os
import sys
import threading
import time
import http.server
import socketserver

os.environ['QT_QPA_PLATFORM'] = 'offscreen'          # 无头
os.environ['PYTHONIOENCODING'] = 'utf-8'

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

import lanzou_gui as G
from lanzou_direct import LanzouFile, download, DownloadCancelled, is_lanzou_url


PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print('  [%s] %s%s' % ('OK' if cond else 'XX', name, ('  -> ' + str(extra)) if extra else ''))


# ---------------------------------------------------------------------------
# 本地假 CDN（用于测下载逻辑）
# ---------------------------------------------------------------------------
PAYLOAD = os.urandom(2 * 1024 * 1024)     # 2 MB


class Handler(http.server.BaseHTTPRequestHandler):
    # HTTP 头只能是 latin-1，中文文件名必须走 RFC 5987 的 filename*=UTF-8'' 形式
    # （顺便验证 guess_filename() 能正确解出这种写法）
    CD = ("attachment; filename=\"test.bin\"; "
          "filename*=UTF-8''%E6%B5%8B%E8%AF%95%E6%96%87%E4%BB%B6.bin")

    def _headers(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(PAYLOAD)))
        self.send_header('Content-Disposition', self.CD)
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/slow'):
            self._headers()
            for i in range(0, len(PAYLOAD), 65536):     # 慢慢发，方便测取消
                try:
                    self.wfile.write(PAYLOAD[i:i + 65536])
                    self.wfile.flush()
                    time.sleep(0.05)
                except Exception:
                    return
            return
        self._headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *a):
        pass


def start_fake_cdn():
    httpd = socketserver.TCPServer(('127.0.0.1', 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


# ---------------------------------------------------------------------------
def main():
    print('=' * 66)
    print('GUI 无头功能测试 / headless GUI functional test')
    print('=' * 66)

    app = QApplication(sys.argv)
    app.setStyleSheet(G.QSS)
    win = G.LanzouGui()
    win.show()
    app.processEvents()

    # ---- 1. 控件齐全 ----
    print('\n[1] 控件')
    for attr in ('url_edit', 'pwd_edit', 'parse_btn', 'lb_name', 'lb_size', 'lb_url',
                 'btn_copy', 'btn_open', 'btn_download', 'btn_folder', 'btn_dir',
                 'btn_clear', 'progress', 'speed_label', 'log_view', 'chip',
                 'dir_label', 'pwd_hint'):
        check('控件存在: %s' % attr, hasattr(win, attr))
    check('下载按钮初始禁用', not win.btn_download.isEnabled())
    check('复制/打开按钮初始禁用',
          not win.btn_copy.isEnabled() and not win.btn_open.isEnabled())

    # ---- 2. 链接识别 / 自动解析防抖 ----
    print('\n[2] 链接识别与自动解析')
    check('非蓝奏云链接不触发防抖', not is_lanzou_url('https://example.com/x'))
    win.url_edit.setText('https://example.com/notlanzou')
    app.processEvents()
    check('输入非蓝奏云链接时定时器未启动', not win._debounce.isActive())

    win.url_edit.setText('https://www.lanzou.com/itestonly')
    app.processEvents()
    check('输入蓝奏云链接后自动解析定时器已启动', win._debounce.isActive())

    # ---- 3. 解析失败路径（真实网络；用不存在的分享）----
    print('\n[3] 解析失败路径（真实网络请求 lanzou.com）')
    seen = {}
    win.sig_parsed.connect(lambda f: seen.setdefault('ok', f))
    win.sig_parse_error.connect(lambda k, m: seen.setdefault('err', (k, m)))

    win._debounce.stop()
    win.start_parse()
    t0 = time.time()
    while time.time() - t0 < 45 and not seen:
        app.processEvents()
        time.sleep(0.2)

    log_text = win.log_view.toPlainText()
    if 'ok' in seen:
        f = seen['ok']
        check('解析成功（说明该分享真的存在）', True, f.direct_url[:60])
        check('文件名已回填', win.lb_name.text() != '—')
    else:
        check('解析失败并上报了错误', 'err' in seen, seen.get('err'))
        check('状态标签变成"解析失败"', '解析失败' in win.chip.text(), win.chip.text())
        check('日志里记录了失败原因', '✘' in log_text, log_text.strip().splitlines()[-1:] )
        check('日志里给出了兜底提示', 'playwright' in log_text)
        check('下载按钮仍为禁用', not win.btn_download.isEnabled())
    print('  ---- 实际日志 ----')
    for line in log_text.splitlines():
        print('  | ' + line)

    # ---- 4. 解析成功后控件应被点亮（用构造的 LanzouFile 直接喂过去）----
    print('\n[4] 解析成功后的界面状态（构造数据喂给槽函数）')
    fake = LanzouFile(direct_url='https://vip.lanzou.com/file/abc',
                      middle_url='https://vip.lanzou.com/file/abc',
                      name='示例文件.zip', size='12.3 M',
                      share_url='https://www.lanzou.com/x')
    win._on_parsed(fake)
    app.processEvents()
    check('文件名回填', win.lb_name.text() == '示例文件.zip', win.lb_name.text())
    check('大小回填', win.lb_size.text() == '12.3 M', win.lb_size.text())
    check('直链回填', win.lb_url.text() == fake.direct_url, win.lb_url.text())
    check('复制按钮已启用', win.btn_copy.isEnabled())
    check('浏览器打开按钮已启用', win.btn_open.isEnabled())
    check('下载按钮已启用', win.btn_download.isEnabled())
    check('状态标签为解析成功', '解析成功' in win.chip.text(), win.chip.text())

    # 超长直链不能把日志窗口刷屏（蓝奏云直链有 300~700 字符）
    long_url = 'https://developer4.lanrar.com/file/?' + 'A' * 600
    long_file = LanzouFile(direct_url=long_url, name='很长的直链.bin', size='28.73 MB')
    win.log_view.clear()
    win._on_parsed(long_file)
    app.processEvents()
    log_text = win.log_view.toPlainText()
    check('直链栏保留完整直链', win.lb_url.text() == long_url)
    check('日志里的直链被截断', long_url not in log_text)
    check('日志提示去直链栏看完整地址', '完整直链见上方' in log_text)
    check('短链接原样显示', G.shorten('https://a.b/c') == 'https://a.b/c')

    # ---- 5. 下载：进度回调 + 完成 ----
    print('\n[5] 下载流程（本地假 CDN）')
    httpd, port = start_fake_cdn()
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix='lanzou_gui_test_')

    win.file = LanzouFile(direct_url='http://127.0.0.1:%d/f.bin' % port,
                          name='测试文件.bin', size='2.00 MB')
    win._save_dir = tmpdir
    win.dir_label.setText(tmpdir)

    done_evt = {}
    win.sig_download_done.connect(
        lambda ok, p, s: done_evt.setdefault('r', (ok, p, s)))
    progresses = []
    win.sig_progress.connect(lambda d, t: progresses.append((d, t)))

    win.start_download()
    t0 = time.time()
    while time.time() - t0 < 60 and not done_evt:
        app.processEvents()
        time.sleep(0.1)

    ok, path, speed = done_evt.get('r', (False, 'no-callback', ''))
    check('下载回调被触发', bool(done_evt), done_evt.get('r'))
    check('下载成功', ok, path)
    if ok:
        check('文件确实写到了磁盘', os.path.isfile(path) and
              os.path.getsize(path) == len(PAYLOAD),
              '%s bytes' % (os.path.getsize(path) if os.path.exists(path) else -1))
        check('文件名按 Content-Disposition 推断', '测试文件' in os.path.basename(path),
              os.path.basename(path))
        check('收到过进度回调', len(progresses) > 0, '%d 次' % len(progresses))
        check('进度百分比最终到 100',
              win.progress.value() == 100 or '完成' in win.progress.format(),
              win.progress.format())
        check('界面已恢复可下载状态', win.btn_download.isEnabled())

    # ---- 6. 下载取消 ----
    print('\n[6] 取消下载（本地慢速 CDN）')
    win.file = LanzouFile(direct_url='http://127.0.0.1:%d/slow.bin' % port,
                          name='slow.bin')
    win._save_dir = tmpdir
    cancelled = {}
    win.sig_download_done.connect(
        lambda ok2, p2, s2: cancelled.setdefault('r', (ok2, p2)))
    win.start_download()
    time.sleep(1.0)
    app.processEvents()
    check('取消期间按钮文字变成"取消下载"', '取消' in win.btn_download.text(),
          win.btn_download.text())
    win.toggle_download()                 # 触发取消
    t0 = time.time()
    while time.time() - t0 < 30 and not cancelled:
        app.processEvents()
        time.sleep(0.1)
    r = cancelled.get('r')
    check('取消回调被触发', bool(r), r)
    check('回调标记为已取消', r and r[0] is False and r[1] == 'CANCELLED', r)
    leftovers = [f for f in os.listdir(tmpdir) if f.startswith('slow')]
    check('半截文件已被清理', not leftovers, leftovers)

    httpd.shutdown()

    # ---- 汇总 ----
    print('\n' + '=' * 66)
    print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败项:')
        for f in FAIL:
            print('  - ' + f)
    print('=' * 66)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
