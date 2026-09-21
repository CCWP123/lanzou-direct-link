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

# 填一些看起来真实的内容
win.url_edit.setText('https://wwx.lanzoux.com/ib8xY2zKqwd')
win.lb_name.setText('示例文件-中文名也可以.zip')
win.lb_size.setText('48.7 M')

win._on_parsed(LanzouFile(
    direct_url='https://vip.lanzou.com/file/i9abcDEF123',
    middle_url='https://vip.lanzou.com/file/i9abcDEF123',
    name='示例文件-中文名也可以.zip',
    size='48.7 M',
    share_url='https://wwx.lanzoux.com/ib8xY2zKqwd',
))

# 手动补几行日志（不真的发请求）
for line in [
    '→ 解析: https://wwx.lanzoux.com/ib8xY2zKqwd',
    '✔ 解析成功',
    '  文件名: 示例文件-中文名也可以.zip',
    '  大小  : 48.7 M',
    '  直链  : https://vip.lanzou.com/file/i9abcDEF123',
]:
    win._on_log(line)

win.progress.setValue(62)
win.progress.setFormat('62%   30.2 MB / 48.7 MB')
win.speed_label.setText('已下载 30.2 MB / 48.7 MB')
win._chip('● 下载中…', G.C_ACCENT)
app.processEvents()

out = os.path.join(_ROOT, 'screenshot.png')
win.grab().save(out)
print('saved:', out, os.path.getsize(out), 'bytes')
