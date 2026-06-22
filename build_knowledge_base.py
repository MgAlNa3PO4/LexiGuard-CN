# -*- coding: utf-8 -*-
"""
网络言论法律分析工具 —— RAG 知识库构建脚本（混合检索版）

优化要点：
1. 向量模型改用 BAAI/bge-small-zh-v1.5（中文检索专用），query 端加指令前缀
   "为这个句子生成表示以用于检索相关文章："，passage 端不加前缀。
2. 混合检索：向量召回（ChromaDB）+ BM25 关键词召回（jieba 分词），用 RRF 融合，
   弥补 "罢课""谣言" 等关键词的精确匹配。
3. embedding 只编码条文正文；法律名称与前后上下文仅作展示，不参与向量化。

切分仍以 "第X条" 为单位（含 "第X条之一" 型），跳过目录与章/节标题，
每条保留前后各一句上下文（存入 metadata 供展示）。

直接运行：python build_knowledge_base.py
"""

import os

# 强制 transformers 使用 PyTorch 后端，避免环境中 TensorFlow/Keras3 冲突
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_TORCH", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

import re
import sys
import glob
import json

from docx import Document
import numpy as np
import chromadb
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi
import jieba

jieba.setLogLevel(20)  # 关闭 jieba 启动日志

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
# 仅依赖本文件所在目录，文件夹改名也无需改代码
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.path.join(BASE_DIR, "law_data")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
COLLECTION_NAME = "laws"
RRF_K = 60          # RRF 融合常数
POOL = 50           # 每路召回候选池大小

# 判例库（与 "laws" 并列的第二个 ChromaDB 集合）。
# cases.json 由典型案例 docx 结构化提取而来（见 case_data/cases.json）。
CASES_JSON = os.path.join(BASE_DIR, "case_data", "cases.json")
CASE_COLLECTION_NAME = "cases"
CASE_VEC_PATH = os.path.join(CHROMA_DIR, "case_vectors.npy")
CASE_CHUNK_PATH = os.path.join(CHROMA_DIR, "case_chunks.json")

# 向量/语料 sidecar（与 ChromaDB 同目录）。
# 说明：chroma-hnswlib 0.7.6 在本机 Windows/Py3.9 环境下不会把 HNSW 二进制索引落盘，
# 导致换进程后无法从 ChromaDB 读回向量。为保证 search.py 跨进程可靠加载，
# 这里把向量与语料另存为 sidecar，检索时从该 sidecar 读取（仍位于 chroma_db 目录内）。
VEC_PATH = os.path.join(CHROMA_DIR, "kb_vectors.npy")
CHUNK_PATH = os.path.join(CHROMA_DIR, "kb_chunks.json")

# 让 Windows 控制台正常输出中文
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 匹配条文开头：第X条 / 第X条之一  （X 为中文数字或阿拉伯数字）
ARTICLE_RE = re.compile(r"^第([一二三四五六七八九十百千零〇两\d]+)条(之[一二三四五六七八九十]+)?")
# 匹配章/节标题（结构性标题，需跳过）：第X章 / 第X节
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千零〇两\d]+[章节]")
# 中文句子结束符
SENT_END = re.compile(r"[。！？；]")


# ----------------------------------------------------------------------------
# 文本解析与切分
# ----------------------------------------------------------------------------
def law_name_from_filename(path):
    base = os.path.splitext(os.path.basename(path))[0]
    base = re.sub(r"_\d{6,8}$", "", base)  # 去掉 _20250627 之类日期
    return base


def read_paragraphs(path):
    doc = Document(path)
    return [p.text.strip() for p in doc.paragraphs if p.text.strip()]


def first_sentence(text):
    if not text:
        return ""
    m = SENT_END.search(text)
    return text[: m.end()] if m else text


def last_sentence(text):
    if not text:
        return ""
    parts = [s for s in SENT_END.split(text) if s.strip()]
    if not parts:
        return text
    tail = parts[-1]
    ends = SENT_END.findall(text)
    return (tail + (ends[-1] if ends else "")).strip()


def split_into_articles(paragraphs, law_name):
    """切分为条文，跳过目录与章/节标题，多段落合并到同一条。"""
    articles = []
    current = None
    started = False

    for para in paragraphs:
        if ARTICLE_RE.match(para):
            started = True
            if current is not None:
                articles.append(current)
            label = ARTICLE_RE.match(para).group(0)
            current = {"law": law_name, "article": label, "content_parts": [para]}
        elif CHAPTER_RE.match(para) and (current is not None):
            articles.append(current)
            current = None
        else:
            if started and current is not None:
                current["content_parts"].append(para)

    if current is not None:
        articles.append(current)

    for a in articles:
        full = "　".join(a.pop("content_parts"))
        full = re.sub(r"^(第[^条]*条(?:之[一二三四五六七八九十]+)?)[　\s]*", r"\1 ", full)
        a["content"] = full.strip()
    return articles


def build_chunks(articles):
    """生成 chunk：embedding 只用条文正文；法律名与前后上下文存 metadata 供展示。"""
    chunks = []
    for i, a in enumerate(articles):
        prev_ctx = last_sentence(articles[i - 1]["content"]) if i > 0 else ""
        next_ctx = first_sentence(articles[i + 1]["content"]) if i < len(articles) - 1 else ""
        chunks.append(
            {
                "id": f"{a['law']}::{a['article']}::{i}",
                "content": a["content"],          # 仅条文正文，用于向量化与 BM25
                "metadata": {
                    "law": a["law"],
                    "article": a["article"],
                    "content": a["content"],
                    "prev_context": prev_ctx,
                    "next_context": next_ctx,
                },
            }
        )
    return chunks


# ----------------------------------------------------------------------------
# 混合检索（向量 + BM25，RRF 融合）
# ----------------------------------------------------------------------------
def tokenize(text):
    return [t for t in jieba.lcut(text) if t.strip()]


def hybrid_search(query, model, collection, bm25, chunks, id2idx, emb_matrix,
                  topn=3, pool=POOL, k=RRF_K):
    """
    返回 list[dict]：{idx, rrf, cos, bm25, vec_rank, bm25_rank, meta}
    """
    # --- 向量召回 ---
    q_emb = model.encode([QUERY_PREFIX + query], normalize_embeddings=True)
    vres = collection.query(query_embeddings=q_emb, n_results=pool)
    vec_ids = vres["ids"][0]
    vec_idx_rank = [id2idx[i] for i in vec_ids]              # 按向量相似度排序的 chunk 下标
    cos_by_idx = {id2idx[i]: 1 - d for i, d in zip(vec_ids, vres["distances"][0])}

    # --- BM25 召回 ---
    q_tokens = tokenize(query)
    bm25_scores = bm25.get_scores(q_tokens)
    bm25_idx_rank = list(np.argsort(bm25_scores)[::-1][:pool])
    bm25_by_idx = {int(i): float(bm25_scores[i]) for i in bm25_idx_rank}

    # --- RRF 融合 ---
    vec_rank = {idx: r for r, idx in enumerate(vec_idx_rank)}
    bm_rank = {int(idx): r for r, idx in enumerate(bm25_idx_rank)}
    fused = {}
    for idx, r in vec_rank.items():
        fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + r + 1)
    for idx, r in bm_rank.items():
        fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + r + 1)

    order = sorted(fused, key=fused.get, reverse=True)[:topn]
    q_vec = q_emb[0]
    results = []
    for idx in order:
        cos = cos_by_idx.get(idx)
        if cos is None:  # 该条只在 BM25 池中命中，按需补算余弦相似度
            cos = float(q_vec @ emb_matrix[idx])
        results.append(
            {
                "idx": idx,
                "rrf": fused[idx],
                "cos": cos,
                "bm25": bm25_by_idx.get(idx, 0.0),
                "vec_rank": vec_rank.get(idx),
                "bm25_rank": bm_rank.get(idx),
                "meta": chunks[idx]["metadata"],
            }
        )
    return results


# ----------------------------------------------------------------------------
# 判例库构建（集合 "cases"）
# ----------------------------------------------------------------------------
def case_embed_text(case):
    """每个案例的 embedding 文本：言论类型 + 涉案言论 + 关键判断标准 拼接。"""
    return (
        f"言论类型：{case.get('speech_type', '')}。"
        f"涉案言论：{case.get('speech_content', '')}。"
        f"关键判断标准：{case.get('key_standard', '')}"
    )


def build_case_chunks(cases):
    """把案例列表转成 chunk：content 用于向量化，metadata 存完整案例信息。"""
    chunks = []
    for c in cases:
        emb_text = case_embed_text(c)
        meta = dict(c)              # 完整案例信息全部进 metadata（字段均为 str/bool，ChromaDB 兼容）
        meta["embed_text"] = emb_text
        chunks.append(
            {
                "id": f"case::{c.get('case_id')}",
                "content": emb_text,
                "metadata": meta,
            }
        )
    return chunks


def build_cases(model, client):
    """构建判例库集合 cases，并写出向量/语料 sidecar（供 search.py 跨进程加载）。"""
    if not os.path.exists(CASES_JSON):
        print(f"[提示] 未找到判例文件 {CASES_JSON}，跳过判例库构建。")
        return
    with open(CASES_JSON, "r", encoding="utf-8") as f:
        cases = json.load(f)
    if not cases:
        print("[提示] cases.json 为空，跳过判例库构建。")
        return

    print(f"\n构建判例库（集合 '{CASE_COLLECTION_NAME}'），共 {len(cases)} 个案例...")
    chunks = build_case_chunks(cases)
    texts = [c["content"] for c in chunks]
    emb = model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
    emb = np.asarray(emb, dtype=np.float32)

    try:
        client.delete_collection(CASE_COLLECTION_NAME)
    except Exception:
        pass
    coll = client.create_collection(
        name=CASE_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    coll.add(
        ids=[c["id"] for c in chunks],
        embeddings=[emb[i].tolist() for i in range(len(chunks))],
        documents=texts,
        metadatas=[c["metadata"] for c in chunks],
    )
    print(f"写入完成，集合 '{CASE_COLLECTION_NAME}' 共 {coll.count()} 条。")

    # sidecar：向量 + 完整案例语料（与向量按下标对齐）
    np.save(CASE_VEC_PATH, emb)
    sidecar = [dict(c["metadata"], id=c["id"]) for c in chunks]
    with open(CASE_CHUNK_PATH, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, ensure_ascii=False)
    print(f"已写出判例向量 sidecar：{CASE_VEC_PATH}（{emb.shape}）")
    print(f"已写出判例语料 sidecar：{CASE_CHUNK_PATH}")


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    docx_files = sorted(glob.glob(os.path.join(LAW_DIR, "*.docx")))
    if not docx_files:
        print(f"[错误] 在 {LAW_DIR} 未找到任何 .docx 文件")
        sys.exit(1)

    print(f"发现 {len(docx_files)} 个法律文件，开始解析...\n")

    all_chunks = []
    per_law_counts = []
    for path in docx_files:
        law_name = law_name_from_filename(path)
        articles = split_into_articles(read_paragraphs(path), law_name)
        chunks = build_chunks(articles)
        all_chunks.extend(chunks)
        per_law_counts.append((law_name, len(chunks)))
        print(f"  - {law_name}：{len(chunks)} 条")

    print(f"\n共解析 {len(docx_files)} 个文件，切分出 {len(all_chunks)} 个条文 chunk。\n")

    # --- 向量化（只编码条文正文，passage 端不加前缀）---
    print(f"加载向量模型：{MODEL_NAME}（首次运行会自动下载）...")
    model = SentenceTransformer(MODEL_NAME)
    contents = [c["content"] for c in all_chunks]
    print("正在编码条文正文...")
    emb_matrix = model.encode(
        contents, normalize_embeddings=True, batch_size=64, show_progress_bar=False
    )
    emb_matrix = np.asarray(emb_matrix, dtype=np.float32)

    # --- 写入 ChromaDB（覆盖旧集合）---
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    print(f"写入 ChromaDB（{CHROMA_DIR}）...")
    batch = 256
    for s in range(0, len(all_chunks), batch):
        part = all_chunks[s : s + batch]
        collection.add(
            ids=[c["id"] for c in part],
            embeddings=[emb_matrix[s + j].tolist() for j in range(len(part))],
            documents=[c["content"] for c in part],
            metadatas=[c["metadata"] for c in part],
        )
    print(f"写入完成，集合 '{COLLECTION_NAME}' 共 {collection.count()} 条。")

    # --- 写出向量/语料 sidecar（供 search.py 跨进程可靠加载）---
    np.save(VEC_PATH, emb_matrix)
    sidecar = [
        {
            "id": c["id"],
            "law": c["metadata"]["law"],
            "article": c["metadata"]["article"],
            "content": c["metadata"]["content"],
            "prev_context": c["metadata"]["prev_context"],
            "next_context": c["metadata"]["next_context"],
        }
        for c in all_chunks
    ]
    with open(CHUNK_PATH, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, ensure_ascii=False)
    print(f"已写出向量 sidecar：{VEC_PATH}（{emb_matrix.shape}）")
    print(f"已写出语料 sidecar：{CHUNK_PATH}\n")

    # --- 构建判例库（集合 cases，复用同一向量模型与 client）---
    build_cases(model, client)

    # --- 构建 BM25 索引（jieba 分词）---
    print("构建 BM25 关键词索引（jieba 分词）...")
    bm25 = BM25Okapi([tokenize(c) for c in contents])
    id2idx = {c["id"]: i for i, c in enumerate(all_chunks)}

    # ------------------------------------------------------------------
    # 召回验证
    # ------------------------------------------------------------------
    queries = [
        "在网络上散布谣言",
        "侮辱他人名誉",
        "煽动他人聚众扰乱秩序",
        "编造虚假信息在网络传播",
        "在公共场所起哄闹事",
    ]
    print("\n" + "=" * 74)
    print("混合检索召回验证（向量 + BM25 → RRF 融合，每个 query 取 top3）")
    print("=" * 74)
    for q in queries:
        res = hybrid_search(q, model, collection, bm25, all_chunks, id2idx, emb_matrix, topn=3)
        print(f"\n【Query】{q}")
        for rank, r in enumerate(res, 1):
            m = r["meta"]
            content = m["content"]
            snippet = content[:78] + ("..." if len(content) > 78 else "")
            vr = r["vec_rank"]
            br = r["bm25_rank"]
            src = []
            if vr is not None:
                src.append(f"向量#{vr + 1}")
            if br is not None:
                src.append(f"BM25#{br + 1}")
            print(
                f"  Top{rank} 《{m['law']}》{m['article']}  "
                f"[RRF {r['rrf']:.4f} | 余弦 {r['cos']:.3f} | BM25 {r['bm25']:.2f} | {'+'.join(src)}]"
            )
            print(f"        {snippet}")

    # --- 汇总 ---
    print("\n" + "=" * 74)
    print("汇总")
    print("=" * 74)
    print(f"处理法律文件数：{len(docx_files)}")
    print(f"切分条文 chunk 数：{len(all_chunks)}")
    for name, cnt in per_law_counts:
        print(f"  - {name}：{cnt}")
    print(f"\n向量模型：{MODEL_NAME}")
    print(f"检索方式：向量 + BM25 混合，RRF(k={RRF_K}) 融合")
    print(f"ChromaDB 持久化目录：{CHROMA_DIR}（集合名：{COLLECTION_NAME}）")


if __name__ == "__main__":
    main()
