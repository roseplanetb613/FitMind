# -*- coding: utf-8 -*-
"""RAG 检索评估：标注集 → 各检索器 → 2x2 混淆 + 阈值扫描。

回答的问题不是"cosine 多少"，而是"挂上去的证据有几成是对的、该闭嘴时闭没闭上"。
两个消费点（qa._rag_science / teach 原 exercise_cue 路径）都是 top_k=1，所以核心指标
是 evidence_precision 与 wrong_evidence_rate，不是 Recall@10 / nDCG。

用法：
    python scripts/eval_rag.py --emit      # 生成/刷新自动正样本到标注集
    python scripts/eval_rag.py             # 全部检索器 + 阈值扫描
    python scripts/eval_rag.py --split 0.5 # dev 上选阈值，报 test 成绩

结构：每个检索器先把每条 query 的 **top1(id, score) 算一次**，之后阈值只是纯过滤——
否则每换一个阈值就要重跑一遍 embed。

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
for p in ("", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

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


def _grams(t: str) -> list[str]:
    t = "".join(t.split())
    return [t[i:i + 2] for i in range(len(t) - 1)] or [t]


class LexIndex:
    """字符二元组 BM25 —— 中文不依赖分词器的词法基线。

    不用 pg_trgm 的原因：实测它在 2 字中文上算不出相似度（'深蹲' vs '深蹲膝盖姿势要点'
    = 0.200，低于默认阈值 0.3）。字符 bigram 没这个问题。索引建一次，多次查询复用。"""

    def __init__(self, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.ids = [i for i, _ in docs]
        self.corpus = [_grams(t) for _, t in docs]
        self.lens = [len(g) for g in self.corpus]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        self.df: Counter = Counter()
        for g in self.corpus:
            self.df.update(set(g))
        self.n = len(self.corpus) or 1

    def top1(self, query: str) -> tuple[str | None, float]:
        if not self.corpus:
            return None, 0.0
        tf_list = [Counter(g) for g in self.corpus]
        best_id, best_s = None, -1.0
        for doc_id, tf, dl in zip(self.ids, tf_list, self.lens):
            s = 0.0
            for term in _grams(query):
                if term not in self.df:
                    continue
                idf = math.log(1 + (self.n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                s += idf * tf[term] * (self.k1 + 1) / (
                    tf[term] + self.k1 * (1 - self.b + self.b * dl / self.avg))
            if s > best_s:
                best_id, best_s = doc_id, s
        return best_id, best_s


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
                    choices=["all", "dense", "lexical", "abstain", "random"])
    ap.add_argument("--chunk-type", default=None)
    ap.add_argument("--split", type=float, default=0.0,
                    help="留出比例：dev 上选阈值，报 test 成绩（0 = 不分，阈值即在报告集上选，偏乐观）")
    ap.add_argument("--grid", default=None, help="逗号分隔阈值，覆盖自动网格")
    args = ap.parse_args()

    if args.emit:
        print(f"emit: {emit_auto_exercise_labels()} 条自动正样本 -> {LABELS}")
        return

    labels = [r for r in load_labels()
              if args.chunk_type is None or r["chunk_type"] == args.chunk_type]
    if not labels:
        print("标注集为空。先跑 --emit，并补齐人工负样本。")
        sys.exit(1)

    from app.rag.store import PgStore
    from app.rag.embedder import OllamaEmbedder
    store, emb = PgStore(), OllamaEmbedder()
    if not emb.healthy():
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

    names = (["dense", "lexical", "abstain", "random"] if args.retriever == "all"
             else [args.retriever])

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
        else:
            dev_t1, test_t1 = lexical_top1(store, dev), lexical_top1(store, test)

        grid = ([float(x) for x in args.grid.split(",")] if args.grid
                else auto_grid([s for _, _, s in dev_t1 if s >= 0]))

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
