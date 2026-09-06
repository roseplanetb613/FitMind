# -*- coding: utf-8 -*-
"""RAG 入湖管线（W1：图数据；W2 补 embedding 块）。
幂等：clear_all 后重建。数据源：lib 只读 repo（不碰 data/）。
性能：build 内复用单一连接 + 单事务批量灌入（避免逐操作 open connection）。"""
from __future__ import annotations
import sys
from pathlib import Path

import psycopg                              # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
for p in ("lib",):                       # ingest 内自足（脚本/import 两用）
    if str(ROOT / p) not in sys.path:
        sys.path.insert(0, str(ROOT / p))

from app.rag.store import PgStore          # noqa: E402
from exercise_repo import ExerciseRepo      # noqa: E402
from screening import CONDITIONS            # noqa: E402


def build(store: PgStore | None = None) -> dict:
    store = store or PgStore()
    store.apply_schema()
    store.clear_all()
    ex = ExerciseRepo()

    # 单一连接 + 单事务：1324 动作 × 多条边一次灌入（提交前不落盘）
    with psycopg.connect(store.dsn, autocommit=False) as conn, conn.cursor() as cur:
        node_ids = {}
        def node(kind, name, meta=None):
            cur.execute(
                "INSERT INTO fitness.graph_nodes(kind,name,meta) VALUES(%s,%s,%s) "
                "ON CONFLICT (name) DO UPDATE SET kind=EXCLUDED.kind, "
                "meta=EXCLUDED.meta RETURNING id",
                (kind, name, psycopg.types.json.Jsonb(meta or {})))
            nid = int(cur.fetchone()[0])
            node_ids.setdefault(kind, {})[name] = nid
            return nid

        def edge(src_id, dst_id, rel):
            cur.execute(
                "INSERT INTO fitness.graph_edges(src_id,dst_id,rel) "
                "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (src_id, dst_id, rel))

        # 动作（name=动作 id，meta 存中英文名）
        for e in ex.by_id.values():
            nid = node("exercise", e["id"],
                       {"name_zh": e.get("name_zh"), "name_en": e.get("name")})
            mus = e.get("muscles_canonical") or {}
            for m in ({mus.get("target"), mus.get("muscle_group")} |
                      set(mus.get("secondary") or [])):
                if m:
                    node("muscle", m)                                  # 规范 id 即 name
                    edge(nid, node_ids["muscle"][m], "targets")
            eq = e.get("normalized_equipment")
            if eq:
                node("equipment", eq)
                edge(nid, node_ids["equipment"][eq], "uses")
            pat = e.get("movement_pattern")
            if pat:
                node("pattern", pat)
                edge(nid, node_ids["pattern"][pat], "pattern_of")

        # 禁忌（疾病名 → 危险动作模式）
        for ci in CONDITIONS:
            head = ci["condition_zh"].split("（")[0].split("/")[0]
            cid = node("condition", head, {"condition_zh": ci["condition_zh"],
                                           "risk_level": ci["risk_level"],
                                           "source": ci.get("source")})
            for dp in ci.get("danger_patterns", []):
                node("pattern", dp)
                edge(cid, node_ids["pattern"][dp], "contraindicates")

        conn.commit()

    return store.counts()


if __name__ == "__main__":
    print("ingest:", build())