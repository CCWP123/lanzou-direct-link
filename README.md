# lanzou-direct-link

> 把蓝奏云（Lanzou）的分享链接解析成**可以直接丢进浏览器的下载直链**，纯 `requests` 实现。
> Turn a Lanzou Cloud share link into a **direct download URL you can paste into a browser** — pure `requests`, no third-party parsing API.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](#-安装--installation)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-requests%20only-brightgreen.svg)](#-安装--installation)

---

## 📖 目录 / Table of Contents

- [这是什么 / What is this](#-这是什么--what-is-this)
- [特性 / Features](#-特性--features)
- [安装 / Installation](#-安装--installation)
- [快速开始 / Quick Start](#-快速开始--quick-start)
- [原理详解 / How It Works](#-原理详解--how-it-works)
- [API 参考 / API Reference](#-api-参考--api-reference)
- [常见问题 / FAQ](#-常见问题--faq)
- [注意事项与免责声明 / Disclaimer](#-注意事项与免责声明--disclaimer)
- [License](#-license)

---

## 🧩 这是什么 / What is this

**中文**

蓝奏云的分享页（`https://xxx.lanzou?.com/abcdef`）**本身不是下载地址**。
真正的直链要靠页面里的一个 `sign` 令牌，向 `ajaxm.php` 换一次才拿得到 ——
这也是为什么你没法直接把分享链接喂给 IDM / aria2 / 浏览器下载。

本库把这一套「换直链」的流程用纯 Python 实现出来：

- 输入：蓝奏云分享链接（可选提取码）
- 输出：`https://vip.lanzou?.com/file/xxxx` 这样的**直链**，可以：
  - 直接粘贴进浏览器地址栏
  - 喂给 IDM / aria2 / curl / wget
  - 或让本工具的 CLI 直接帮你下载

**English**

A Lanzou share page (`https://xxx.lanzou?.com/abcdef`) is **not** a download URL.
The real link is obtained by exchanging an in-page `sign` token against
`ajaxm.php` — which is why you cannot hand a share link straight to IDM, aria2 or
your browser.

This library implements that exchange in pure Python:

- **Input**: a Lanzou share link (+ optional extraction code)
- **Output**: a **direct URL** like `https://vip.lanzou?.com/file/xxxx` that you can
  paste into a browser, feed to IDM / aria2 / curl, or download with the bundled CLI

---

## ✨ 特性 / Features

- **纯 `requests`，只依赖一个库**
  Pure `requests` — exactly one dependency.
- **不依赖任何第三方解析服务**
  不调用别人的 API，不受第三方限流/下线影响，也不会把你的链接送到别人服务器上。
  No third-party parsing API: no rate limits, no downtime, and your links never
  leave your machine.
- **自适应蓝奏云的多轮前端改版**
  `sign` / `signs` / `ves` / ajax 端点都排了多组正则，域名支持 `lanzou*` / `lanzo*` / `lanzn` 全系列镜像。
  Multiple regex patterns for `sign` / `signs` / `ves` / ajax endpoints, and
  support for the whole `lanzou*` / `lanzo*` / `lanzn` mirror family.
- **自动进 `iframe`**
  蓝奏云常把正文放在 iframe 里，本库会自动跟进去再提取。
  Automatically descends into the content `iframe`.
- **支持提取码**
  传入 `pwd` 会随请求一起提交，并能区分「真的解析失败」和「需要提取码」。
  Password-protected shares are supported, and "needs a code" is a distinct error.
- **可选的浏览器兜底**
  当页面必须执行 JS 才生成 `sign` 时，可以挂 Playwright 无头浏览器（复用系统 Edge/Chrome，不必额外下载内核）。
  Optional Playwright fallback that reuses your system Edge/Chrome.
- **可选的最终地址解析**
  `resolve=True` 再跟一层重定向，拿到带真实文件名的 CDN 地址。
  `resolve=True` follows redirects to the final CDN URL with the real filename.
- **完整 CLI + 离线测试**
  38 个单元测试包含用 mock session 跑通的端到端流程，测试**不发任何网络请求**。
  38 unit tests, including a fully mocked end-to-end flow — **no network needed**.

---

## 🚀 安装 / Installation

**中文**

```bash
git clone https://github.com/CCWP123/lanzou-direct-link.git
cd lanzou-direct-link

pip install -r requirements.txt      # 就一个 requests

# 可选：装浏览器兜底
# pip install playwright
# playwright install chromium        # 系统有 Edge/Chrome 可跳过

# 跑测试
python tests/test_extract.py         # 38 个测试，全离线
```

**English**

```bash
git clone https://github.com/CCWP123/lanzou-direct-link.git
cd lanzou-direct-link

pip install -r requirements.txt      # just requests

# optional browser fallback
# pip install playwright
# playwright install chromium        # skip if you already have Edge/Chrome

python tests/test_extract.py         # 38 offline tests
```

---

## ⚡ 快速开始 / Quick Start

### 命令行 / CLI

```bash
# 最常用：解析并打印直链
python cli.py "https://www.lanzou.com/xxxxx"

# 带提取码
python cli.py "https://www.lanzou.com/xxxxx" --pwd 1234

# 只要直链本身（方便脚本/管道）
python cli.py "https://www.lanzou.com/xxxxx" --quiet
URL=$(python cli.py "https://www.lanzou.com/xxxxx" --quiet)

# 直接丢进默认浏览器
python cli.py "https://www.lanzou.com/xxxxx" --open

# 用本工具下载到当前目录
python cli.py "https://www.lanzou.com/xxxxx" --download .

# 再跟一层重定向，拿最终 CDN 地址
python cli.py "https://www.lanzou.com/xxxxx" --resolve

# 静态解析失败时用无头浏览器兜底
python cli.py "https://www.lanzou.com/xxxxx" --browser

# 结构化输出
python cli.py "https://www.lanzou.com/xxxxx" --json
```

输出示例 / sample output:

```
──────────────────────────────────────────────────────────────
文件名 / name   : 示例文件.zip
大小   / size   : 12.3 M
分享页 / share  : https://www.lanzou.com/xxxxx
──────────────────────────────────────────────────────────────
直链 / direct URL:
  https://vip.lanzou.com/file/i9abc123
──────────────────────────────────────────────────────────────
提示：直接把这个地址粘贴进浏览器即可下载；
      若浏览器提示 403，说明该 CDN 需要 Referer —— 改用 --download。
```

`--json` 输出 / JSON output:

```json
{
  "direct_url": "https://vip.lanzou.com/file/i9abc123",
  "middle_url": "https://vip.lanzou.com/file/i9abc123",
  "name": "示例文件.zip",
  "size": "12.3 M",
  "share_url": "https://www.lanzou.com/xxxxx",
  "real_url": "",
  "raw": {"zt": 1, "dom": "https://vip.lanzou.com", "url": "i9abc123"}
}
```

### 作为库使用 / As a library

```python
from lanzou_direct import parse

f = parse('https://www.lanzou.com/xxxxx')          # 或 pwd='1234'
print(f.name)          # 示例文件.zip
print(f.size)          # 12.3 M
print(f.direct_url)    # https://vip.lanzou.com/file/i9abc123

# 再跟一层重定向
f = parse('https://www.lanzou.com/xxxxx', resolve=True)
print(f.real_url)
```

---

## 🔬 原理详解 / How It Works

### 七步换直链 / The seven steps

```
① GET   分享页                      → HTML（跟随重定向，记下最终 URL 作为 Referer）
② GET   <iframe src>（如果有）      → 蓝奏云常把正文塞在 iframe 里，要跟进去
③ 从 HTML 里提取：
        sign       形如  var sign = 'xxxx'         ← 最关键，没有它就换不出直链
        signs      部分页面才有
        ves        版本号，默认 "1"
        ajax 路径  默认 /ajaxm.php
        fid        文件数字 ID（老接口要拼在 ?file= 后面）
④ POST  <ajax>?file=<fid>
         action=downprocess & sign=<sign> & ves=<ves> [& signs=<signs>] [& p=<提取码>]
⑤ 返回 JSON：
         {"zt":1, "dom":"https://vip.lanzou.com", "url":"i9abc123",
          "inf":"示例文件.zip", "size":"12.3 M"}
⑥ 拼直链： direct = dom + "/file/" + url
⑦ 可选：跟随重定向 → 最终带文件名的 CDN 地址
```

对应的关键代码 / the corresponding code:

```python
sign = extract_sign(html)                       # ③
out  = _exchange_direct_url(sess, base, referer, # ④⑤
                            sign=sign, ves=extract_ves(html),
                            fid=extract_fid(html), pwd=pwd,
                            ajax_path=extract_ajax_path(html))
direct = out['direct_url']                      # ⑥  dom + "/file/" + url
real   = resolve_real_url(direct)               # ⑦
```

### 为什么直链粘进浏览器有时 403 / Why the browser sometimes 403s

蓝奏云的 `dom/file/xxx` 是**中间页**，它的 CDN 会校验 `Referer`。
浏览器直接打开时没有正确的 Referer，所以可能 403。
三种解法：

1. 用 `--resolve` 拿到跟随重定向后的**最终 CDN 地址**（通常可以直接下载）
2. 用 `--download` 让本工具带正确的 Referer 帮你下
3. 直接把这个直链喂给 IDM / aria2 这类能自定义请求头的下载器

> Lanzou's `dom/file/xxx` is an intermediate page whose CDN checks `Referer`.
> Use `--resolve` for the final CDN URL, `--download` to let this tool send the
> right headers, or feed the link to a downloader that supports custom headers.

### 关于 `ves` 的贪婪匹配坑 / The greedy `ves` gotcha

很多老脚本（包括本项目的来源脚本）用这个正则取版本号：

```python
re.search(r"var\s+ves\s*=\s*['\"]?([^'\"]+)['\"]?", html)
```

`[^'"]+` 是「除了引号什么都吃」的贪婪匹配。遇到 `var ves = 2;` 这种**没有结尾引号**的写法，
它会把后面整段 JS 都吞进去，导致 `ves` 变成一个巨大的字符串，`ajaxm.php` 直接拒绝。

本项目改成了 `[\w.\-]+` 并在测试里锁死了这个回归：

```
tests/test_extract.py::TestExtractSignsVesFid::test_ves_unquoted
```

---

## 📚 API 参考 / API Reference

### `lanzou_direct.py`

| 名称 | 说明 |
|---|---|
| `parse(share_url, pwd='', *, resolve=False, timeout=15, session=None, allow_browser_fallback=False)` | **主入口**，返回 `LanzouFile` |
| `resolve_real_url(middle_url, *, timeout, session)` | 跟随重定向拿最终 CDN 地址 |
| `is_lanzou_url(url)` | 判断是否蓝奏云分享链接（只认 http/https） |
| `LanzouFile` | 结果数据类：`direct_url` / `middle_url` / `name` / `size` / `share_url` / `real_url` / `raw`，含 `ok` / `to_dict()` / `to_json()` |
| `extract_sign` / `extract_signs` / `extract_ves` / `extract_ajax_path` / `extract_fid` / `extract_iframe` | 独立的 HTML 提取函数（便于自己组合或测试） |
| `NotLanzouUrl` / `PasswordRequired` / `ParseFailed` | 异常类型，三者的语义各不相同 |

### `browser_fallback.py`（可选）

| 名称 | 说明 |
|---|---|
| `parse_with_browser(share_url, pwd='', *, resolve, timeout, headless)` | 用无头浏览器解析，返回 `LanzouFile` |
| `render_page(url, *, pwd, timeout, headless, wait_seconds)` | 只渲染页面并返回 `{html, final_url, sign, signs, ves, fid, file_link, api}` |
| `PlaywrightUnavailable` | 没装 playwright 或找不到浏览器内核时抛出 |

浏览器内核尝试顺序：**系统 Edge → 系统 Chrome → Playwright 自带 Chromium**，
所以大多数 Windows 用户不用额外 `playwright install` 就能用。

---

## ❓ 常见问题 / FAQ

<details>
<summary><b>提示「未能从页面提取 sign」？</b> / "could not extract sign"?</summary>

说明蓝奏云又改版了，或者这个页面必须执行 JS 才有 `sign`。

1. 先试 `--browser` 用无头浏览器兜底
2. 还不行就是页面结构变了 —— 打开分享页按 F12，找 `sign` 出现在哪，
   往 `lanzou_direct.py` 的 `extract_sign()` 里的 `patterns` 列表前面加一条正则即可
3. 提 Issue 时请附上 **分享页的 HTML 片段**（不要发文件本身）
</details>

<details>
<summary><b>提示「该分享需要提取码」？</b> / "extraction code required"?</summary>

加 `--pwd <提取码>`。本库对「需要提取码」和「真的解析失败」做了区分（`PasswordRequired` vs `ParseFailed`），
方便你在脚本里分别处理。
</details>

<details>
<summary><b>拿到直链了，但浏览器打开 403？</b> / Got the link but it 403s?</summary>

见上文 [为什么直链粘进浏览器有时 403](#为什么直链粘进浏览器有时-403--why-the-browser-sometimes-403s)，用 `--resolve` 或 `--download`。
</details>

<details>
<summary><b>支持文件夹分享吗？</b> / Folder shares?</summary>

不支持。文件夹分享的页面结构是另一套（要遍历文件列表再逐个换 `sign`），
本库只处理**单文件分享**。这是有意的取舍 —— 保持核心逻辑可读可测。
</details>

<details>
<summary><b>为什么不内置第三方解析 API？</b> / Why no third-party API?</summary>

来源脚本里有一批**第三方解析接口**（别人的免费 API）。本库**刻意没有采用**，因为：

- 那是别人的免费服务，拿去打包进开源项目会给人造成压力和滥用风险
- 第三方接口随时可能限流、下线、或偷偷记录你提交的链接
- 直连解析（本库的做法）才是最稳、最不依赖外部的方案

所以本库**只有 `requests` 一个依赖**，所有解析都在本地完成。
</details>

---

## ⚠️ 注意事项与免责声明 / Disclaimer

**中文**

1. **本库只做一件事**：读取公开分享页 → 换取蓝奏云自己返回的直链。
   它**不绕过付费、不破解提取码、不修改蓝奏云任何数据**。
2. **请遵守蓝奏云的服务条款**，仅用于你自己的文件或已获授权的分享。
   请勿用于批量抓取、绕过限速牟利等用途。
3. **蓝奏云前端随时可能改版**，届时正则需要相应更新。
4. **本项目与蓝奏云（Lanzou）官方无任何关系。**
5. 下载的文件由分享者提供，请自行判断安全性。

**English**

1. **This library does one thing**: read a public share page and exchange it for
   the direct URL Lanzou itself returns. It does **not** bypass payment, crack
   extraction codes, or modify any Lanzou data.
2. **Respect Lanzou's Terms of Service.** Use it only on your own files or shares
   you are authorised to access. No bulk scraping or rate-limit circumvention.
3. **Lanzou changes its front-end regularly**; the regexes may need updating.
4. **Not affiliated with Lanzou in any way.**
5. Downloaded files come from whoever shared them — judge their safety yourself.

---

## 📄 License

[MIT](LICENSE) © 2026 The lanzou-direct-link Authors

---

## 🔗 相关项目 / Related

- **[chromium-pref-hash](https://github.com/CCWP123/chromium-pref-hash)** —
  Chromium `Secure Preferences` HMAC 签名算法的纯 Python 实现
- **[steam-ext-installer](https://github.com/CCWP123/steam-ext-installer)** —
  一键把 `.crx` 扩展装进 Steam 内置浏览器
