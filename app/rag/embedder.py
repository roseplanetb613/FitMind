# -*- coding: utf-8 -*-
"""Embedding 供应商：Ollama bge-m3（dense 1024）。可配置 base_url/model；接口留位。"""
from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
# 注意：不用 OLLAMA_MODEL（本机环境绑定了聊天模型 qwen3-local:latest，非 embedding）。
# 用独立的 embedding 模型变量，default 始终为 bge-m3。
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")


class OllamaEmbedder:
    def __init__(self, base_url: str = OLLAMA_BASE, model: str = OLLAMA_EMBED_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def embed(self, texts: list[str], batch: int = 32) -> list[list[float]]:
        """批量 embed；返回与输入等长的向量列表（BGE-M3 dense）。
        对瞬时网络/连接错误重试（WSL2 端口转发偶发抖动），仍失败则抛错由上层降级跳过。"""
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            for attempt in range(3):
                try:
                    req = urllib.request.Request(
                        f"{self.base_url}/api/embed",
                        data=json.dumps({"model": self.model,
                                         "input": chunk}).encode(),
                        headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        data = json.loads(resp.read())
                    break
                except (urllib.error.HTTPError, urllib.error.URLError,
                        TimeoutError, OSError) as e:
                    if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                        raise                       # 4xx 非瞬时，直接失败
                    if attempt == 2:
                        raise
                    time.sleep(0.5 * (attempt + 1))
            else:
                raise RuntimeError("embed 重试耗尽")
            out.extend(data["embeddings"])
        return out

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def healthy(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            urllib.request.urlopen(req, timeout=3).read()
            return True
        except Exception:
            return False