#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lanzou-direct-link / lanzou_gui.py
==================================
图形界面：粘贴蓝奏云分享链接 → 自动解析 → 一键下载。
GUI: paste a Lanzou share link → auto-parse → one-click download.

用法 / Usage
------------
    python lanzou_gui.py

依赖 / Dependencies
-------------------
    pip install PyQt6          # 仅 GUI 需要 / GUI only
    （核心库 lanzou_direct.py 只要 requests）

打包成 exe / Build a standalone exe
-----------------------------------
    pip install pyinstaller
    pyinstaller --onefile --windowed --name 蓝奏云直链下载器 lanzou_gui.py

特性 / Features
---------------
  * 粘贴链接即自动解析（也可按回车或点「解析」）
  * 启动时自动读取剪贴板里的蓝奏云链接
  * 可选提取码
  * 直链一键复制 / 一键用浏览器打开
  * 内置下载器：自选目录、实时进度与速度、可取消（自动带上 CDN 需要的 Referer）
  * 解析失败时提示可改用浏览器兜底
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog,
        QMessageBox, QFrame, QProgressBar, QSizePolicy, QComboBox,
    )
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QUrl
    from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QLinearGradient, \
        QDesktopServices, QGuiApplication
except ImportError:
    sys.stderr.write(
        '未安装 PyQt6 / PyQt6 is not installed.\n'
        '请先执行 / please run:  pip install PyQt6\n')
    sys.exit(1)

from lanzou_direct import (                       # noqa: E402
    LanzouFile, LanzouError, NotLanzouUrl, ParseFailed, PasswordRequired,
    DownloadCancelled, is_lanzou_url, parse, download, guess_filename,
)


# ===========================================================================
#  配色与样式 / Colors & stylesheet
# ===========================================================================

C_BG_TOP = '#0f1419'
C_BG_BOTTOM = '#0a0e14'
C_CARD = '#1a1f2e'
C_BORDER = '#2a3142'
C_ACCENT = '#6366f1'
C_ACCENT2 = '#8b5cf6'
C_CYAN = '#06b6d4'
C_SUCCESS = '#10b981'
C_ERROR = '#ef4444'
C_WARN = '#f59e0b'
C_TEXT = '#e5e7eb'
C_TEXT_DIM = '#9ca3af'
C_TEXT_MUTED = '#6b7280'

QSS = f"""
QMainWindow, QWidget#Root {{ background: {C_BG_TOP}; }}
QLabel {{ color: {C_TEXT}; background: transparent; }}
QLabel#Title {{ font-family: 'Microsoft YaHei UI'; font-size: 21px; font-weight: 700; }}
QLabel#Subtitle {{ font-family: 'Microsoft YaHei UI'; font-size: 12px; color: {C_TEXT_DIM}; }}
QLabel#CardTitle {{
    font-family: 'Microsoft YaHei UI'; font-size: 12px; font-weight: 600;
    color: {C_CYAN}; letter-spacing: 1px;
}}
QLabel#InfoKey {{ color: {C_TEXT_DIM}; font-size: 12px; }}
QLabel#InfoVal {{ color: {C_TEXT}; font-size: 12px; }}
QLabel#Url {{ color: {C_CYAN}; font-size: 12px; font-family: Consolas, monospace; }}
QFrame#Card {{ background: {C_CARD}; border: 1px solid {C_BORDER}; border-radius: 12px; }}
QLineEdit {{
    background: #0d1117; border: 1px solid {C_BORDER}; border-radius: 8px;
    padding: 9px 12px; color: {C_TEXT};
    font-family: 'Microsoft YaHei UI'; font-size: 13px;
    selection-background-color: {C_ACCENT};
}}
QLineEdit:focus {{ border: 1px solid {C_ACCENT}; }}
QLineEdit#Pwd {{ max-width: 110px; }}
QTextEdit#Log {{
    background: #0d1117; border: 1px solid {C_BORDER}; border-radius: 8px;
    color: #c9d1d9; font-family: Consolas, 'Cascadia Mono', monospace;
    font-size: 12px; padding: 8px; selection-background-color: {C_ACCENT};
}}
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {C_ACCENT}, stop:1 {C_ACCENT2});
    color: white; border: none; border-radius: 8px; padding: 10px 20px;
    font-family: 'Microsoft YaHei UI'; font-size: 13px; font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c7ff2, stop:1 #a78bfa);
}}
QPushButton#Primary:disabled {{ background: #2a3142; color: {C_TEXT_MUTED}; }}
QPushButton#Ghost {{
    background: transparent; color: {C_TEXT_DIM};
    border: 1px solid {C_BORDER}; border-radius: 8px; padding: 9px 16px;
    font-family: 'Microsoft YaHei UI'; font-size: 12px;
}}
QPushButton#Ghost:hover {{ color: {C_TEXT}; border-color: {C_ACCENT}; background: rgba(99,102,241,0.08); }}
QPushButton#Ghost:disabled {{ color: #4b5563; border-color: #232838; }}
QPushButton#Danger {{
    background: transparent; color: {C_ERROR};
    border: 1px solid {C_ERROR}; border-radius: 8px; padding: 9px 16px;
    font-family: 'Microsoft YaHei UI'; font-size: 12px;
}}
QPushButton#Danger:hover {{ background: rgba(239,68,68,0.12); }}
QProgressBar {{
    background: #0d1117; border: 1px solid {C_BORDER}; border-radius: 6px;
    height: 14px; text-align: center; color: {C_TEXT}; font-size: 11px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {C_ACCENT}, stop:1 {C_CYAN});
    border-radius: 5px;
}}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {C_BORDER}; border-radius: 4px; min-height: 20px; }}
QScrollBar::handle:vertical:hover {{ background: {C_ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QComboBox {{
    background: #0d1117; border: 1px solid {C_BORDER}; border-radius: 8px;
    padding: 8px 10px; color: {C_TEXT}; font-size: 12px;
}}
QComboBox QAbstractItemView {{
    background: {C_CARD}; color: {C_TEXT}; selection-background-color: {C_ACCENT};
}}
"""


def human(n: float) -> str:
    """人类可读的大小 / human-readable size."""
    if not n:
        return '0 B'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return '%.2f %s' % (n, unit) if unit != 'B' else '%d B' % n
        n /= 1024.0
    return '%.2f TB' % n


def default_download_dir() -> str:
    """默认保存目录：优先「下载」文件夹，退回桌面，再退回用户主目录。"""
    home = os.path.expanduser('~')
    for cand in (os.path.join(home, 'Downloads'),
                 os.path.join(home, '下载'),
                 os.path.join(home, 'Desktop'),
                 os.path.join(home, '桌面')):
        if os.path.isdir(cand):
            return cand
    return home


# ===========================================================================
#  主窗口 / Main window
# ===========================================================================

class GradientRoot(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0.0, QColor(C_BG_TOP))
        g.setColorAt(1.0, QColor(C_BG_BOTTOM))
        p.fillRect(self.rect(), QBrush(g))


class LanzouGui(QMainWindow):

    # ---- 跨线程信号（所有 UI 更新都在主线程）----
    sig_parsed = pyqtSignal(object)          # LanzouFile
    sig_parse_error = pyqtSignal(str, str)   # (kind, message)
    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(int, int)      # (done, total)
    sig_download_done = pyqtSignal(bool, str, str)   # (ok, path_or_msg, human_size)
    sig_busy = pyqtSignal(bool)              # 解析中

    def __init__(self):
        super().__init__()
        self.setWindowTitle('蓝奏云直链下载器')
        self.resize(880, 700)
        self.setMinimumSize(760, 600)

        self.file: LanzouFile = None
        self._parse_running = False
        self._downloading = False
        self._cancel_download = False
        self._save_dir = default_download_dir()
        self._last_parsed_url = ''

        self._connect_signals()
        self._build_ui()

        # 粘贴后自动解析的防抖定时器
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.start_parse)

        # 启动时看看剪贴板里是不是已经有蓝奏云链接
        QTimer.singleShot(150, self._check_clipboard)

    # ---------------------------------------------------------------- 信号
    def _connect_signals(self):
        self.sig_parsed.connect(self._on_parsed)
        self.sig_parse_error.connect(self._on_parse_error)
        self.sig_log.connect(self._on_log)
        self.sig_progress.connect(self._on_progress)
        self.sig_download_done.connect(self._on_download_done)
        self.sig_busy.connect(self._on_busy)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = GradientRoot()
        root.setObjectName('Root')
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(12)

        # ---- 标题 ----
        head = QHBoxLayout()
        tb = QVBoxLayout()
        tb.setSpacing(2)
        t = QLabel('蓝奏云直链下载器')
        t.setObjectName('Title')
        s = QLabel('粘贴分享链接 → 自动解析 → 一键下载 · 纯本地解析，不经过第三方服务')
        s.setObjectName('Subtitle')
        tb.addWidget(t)
        tb.addWidget(s)
        head.addLayout(tb)
        head.addStretch()

        self.chip = QLabel('● 就绪')
        self.chip.setStyleSheet(
            f'color:{C_TEXT_MUTED}; background:{C_CARD}; padding:6px 14px;'
            f'border-radius:12px; border:1px solid {C_BORDER}; font-size:12px;')
        head.addWidget(self.chip)
        outer.addLayout(head)

        # ---- 输入卡片 ----
        in_card, in_lay = self._card('分享链接 / SHARE LINK')
        row = QHBoxLayout()
        row.setSpacing(8)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText('在此粘贴蓝奏云分享链接，例如 https://www.lanzou.com/xxxxx')
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.textChanged.connect(self._on_url_changed)
        self.url_edit.returnPressed.connect(self.start_parse)
        row.addWidget(self.url_edit, 1)

        self.pwd_edit = QLineEdit()
        self.pwd_edit.setObjectName('Pwd')
        self.pwd_edit.setPlaceholderText('提取码')
        self.pwd_edit.returnPressed.connect(self.start_parse)
        row.addWidget(self.pwd_edit)

        self.parse_btn = QPushButton('解析')
        self.parse_btn.setObjectName('Primary')
        self.parse_btn.setMinimumWidth(96)
        self.parse_btn.clicked.connect(self.start_parse)
        row.addWidget(self.parse_btn)

        in_lay.addLayout(row)

        self.pwd_hint = QLabel('')
        self.pwd_hint.setStyleSheet(f'color:{C_WARN}; font-size:11px;')
        self.pwd_hint.hide()
        in_lay.addWidget(self.pwd_hint)
        outer.addWidget(in_card)

        # ---- 结果卡片 ----
        res_card, res_lay = self._card('文件信息 / FILE INFO')

        self.lb_name = self._info_row(res_lay, '文件名')
        self.lb_size = self._info_row(res_lay, '大小')
        self.lb_url = self._info_row(res_lay, '直链', url_style=True)

        act = QHBoxLayout()
        act.setSpacing(8)
        act.addStretch()

        self.btn_copy = QPushButton('复制直链')
        self.btn_copy.setObjectName('Ghost')
        self.btn_copy.clicked.connect(self.copy_url)
        act.addWidget(self.btn_copy)

        self.btn_open = QPushButton('浏览器打开')
        self.btn_open.setObjectName('Ghost')
        self.btn_open.clicked.connect(self.open_url)
        act.addWidget(self.btn_open)
        res_lay.addLayout(act)

        outer.addWidget(res_card)

        # ---- 下载卡片 ----
        dl_card, dl_lay = self._card('下载 / DOWNLOAD')

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(QLabel('保存到', objectName='InfoKey'))
        self.dir_label = QLabel(self._save_dir)
        self.dir_label.setObjectName('InfoVal')
        self.dir_label.setWordWrap(True)
        self.dir_label.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Preferred)
        dir_row.addWidget(self.dir_label, 1)

        self.btn_dir = QPushButton('选择目录')
        self.btn_dir.setObjectName('Ghost')
        self.btn_dir.clicked.connect(self.choose_dir)
        dir_row.addWidget(self.btn_dir)
        dl_lay.addLayout(dir_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat('等待解析…')
        dl_lay.addWidget(self.progress)

        self.speed_label = QLabel('')
        self.speed_label.setStyleSheet(f'color:{C_TEXT_DIM}; font-size:11px;')
        dl_lay.addWidget(self.speed_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_download = QPushButton('开始下载')
        self.btn_download.setObjectName('Primary')
        self.btn_download.setMinimumHeight(40)
        self.btn_download.clicked.connect(self.toggle_download)
        self.btn_download.setEnabled(False)
        btn_row.addWidget(self.btn_download)

        self.btn_folder = QPushButton('打开所在文件夹')
        self.btn_folder.setObjectName('Ghost')
        self.btn_folder.setMinimumHeight(40)
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_folder.setEnabled(False)
        btn_row.addWidget(self.btn_folder)

        self.btn_clear = QPushButton('清空日志')
        self.btn_clear.setObjectName('Ghost')
        self.btn_clear.setMinimumHeight(40)
        self.btn_clear.clicked.connect(lambda: self.log_view.clear())
        btn_row.addWidget(self.btn_clear)

        btn_row.addStretch()
        dl_lay.addLayout(btn_row)
        outer.addWidget(dl_card)

        # ---- 日志 ----
        log_card, log_lay = self._card('日志 / LOG')
        self.log_view = QTextEdit()
        self.log_view.setObjectName('Log')
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(150)
        log_lay.addWidget(self.log_view)
        outer.addWidget(log_card, stretch=1)

        self._set_enabled_actions(False)

    def _card(self, title):
        card = QFrame()
        card.setObjectName('Card')
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        lb = QLabel(title)
        lb.setObjectName('CardTitle')
        lay.addWidget(lb)
        return card, lay

    def _info_row(self, layout, key, url_style=False):
        row = QHBoxLayout()
        row.setSpacing(8)
        k = QLabel(key)
        k.setObjectName('InfoKey')
        k.setFixedWidth(58)
        v = QLabel('—')
        v.setObjectName('Url' if url_style else 'InfoVal')
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(k)
        row.addWidget(v, 1)
        layout.addLayout(row)
        return v

    # ------------------------------------------------------------- 小工具
    def log(self, msg=''):
        self.sig_log.emit(str(msg))

    def _on_log(self, msg):
        self.log_view.append(msg)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _chip(self, text, color):
        self.chip.setText(text)
        self.chip.setStyleSheet(
            f'color:{color}; background:{C_CARD}; padding:6px 14px;'
            f'border-radius:12px; border:1px solid {C_BORDER}; font-size:12px;')

    def _set_enabled_actions(self, on):
        for b in (self.btn_copy, self.btn_open):
            b.setEnabled(on)

    def _on_busy(self, busy):
        self._parse_running = busy
        self.parse_btn.setEnabled(not busy)
        self.parse_btn.setText('解析中…' if busy else '解析')

    # --------------------------------------------------------- 剪贴板/输入
    def _check_clipboard(self):
        try:
            cb = QGuiApplication.clipboard()
            text = (cb.text() or '').strip()
        except Exception:                            # noqa: BLE001
            return
        if is_lanzou_url(text) and not self.url_edit.text().strip():
            self.url_edit.setText(text)
            self.log('已从剪贴板填入链接，开始自动解析…')

    def _on_url_changed(self, text):
        """粘贴/输入链接后自动解析（防抖 500ms）。"""
        text = (text or '').strip()
        self.pwd_hint.hide()
        if is_lanzou_url(text) and text != self._last_parsed_url:
            self._debounce.start(500)

    def choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, '选择保存目录', self._save_dir)
        if d:
            self._save_dir = d
            self.dir_label.setText(d)

    # ---------------------------------------------------------------- 解析
    def start_parse(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        if not is_lanzou_url(url):
            self._on_parse_error('bad_url', '这不是蓝奏云分享链接')
            return
        if self._parse_running or self._downloading:
            return

        self._last_parsed_url = url
        pwd = self.pwd_edit.text().strip()
        self._set_enabled_actions(False)
        self.file = None
        self.btn_download.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setFormat('解析中…')
        self.sig_busy.emit(True)
        self._chip('● 解析中…', C_ACCENT)
        self.log('')
        self.log('→ 解析: %s%s' % (url, '  (带提取码)' if pwd else ''))

        def worker():
            try:
                # engine='auto'：先试纯 HTTP 快路，失败自动切浏览器引擎。
                # 现代蓝奏云基本都会走浏览器那条；浏览器版本会**一次启动**同时
                # 完成「渲染分享页 → 拿直链 → 过 CDN 反爬」，所以稍慢几秒。
                # 日志实时回传，让用户看到「正在渲染页面 / 正在尝试第 N 种方法」。
                f = parse(url, pwd=pwd, timeout=30,
                          log=lambda m: self.sig_log.emit(str(m)))
                self.sig_parsed.emit(f)
            except PasswordRequired:
                self.sig_parse_error.emit('pwd', '该分享需要提取码，请填写后重试')
            except NotLanzouUrl:
                self.sig_parse_error.emit('bad_url', '这不是蓝奏云分享链接')
            except ParseFailed as e:
                self.sig_parse_error.emit('parse', str(e))
            except LanzouError as e:
                self.sig_parse_error.emit('parse', str(e))
            except Exception as e:                   # noqa: BLE001
                self.sig_parse_error.emit('net', '网络错误: %s' % e)
            finally:
                self.sig_busy.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_parsed(self, f):
        self.file = f
        self.lb_name.setText(f.name or '(未提供)')
        self.lb_size.setText(f.size or '(未知)')
        self.lb_url.setText(f.direct_url or '—')
        self._set_enabled_actions(True)
        self.btn_download.setEnabled(not self._downloading)
        self.btn_download.setText('开始下载')
        self.progress.setValue(0)
        self.progress.setFormat('准备就绪，点「开始下载」')
        self.speed_label.setText('')
        self._chip('● 解析成功', C_SUCCESS)
        self.log('✔ 解析成功')
        if f.name:
            self.log('  文件名: %s' % f.name)
        if f.size:
            self.log('  大小  : %s' % f.size)
        self.log('  直链  : %s' % f.direct_url)

    def _on_parse_error(self, kind, msg):
        self.btn_download.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setFormat('解析失败')
        if kind == 'pwd':
            self._chip('● 需要提取码', C_WARN)
            self.pwd_hint.setText('此分享需要提取码 —— 请在上方「提取码」框填写后重新解析')
            self.pwd_hint.show()
            self.pwd_edit.setFocus()
        elif kind == 'bad_url':
            self._chip('● 链接无效', C_ERROR)
        else:
            self._chip('● 解析失败', C_ERROR)
        self.log('✘ %s' % msg)
        if kind in ('parse', 'net'):
            self.log('  提示: 蓝奏云多数分享页需要浏览器渲染，本引擎会自动处理；')
            self.log('        若一直失败，先确认浏览器引擎装好了 ——')
            self.log('        命令: pip install playwright 然后 playwright install chromium')

    # ---------------------------------------------------------------- 直链
    def copy_url(self):
        if not (self.file and self.file.direct_url):
            return
        QGuiApplication.clipboard().setText(self.file.direct_url)
        self.log('已复制直链到剪贴板')
        self._chip('● 已复制', C_SUCCESS)

    def open_url(self):
        if not (self.file and self.file.direct_url):
            return
        QDesktopServices.openUrl(QUrl(self.file.direct_url))
        self.log('已用默认浏览器打开直链')
        self.log('  注意: 下载节点有 JS 反爬，浏览器会先闪一个校验页再开始下载；')
        self.log('        若浏览器只是显示一段脚本或 403，请改用本工具的「开始下载」。')

    def open_folder(self):
        if os.path.isdir(self._save_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._save_dir))

    # ---------------------------------------------------------------- 下载
    def toggle_download(self):
        if self._downloading:
            self._cancel_download = True
            self.btn_download.setEnabled(False)
            self.btn_download.setText('正在取消…')
            self.log('→ 已请求取消下载')
            return
        self.start_download()

    def start_download(self):
        if not (self.file and self.file.direct_url):
            return
        if self._downloading or self._parse_running:
            return
        if not os.path.isdir(self._save_dir):
            QMessageBox.warning(self, '目录不存在', '保存目录不存在，请重新选择。')
            return

        self._downloading = True
        self._cancel_download = False
        self.btn_download.setText('取消下载')
        self.btn_download.setObjectName('Danger')
        self.btn_download.setStyleSheet('')          # 让 QSS 重新匹配 objectName
        self.btn_download.style().unpolish(self.btn_download)
        self.btn_download.style().polish(self.btn_download)
        self.progress.setValue(0)
        self.progress.setFormat('开始下载…')
        self._chip('● 下载中…', C_ACCENT)

        url = self.file.direct_url
        dest = self._save_dir
        # 解析时顺手过掉的 CDN 反爬 cookie，带上就不用再过一次
        cookies = dict(getattr(self.file, 'cookies', None) or {})
        self.log('')
        self.log('→ 开始下载到: %s' % dest)
        if cookies:
            self.log('  已带上 CDN 反爬 cookie（%s）' % ', '.join(sorted(cookies)))

        t0 = time.time()
        last = {'t': 0.0}

        def on_progress(done, total):
            now = time.time()
            if now - last['t'] >= 0.12 or done == total:
                last['t'] = now
                self.sig_progress.emit(done, total)

        def worker():
            try:
                path = download(url, dest, timeout=30,
                                cookies=cookies,
                                progress=on_progress,
                                cancel=lambda: self._cancel_download,
                                log=lambda m: self.sig_log.emit(str(m)))
                elapsed = max(0.001, time.time() - t0)
                size = os.path.getsize(path) if os.path.exists(path) else 0
                speed = size / elapsed
                self.sig_download_done.emit(True, path, human(speed) + '/s')
            except DownloadCancelled:
                self.sig_download_done.emit(False, 'CANCELLED', '')
            except Exception as e:                   # noqa: BLE001
                self.sig_download_done.emit(False, str(e), '')
            finally:
                self._downloading = False

        threading.Thread(target=worker, daemon=True).start()

    def _on_progress(self, done, total):
        if total:
            pct = int(done * 100 / total)
            self.progress.setValue(pct)
            self.progress.setFormat('%d%%   %s / %s'
                                    % (pct, human(done), human(total)))
            self.speed_label.setText('已下载 %s / %s' % (human(done), human(total)))
        else:
            self.progress.setRange(0, 0)             # 未知长度 → 滚动条
            self.progress.setFormat('已下载 %s' % human(done))

    def _on_download_done(self, ok, path_or_msg, speed):
        self._downloading = False
        self.progress.setRange(0, 100)
        self.btn_download.setEnabled(True)
        self.btn_download.setText('开始下载')
        self.btn_download.setObjectName('Primary')
        self.btn_download.style().unpolish(self.btn_download)
        self.btn_download.style().polish(self.btn_download)
        self.btn_download.setStyleSheet('')

        if ok:
            self.progress.setValue(100)
            sz = os.path.getsize(path_or_msg) if os.path.exists(path_or_msg) else 0
            self.progress.setFormat('完成  %s' % human(sz))
            self.speed_label.setText('平均速度 %s' % speed)
            self._chip('● 下载完成', C_SUCCESS)
            self.log('✔ 下载完成: %s  (%s, 平均 %s)' % (path_or_msg, human(sz), speed))
            self.btn_folder.setEnabled(True)
        elif path_or_msg == 'CANCELLED':
            self.progress.setValue(0)
            self.progress.setFormat('已取消')
            self.speed_label.setText('')
            self._chip('● 已取消', C_WARN)
            self.log('✘ 下载已取消（半截文件已删除）')
        else:
            self.progress.setValue(0)
            self.progress.setFormat('下载失败')
            self.speed_label.setText('')
            self._chip('● 下载失败', C_ERROR)
            self.log('✘ 下载失败: %s' % path_or_msg)
            self.log('  提示: 链接可能已过期，或需要重新解析获取新直链。')

    def closeEvent(self, event):
        if self._downloading:
            r = QMessageBox.question(
                self, '正在下载',
                '下载还没结束，确定要退出吗？\n（半截文件会被删除）',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._cancel_download = True
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(QSS)
    win = LanzouGui()
    win.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
