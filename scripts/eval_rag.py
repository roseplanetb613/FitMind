# -*- coding: utf-8 -*-
"""RAG 检索评估：标注集 → 各检索器 → 2x2 混淆 + 阈值扫描。

回答的问题不是"cosine 多少"，而是"挂上去的证据有几成是对的、该闭嘴时闭没闭上"。
两个消费点（qa._rag_science / teach 原 exercise_cue 路径）都是 top_k=1，所以核心指标
是 evidence_precision 与 wrong_evidence_rate，不是 Recall@10 / nDCG。

用法：
    python scripts/eval_rag.py --emit      # 生成/刷新自动正样本到标注集
    python scripts/eval_rag.py             # 全部检索器 + 阈值扫描
    python scripts/eval_rag.py --split 0.5 # dev 上选阈值，报 test 成绩
    python scripts/eval_rag.py --retriever policy --split 0.5          # 判决层
    python scripts/eval_rag.py --retriever policy --chunk-type exercise_cue --split 0.5

结构：每个检索器先把每条 query 的 **top1(id, score) 算一次**，之后阈值只是纯过滤——
否则每换一个阈值就要重跑一遍 embed。

已测结论（2026-09-14，science_doc 25 正 / 12 负）：
    检索器          Acc    证据准  召回   WRONG  LEAK   备注
    dense           0.76   0.74   0.68    5      1     当时的 MIN_SCORE=0.55
    字符bigram BM25  0.65   0.63   0.48    7      0     词法在此语料偏弱
    hybrid(RRF)     0.54   0.48   0.64    9      8     **比单路差**：融合稀释强路
    ce_rerank       0.81   0.79   0.76    4      1     提升来自**弃权分数**而非排序
  · 天花板：正确答案 **100% 落在稠密 top-5 内**（80% 在 top-1）→ 错误全是排序错误
  · 但 cross-encoder 的 argmax 只修好 3 条、弄坏 3 条（净 0）——**够不到天花板**
  · llm_rerank（qwen3:8b）实测会把本机 Ollama 压垮，见该函数注释，勿用

已测结论（2026-09-20，标注集扩到 221 条，`--split 0.5` 的**留出 test**，n=104）：
    检索器     证据准  召回   弃权准  WRONG  LEAK   备注
    dense      0.83   0.84   0.83    10     4     dev 选出阈值 0.624
    lexical    0.87   0.84   0.96     9     1     **只接一路就接它**
    hybrid     0.67   0.85   0.12    12    21     RRF 最差：融合分把弃权信号洗掉
    policy     0.79   0.86   0.50     6    12     **2026-09-20 新增**：判决层
  · `policy` = `app.rag.policy`（按 chunk_type 分化 + 词法旁证 + fail-closed）。
    在 `exercise_cue` 上留出 test 是 **准 1.000 / WRONG 0 / LEAK 0**，且召回不掉。
  · ⚠⚠ **扩集后 `policy` 看着比 `dense` 差，别被这一行误导** —— 不是同类比较：
    本脚本会给 `dense` **调阈值**（网格扫描，选出 0.624），而 `policy` 的网格被固定成
    `[-1.0, 0.0]`，**它一直用 `MIN_SCORE`**。同一阈值下比才公平（见 §阈值表）。
  · ⚠ **`MIN_SCORE` 已从 0.55 调到 0.60**（2026-09-20 晚）。依据：**负样本 17→50**
    之后，0.55 在全量 test 上 **LEAK 14/24、弃权准 0.417**；0.60 是 LEAK 5 / 弃权准 0.792。
    旧集只有 17 个负样本（进 test 更少），**把 LEAK 照得很小**，所以此前"0.55 够用"
    的结论是**集合太瘦**造成的假象。
  · ⚠ 本脚本的**阈值网格是给 `dense` 探边界用的**，不是自动调参器。要用它选值，
    先确认负样本够（2026-09-20 前是 17 个，不够）。
  · ⚠ 集合是 **171 正 / 50 负**（扩集前 145/17，`accuracy ≈ recall` 的偏差已明显缓解，
    但正仍多于负）。读 Acc 时记住这点。

判据（每 query 一个格子）：
    expected 有值 且 returned == expected → TP
    expected 有值 但 returned 是别的块     → WRONG  ← 危险格：挂了错证据
    expected 有值 但 abstain               → MISS
    expected 为 null（库里本就没有）且 abstain → TN
    expected 为 null 却 returned           → LEAK   ← 危险格：无中生有
"""
from __future__ import annotations
import argparse
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "lib", "app"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

# 词法索引的**唯一实现**在 app/rag/lex_index.py（评测与运行期共用，防同源漂移）。
# `LexIndex` / `_grams` 以 import 别名保留本脚本内的既有调用点 —— **不是子类**：
# 子类会让人以为"评测可以覆盖实现"，而本设计的全部意义正是两边**同一份**实现。
from app.rag.lex_index import LexIndex, _grams                # noqa: E402

LABELS = ROOT / "data" / "rag_eval" / "queries.jsonl"
SEED = 20260914


# ---------------- 纯逻辑：可单测，不碰 DB ----------------

def classify(expected: str | list[str] | None, returned: str | None) -> str:
    """单条 query 的结局。五格，不是四格——WRONG 和 LEAK 都算错证。

    `expected` 可以是单个 id 或可接受 id 的列表：有些 query 确实不止一条正确块
    （"有氧运动的强度区间"同时命中 cardiorespiratory 与 fitt_intensity）。用单标签
    会把它当错，**系统性地低估**性能。None 仍表示"库里本就没有"。"""
    if expected is not None:
        if returned is None:
            return "MISS"
        ok = {expected} if isinstance(expected, str) else set(expected)
        return "TP" if returned in ok else "WRONG"
    return "TN" if returned is None else "LEAK"


def score_run(outcomes: list[str]) -> dict:
    """五格计数 → 指标。

    分母的取法（最容易搞错的地方）：
      evidence_precision 的分母是**所有返回过证据的 query**（含 LEAK）——只算正样本的话，
        一个对无关问题乱答的系统会被算成满分。
      recall 的分母是正样本数：MISS 算漏，WRONG 也算漏（没拿到对的那条）。"""
    c = Counter(outcomes)
    tp, wrong, miss = c["TP"], c["WRONG"], c["MISS"]
    tn, leak = c["TN"], c["LEAK"]
    n_pos, n_neg = tp + wrong + miss, tn + leak
    returned = tp + wrong + leak
    n = n_pos + n_neg
    return {
        "n": n, "n_pos": n_pos, "n_neg": n_neg,
        "TP": tp, "WRONG": wrong, "MISS": miss, "TN": tn, "LEAK": leak,
        "evidence_precision": round(tp / returned, 4) if returned else None,
        "wrong_evidence_rate": round((wrong + leak) / returned, 4) if returned else None,
        "recall": round(tp / n_pos, 4) if n_pos else None,
        "abstain_accuracy": round(tn / n_neg, 4) if n_neg else None,
        "accuracy": round((tp + tn) / n, 4) if n else None,
    }


def apply_threshold(top1: list[tuple[str | None, str | None, float]],
                    threshold: float) -> list[str]:
    """[(expected, returned_id, score)] → 五格结局序列。阈值只在这里起作用。"""
    return [classify(exp, rid if s >= threshold else None)
            for exp, rid, s in top1]


def auto_grid(scores: list[float], n: int = 9) -> list[float]:
    """按观测到的 top1 分数铺网格：0 → p95。

    不同检索器的分数尺度不可比（cosine 有界，BM25 无界），所以各自铺自己的，
    报表每行都带阈值，不跨检索器比阈值。"""
    if not scores:
        return [0.0]
    s = sorted(scores)
    hi = s[min(len(s) - 1, int(len(s) * 0.95))]
    return [round(hi * i / (n - 1), 4) for i in range(n)]


# ⚠ `_grams` / `LexIndex` 的实现已**逐字搬到** `app/rag/lex_index.py`（2026-09-21），
# 本脚本改为**直接 import**，不再本地重复定义（原先留过一层子类别名，已去掉）。
# 搬移动机：接线 `exercise_cue` 判决层时运行期也要算词法旁证，必须是同一份实现。
# 行为逐字一致由 `--retriever all` 的四行数字不变来保证；任何差异都说明搬移改了算法。
#
# 不用 pg_trgm 的原因：实测它在 2 字中文上算不出相似度（'深蹲' vs '深蹲膝盖姿势要点'
# = 0.200，低于默认阈值 0.3）。字符 bigram 没这个问题。索引建一次，多次查询复用。


# ---------------- LLM rerank（本地 Ollama，零下载） ----------------

_RERANK_PROMPT = (
    "下面是一个健身相关问题，以及 {n} 段候选知识。请选出最能回答该问题的候选编号。\n"
    "只能回答一个数字：候选编号（1-{n}）；如果都不相关，回答 0。不要解释。\n\n"
    "问题：{q}\n\n{cands}\n编号：")


def _parse_pick(text: str, n: int) -> int:
    """从模型输出抽出候选编号，落在 1..n 才接受，否则 0（都不相关）。

    解析失败一律当 0 —— **宁可弃权也不猜**（与项目"错证据比没证据更糟"一致）。
    模型输出不是裸数字时，只在**输出很短**（≤12 字，如"编号：3"）时才从里面抠数字；
    长输出里可能含"5 段都不相关"这种会把 5 误当选择的句子，一律拒绝。"""
    import re
    t = (text or "").strip()
    if t.isdigit():
        v = int(t)
        return v if 1 <= v <= n else 0
    if len(t) <= 12:
        m = re.search(r"\d+", t)
        if m:
            v = int(m.group())
            return v if 1 <= v <= n else 0
    return 0


def _ollama_pick(query: str, cands: list[tuple[str, str]], model: str) -> tuple[int, str]:
    """问本地模型选哪条。返回 (编号, 原始输出)。网络/解析异常一律 (0, 原因)。"""
    import urllib.request
    body = "\n".join(f"{i}. {c}" for i, (_, c) in enumerate(cands, 1))
    prompt = _RERANK_PROMPT.format(n=len(cands), q=query, cands=body)
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                             "think": False,
                             "options": {"temperature": 0}}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = json.loads(r.read()).get("response", "")
    except Exception as e:
        return 0, f"<err {type(e).__name__}>"
    return _parse_pick(raw, len(cands)), raw


def llm_rerank_top1(store, emb, rows: list[dict], n: int = 5,
                    model: str = "qwen3:8b") -> list[tuple]:
    """稠密取 top-N → 本地 LLM 从 N 条里选 1 条（或选 0 = 都不相关）。

    ⚠ **实测在本机不可用**（2026-09-14）：跑全量时把 WSL 里的 Ollama **压垮了**
    （8GB 显存的 4060 Laptop 装不下 qwen3:8b 5GB + 已加载的 bge-m3）。单条调用
    ~8s，37 条中途服务就没了。**别在生产路径上用它**；要 LLM 判断力请用
    ce_rerank（BERT-base 量级、不生成 token）。保留此实现只为记录这条负面结论。

    天花板依据：实测正确答案 **100% 落在稠密 top-5 内**，所以错误全是排序错误。

    返回的 score 只编码"选没选"：选中=1.0、选 0=-1.0。这样
    `apply_threshold(..., 0.0)` 就等于"LLM 说弃权就弃权"，**弃权由模型判断，
    不再用余弦门槛**（余弦门槛与 rerank 排序是两套不兼容的判据）。"""
    from app.rag import retriever
    out = []
    for r in rows:
        hits = retriever.vector_search(
            store, emb.embed_one(r["query"]), "bge-m3", top_k=n,
            chunk_types=(r["chunk_type"],), min_score=None)
        cands = [(h["source_ref"].get("id"), h["content"]) for h in hits]
        if not cands:
            out.append((r.get("expected"), None, -1.0))
            continue
        pick, _raw = _ollama_pick(r["query"], cands, model)
        out.append((r.get("expected"),
                    cands[pick - 1][0] if pick else None,
                    1.0 if pick else -1.0))
    return out


# ---------------- Cross-encoder rerank（bge-reranker-base，本地权重） ----------------

# 两段式解耦（本机约束）：cross-encoder 与 Ollama 同时活跃会压垮 Ollama
# （8GB 卡 + 主机内存紧张，实测连续三次）。所以支持先把稠密候选 dump 到文件，
# 再由只加载 cross-encoder 的进程读取 —— 两个模型永不同时驻留。
CAND_DUMP: Path | None = None


def dump_candidates(store, emb, rows: list[dict], n: int, path: Path) -> None:
    """把每条 query 的稠密 top-N（含正文）落盘，供 CE 阶段离线重排。"""
    from app.rag import retriever
    data = {}
    for r in rows:
        hits = retriever.vector_search(
            store, emb.embed_one(r["query"]), "bge-m3", top_k=n,
            chunk_types=(r["chunk_type"],), min_score=None)
        data[f"{r['chunk_type']}	{r['query']}"] = [
            [h["source_ref"].get("id"), h["content"], h["score"]] for h in hits]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def ce_rerank_top1(store, emb, rows: list[dict], n: int = 5) -> list[tuple]:
    """稠密取 top-N → cross-encoder 重排 → 取最高分。

    与 llm_rerank 的区别：不生成 token、只做一次前向，慢一个数量级都不止；
    而且**模型缺失时会安静退回稠密排序**（不抛、不崩服务）。

    分数是 sigmoid 后的相关性 [0,1] —— 有界，所以能像余弦一样扫阈值。
    这一点优于 RRF：RRF 的融合分把弃权信号洗掉了（实测 abstain_accuracy 0.33）。"""
    from app.rag import retriever
    from app.rag.reranker import Reranker
    rr = Reranker.get()
    cached = (json.loads(CAND_DUMP.read_text(encoding="utf-8"))
              if CAND_DUMP and CAND_DUMP.exists() else None)
    out = []
    for r in rows:
        if cached is not None:                       # 从 dump 读，不碰 Ollama
            cands = cached.get(f"{r['chunk_type']}	{r['query']}") or []
            ids = [c[0] for c in cands]
            texts = [c[1] for c in cands]
            dense_score = cands[0][2] if cands else -1.0
        else:
            hits = retriever.vector_search(
                store, emb.embed_one(r["query"]), "bge-m3", top_k=n,
                chunk_types=(r["chunk_type"],), min_score=None)
            ids = [h["source_ref"].get("id") for h in hits]
            texts = [h["content"] for h in hits]
            dense_score = hits[0]["score"] if hits else -1.0
        if not ids:
            out.append((r.get("expected"), None, -1.0))
            continue
        scores = rr.score(r["query"], texts) if rr else []
        if scores:                                   # 正常：交叉编码器重排
            bi = max(range(len(scores)), key=lambda i: scores[i])
            out.append((r.get("expected"), ids[bi], scores[bi]))
        else:                                        # 降级：退回稠密 top1
            out.append((r.get("expected"), ids[0], dense_score))
    return out


# ---------------- 标注集 ----------------

def load_labels(path: Path = LABELS) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def emit_auto_exercise_labels(n: int = 120) -> int:
    """按 name_zh + 模板生成 exercise_cue 正样本，写入标注集（幂等：先删同 kind）。

    自动标签是**易样本**（query 里含动作原名，词法方法几乎必中），衡量的是召回上限
    而非真实难度。真正有区分度的是人工写的硬负样本（kind=negative）。"""
    from exercise_repo import ExerciseRepo
    ex = ExerciseRepo()
    pool = sorted(eid for eid, e in ex.by_id.items()
                  if len(e.get("name_zh") or "") >= 3)
    picked = random.Random(SEED).sample(pool, min(n, len(pool)))
    tpl = ("{n}怎么练", "{n}的要领", "{n}练哪块肌肉", "{n}的标准动作")
    rows = [{"query": tpl[i % len(tpl)].format(n=ex.by_id[eid]["name_zh"]),
             "chunk_type": "exercise_cue", "expected": eid,
             "kind": "auto_name", "note": ""}
            for i, eid in enumerate(picked)]
    kept = [r for r in load_labels() if r.get("kind") != "auto_name"]
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    LABELS.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                for r in kept + rows) + "\n", encoding="utf-8")
    return len(rows)


# ---------------- 检索器：各自算一次 top1 ----------------

def dense_top1(store, emb, rows: list[dict]) -> list[tuple]:
    """返回 [(expected, returned_id, score)]。min_score=None 拿原始近邻排序。"""
    from app.rag import retriever
    out = []
    for r in rows:
        hits = retriever.vector_search(
            store, emb.embed_one(r["query"]), "bge-m3", top_k=1,
            chunk_types=(r["chunk_type"],), min_score=None)
        out.append((r.get("expected"),
                    hits[0]["source_ref"].get("id") if hits else None,
                    hits[0]["score"] if hits else -1.0))
    return out


def lexical_top1(store, rows: list[dict]) -> list[tuple]:
    """词法基线与生产一致地走 expand_aliases + norm_zh。"""
    from exercise_repo import expand_aliases, norm_zh
    idx: dict[str, LexIndex] = {}
    for r in rows:
        t = r["chunk_type"]
        if t not in idx:
            with store.conn() as c, c.cursor() as cur:
                cur.execute("SELECT source_ref->>'id', content FROM "
                            "fitness.embeddings WHERE chunk_type=%s", (t,))
                idx[t] = LexIndex([(i, norm_zh(x)) for i, x in cur.fetchall()])
    out = []
    for r in rows:
        rid, s = idx[r["chunk_type"]].top1(norm_zh(expand_aliases(r["query"])))
        out.append((r.get("expected"), rid, s))
    return out


def policy_top1(store, emb, rows: list[dict]) -> list[tuple]:
    """判决层（`app.rag.policy`）的 top1 —— 让规格里的规则在**标准工具**里可复算。

    2026-09-20：判决此前焊在 `vector_search` 的 SQL 门槛里（一个标量要同时管
    `exercise_cue` 与 `science_doc` 两种分布）。实测该标量在两个分布上重叠，
    判决层把"接受/弃权"拆出来，按 chunk_type 分化：
    `exercise_cue` 要求**词法旁证**（严格相等），`science_doc` 保持纯标量。

    ⚠ 门槛本身（`MIN_SCORE`）**当天晚上从 0.55 调到了 0.60** —— 标注集扩到 221 条、
    负样本 17→50 后，0.55 在全量 test 上 LEAK 14/24、弃权准 0.417，明显太低。
    旧集负样本太少，把 LEAK 照小了。所以本行 `policy` 的成绩与 `dense` 行**不是
    同一阈值下的比较**（`dense` 会被网格调到 0.624）。要比阈值请看
    `MIN_SCORE` 的注释里的表。

    ⚠ 返回的 score **只编码"接受/弃权"**（接受 1.0 / 弃权 -1.0）—— 照抄
    `llm_rerank` 的先例：判决层已自带门槛（`policy.MIN_SCORE`），不该再被外层
    网格扫一遍（`main` 对这两个检索器把 grid 固定成 `[-1.0, 0.0]`）。

    ⚠ 旁证用的是本脚本的 `LexIndex`（跑在 `norm_zh(content)` = cue 正文上），
    **不是**生产的 `exercise_repo.search_zh`（跑在动作名/别名上）。两条口径实测
    top1 一致率 111/125；精度都打满（准 1.000 / WRONG 0 / LEAK 0），但召回
    0.900（本口径）vs 0.842（生产口径）。口径差是**已知且量化过**的，见规格 §4.3。
    """
    from app.rag import policy
    from app.rag import retriever
    from exercise_repo import expand_aliases, norm_zh
    idx: dict[str, LexIndex] = {}
    out = []
    for r in rows:
        t = r["chunk_type"]
        if t not in idx:
            idx[t] = _lex_index(store, t)
        hits = retriever.vector_search(
            store, emb.embed_one(r["query"]), "bge-m3", top_k=1,
            chunk_types=(t,), min_score=None)
        ranked = policy.from_hits(hits)
        lex_id, _ = idx[t].top1(norm_zh(expand_aliases(r["query"])))
        d = policy.decide(ranked, chunk_type=t, lexical_top1_id=lex_id)
        out.append((r.get("expected"),
                    ranked[0][0] if (d.accepted and ranked) else None,
                    1.0 if d.accepted else -1.0))
    return out


def graph_top1(store, emb, rows: list[dict]) -> list[tuple]:
    """图通道（`app.rag.fusion` + 图扩展）的 top1 —— 让新能力在**标准工具**里可复算。

    ⚠ 本通道的产出是**候选集 + 事实**，不是"从全库选一条"，所以它与 dense/lexical
    的 top1 **不是同类比较**。这里报的是"图扩展集里是否有 expected"：
      · 扩展集非空且 expected 在内 → 用 expected 作为 returned（TP）
      · 扩展集非空但 expected 不在 → 用扩展集首条（WRONG —— 图给了个别的）
      · 扩展集为空 / 图不可用 → 返回 None（MISS/TN，诚实弃权）
    ⚠ 分数只编码"接没接"（接受 1.0 / 弃权 -1.0），照抄 `policy_top1` 的先例：
    图通道自带门槛（`MIN_SCORE`），不该再被外层网格扫一遍。

    新能力的**主验收**不在这里（本表衡量不了枚举能力），而在
    `app/tests/test_graph_channel.py` 的可见性用例。本函数只保证它**可复算、可对比**。
    """
    from app.graph.store import GraphStore
    from app.rag import fusion, retriever
    graph = GraphStore.get()
    out = []
    for r in rows:
        exp = r.get("expected")
        ok = ({exp} if isinstance(exp, str) else set(exp)) if exp else set()
        if graph is None:
            out.append((exp, None, -1.0))
            continue
        try:
            vec = retriever.embed_query(emb, r["query"])
            seed = retriever.seed_exercise(store, vec, "bge-m3")
            facts = fusion.compose(graph, seed)["facts"]
            cands = [p["id"] for p in facts.get("peers", [])] + \
                    [a["id"] for a in facts.get("alternatives", [])]
        except Exception:
            cands = []
        hit = next((c for c in cands if c in ok), None)
        rid = hit or (cands[0] if cands else None)
        out.append((exp, rid, 1.0 if rid else -1.0))
    return out


def _rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)。

    只用**排名**，不用分数——所以不必把 cosine（有界 0~1）与 BM25（无界）的尺度
    对齐，这正是 RRF 存在的理由。k=60 取原论文（Cormack et al. 2009）默认值。

    ⚠ 代价：融合分数对**无关 query 也恒有值**（每条路都会返回 top-N），所以它
    没有"该弃权"的信息。RRF 的分数适合排序，不适合当门槛——门槛应继续用稠密的
    余弦（见 hybrid_top1 的注释与 eval 报表里 threshold=0 那一列）。"""
    agg: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            if doc_id:
                agg[doc_id] = agg.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(agg.items(), key=lambda kv: -kv[1])


def _lex_index(store, chunk_type: str) -> LexIndex:
    from exercise_repo import norm_zh
    with store.conn() as c, c.cursor() as cur:
        cur.execute("SELECT source_ref->>'id', content FROM "
                    "fitness.embeddings WHERE chunk_type=%s", (chunk_type,))
        return LexIndex([(i, norm_zh(x)) for i, x in cur.fetchall()])


def hybrid_top1(store, emb, rows: list[dict], n: int = 10,
                k: int = 60) -> list[tuple]:
    """dense(top-N) + 字符 bigram BM25(top-N) 做 RRF 融合，取融合后的 top1。

    两条路都取 **top-N** 再融合——只取 top1 没有融合可言。`n` 同时是"错块必须落在
    池子里才可能被救回来"的上限，所以它是这个方案的关键旋钮。

    返回的 score 是**融合分**（用于排序）。看排序质量请读报表里 threshold=0 那列；
    看"该闭嘴时能不能闭嘴"要看稠密自己的余弦，融合分会把弃权信号洗掉。"""
    from app.rag import retriever
    from exercise_repo import expand_aliases, norm_zh
    idx: dict[str, LexIndex] = {}
    out = []
    for r in rows:
        t = r["chunk_type"]
        if t not in idx:
            idx[t] = _lex_index(store, t)
        dense = [h["source_ref"].get("id") for h in retriever.vector_search(
            store, emb.embed_one(r["query"]), "bge-m3", top_k=n,
            chunk_types=(t,), min_score=None)]
        lex = [i for i, _ in idx[t].ranked(
            norm_zh(expand_aliases(r["query"])), n)]
        fused = _rrf([dense, lex], k)
        rid, s = fused[0] if fused else (None, -1.0)
        out.append((r.get("expected"), rid, s))
    return out


def abstain_top1(rows: list[dict]) -> list[tuple]:
    return [(r.get("expected"), None, -1.0) for r in rows]


def random_top1(store, rows: list[dict], rng) -> list[tuple]:
    pool: dict[str, list[str]] = {}
    for r in rows:
        t = r["chunk_type"]
        if t not in pool:
            with store.conn() as c, c.cursor() as cur:
                cur.execute("SELECT source_ref->>'id' FROM fitness.embeddings "
                            "WHERE chunk_type=%s", (t,))
                pool[t] = [x[0] for x in cur.fetchall()]
    return [(r.get("expected"),
             rng.choice(pool[r["chunk_type"]]) if pool[r["chunk_type"]] else None,
             -1.0) for r in rows]


# ---------------- 报表 ----------------

def _p(v) -> str:
    return "—" if v is None else f"{v:.2f}"


def _fmt(m: dict) -> str:
    return (f"TP={m['TP']:<3} WRONG={m['WRONG']:<3} MISS={m['MISS']:<3} "
            f"TN={m['TN']:<3} LEAK={m['LEAK']:<3} | "
            f"证据准={_p(m['evidence_precision'])} "
            f"错证率={_p(m['wrong_evidence_rate'])} "
            f"召回={_p(m['recall'])} 该闭嘴时闭嘴={_p(m['abstain_accuracy'])}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--retriever", default="all",
                    choices=["all", "dense", "lexical", "hybrid", "ce_rerank",
                             "llm_rerank", "policy", "graph", "abstain", "random"])
    ap.add_argument("--chunk-type", default=None)
    ap.add_argument("--split", type=float, default=0.0,
                    help="留出比例：dev 上选阈值，报 test 成绩（0 = 不分，阈值即在报告集上选，偏乐观）")
    ap.add_argument("--grid", default=None, help="逗号分隔阈值，覆盖自动网格")
    ap.add_argument("--dump", default=None,
                    help="把稠密 top-N 候选落盘到此路径后退出（两段式第一步）")
    ap.add_argument("--cands", default=None,
                    help="从该 dump 读候选，不再调用 Ollama（两段式第二步）")
    args = ap.parse_args()

    if args.emit:
        print(f"emit: {emit_auto_exercise_labels()} 条自动正样本 -> {LABELS}")
        return

    labels = [r for r in load_labels()
              if args.chunk_type is None or r["chunk_type"] == args.chunk_type]
    if not labels:
        print("标注集为空。先跑 --emit，并补齐人工负样本。")
        sys.exit(1)

    global CAND_DUMP
    if args.cands:
        CAND_DUMP = Path(args.cands)
        print(f"候选来自 dump: {CAND_DUMP}（本阶段不碰 Ollama）")

    from app.rag.store import PgStore
    from app.rag.embedder import OllamaEmbedder
    store, emb = PgStore(), OllamaEmbedder()

    # PG 预检（2026-09-20）：与下面 Ollama 那条同理——**不能**静默降级。
    # 此前 PG 不可达时直接抛 psycopg 的 traceback，读的人只看到
    # "fe_sendauth: no password supplied"，不知道缺的是 DATABASE_URL。
    try:
        with store.conn():
            pass
    except Exception as e:
        print("[FAIL] PG 不可达 —— 向量通道无法评估，且**不能**静默降级成 skip：")
        print(f"       {type(e).__name__}: {str(e).splitlines()[0]}")
        print("       修法：设 DATABASE_URL（缺省 DSN 无密码，见 app/rag/store.py）")
        sys.exit(2)

    if args.dump:
        dump_candidates(store, emb, labels, 5, Path(args.dump))
        print(f"已 dump 稠密 top-5 -> {args.dump}（两段式第一步完成）")
        return
    if CAND_DUMP is None and not emb.healthy():
        print("[FAIL] Ollama 不可达 —— 评估**不能**静默降级成 skip，否则报告会假绿")
        sys.exit(2)

    # 陈旧检查：正样本指向的 id 若已不在语料里，指标会被无谓拉低
    with store.conn() as c, c.cursor() as cur:
        cur.execute("SELECT chunk_type, source_ref->>'id' FROM fitness.embeddings")
        live: dict[str, set] = {}
        for t, i in cur.fetchall():
            live.setdefault(t, set()).add(i)
    for r in labels:
        exp = r.get("expected")
        if not exp:
            continue
        ids = [exp] if isinstance(exp, str) else exp
        gone = [i for i in ids if i not in live.get(r["chunk_type"], set())]
        if gone:
            print(f"[STALE] {r['chunk_type']} 标注 id 不在语料中: {gone} ({r['query']})")

    if args.split:
        dev, test = [], []
        for i, r in enumerate(labels):
            (dev if random.Random(SEED + i).random() < args.split else test).append(r)
        print(f"dev={len(dev)} test={len(test)}（阈值在 dev 上选，成绩报 test）")
    else:
        dev = test = labels
        print("[注意] --split 0：阈值与成绩在同一集合上，阈值会偏乐观。")
    n_pos = sum(1 for r in test if r.get("expected"))
    print(f"标注集 {len(labels)} 条（正 {n_pos} / 负 {len(test) - n_pos}）\n")

    names = (["dense", "lexical", "hybrid", "policy", "abstain", "random"]
             if args.retriever == "all" else [args.retriever])

    for name in names:
        if name == "abstain":
            print(f"[abstain] 永远不召回（下限）  "
                  f"{_fmt(score_run(apply_threshold(abstain_top1(test), -1.0)))}")
            continue
        if name == "random":
            trials = [score_run(apply_threshold(
                random_top1(store, test, random.Random(SEED + k)), -1.0))
                for k in range(20)]
            avg = {}
            for k in ("evidence_precision", "wrong_evidence_rate",
                      "recall", "abstain_accuracy"):
                vals = [t[k] for t in trials if t[k] is not None]
                avg[k] = round(sum(vals) / len(vals), 4) if vals else None
            print(f"[random] 20 轮均值 证据准={_p(avg['evidence_precision'])} "
                  f"错证率={_p(avg['wrong_evidence_rate'])} "
                  f"召回={_p(avg['recall'])} 该闭嘴时闭嘴={_p(avg['abstain_accuracy'])}")
            continue

        if name == "dense":
            dev_t1, test_t1 = dense_top1(store, emb, dev), dense_top1(store, emb, test)
        elif name == "policy":
            dev_t1 = policy_top1(store, emb, dev)
            test_t1 = policy_top1(store, emb, test)
        elif name == "hybrid":
            dev_t1 = hybrid_top1(store, emb, dev)
            test_t1 = hybrid_top1(store, emb, test)
        elif name == "graph":
            dev_t1 = graph_top1(store, emb, dev)
            test_t1 = graph_top1(store, emb, test)
        elif name == "llm_rerank":
            dev_t1 = llm_rerank_top1(store, emb, dev)
            test_t1 = llm_rerank_top1(store, emb, test)
        elif name == "ce_rerank":
            dev_t1 = ce_rerank_top1(store, emb, dev)
            test_t1 = ce_rerank_top1(store, emb, test)
        else:
            dev_t1, test_t1 = lexical_top1(store, dev), lexical_top1(store, test)

        if args.grid:
            grid = [float(x) for x in args.grid.split(",")]
        elif name in ("llm_rerank", "policy", "graph"):
            # 分数只编码"选没选/接不接"（接受=1.0 / 弃权=-1.0），没有连续尺度可扫
            # —— 判决由检索器自己下（policy/graph 自带 MIN_SCORE 门槛），不该被网格再扫
            grid = [-1.0, 0.0]
        else:
            grid = auto_grid([s for _, _, s in dev_t1 if s >= 0])

        # 阈值在 dev 上选，按 accuracy。
        # 不要用"precision 优先、再比 recall"——precision 对阈值单调不减，那个规则会把
        # 阈值一路推到极端（第一次跑时 dense 就选了 recall=0.06 的退化解）。
        # accuracy = (TP+TN)/n 同时惩罚挂错证（WRONG/LEAK）和漏召回（MISS）。
        dev_tbl = [(t, score_run(apply_threshold(dev_t1, t))) for t in grid]
        best_t, best_dev = max(dev_tbl, key=lambda kv: kv[1]["accuracy"] or 0)
        print(f"[{name}] 阈值={best_t:.3f}（dev 上选出）")
        print(f"    test  {_fmt(score_run(apply_threshold(test_t1, best_t)))}")
        print(f"    dev   {_fmt(best_dev)}")
        # 扫描行必须带 WRONG/LEAK —— 只看 precision/recall 会把危险格藏起来
        print("    扫描: " + " | ".join(
            f"{t:.3f}→准{_p(m['evidence_precision'])}/回{_p(m['recall'])}"
            f"/错{m['WRONG']}/漏{m['LEAK']}/Acc{_p(m['accuracy'])}"
            for t, m in dev_tbl))


if __name__ == "__main__":
    main()
