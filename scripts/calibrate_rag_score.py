# -*- coding: utf-8 -*-
"""实测 fitness.embeddings 的余弦分数分布，为 app/rag/retriever.MIN_SCORE 定稿提供依据。

相关查询与无关查询各一组，分别看 top1 相似度的 min/p50/max——阈值应当落在
"无关的上限"之上，否则它一条噪声都挡不住。

用法：
    python scripts/calibrate_rag_score.py            # 语料为空时自动 build
    python scripts/calibrate_rag_score.py --build    # 强制重建语料

⚠ 读数时注意：bge-m3 在中文上有很高的相似度地板，无关查询也能拿到 0.49~0.52，
所以两个分布很可能是**重叠**的（2026-09-14 实测就是）。重叠意味着不存在能干净
分开的阈值——那是 dense 单路的固有限制，解法是 rerank 或 dense+sparse 混合召回，
不是继续调这个数。本脚本只负责把事实摆出来。
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

from app.rag.store import PgStore                              # noqa: E402
from app.rag.embedder import OllamaEmbedder                    # noqa: E402
from app.rag.ingest import build                               # noqa: E402
from app.rag import retriever                                  # noqa: E402

# 相关：科学/医学语境下用户真会问的（会过 qa._SCIENCE_KW 那条门）
RELEVANT = ["肌肉生长的科学原理", "增肌的训练强度怎么定", "渐进超负荷是什么",
            "为什么练完会酸痛", "有氧会不会掉肌肉", "蛋白质吃多少增肌",
            "训练频率和恢复的关系", "RPE 是什么意思", "深蹲膝盖姿势", "卧推怎么发力"]
# 无关：完全不在语料范围内，用来量"噪声天花板"
IRRELEVANT = ["今天天气怎么样", "推荐个电影", "帮我订张机票", "股票怎么买",
              "写一首诗", "北京有什么好吃的", "怎么修电脑", "英语怎么学"]


def _top1(emb, store, chunk_type: str, query: str) -> float | None:
    """min_score=None —— 标定要的是原始近邻排序，不能被现阈值截断。"""
    hits = retriever.vector_search(store, emb.embed_one(query), "bge-m3",
                                   top_k=1, chunk_types=(chunk_type,),
                                   min_score=None)
    return hits[0]["score"] if hits else None


def _stat(scores: list[float]) -> str:
    s = sorted(scores)
    return f"n={len(s)} min={s[0]:.3f} p50={s[len(s) // 2]:.3f} max={s[-1]:.3f}"


def main() -> None:
    emb = OllamaEmbedder()
    if not emb.healthy():
        print("[WARN] Ollama 不可达，无法标定")
        sys.exit(1)
    store = PgStore()
    if "--build" in sys.argv or store.counts()["embeddings"] == 0:
        print("building…", flush=True)
        print(build(store), flush=True)

    for chunk_type in ("science_doc", "exercise_cue"):
        rel = [s for s in (_top1(emb, store, chunk_type, q)
                           for q in RELEVANT) if s is not None]
        irr = [s for s in (_top1(emb, store, chunk_type, q)
                           for q in IRRELEVANT) if s is not None]
        print(f"\n=== {chunk_type} ===")
        print(f"  相关   {_stat(rel)}")
        print(f"  无关   {_stat(irr)}")
        print(f"  无关明细: {' '.join(f'{s:.3f}' for s in sorted(irr))}")
        print(f"  相关明细: {' '.join(f'{s:.3f}' for s in sorted(rel))}")
        # 输出只用 ASCII 标记：Windows 控制台是 GBK，⚠/✓ 之类的字符会 UnicodeEncodeError
        if min(rel) <= max(irr):
            print(f"  [OVERLAP] 分布重叠（相关 min {min(rel):.3f} <= 无关 max "
                  f"{max(irr):.3f}）：不存在能干净分开的阈值")
        else:
            print(f"  [SEPARABLE] 可分：阈值落在 ({max(irr):.3f}, {min(rel):.3f}]")


if __name__ == "__main__":
    main()
