# LexiGuard-CN · 中文网络暴力言论法律风险检测

> 输入一段中文文字，自动判断是否构成网络暴力/违法言论，
> 给出具体法律依据、话语证据对应和司法判例参考。

## 功能特性
- 支持直接输入文本或公众号文章 URL
- 基于 11 部中国法律法规（2278 条条文）的 RAG 检索
- 引用最高人民法院真实判例（12 个典型案例）对标司法实践
- 输出：是否违法 / 风险等级 / 涉及法条 / 话语证据 / 参考判例 / 风险升级提示
- 一键生成宋体格式 Word 分析报告
- Gradio 网页界面，支持本地和云端部署

## 技术栈
- 检索：BAAI/bge-small-zh-v1.5 + BM25 混合检索 + RRF 融合
- 向量库：ChromaDB
- 大模型：DeepSeek API（兼容 OpenAI 格式）
- 框架：LangChain + Gradio
- 文档：python-docx

## 快速开始

### 本地部署
```bash
git clone https://github.com/你的用户名/LexiGuard-CN.git
cd LexiGuard-CN
pip install -r requirements.txt

# 设置 API Key
export DEEPSEEK_API_KEY="your_key_here"

# 构建知识库（首次运行必须）
python build_knowledge_base.py

# 启动网页界面
python app.py
```
访问 http://localhost:7860

### 服务器部署（无 GPU）
```bash
pip install -r requirements_server.txt
```

## 项目结构
```
LexiGuard-CN/
├── app.py                    # Gradio 网页界面
├── analyzer.py               # 分析 Agent 核心
├── search.py                 # 混合检索模块
├── fetcher.py                # 公众号文章抓取
├── build_knowledge_base.py   # 知识库构建（离线运行）
├── main.py                   # 命令行入口
├── eval_cases.py             # 评测脚本
├── requirements.txt          # 本地依赖
├── requirements_server.txt   # 服务器 CPU 版依赖
├── 国家法律法规数据/          # 11 部法律法规原文
├── 典型案例及判决/            # 最高法典型判例
└── docs/
    └── technical-summary.md  # 项目技术总结
```

## 注意事项
- 本工具分析结果仅供参考，不构成法律意见
- 最终法律定性建议由专业人士复核
- 仅支持中文内容检测
- 知识库基于中国现行法律法规，境外法律不适用

## License
MIT License

---

# LexiGuard-CN · Chinese Online Hate Speech Legal Risk Detector

> Automatically analyzes Chinese text to detect potential
> online violence/illegal speech, with legal references,
> evidence mapping, and judicial case citations.

## Features
- Supports direct text input or WeChat public account URLs
- RAG retrieval from 11 Chinese laws (2,278 articles)
- References 12 real Supreme Court cases for judicial alignment
- Output: legality verdict / risk level / applicable laws /
  speech evidence / case references / escalation warnings
- One-click Word report generation
- Gradio web UI for local and cloud deployment

## Tech Stack
- Retrieval: BAAI/bge-small-zh-v1.5 + BM25 hybrid retrieval + RRF fusion
- Vector store: ChromaDB
- LLM: DeepSeek API (OpenAI-compatible)
- Framework: LangChain + Gradio
- Document: python-docx

## Quick Start

### Local
```bash
git clone https://github.com/your-username/LexiGuard-CN.git
cd LexiGuard-CN
pip install -r requirements.txt

# Set API Key
export DEEPSEEK_API_KEY="your_key_here"

# Build the knowledge base (required on first run)
python build_knowledge_base.py

# Launch the web UI
python app.py
```
Visit http://localhost:7860

### Server deployment (CPU-only)
```bash
pip install -r requirements_server.txt
```

## Disclaimer
Results are for reference only and do not constitute
legal advice. Final determination should be made by
qualified legal professionals.

## License
MIT License
