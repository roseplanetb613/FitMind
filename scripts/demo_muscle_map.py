# -*- coding: utf-8 -*-
"""肌群状态图样张生成（可复现）。

用法：
    python scripts/demo_muscle_map.py                # 用内置演示打卡
    python scripts/demo_muscle_map.py <user_id>      # 用真实用户（只读，不写）

产出：storage_output/muscle_map_demo.svg（浏览器直接打开）
另打印每个有记录肌群的中文名 + 恢复度，便于先核对数值是否合理再谈视觉。
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

OUT = ROOT / "storage_output" / "muscle_map_demo.svg"
# 演示打卡（仅在未指定 user_id 时使用；用独立 uid 且跑完清理）
DEMO_CHECKINS = ("昨天我练了杠铃卧推", "前天我练了深蹲", "今天练了引体向上")


def main(argv=None) -> int:
    import recovery
    import muscle_map
    from app.graph.memory import MemoryStore
    from app.runtime.repos import exercise_repo

    argv = argv if argv is not None else sys.argv[1:]
    m = MemoryStore.get()
    if m is None:
        print("Neo4j 不可用 —— 无法生成样张")
        return 1

    uid = argv[0] if argv else f"demo_{datetime.now().strftime('%H%M%S')}"
    if not argv:
        from app.graph.memory_extract import apply_memory_extract
        print(f"[演示] 使用临时 uid={uid}，跑完即清理")
        for t in DEMO_CHECKINS:
            apply_memory_extract(t, uid)

    now = datetime.now(timezone.utc)
    states = recovery.recovery_map(m.muscle_load_map(uid, days=7), now)
    labels = muscle_map.labels_from_repo()
    # 无记录的肌群显式置 None → 渲染为"未知态"（与"恢复满"视觉可区分）
    for mid in labels:
        states.setdefault(mid, None)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(muscle_map.render(states, labels=labels), encoding="utf-8")
    print(f"\n样张已写 {OUT}（浏览器直接打开）\n")
    hit = [(k, v) for k, v in states.items() if v]
    for mid, st in sorted(hit, key=lambda kv: kv[1]["recovery"]):
        print(f"  {labels.get(mid, mid):8} {recovery.describe(mid, st)}")
    print(f"\n有记录 {len(hit)} 个肌群 / 共 {len(states)} 个")

    if not argv:
        m.forget(uid)
        print("[演示] 临时数据已清理")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
