#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时脚本：离屏渲染 GUI 并截图，用于 README。"""
import os
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PyQt6.QtWidgets import QApplication
import lanzou_gui as G
from lanzou_direct import LanzouFile

app = QApplication(sys.argv)
app.setStyle('Fusion')
app.setStyleSheet(G.QSS)

win = G.LanzouGui()
win.resize(880, 720)
win.show()
app.processEvents()

# 填一些看起来真实的内容（故意用假数据，别把真实分享链接/文件名截进仓库）
SHARE = 'https://wwx.lanzoux.com/ib8xY2zKqwd'
DIRECT = ('https://developer4.lanrar.com/file/?UzUFOwo7AzIDCgM7VmNUOFFuVGxR5gay'
          'V9UE6gLVU+NQtgbgXIgAsVGLCsoBswaNUohXulGNUe4F51rHVLYB5wKWUJgAaQR+A2YDYl')
NAME = '示例文件-中文名也可以.zip'
SIZE = '28.73 MB'

win.url_edit.setText(SHARE)
win.lb_name.setText(NAME)
win.lb_size.setText(SIZE)

win._on_parsed(LanzouFile(
    direct_url=DIRECT,
    middle_url=DIRECT,
    name=NAME,
    size=SIZE,
    share_url=SHARE,
    engine='browser',
    cookies={'acw_sc__v2': 'x', 'acw_tc': 'y', 'cdn_sec_tc': 'z', 'down_ip': '1'},
))

# 手动补几行日志（不真的发请求）—— 展示「浏览器引擎 + 过 CDN 反爬」这条新流程
for line in [
    '→ 解析: %s' % SHARE,
    '  [+] 引擎启动中 · 第 1 次尝试...',
    '  [+] 防检测模式已开启 · 稳定连接中...',
    '  [+] 成功获取真实地址 · 正在请求服务器签名...',
    '  【3/28】浏览器静默提取 ...',
    '  ✔ 浏览器引擎拿到直链',
    '      ⚠ 直链节点要求过 CDN 反爬，改用浏览器…',
    '      ✔ 已过 CDN 反爬（acw_sc__v2, acw_tc, cdn_sec_tc, down_ip）',
    '      ✔ 直链可用 · %s · %s' % (NAME, SIZE),
    '✔ 解析成功',
    '  文件名: %s' % NAME,
    '  大小  : %s' % SIZE,
    '  直链  : %s' % G.shorten(DIRECT),
    '',
    '→ 开始下载到: C:\\Users\\%USERNAME%\\Downloads',
    '  已带上 CDN 反爬 cookie（acw_sc__v2, acw_tc, cdn_sec_tc, down_ip）',
]:
    win._on_log(line)

win.progress.setValue(62)
win.progress.setFormat('62%%   17.8 MB / %s' % SIZE)
win.speed_label.setText('已下载 17.8 MB / %s' % SIZE)
win._chip('● 下载中…', G.C_ACCENT)
app.processEvents()

out = os.path.join(_ROOT, 'screenshot.png')
win.grab().save(out)
print('saved:', out, os.path.getsize(out), 'bytes')
