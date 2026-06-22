# -*- coding: utf-8 -*-
"""
法条检索模块（混合检索：向量 + BM25，RRF 融合）

对外接口：
    from search import search
    results = search("在网络上散布谣言", top_k=5)

设计说明：
- 向量数据从 chroma_db 目录读取（kb_vectors.npy / kb_chunks.json sidecar，
  由 build_knowledge_base.py 写出；之所以用 sidecar 而非直接读 ChromaDB 的 HNSW，
  是因为 chroma-hnswlib 0.7.6 在本机 Windows/Py3.9 下不会把向量索引落盘，
  换进程后无法从 ChromaDB 读回向量）。
- BM25 索引在首次调用时从 1018 条 chunk 用 jieba 分词重建，并缓存在内存，
  之后的调用不再重复分词、不再重复加载模型。
- 向量检索：bge-small-zh-v1.5（query 端加指令前缀），1018 条规模直接做精确余弦，
  与 ChromaDB 同样的 cosine 排序结果且更稳定。
"""

import os

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_TORCH", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

import json

import numpy as np
import jieba
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

jieba.setLogLevel(20)

# ----------------------------------------------------------------------------
# 配置（与 build_knowledge_base.py 保持一致）
# ----------------------------------------------------------------------------
# 仅依赖本文件所在目录，文件夹改名也无需改代码
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")
VEC_PATH = os.path.join(CHROMA_DIR, "kb_vectors.npy")
CHUNK_PATH = os.path.join(CHROMA_DIR, "kb_chunks.json")

# 判例库 sidecar（集合 "cases"，由 build_knowledge_base.py 写出）
CASE_VEC_PATH = os.path.join(CHROMA_DIR, "case_vectors.npy")
CASE_CHUNK_PATH = os.path.join(CHROMA_DIR, "case_chunks.json")

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
RRF_K = 60
POOL = 50  # 每路召回候选池大小


def _tokenize(text):
    return [t for t in jieba.lcut(text) if t.strip()]


class Retriever:
    """单例式检索器：模型、向量、BM25 全部只加载/构建一次并缓存。"""

    _instance = None

    def __init__(self):
        if not os.path.exists(VEC_PATH) or not os.path.exists(CHUNK_PATH):
            raise FileNotFoundError(
                f"未找到向量库文件：\n  {VEC_PATH}\n  {CHUNK_PATH}\n"
                "请先运行 build_knowledge_base.py 构建知识库。"
            )
        # 语料（与向量按下标对齐）
        with open(CHUNK_PATH, "r", encoding="utf-8") as f:
            self.chunks = json.load(f)
        # 向量（已归一化，float32）
        self.vectors = np.load(VEC_PATH).astype(np.float32)
        assert len(self.chunks) == self.vectors.shape[0], "向量与语料数量不一致"

        # 向量模型（懒加载一次）
        self.model = SentenceTransformer(MODEL_NAME)

        # BM25 索引：启动时分词一次并缓存
        self.bm25 = BM25Okapi([_tokenize(c["content"]) for c in self.chunks])

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search(self, query, top_k=5, pool=POOL, k=RRF_K):
        query = (query or "").strip()
        if not query:
            return []

        # --- 向量召回（精确余弦）---
        q_emb = self.model.encode(
            [QUERY_PREFIX + query], normalize_embeddings=True
        )[0].astype(np.float32)
        cos = self.vectors @ q_emb  # 向量已归一化，点积即余弦
        vec_rank_idx = list(np.argsort(cos)[::-1][:pool])
        vec_rank = {int(idx): r for r, idx in enumerate(vec_rank_idx)}

        # --- BM25 召回 ---
        q_tokens = _tokenize(query)
        bm25_scores = self.bm25.get_scores(q_tokens)
        bm25_rank_idx = list(np.argsort(bm25_scores)[::-1][:pool])
        bm_rank = {int(idx): r for r, idx in enumerate(bm25_rank_idx)}

        # --- RRF 融合 ---
        fused = {}
        for idx, r in vec_rank.items():
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + r + 1)
        for idx, r in bm_rank.items():
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + r + 1)

        order = sorted(fused, key=fused.get, reverse=True)[:top_k]
        results = []
        for idx in order:
            c = self.chunks[idx]
            vr = vec_rank.get(idx)
            br = bm_rank.get(idx)
            src = []
            if vr is not None:
                src.append(f"向量#{vr + 1}")
            if br is not None:
                src.append(f"BM25#{br + 1}")
            results.append(
                {
                    "law": c["law"],
                    "article": c["article"],
                    "content": c["content"],
                    "prev_context": c["prev_context"],
                    "next_context": c["next_context"],
                    "rrf_score": round(float(fused[idx]), 5),
                    "cosine": round(float(cos[idx]), 4),
                    "bm25": round(float(bm25_scores[idx]), 3),
                    "sources": src,
                }
            )
        return results


def search(query, top_k=5):
    """对外主函数：返回与 query 最相关的 top_k 条法条。"""
    return Retriever.instance().search(query, top_k=top_k)


# ----------------------------------------------------------------------------
# 判例检索（集合 "cases"）
# ----------------------------------------------------------------------------
class CaseRetriever:
    """判例检索器：案例规模小（十余个），直接做精确余弦相似度即可。

    向量模型与法条检索器共用同一个 SentenceTransformer 实例，避免重复加载。
    """

    _instance = None

    def __init__(self):
        if not os.path.exists(CASE_VEC_PATH) or not os.path.exists(CASE_CHUNK_PATH):
            raise FileNotFoundError(
                f"未找到判例库文件：\n  {CASE_VEC_PATH}\n  {CASE_CHUNK_PATH}\n"
                "请先运行 build_knowledge_base.py 构建知识库（含判例库）。"
            )
        with open(CASE_CHUNK_PATH, "r", encoding="utf-8") as f:
            self.cases = json.load(f)
        self.vectors = np.load(CASE_VEC_PATH).astype(np.float32)
        assert len(self.cases) == self.vectors.shape[0], "判例向量与语料数量不一致"
        # 复用法条检索器已加载的向量模型
        self.model = Retriever.instance().model

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search(self, query, top_k=3):
        query = (query or "").strip()
        if not query:
            return []
        q_emb = self.model.encode(
            [QUERY_PREFIX + query], normalize_embeddings=True
        )[0].astype(np.float32)
        cos = self.vectors @ q_emb  # 向量已归一化，点积即余弦相似度
        order = list(np.argsort(cos)[::-1][:top_k])
        results = []
        for idx in order:
            c = dict(self.cases[idx])
            sim = float(cos[idx])
            c["cosine"] = round(sim, 4)
            c["similarity"] = max(0, round(sim * 100))  # 百分制相似度，供报告展示
            results.append(c)
        return results


def search_cases(query, top_k=3):
    """对外主函数：返回与 query 最相似的 top_k 个判例（含完整案例信息与相似度）。"""
    return CaseRetriever.instance().search(query, top_k=top_k)


if __name__ == "__main__":
    # 简单自测
    for q in ["在网络上散布谣言", "在公共场所起哄闹事", "编造虚假信息在网络传播"]:
        print(f"\n【Query】{q}")
        for i, r in enumerate(search(q, top_k=3), 1):
            print(
                f"  {i}. 《{r['law']}》{r['article']}  "
                f"[RRF {r['rrf_score']} | 余弦 {r['cosine']} | BM25 {r['bm25']} | {'+'.join(r['sources'])}]"
            )
            print(f"     {r['content'][:70]}...")
