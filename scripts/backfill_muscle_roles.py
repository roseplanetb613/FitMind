# -*- coding: utf-8 -*-
"""回填 [:targets] 边的 role 属性 —— target（主动肌）/ synergist（协同肌）。

背景（2026-09-11）：ingest 旧版把 target/muscle_group/secondary 并成一个集合建边，
3861 条边**全无属性、全等权** → 消费方无法区分"主要练胸、三头只是协同"，per-muscle
负荷/恢复无法加权。ingest 已修（新库自带 role），本脚本回填**存量边**。

幂等：只对**已存在**的边 SET 属性，不建新边、不清库（区别于 ingest.build 的
clear_subgraph + clear_all —— 那是重建，不能用来做迁移）。

用法：
    python scripts/backfill_muscle_roles.py --dry-run    # 只看会改多少
    python scripts/backfill_muscle_roles.py
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

BATCH = 500


def roles_for(exercise: dict) -> dict[str, str]:
    """单动作 → {肌群: role}。muscle_group 恒为 secondary 子集（0/1324 例外），
    故并入 synergist 不丢边、不引入第三态。"""
    mus = exercise.get("muscles_canonical") or {}
    out: dict[str, str] = {}
    if mus.get("target"):
        out[mus["target"]] = "target"
    for m in (mus.get("muscle_group"), *(mus.get("secondary") or [])):
        if m:
            out.setdefault(m, "synergist")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from app.graph.store import GraphStore
    from app.runtime.repos import exercise_repo

    g = GraphStore.get()
    if g is None:
        print("Neo4j 不可用 —— 无法回填")
        return 1
    ex = exercise_repo()

    rows = [{"id": e["id"], "muscle": m, "role": r}
            for e in ex.by_id.values() for m, r in roles_for(e).items()]
    print(f"待设属性的边: {len(rows)} 条（{len(ex.by_id)} 动作）")

    with g._driver.session(database=g._database) as s:
        total = s.run("MATCH ()-[t:targets]->() RETURN count(t) AS c").single()["c"]
        already = s.run(
            "MATCH ()-[t:targets]->() WHERE t.role IS NOT NULL "
            "RETURN count(t) AS c").single()["c"]
        print(f"存量 [:targets] 边 {total} 条，其中已有 role 的 {already} 条")

        if args.dry_run:
            hit = 0
            for i in range(0, len(rows), BATCH):
                hit += s.run(
                    "UNWIND $rows AS row "
                    "MATCH (e:Exercise {id: row.id})-[t:targets]->"
                    "(mm:Muscle {name: row.muscle}) "
                    "RETURN count(t) AS c", rows=rows[i:i + BATCH]).single()["c"]
            print(f"[dry-run] 可匹配到的边: {hit} 条（不改动）")
            return 0

        updated = 0
        for i in range(0, len(rows), BATCH):
            res = s.run(
                "UNWIND $rows AS row "
                "MATCH (e:Exercise {id: row.id})-[t:targets]->"
                "(mm:Muscle {name: row.muscle}) "
                "SET t.role = row.role "
                "RETURN count(t) AS c", rows=rows[i:i + BATCH]).single()
            updated += res["c"]

        after = s.run(
            "MATCH ()-[t:targets]->() WHERE t.role IS NOT NULL "
            "RETURN count(t) AS c").single()["c"]
        by_role = {r["r"]: r["c"] for r in s.run(
            "MATCH ()-[t:targets]->() WHERE t.role IS NOT NULL "
            "RETURN t.role AS r, count(*) AS c")}

    print(f"已设属性 {updated} 条；带 role 的边现为 {after}/{total}")
    print(f"角色分布: {by_role}")
    if after != total:
        print(f"⚠ 仍有 {total - after} 条边无 role —— 核对 exercises.json 与图谱是否同步")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
