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
