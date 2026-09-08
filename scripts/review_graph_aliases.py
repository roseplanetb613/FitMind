# -*- coding: utf-8 -*-
"""图谱别名审核 CLI（W5，风格对齐 review_intent_miss.py）：
pending 队列列表 / 批准 / 拒绝 / 抽样参数。

用法：
  python scripts/review_graph_aliases.py                    # pending 列表（默认 50）
  python scripts/review_graph_aliases.py --limit 20          # 前 20 条
  python scripts/review_graph_aliases.py --approve 别名=domain
  python scripts/review_graph_aliases.py --reject  别名=domain
  python scripts/review_graph_aliases.py --sample 0.2        # 抽样比例（默认全量）
"""
from __future__ import annotations
import argparse
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

from app.graph.store import GraphStore  # noqa: E402


def _set_status(g: GraphStore, spec: str, status: str) -> bool:
    """把 'alias=domain' 的 ALIAS_OF 边状态改为 approved/rejected（幂等）。"""
    alias, sep, domain = spec.partition("=")
    if not sep or not alias.strip() or not domain.strip():
        print(f"用法: --{status} <alias>=<domain>（如 --{status} 蛋白质粉=food）")
        return False
    with g._driver.session(database=g._database) as s:
        res = s.run(
            "MATCH (a:Alias {text: $alias})"
            "-[r:ALIAS_OF]->(t:StandardTerm {domain: $domain}) "
            "SET r.status = $status RETURN count(r) AS n",
            alias=alias.strip(), domain=domain.strip(), status=status)
        return res.single()["n"] > 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=50, help="pending 列表条数")
    ap.add_argument("--sample", type=float, default=None, help="抽样比例 0~1")
    ap.add_argument("--approve", metavar="alias=domain", help="批准一条 pending")
    ap.add_argument("--reject", metavar="alias=domain", help="拒绝一条 pending")
    args = ap.parse_args()

    g = GraphStore.get()
    if g is None:
        print("[降级] Neo4j 不可用，审核需图谱运行（enabled=false / 连接失败）。")
        return 1

    if args.approve or args.reject:
        spec = args.approve or args.reject
        status = "approved" if args.approve else "rejected"
        ok = _set_status(g, spec, status)
        print(f"{status}: {spec} → {'完成' if ok else '未找到该别名/域'}")
        return 0

    rows = g.pending_review(limit=args.limit)
    if args.sample is not None:
        rows = random.sample(rows, max(1, int(len(rows) * args.sample)))
    print(f"pending 审核队列 {len(rows)} 条（domain 分组）\n")
    for d, n in sorted(Counter(r["domain"] for r in rows).items()):
        print(f"  {d:<10} {n:>3}")
    print("\n=== pending 明细 ===")
    for r in rows:
        print(f"  [{str(r['created_at'])[:19]}] {str(r['alias'])[:24]:<24} → "
              f"{str(r['term'])[:16]:<16} ({r['domain']}) conf={r['confidence']:.2f} "
              f"hits={r['hit_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())