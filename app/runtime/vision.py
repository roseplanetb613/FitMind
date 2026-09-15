# -*- coding: utf-8 -*-
"""食物识别（DeepSeek VLM）。

**核心不变式：这里绝不产出营养数字。** 模型只回答"这是什么、大概多少克"，
数字一律由 `lib/dish_repo.nutrition_from_recipe()` 查库求和得出。prompt 里
显式禁止模型算热量——这是不变式的第一道防线，`test_prompt_forbids_nutrition_numbers`
把它钉住了。

形状对齐 `app/runtime/asr.py`：单例 + 懒加载 + `status()` 探活**零代价**。
与 ASR 不同的是这个模型在云端，不占显存，所以不需要那把串行锁。

⚠ **model id 未经官方文档核实**（api-docs.deepseek.com 被网络策略拦截，
只拿到媒体口径）。所以它走配置文件、可覆盖，改 id 不用改代码。
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
from pathlib import Path

VISION_CONFIG = Path(__file__).resolve().parent.parent / "config" / "vision_config.json"
DEFAULT_MODEL = "deepseek-v4-flash-vision-exp"
DEFAULT_BASE_URL = "https://api.deepseek.com"

_PROMPT = """你看一张食物照片，判断这是什么、大概多少克。

**只输出 JSON，一个营养数字都不要给**（热量/蛋白质/脂肪/碳水一律不要计算、
不要估计）。营养成分由我们的数据库查表得出，你给的数字会被丢弃且会污染结果。

输出格式：
{
  "is_food": true/false,
  "dish_name": "菜名（中文，尽量用常见叫法，如 红烧肉）",
  "confidence": 0.0~1.0,
  "portion_g": 这一份大约多少克（整数）,
  "ingredients": [{"name": "食材中文名", "grams": 估算克数}]
}

规则：
- 照片里没有食物（风景/人像/截图/宠物）→ is_food 置 false，其余字段留空。
- dish_name 用最常见的叫法；认不出具体菜名时给主要食材组成的描述。
- ingredients 按你的判断拆出主要食材与用量（含食用油、糖、酱油这类调料），
  这是菜名认不出来时的兜底依据，**尽量拆全**。
- portion_g 是整份的重量估计，不是单个食材。
"""

_lock = threading.Lock()
_provider_cache = None


def load_config(path: str | None = None) -> dict:
    """读配置。文件缺失/损坏一律回落默认值（与 `load_llm_config` 的契约一致：
    配置坏了不该让功能整个起不来，只是不可用）。"""
    p = Path(path) if path else VISION_CONFIG
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    return {"enabled": bool(cfg.get("enabled", False)),
            "model": cfg.get("model") or DEFAULT_MODEL,
            "base_url": cfg.get("base_url") or DEFAULT_BASE_URL,
            "detail": cfg.get("detail") or "low",
            "timeout": int(cfg.get("timeout") or 30)}


def _api_key() -> str:
    from app.core.llm import load_dotenv
    load_dotenv()                                   # .env 兜底（幂等）
    return os.environ.get("DEEPSEEK_API_KEY") or ""


def status() -> dict:
    """探活。**零代价**：不构造 client、不发请求。

    前端据此决定要不要渲染 📷 按钮 —— 探活要是会调模型，看一眼就等于花钱。
    """
    cfg = load_config()
    key = bool(_api_key())
    error = None
    if not cfg["enabled"]:
        error = "vision_config.json 的 enabled=false"
    elif not key:
        error = "DEEPSEEK_API_KEY 未设置"
    return {"enabled": cfg["enabled"], "model": cfg["model"],
            "key_configured": key, "error": error}


def _provider(cfg: dict):
    """单例懒加载的 ChatOpenAI。测试 monkeypatch 掉它。"""
    global _provider_cache
    with _lock:
        if _provider_cache is None:
            from langchain_openai import ChatOpenAI
            _provider_cache = ChatOpenAI(
                model=cfg["model"], api_key=_api_key(),
                base_url=cfg["base_url"],
                temperature=0.0,
                request_timeout=cfg["timeout"], max_retries=1)
        return _provider_cache


def _reset_provider() -> None:
    """测试/配置变更后清缓存。"""
    global _provider_cache
    with _lock:
        _provider_cache = None


def _parse(text: str) -> dict:
    """剥掉 ```json 围栏后解析。解析不出就抛 ValueError（上层转 503）。"""
    raw = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    if not raw:
        raise ValueError("模型返回空内容")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"模型返回不是合法 JSON：{raw[:200]}") from e
    if not isinstance(data, dict):
        raise ValueError(f"模型返回不是 JSON 对象：{raw[:200]}")
    return data


def _build_prompt() -> str:
    return _PROMPT


def _call_vision(image: bytes, mime: str, cfg: dict) -> dict:
    """一次调用。图片只能放 **user 消息**（放 system/assistant 报 400）。"""
    b64 = base64.b64encode(image).decode("ascii")
    blocks = [
        {"type": "text", "text": _build_prompt()},
        {"type": "image_url",
         "image_url": {"url": f"data:{mime};base64,{b64}",
                       "detail": cfg.get("detail", "low")}},
    ]
    resp = _provider(cfg).invoke([("user", blocks)])
    text = getattr(resp, "content", None) or ""
    return _parse(text)
