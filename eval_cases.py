# -*- coding: utf-8 -*-
"""
判例评测集：用 12 个最高法网络暴力典型案例验证系统判断准确率。

做法：
  - 把每个案例的「涉案言论」输入分析系统（analyzer.analyze）
  - 对比系统判断（is_illegal: 是/否/存疑）与法院判决（全部为违法/犯罪/侵权，期望=是）
  - 计算准确率 = 判断正确数 / 总数

运行：USE_TF=0 python eval_cases.py
结果同时写出到 case_data/eval_result.json
"""

import os

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_TORCH", "1")

import sys
import json
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from analyzer import analyze

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CASES_JSON = os.path.join(BASE_DIR, "case_data", "cases.json")
OUT_JSON = os.path.join(BASE_DIR, "case_data", "eval_result.json")


def main():
    with open(CASES_JSON, "r", encoding="utf-8") as f:
        cases = json.load(f)

    # 法院对全部 12 案均认定违法/犯罪/侵权 → 系统期望判定为「是」
    rows = []
    correct = 0
    for c in cases:
        text = c["speech_content"]
        try:
            res = analyze(text)
            sys_illegal = res.get("is_illegal", "存疑")
            risk = res.get("risk_level", "")
            arts = "；".join(f"《{a['law']}》{a['article']}" for a in res.get("articles", [])[:3])
        except Exception as e:
            sys_illegal, risk, arts = f"[错误]{e}", "", ""
        expected = "是" if c["is_illegal"] else "否"
        is_correct = (sys_illegal == expected)
        if is_correct:
            correct += 1
        rows.append(
            {
                "case_id": c["case_id"],
                "case_name": c["case_name"],
                "court_finding": c["court_finding"],
                "verdict": c["verdict"],
                "expected": expected,
                "system_is_illegal": sys_illegal,
                "system_risk": risk,
                "system_articles": arts,
                "correct": is_correct,
            }
        )
        mark = "✓正确" if is_correct else "✗偏差"
        print(f"[{c['case_id']:>2}] {mark}  系统={sys_illegal}/{risk}  期望={expected}  {c['case_name']}")
        time.sleep(0.5)

    total = len(cases)
    acc = correct / total if total else 0.0
    print("\n" + "=" * 60)
    print(f"总体准确率：{correct}/{total} = {acc:.1%}")
    print("=" * 60)

    summary = {"total": total, "correct": correct, "accuracy": round(acc, 4), "rows": rows}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写出：{OUT_JSON}")


if __name__ == "__main__":
    main()
