# -*- coding: utf-8 -*-
"""Cross-encoder rerank：`BAAI/bge-reranker-base`（XLM-R base，单输出相关性分）。

**为什么需要它**：实测（2026-09-14，data/rag_eval 的 25 条 science 正样本）稠密检索的
正确答案 **100% 落在 top-5 内**，但只有 80% 排在 top-1 —— 错误**全是排序错误**，
不是召回失败。所以 rerank 的理论上限是把 top-1 准确率从 80% 提到 100%。

**为什么不用本地 LLM 当 reranker**：试过（qwen3:8b 逐条选号，见 scripts/eval_rag.py
的 llm_rerank）。结果是在这台 8GB 显存的机器上把 Ollama 服务压垮了——chat 模型与
嵌入模型同驻显存不够。cross-encoder 是 BERT-base 量级（fp32 约 1.1GB）、只做一次
前向、不生成 token，代价低一个数量级。

**降级**：模型目录缺失 / torch 或 transformers 不可用 / 加载失败 → `get()` 返回 None，
调用方退回稠密排序（项目"全降级"传统）。绝不抛出。
"""
from __future__ import annotations
import math
import os
from pathlib import Path

# 模型权重不在仓库里（1.1GB）。默认路径可用 RAG_RERANKER_DIR 覆盖。
# 来源：modelscope 的 BAAI/bge-reranker-base（HuggingFace 本机不可达）。
MODEL_DIR = Path(os.environ.get("RAG_RERANKER_DIR",
                                r"D:\data\models\bge-reranker-base"))


def _sigmoid(x: float) -> float:
    """logit → [0,1]。cross-encoder 输出是无界 logit，阈值/展示都需要有界尺度。"""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class Reranker:
    _instance = None
    _tried = False

    def __init__(self, model_dir: Path):
        from transformers import (AutoModelForSequenceClassification,
                                  AutoTokenizer)
        import torch
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(str(model_dir))
        self._model = AutoModelForSequenceClassification.from_pretrained(
            str(model_dir))
        self._model.eval()
        # 与嵌入模型同驻显存：4060 Laptop 只有 8GB，故默认跟 CUDA 但失败即退 CPU
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            self._model.to(self.device)
        except Exception:
            self.device = "cpu"
            self._model.to("cpu")

    @classmethod
    def get(cls, model_dir: Path | None = None) -> "Reranker | None":
        """懒加载单例。任何一步失败 → None（调用方退回稠密），且只试一次。"""
        if cls._instance is not None:
            return cls._instance
        if cls._tried:
            return None
        cls._tried = True
        d = model_dir or MODEL_DIR
        try:
            if not (d / "config.json").exists():
                return None
            cls._instance = cls(d)
        except Exception:
            return None
        return cls._instance

    def score(self, query: str, passages: list[str]) -> list[float]:
        """逐 (query, passage) 打分，返回 sigmoid 后的相关性 [0,1]，与输入等长。

        异常 → 返回空列表（调用方退回稠密排序），绝不抛。"""
        if not passages:
            return []
        try:
            torch = self._torch
            pairs = [[query, p] for p in passages]
            with torch.no_grad():
                inp = self._tok(pairs, padding=True, truncation=True,
                                max_length=512, return_tensors="pt").to(self.device)
                logits = self._model(**inp).logits.view(-1)
                return [_sigmoid(float(x)) for x in logits.float().cpu().tolist()]
        except Exception:
            return []


def rank_ids(query: str, ids: list[str], texts: list[str]) -> list[str]:
    """按 cross-encoder 分数重排 `ids`。模型不可用/失败 ⇒ **原样返回**（全降级）。

    ## 新定位：给**没有现成序**的候选集定序

    实测（`scripts/eval_rag.py` 文件头，2026-09-14）：正确答案 100% 落在稠密 top-5
    内，但 cross-encoder 的 argmax 只修好 3 条、弄坏 3 条（**净 0**）——
    所以"重排 dense top-5"是**已被证伪**的用法（那里本来就有稠密序，重排跟自己打架）。

    本函数服务的是**图扩展出来的候选集**（同主肌动作、同族替代）——
    那里 dense 从未排序过，**没有现成的序可以打输**，"净修 0"的结论不适用。
    ⚠ 但这也**不是**已知有收益的改动：探针 P3 必须实测净修条数，
    数字不支持就只当"确定性顺序"用，不写进收益。

    ⚠ 本函数**不提供门槛**：`score` 是有界 [0,1]（可当门槛），但本设计的
    零自由度不变式只允许 `MIN_SCORE` 一个门槛。定序与判决分开。
    """
    if len(ids) < 2:
        return list(ids)
    rr = Reranker.get()
    if rr is None:
        return list(ids)
    scores = rr.score(query, texts)
    if len(scores) != len(ids):
        return list(ids)
    order = sorted(range(len(ids)), key=lambda i: -scores[i])
    return [ids[i] for i in order]
