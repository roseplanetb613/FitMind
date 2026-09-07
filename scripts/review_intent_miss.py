# -*- coding: utf-8 -*-
"""意图缺口周审脚本：按 guessed 分组统计 intent_miss 表 + 导出最近 N 条。
人工周审后手动回流 vocab.py（症状词）/ intent_exemplars.json（例句）。

用法：
  python scripts/review_intent_miss.py            # 控制台按组统计 + 最近 20 条
  python scripts/review_intent_miss.py --json    # 输出 JSON（供管道消费）
  python scripts/review_intent_miss.py --limit=50 # 最近 50 条
"""
from __future__ import annotations
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

from app.storage.db import DEFAULT_DB, LogStore  # noqa: E402


def main() -> None:
    as_json = "json" in sys.argv or "--json" in sys.argv
    limit = 20
    for a in sys.argv:
        if a.startswith("--limit="):
            try:
                limit = int(a.split("=", 1)[1])
            except ValueError:
                pass
    rows = LogStore(DEFAULT_DB).misses(limit=limit)
    groups = Counter(r["guessed"] for r in rows)
    if as_json:
        print(json.dumps({
            "total": len(rows),
            "by_guessed": dict(groups),
            "rows": rows,
        }, ensure_ascii=False, indent=2))
        return
    print(f"intent_miss 最近 {len(rows)} 条记录（guessed 分组）\n")
    for tt, n in sorted(groups.items()):
        print(f"  {tt:<12} {n:>3}")
    print("\n=== 明细（最近 N 条）===")
    for r in rows:
        print(f"  [{r['created_at']}] {r['guessed']:<12} conf={r['confidence']:.2f} "
              f"| {r['text'][:40]}")


if __name__ == "__main__":
    main()