# -*- coding: utf-8 -*-
"""语音转写（Whisper）共享单例。

**为什么是单例 + 懒加载**：medium 模型常驻显存约 4.6GB，加载一次 4.4s。
本模块**绝不在 import 期或 create_app() 期加载模型** —— `create_app()` 在每个
测试里都会跑（app/tests 下几十个模块），一旦在这里加载等于把整个测试套件
拖上 GPU。模型只在第一次真正转写时才加载，之后常驻。

**为什么加载与推理都要持锁**：单张卡上 medium 占 4.6GB，两个请求并发推理
会把显存打爆（实测同进程加载 base+medium 两个模型即 OOM）。串行化之后
并发请求排队而不是抢显存 —— 慢，但不会炸。

**为什么不做能量/静音门限**：`E:\\ASR\\myasr\\asr_engine.py` 里有个
`SILENCE_PEAK = 0.01` 的峰值判据，实测对真实录音完全失效（那批录音峰值
0.03~0.067，全放过去了）。而且 base 模型在这类音频上会把同一句幻觉重复
几十遍，medium 则干净返回空 —— 所以**模型选择本身就是第一道防线**，
这里只再叠一层段级 `no_speech_prob` 过滤兜底。

实测（RTX 4060 8GB / medium / fp16）：
  · 加载 4.4s，10.8s 音频转写 0.73s，峰值显存 4.58GB
  · 静音录音 → 空文本（base 会把「在那邊」重复 56 次）
  · webm/opus 可直接喂（ffmpeg 内部解码），无需先转码
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

# app/config/asr_config.json —— 与 llm_config.json / graph_config.json 同目录同风格
ASR_CONFIG = Path(__file__).resolve().parent.parent / "config" / "asr_config.json"

# 段级「无语音」过滤阈值。
#
# **不要显式传 no_speech_threshold 给 whisper**，用它的默认 0.6。
# 实测教训：`E:\ASR\myasr\asr_engine.py` 里写的 0.9（理由是该文件注释说的
# "base 对真实语音也会报 0.5~0.7，抬高阈值避免误杀"）在 medium 上是有害的——
# 同一段静音录音，**不传该参数时干净返回空**，传 0.9 反而吐出一个「我」字。
# 0.9 把本该被拦的段放了过去，方向正好反了。
#
# 这里仍保留一个自己的过滤（见 _join_segments），但阈值取 whisper 的默认值，
# 于是只做"兜底"，不覆盖库的默认行为。
NO_SPEECH_THRESHOLD = 0.6

# 兜底模型路径。不写死在配置里的原因：配置文件可能被删/被改坏，
# 缺配置时仍应能跑起来（与 load_llm_config 返回 {} 等价的契约）。
DEFAULT_MODEL = r"E:\ASR\model\medium.pt"

_MODEL = None
_LOCK = threading.RLock()
_LOAD_ERROR: str | None = None


# ------------------------------------------------------------------ 配置
def load_asr_config(path: str | None = None) -> dict:
    """读 asr_config.json。缺文件/坏 JSON → {}，**不抛**（照 load_llm_config）。"""
    p = Path(path) if path else ASR_CONFIG
    if not p.exists():
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _cfg() -> dict:
    """配置 + .env。`.env` 用手写的零依赖加载器（本仓不依赖 python-dotenv）。"""
    try:
        from app.core.llm import load_dotenv
        load_dotenv()
    except Exception:
        pass                       # .env 是锦上添花，读不到不影响默认值
    return load_asr_config()


def _enabled() -> bool:
    return bool(_cfg().get("enabled", True))


def model_path() -> str:
    """模型权重路径。优先级：环境变量 > 配置 > 默认。

    认 `FITMIND_ASR_MODEL`；同时认 `MYASR_WHISPER_MODEL` —— 那个是你已有的
    桌面工具（E:\\ASR\\myasr）在用的变量，共用一套就不用维护两份。
    """
    cfg = _cfg()
    for key in ("FITMIND_ASR_MODEL", "MYASR_WHISPER_MODEL"):
        v = os.environ.get(key)
        if v:
            return v
    return str(cfg.get("model") or "").strip() or DEFAULT_MODEL


def _device() -> str:
    """auto → 有 CUDA 用 CUDA，否则 CPU。显式配 cuda 但不可用也回落 CPU。"""
    want = str(_cfg().get("device") or "auto").strip().lower()
    if want == "cpu":
        return "cpu"
    try:
        import torch
        if not torch.cuda.is_available():
            return "cpu"
    except Exception:
        return "cpu"
    return "cuda" if want in ("auto", "cuda") else want


def _ensure_whisper_importable() -> None:
    """`import whisper` 失败时，把配置里的目录挂上 sys.path 再试。

    正常情况下 openai-whisper 是 **editable 安装**（实测 `whisper.__file__` 指向
    `E:\\ASR\\whisper\\whisper\\__init__.py`），直接就能 import，这里什么都不做。
    这一段是给「editable 安装被破坏」或「换机器」兜底的。

    ⚠ `whisper_path` 指向**仓库根**（`E:\\ASR\\whisper`，其下才有 `whisper/` 包），
    不是包目录本身 —— 把 `E:\\ASR\\whisper\\whisper` 挂上去会 import 不出来。
    所以这里顺手兼容一下用户直接填了包目录的情况。
    """
    try:
        import whisper  # noqa: F401
        return
    except ImportError:
        pass
    p = str(_cfg().get("whisper_path") or "").strip()
    if not p:
        raise ImportError("whisper 不可用，且未配置 whisper_path")
    cand = Path(p)
    # 填成包目录（其下有 __init__.py）时，取其父目录，否则 import whisper 会落空
    home = cand.parent if (cand / "__init__.py").exists() else cand
    s = str(home)
    if s not in sys.path:
        sys.path.insert(0, s)
    import whisper  # noqa: F401


# ------------------------------------------------------------------ 模型
def asr_model():
    """懒加载并缓存模型。**测试里 monkeypatch 这个函数**即可绕开真模型。"""
    global _MODEL, _LOAD_ERROR
    if _MODEL is not None:
        return _MODEL
    with _LOCK:
        if _MODEL is not None:                       # 双检：等锁期间别人可能已加载
            return _MODEL
        path = model_path()
        if not Path(path).exists():
            _LOAD_ERROR = f"模型文件不存在：{path}"
            raise FileNotFoundError(_LOAD_ERROR)
        _ensure_whisper_importable()
        import whisper
        device = _device()
        try:
            _MODEL = whisper.load_model(path, device=device)
        except Exception as exc:                      # noqa: BLE001
            # **不做 CPU 回落。** 试过，实践上只会把事情弄得更糟：显存不够时
            # 转去 CPU，等于把 1.5GB 权重再往系统内存里塞一份 —— 真正显存紧张的
            # 机器通常内存也不宽裕，崩在 `DefaultCPUAllocator: not enough memory`
            # 上，报出来的错和"显存不足"毫无关系，白等几十秒还更难排查。
            # 显存不足本来就是运维问题（关掉别的占卡进程），如实报出来最好。
            _LOAD_ERROR = f"模型加载失败：{type(exc).__name__}: {exc}"
            raise
        _LOAD_ERROR = None
        return _MODEL


def reset_model() -> None:
    """丢弃缓存的模型（测试用；也可用于释放显存）。"""
    global _MODEL
    with _LOCK:
        _MODEL = None


def status() -> dict:
    """给 /health 与前端探活用。**绝不触发加载** —— 只报告配置与缓存状态。"""
    enabled = _enabled()
    path = model_path()
    return {
        "enabled": enabled,
        "model": path,
        "model_exists": Path(path).exists(),
        "loaded": _MODEL is not None,
        "device": _device() if enabled else None,
        "error": _LOAD_ERROR,
    }


# ------------------------------------------------------------------ 转写
def _join_segments(segments: list[dict]) -> str:
    """过滤幻觉段并拼接。

    **必须丢段后重拼，不能用 `resp["text"]`** —— 后者是过滤前的全文，
    悄悄把被丢掉的幻觉内容又带回来了（那正是要拦的东西）。
    中文不加分隔符；段间用 whisper 自己的空格风格只在非 CJK 时才需要，
    这里统一直接拼，与 asr_engine 的行为一致。
    """
    out: list[str] = []
    for seg in segments:
        if float(seg.get("no_speech_prob") or 0.0) > NO_SPEECH_THRESHOLD:
            continue
        t = (seg.get("text") or "").strip()
        if t:
            out.append(t)
    return "".join(out).strip()


def transcribe(path: str) -> dict:
    """转写一个音频文件。返回 `{text, language, duration}`。

    接受任意 ffmpeg 能解的形式（webm/opus、wav、flac…）—— 浏览器
    MediaRecorder 录出来的是 webm/opus，**不需要前端先转码**。
    """
    cfg = _cfg()
    model = asr_model()
    kw = {
        "language": str(cfg.get("language") or "zh"),
        "task": "transcribe",
        "fp16": _device() != "cpu",     # CPU 上 fp16 会报错/极慢
        "temperature": 0.0,             # 贪心解码：要的是稳定复现，不是多样性
        # ⚠ **故意不传 no_speech_threshold** —— 传了会覆盖 whisper 的默认 0.6，
        # 而实测把阈值抬到 0.9 会让静音录音吐出幻觉字（详见 NO_SPEECH_THRESHOLD
        # 处的说明）。让它用默认值；我们自己的段过滤只做兜底。
    }
    prompt = str(cfg.get("prompt") or "").strip()
    if prompt:
        # 默认不发 prompt。词表预热实测收益不稳定（1318 个动作名塞不进 whisper
        # 的 ~224 token 窗口；手挑 60 词压到 329 token 后仍有句子被带偏），
        # 所以留成配置项而不是写死 —— 想试随时能开。
        kw["initial_prompt"] = prompt
    # 串行化推理：与加载共用一把锁，见模块头「为什么加载与推理都要持锁」
    with _LOCK:
        resp = model.transcribe(path, **kw)
    segments = list(resp.get("segments") or [])
    return {
        "text": _join_segments(segments),
        "language": resp.get("language") or kw["language"],
        # ⚠ whisper 的返回值里**没有** duration 这个键（transcribe.py 里的
        # duration 只是段循环的局部变量）。早先照抄 asr_engine 写上
        # `resp.get("duration")` 于是恒为 0 —— 一个"看起来有、其实永远为 0"
        # 的字段比没有更坏。这里用最后一段的 end 兜底（无段即 0）。
        "duration": round(float(segments[-1].get("end") or 0.0), 2) if segments else 0.0,
        # 排障用：**只带概率**，不带段文本 —— 段文本是转写结果的一部分，
        # 再抄一份进响应等于把同一段话发两遍，而这里要的只是
        # "过滤是因为什么、又放过了什么"（静音幻觉就藏在这几个数里）。
        "no_speech_probs": [s.get("no_speech_prob") for s in segments],
    }
