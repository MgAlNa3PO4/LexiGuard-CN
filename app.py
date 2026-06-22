# -*- coding: utf-8 -*-
"""
LexiGuard-CN · 网络言论法律风险检测 —— Gradio 网页界面（浅色主题）

启动：python app.py
浏览器访问：http://localhost:7860

调用现有模块：
  - analyzer.py：decompose_query / retrieve_candidates / judge / generate_report
  - fetcher.py ：fetch_article（公众号文章抓取）
"""

import os

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # 关闭联网遥测（内网部署）

import gradio as gr

from analyzer import decompose_query, retrieve_candidates, judge, generate_report
from search import search_cases
from fetcher import fetch_article, FetchError

MAX_CHARS = 3000

# ---------------- 配色方案（政务风） ----------------
PRIMARY = "#1a3a5c"        # 主色：深蓝
ACCENT = "#e63946"         # 强调色：红（高风险）
RISK_COLORS = {"高": "#e63946", "中": "#f4a261", "低": "#2a9d8f", "无": "#9aa5b1"}
ILLEGAL_COLORS = {"是": "#e63946", "存疑": "#9aa5b1", "否": "#2a9d8f"}
LAW_BLUE = "#1a5fb4"

CSS = """
.gradio-container {background:#f5f7fa !important; max-width:1320px !important;}
footer {display:none !important;}
#analyze-btn {background:#1a3a5c !important; border:none !important; color:#fff !important;}
#analyze-btn:hover {background:#244b73 !important;}
#app-banner, #app-banner * {color:#ffffff !important;}
"""

# 强制浅色主题（不随浏览器深色模式切换），满足政务风浅色要求
FORCE_LIGHT_JS = """
function() {
    const url = new URL(window.location);
    if (url.searchParams.get('__theme') !== 'light') {
        url.searchParams.set('__theme', 'light');
        window.location.href = url.href;
    }
}
"""


# ---------------- HTML 片段 ----------------
def _tag(text, color):
    return (
        f"<span style='display:inline-block;background:{color};color:#fff;font-weight:bold;"
        f"padding:4px 16px;border-radius:14px;font-size:15px;'>{text}</span>"
    )


def _card(title, body_html):
    return (
        "<div style='background:#ffffff;border:1px solid #e3e8ef;border-radius:10px;"
        "box-shadow:0 1px 4px rgba(0,0,0,0.06);padding:16px 20px;margin-bottom:16px;'>"
        f"<div style='font-size:16px;font-weight:bold;color:{PRIMARY};border-left:4px solid {PRIMARY};"
        f"padding-left:10px;margin-bottom:14px;'>{title}</div>"
        f"{body_html}</div>"
    )


def _placeholder(msg="请输入内容并点击「开始分析」"):
    return (
        "<div style='background:#ffffff;border:1px dashed #c7d0db;border-radius:10px;"
        "padding:48px 20px;text-align:center;color:#8895a7;font-size:15px;'>"
        f"{msg}</div>"
    )


def _result_html(result):
    blocks = []

    # 1) 分析结论
    illegal = result.get("is_illegal", "存疑")
    risk = result.get("risk_level", "无")
    conclusion = (
        "<div style='line-height:2.4;font-size:15px;'>"
        f"<span style='color:#555;'>是否违法：</span>&nbsp;{_tag(illegal, ILLEGAL_COLORS.get(illegal, '#9aa5b1'))}"
        "&nbsp;&nbsp;&nbsp;&nbsp;"
        f"<span style='color:#555;'>风险等级：</span>&nbsp;{_tag(risk, RISK_COLORS.get(risk, '#9aa5b1'))}"
        "</div>"
    )
    blocks.append(_card("分析结论", conclusion))

    # 2) 话语证据与法条对应（每条独立卡片，左侧蓝色竖线）
    blocks.append(
        f"<div style='font-size:16px;font-weight:bold;color:{PRIMARY};margin:4px 0 12px 2px;'>"
        "话语证据与法条对应</div>"
    )
    evidence = result.get("evidence_mapping", [])
    if not evidence:
        blocks.append(_placeholder("（未提取到明确的话语证据）"))
    else:
        for i, e in enumerate(evidence, 1):
            blocks.append(
                "<div style='background:#ffffff;border:1px solid #e3e8ef;"
                f"border-left:4px solid {LAW_BLUE};border-radius:8px;"
                "box-shadow:0 1px 3px rgba(0,0,0,0.06);padding:12px 16px;margin-bottom:12px;'>"
                f"<div style='font-weight:bold;color:{PRIMARY};margin-bottom:6px;'>证据{i}</div>"
                f"<div style='color:#1f2937;margin-bottom:8px;line-height:1.6;'>“{e['quote']}”</div>"
                "<div style='color:#b45309;font-weight:bold;margin-bottom:6px;'>↓ 涉嫌违反</div>"
                f"<div style='color:{LAW_BLUE};font-weight:bold;margin-bottom:6px;'>"
                f"《{e['law']}》{e['article']}</div>"
                f"<div style='color:#4b5563;line-height:1.6;'>说明：{e.get('reason') or '（无）'}</div>"
                "</div>"
            )

    # 3) 涉及法条
    articles = result.get("articles", [])
    if not articles:
        art_body = "<span style='color:#8895a7;'>（未匹配到明确适用的法律条款）</span>"
    else:
        rows = []
        for i, a in enumerate(articles, 1):
            rows.append(
                "<div style='margin-bottom:14px;'>"
                f"<div style='font-weight:bold;color:#1f2937;margin-bottom:4px;'>{i}. 《{a['law']}》{a['article']}</div>"
                f"<div style='color:#4b5563;margin-bottom:2px;line-height:1.6;'>条文内容：{a['content']}</div>"
                f"<div style='color:{LAW_BLUE};line-height:1.6;'>违法说明：{a.get('reason') or '（无）'}</div>"
                "</div>"
            )
        art_body = "".join(rows)
    blocks.append(_card("涉及法条", art_body))

    # 3.5) 参考判例（放在涉及法条之后）
    reference_cases = result.get("reference_cases", [])
    if not reference_cases:
        ref_body = "<span style='color:#8895a7;'>（未匹配到可参考的典型判例）</span>"
    else:
        rrows = []
        for i, rc in enumerate(reference_cases, 1):
            sim = rc.get("similarity")
            sim_txt = f"（相似度{sim}%）" if sim is not None else ""
            speech = (rc.get("speech") or "").strip()
            if len(speech) > 60:
                speech = speech[:60] + "……"
            rrows.append(
                "<div style='margin-bottom:14px;padding-left:12px;border-left:3px solid #2563eb;'>"
                f"<div style='font-weight:bold;color:#1f2937;margin-bottom:4px;'>案例{i}：{rc.get('case_name','')}{sim_txt}</div>"
                f"<div style='color:#4b5563;margin-bottom:2px;line-height:1.6;'>涉案言论：“{speech}”</div>"
                f"<div style='color:#4b5563;margin-bottom:2px;line-height:1.6;'>判决结果：{rc.get('verdict') or '（无）'}</div>"
                f"<div style='color:{LAW_BLUE};line-height:1.6;'>参考意义：{rc.get('relevance') or '（无）'}</div>"
                "</div>"
            )
        ref_body = "".join(rrows)
    blocks.append(_card("参考判例", ref_body))

    # 4) 处理建议与风险预警（合并）
    adv = [
        f"<div style='margin-bottom:4px;line-height:1.7;'><b style='color:{PRIMARY};'>当前建议：</b>"
        f"{result.get('suggestion') or '（无）'}</div>",
        "<hr style='border:none;border-top:1px dashed #cdd5df;margin:12px 0;'>",
    ]
    escalation = result.get("escalation", [])
    if not escalation:
        adv.append("<div><b style='color:#b45309;'>风险预警：</b>当前未发现明显的升级风险。</div>")
    else:
        adv.append(
            "<div style='margin-bottom:6px;'><b style='color:#b45309;'>风险预警：</b>"
            "当前定性虽未构成，但若出现以下情况可能升级：</div>"
        )
        for e in escalation:
            adv.append(
                "<div style='margin-top:8px;padding-left:12px;border-left:3px solid #f4a261;'>"
                f"<div style='color:{LAW_BLUE};font-weight:bold;'>《{e['law']}》{e['article']}</div>"
                f"<div style='color:#4b5563;line-height:1.6;'>升级条件：{e.get('condition') or '（无）'}</div>"
                f"<div style='color:#4b5563;line-height:1.6;'>建议：{e.get('suggestion') or '（无）'}</div>"
                "</div>"
            )
    blocks.append(_card("处理建议与风险预警", "".join(adv)))

    return "<div>" + "".join(blocks) + "</div>"


def _status(msg, color=PRIMARY):
    return f"<div style='font-size:14px;color:{color};padding:4px 0;'>{msg}</div>"


# ---------------- 分析主流程（生成器，带进度） ----------------
def run_analysis(text_input, url_input, active_tab):
    """输出 = [状态, 结果HTML, 下载文件]。"""
    analyzing = _placeholder("正在分析，请稍候…")

    source = None
    if active_tab == "link":
        url = (url_input or "").strip()
        if not url:
            yield _status("请先粘贴文章链接。", ACCENT), _placeholder(), None
            return
        yield _status("正在抓取文章正文…"), analyzing, None
        try:
            art = fetch_article(url)
        except FetchError as e:
            yield _status(f"抓取失败：{e} 请改用「粘贴文本」手动录入。", ACCENT), _placeholder(), None
            return
        content = art["text"]
        source = {"title": art["title"], "account": art["account"], "url": url}
    else:
        content = (text_input or "").strip()
        if not content:
            yield _status("请先输入要分析的文本内容。", ACCENT), _placeholder(), None
            return

    excerpt = False
    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS]
        excerpt = True

    try:
        yield _status("正在拆解检索角度…"), analyzing, None
        angles = decompose_query(content)
        yield _status("正在检索法条…"), analyzing, None
        candidates = retrieve_candidates(angles)
        try:
            case_candidates = search_cases(content, top_k=3)
        except Exception:
            case_candidates = []
        yield _status("正在分析内容…"), analyzing, None
        verdict = judge(content, candidates, case_candidates)
    except Exception as e:
        yield _status(f"分析失败：{e}", ACCENT), _placeholder(), None
        return

    result = {"text": content, "angles": angles, "candidate_count": len(candidates), **verdict}
    path = generate_report(content, result, excerpt=excerpt, source=source)

    done = "分析完成"
    if excerpt:
        done += "（正文较长，仅分析前 3000 字）"
    if source:
        done += f"　来源：《{source['title'] or '文章'}》"
    yield _status(done, "#2a9d8f"), _result_html(result), path


# ---------------- 界面 ----------------
def build_ui():
    with gr.Blocks(title="LexiGuard-CN 网络言论法律风险检测", theme=gr.themes.Soft(),
                   css=CSS, js=FORCE_LIGHT_JS) as demo:
        # 顶部标题栏
        gr.HTML(
            f"<div id='app-banner' style='background:{PRIMARY};padding:20px 28px;border-radius:12px;"
            "margin-bottom:18px;'>"
            "<div style='font-size:24px;font-weight:bold;letter-spacing:1px;color:#ffffff;'>"
            "LexiGuard-CN · 网络言论法律风险检测</div>"
            "<div style='font-size:14px;margin-top:6px;color:#ffffff;'>"
            "中文文本法律风险智能检测 · 法条检索与司法判例对标</div>"
            "</div>"
        )

        active_tab = gr.State("link")

        with gr.Row():
            # 左侧 1/3：输入区
            with gr.Column(scale=1):
                with gr.Group():
                    with gr.Tabs():
                        with gr.TabItem("输入链接") as tab_link:
                            url_in = gr.Textbox(
                                label="文章链接（适用于公众号文章、网页等）",
                                placeholder="https://mp.weixin.qq.com/s/xxxxxx",
                                lines=1,
                            )
                        with gr.TabItem("粘贴文本") as tab_text:
                            text_in = gr.Textbox(
                                label="文本内容（适用于评论、帖子、文章等）",
                                placeholder="在此粘贴评论、帖子或文章正文…",
                                lines=12,
                            )
                    analyze_btn = gr.Button("开始分析", variant="primary", size="lg", elem_id="analyze-btn")
                    status_box = gr.HTML(value=_status("等待输入…", "#8895a7"))
                    file_out = gr.File(label="下载 Word 报告", interactive=False)

            # 右侧 2/3：结果区
            with gr.Column(scale=2):
                result_html = gr.HTML(value=_placeholder())

        tab_text.select(lambda: "text", outputs=active_tab)
        tab_link.select(lambda: "link", outputs=active_tab)

        # 底部免责声明
        gr.HTML(
            "<div style='text-align:center;color:#8895a7;font-size:13px;margin-top:18px;"
            "padding:14px;border-top:1px solid #e3e8ef;'>"
            "本工具分析结果仅供参考，不构成法律意见，最终定性建议由专业法律人士复核"
            "</div>"
        )

        analyze_btn.click(
            fn=run_analysis,
            inputs=[text_in, url_in, active_tab],
            outputs=[status_box, result_html, file_out],
        )

    return demo


if __name__ == "__main__":
    demo = build_ui()
    demo.queue()
    demo.launch(server_name="0.0.0.0", server_port=7860, show_error=True)
