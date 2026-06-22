# -*- coding: utf-8 -*-
"""
网络言论法律分析工具 —— 命令行入口

两种分析入口：
  1) 文本：直接输入一段文字进行分析
  2) 公众号文章：输入 https://mp.weixin.qq.com/s/xxxxxx，自动抓取正文后分析

用法：
  交互模式：    python main.py
  直接传参：    python main.py "这家公司的产品全是假货，老板就是个骗子"
               python main.py https://mp.weixin.qq.com/s/xxxxxx

说明：
  - 传入的参数若是公众号链接（mp.weixin.qq.com/s/）则走抓取流程，否则按文本分析。
  - 文章正文超过 3000 字时，只截取前 3000 字分析，并在报告里注明“以下为文章节选分析”。
  - 运行前需在 analyzer.py 顶部配置 DeepSeek API Key（或设置环境变量 DEEPSEEK_API_KEY）。
"""

import os
import re
import sys

os.environ.setdefault("USE_TF", "0")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from analyzer import analyze, generate_report
from fetcher import fetch_article, FetchError

MAX_CHARS = 3000
WECHAT_RE = re.compile(r"^https?://mp\.weixin\.qq\.com/s/", re.I)


def is_wechat_url(s):
    return bool(WECHAT_RE.match(s.strip()))


def get_raw_input(argv):
    """返回用户输入（文本或URL）。无参数时进入交互菜单。"""
    if len(argv) > 1:
        return " ".join(argv[1:]).strip()

    print("=" * 50)
    print("网络言论法律分析工具")
    print("=" * 50)
    print("请选择输入方式：")
    print("  1) 输入文本内容")
    print("  2) 输入微信公众号文章链接")
    choice = input("请输入 1 或 2（默认 1）：").strip()
    if choice == "2":
        return input("请粘贴公众号文章链接：").strip()
    print("请输入文本内容（输入后回车）：")
    return input().strip()


def resolve_text(raw):
    """
    把原始输入解析为待分析文本。
    返回：(text, excerpt, source)
      excerpt: 是否为节选（截断到 3000 字）
      source:  公众号来源信息 dict 或 None
    """
    source = None
    if is_wechat_url(raw):
        print("\n检测到公众号链接，正在抓取正文……")
        try:
            art = fetch_article(raw)
        except FetchError as e:
            print(f"\n[抓取失败] {e}")
            print("→ 请手动复制文章正文，然后用文本模式重新运行：")
            print('   python main.py "在此粘贴文章正文"')
            return None, False, None
        print(f"抓取成功：《{art['title'] or '无标题'}》"
              f"（公众号：{art['account'] or '未知'}，正文 {len(art['text'])} 字，"
              f"图片说明 {art['n_img_alt']} 条）")
        text = art["text"]
        source = {"title": art["title"], "account": art["account"], "url": raw}
    else:
        text = raw

    excerpt = False
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        excerpt = True
        print(f"（正文较长，已截取前 {MAX_CHARS} 字进行分析）")

    return text, excerpt, source


def main():
    raw = get_raw_input(sys.argv)
    if not raw:
        print("未输入内容，已退出。")
        return

    text, excerpt, source = resolve_text(raw)
    if not text:
        return

    print("\n正在分析，请稍候……\n")
    try:
        result = analyze(text)
    except Exception as e:
        print(f"[分析失败] {e}")
        return

    # 控制台摘要
    print("=" * 50)
    print("分析完成")
    print("=" * 50)
    if source:
        print("来源：公众号文章 -", source.get("title") or raw)
    print("检索角度：", " / ".join(result["angles"]))
    print("候选法条数：", result["candidate_count"])
    print("是否违法：", result["is_illegal"])
    print("风险等级：", result["risk_level"])
    if result["articles"]:
        print("涉及法条：")
        for i, a in enumerate(result["articles"], 1):
            print(f"  {i}. 《{a['law']}》{a['article']}")
    else:
        print("涉及法条：（无明确适用条款）")
    if result.get("escalation"):
        print("风险升级提示：")
        for e in result["escalation"]:
            print(f"  - 《{e['law']}》{e['article']}")

    # 生成报告
    path = generate_report(text, result, excerpt=excerpt, source=source)
    print("\n报告已生成：", path)


if __name__ == "__main__":
    main()
