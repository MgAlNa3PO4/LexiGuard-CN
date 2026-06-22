# -*- coding: utf-8 -*-
"""
微信公众号文章抓取模块

输入：公众号文章 URL（https://mp.weixin.qq.com/s/xxxxxx）
输出：清洗后的正文纯文本（去广告/菜单/版权声明等），并补充图片 alt 文字作为上下文。

用法：
    from fetcher import fetch_article, FetchError
    result = fetch_article(url)   # 返回 dict：title / account / text / n_img_alt
    # 失败时抛出 FetchError，调用方据此提示用户手动复制文章内容。
"""

import re

import requests
from bs4 import BeautifulSoup

# 模拟浏览器请求头（公众号会拒绝无 UA / 非浏览器的请求）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://mp.weixin.qq.com/",
}

# 公众号常见的无关样板文字（菜单、引导关注、版权声明等），整行匹配则丢弃
BOILERPLATE_PATTERNS = [
    r"^预览时标签不可点$",
    r"^微信扫一扫.*$",
    r"^长按.*(识别|关注|二维码).*$",
    r"^扫描二维码.*$",
    r"^点击上方.*关注.*$",
    r"^点击.*蓝字.*关注.*$",
    r"^关注我们?$",
    r"^喜欢此内容的人还喜欢$",
    r"^阅读原文$",
    r"^继续滑动看下一个$",
    r"^向上滑动看下一个$",
    r"^轻触阅读原文$",
    r"^分享$|^赞$|^在看$|^收藏$|^点赞$|^写留言$|^留言$",
    r"^版权声明.*$",
    r"^免责声明.*$",
    r"^来源[:：].{0,40}$",
    r"^编辑[:：].{0,20}$",
    r"^责编[:：].{0,20}$",
    r"^投稿.*$",
    r"^商务合作.*$",
    r"^广告$",
]
_BOILERPLATE_RE = [re.compile(p) for p in BOILERPLATE_PATTERNS]

MAX_CHARS = 3000  # 超过则截取前 N 字分析（实际截断在 main.py 处理，这里仅提供常量）


class FetchError(Exception):
    """抓取/解析失败。"""


def _is_boilerplate(line):
    for r in _BOILERPLATE_RE:
        if r.match(line):
            return True
    return False


def _clean_text(raw):
    """按行清洗：去空行、去样板、压缩空白。"""
    lines = []
    seen = set()
    for line in raw.splitlines():
        line = re.sub(r"[​‌‍﻿]", "", line)  # 零宽字符
        line = re.sub(r"[ \t\xa0　]+", " ", line).strip()
        if not line:
            continue
        if _is_boilerplate(line):
            continue
        # 连续完全重复的行去重（公众号常见重复标语）
        if line in seen and len(line) < 20:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def fetch_article(url, timeout=20):
    """
    抓取并清洗公众号文章正文。
    返回 dict：{title, account, text, n_img_alt}
    失败抛出 FetchError。
    """
    url = (url or "").strip()
    if not re.match(r"^https?://mp\.weixin\.qq\.com/s/", url):
        raise FetchError(
            "URL 格式不正确。应为公众号文章链接：https://mp.weixin.qq.com/s/xxxxxx"
        )

    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise FetchError(f"网络请求失败：{e}")

    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    # 标题
    title = ""
    h1 = soup.find(id="activity-name") or soup.find("h1", class_="rich_media_title")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()

    # 公众号名称
    account = ""
    acc = soup.find(id="js_name")
    if acc:
        account = acc.get_text(strip=True)

    # 正文容器
    content = soup.find(id="js_content") or soup.find("div", class_="rich_media_content")
    if content is None:
        # 可能是文章被删除/需要验证/不是文章页
        hint = ""
        if "环境异常" in resp.text or "去验证" in resp.text:
            hint = "（页面要求人机验证）"
        elif "该内容已被发布者删除" in resp.text or "此内容因违规无法查看" in resp.text:
            hint = "（文章已被删除或违规下架）"
        raise FetchError(
            f"未能在页面中找到文章正文{hint}。请手动复制文章文字后，用文本模式分析。"
        )

    # 去掉脚本/样式
    for tag in content.find_all(["script", "style"]):
        tag.decompose()

    # 提取图片 alt 文字（补充上下文）
    img_alts = []
    for img in content.find_all("img"):
        alt = (img.get("alt") or "").strip()
        if alt and len(alt) >= 2 and not _is_boilerplate(alt):
            img_alts.append(alt)

    # 提取正文文字
    body = content.get_text(separator="\n")
    body = _clean_text(body)

    text = body
    if img_alts:
        text = body + "\n" + "\n".join(f"【图片说明】{a}" for a in img_alts)

    if len(text.strip()) < 30:
        raise FetchError(
            "抓取到的正文内容过少，可能是图片型文章或解析失败。请手动复制文章文字后，用文本模式分析。"
        )

    return {
        "title": title,
        "account": account,
        "text": text.strip(),
        "n_img_alt": len(img_alts),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法：python fetcher.py <公众号文章URL>")
        sys.exit(0)
    try:
        r = fetch_article(sys.argv[1])
        print("标题：", r["title"])
        print("公众号：", r["account"])
        print("图片alt数：", r["n_img_alt"])
        print("正文字数：", len(r["text"]))
        print("-" * 40)
        print(r["text"][:1000])
    except FetchError as e:
        print("[抓取失败]", e)
