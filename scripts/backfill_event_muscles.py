# -*- coding: utf-8 -*-
"""回填 [:TARGETS] 边 —— 给**历史打卡事件**补上"这次练了哪些肌群"。

背景：`log_event(muscles=...)` 建边的能力是 2026-09-11 才有的（同日还补了 role）。
在那之前写入的 checkin 事件**只有 Event、没有边**，而 `muscle_load_map` 的查询要求
`(u:User)-[:LOGGED]->(e:Event)-[:TARGETS]->(mm:Muscle)` —— 没有边就一条都读不出来，
于是一整段训练史在 3D 视图上表现为"什么都没有"，且**不会自愈**：补录是靠当时的
写入路径建的边，事后不会自动补。

与 `backfill_muscle_roles.py` 的区别：那个只给**已存在**的边 SET role（注释里明确
写了"不建新边"），解决不了"边压根没有"这一层。本脚本从事件 payload 里的
`items[].name/raw` 重新解析肌群并**建边**。

幂等：MERGE，重复跑不会产生重复边。只补**边数为 0** 的事件，已有边的一律不碰
（避免把当时正确的记录按今天的解析结果改写）。

用法：
    python scripts/backfill_event_muscles.py --dry-run   # 只看会补多少
    python scripts/backfill_event_muscles.py             # 真补
    python scripts/backfill_event_muscles.py --user local --days 3650
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)


# 单字 token 一律不解析。实测 `['练']` 会 CONTAINS 命中 19 个动作名 -> 19 块肌肉
# （abductors ... upper_back）：它是**动词**，不是动作。照单全收的话"我今天练了"
# 会把大半个身体标成刚练完 —— 比不亮更糟。
#
# 中文动作名没有单字的（深蹲/卧推/引体向上…至少两字），所以这条界限既简单又不误伤。
MIN_NAME_LEN = 2


def item_names(payload: dict) -> list[str]:
    """payload - 待解析的动作名列表。
    `name`（归一）与 `raw`（用户原词）都收 —— 与 memory_extract 的 checkin 分支同口径，
    宁多挂不漏挂；解析不中的自然落空。"""
    out: list[str] = []
    for it in (payload.get("items") or []):
        if not isinstance(it, dict):
            continue
        for k in ("name", "raw"):
            v = it.get(k)
            if v and len(str(v).strip()) >= MIN_NAME_LEN:
                out.append(str(v))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="local")
    ap.add_argument("--days", type=int, default=3650,
                    help="回看窗口（天）。默认 10 年，即全部")
    ap.add_argument("--event-type", default="checkin")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from app.graph.memory import MemoryStore
    m = MemoryStore.get()
    if m is None:
        print("x 图谱不可用（MemoryStore.get() 返回 None）")
        return 1

    with m._g._driver.session(database=m._g._database) as s:
        rows = [dict(r) for r in s.run(
            "MATCH (u:User {user_id: $uid})-[:LOGGED]->(e:Event) "
            "WHERE e.type = $t AND e.invalidated_at IS NULL "
            "  AND NOT (e)-[:TARGETS]->(:Muscle) "
            "RETURN e.event_id AS eid, e.occurred_at AS at, e.payload AS payload "
            "ORDER BY e.occurred_at",
            uid=args.user, t=args.event_type)]

    print(f"用户 {args.user!r}：{len(rows)} 条 {args.event_type} 事件没有 TARGETS 边")

    filled = skipped = 0
    total_edges = 0
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        names = item_names(payload)
        muscles = m.muscles_of_exercises(names) if names else []
        if not muscles:
            skipped += 1
            print(f"  · {r['at']}  解析不中：{names} —— 跳过（原样保留，不猜）")
            continue
        roles = m.muscle_roles_of_exercises(names)
        print(f"  - {r['at']}  {names} -> {len(muscles)} 块：{', '.join(muscles)}")
        if args.dry_run:
            filled += 1
            total_edges += len(muscles)
            continue
        if m.add_event_muscles(r["eid"], muscles, roles):
            filled += 1
            total_edges += len(muscles)
        else:
            print(f"    x 写边失败：{r['eid']}")

    verb = "将补" if args.dry_run else "已补"
    print(f"\n{verb} {filled} 条事件 / {total_edges} 条边；{skipped} 条解析不中而跳过")
    if skipped:
        print("  ! 解析不中的那些**没有被猜**：它们要么是动词/部位说法（'完腿'），")
        print("    要么是库里没有的动作。硬猜会点亮无关肌肉，比不亮更糟。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
