# -*- coding: utf-8 -*-
"""Neo4j 统一图存储（app/graph/store.py，合并计划 §4）。

- 单例 get()：driver 未装 / 连接失败 / graph_config.enabled=false → None，
  上层全部按 None 静默降级（沿用项目"异常静默"传统）。
- 实体子图（seed 重建用）：merge_entity / merge_rel / clear_subgraph。
- 图检索（retriever 迁移用）：context / muscle_exercises / family_alternatives。
- 别名子图（在线沉淀用）：lookup_alias / upsert_alias / lookup_intent / pending_review。
全部 Cypher 参数化；连接/查询超时约束，绝不让外部服务拖慢主路径。
"""
from __future__ import annotations
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.core.llm import load_dotenv
from lib.screening import sort_by_risk

load_dotenv()

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "graph_config.json"

# 实体子图标签 + rel 名（与 ingest 迁移保持一致：rel 小写）
_ENTITY_LABELS = ("Exercise", "Muscle", "Equipment", "Pattern", "Condition", "Family")

# 节点唯一键 —— **恢复跨区边时**用它重新定位两端（clear_subgraph 会连带删掉跨区边，
# 重建实体子图后必须按属性键把边接回来，不能靠节点 identity）。
# 键名与 merge_entity/merge_entities_batch 用的 key 一致（Exercise/Family 按 id，
# 其余实体按 name）；个人域节点各自的 id 属性名见 memory.py 的写入侧。
_NODE_KEYS = {
    "Exercise": "id", "Family": "id",
    "Muscle": "name", "Equipment": "name", "Pattern": "name", "Condition": "name",
    "Event": "event_id", "StateFact": "fact_id", "PlanVersion": "plan_id",
    "User": "user_id", "Food": "name",
}

# 跨区边白名单：**个人域 → 实体子图**。它们的远端不在 _ENTITY_LABELS 里，
# 近端是实体节点，故 `DETACH DELETE` 掉实体端点会连带删除它们。
# 用白名单（而不是照抄任何边）是为了恢复时能安全地把 rel 名拼进 Cypher。
# ⚠ 新增跨区边时必须登记到这里，否则重建图谱时会被静默丢弃（_snapshot 只恢复白名单内的边）。
_CROSS_RELS = ("TARGETS", "ABOUT_MUSCLE")

# 同族替代的条数上限。词干族最大 49 人，即使语义正确（如 f003 'curl' 27 人全是
# biceps），26 条"替代动作"也不是一份可用的清单。6 是"够挑几个换着练"的量级。
_FAMILY_ALT_LIMIT = 6
# 同主肌动作的条数上限。28 块肌群里大肌群（如 pectorals）会有上百个动作，
# 无上限的清单对用户不可用。取值依据见 docs/SDD/2026-09-23-graph-channel-probe.md 的 P2。
_GRAPH_PEER_LIMIT = 6
_REL_MAP = {  # 冻结 rel 白名单（防注入）
    "targets", "uses", "pattern_of", "contraindicates", "member_of",
    "ALIAS_OF", "IMPLIES_INTENT", "MAPS_TO",
}

# 约束 DDL（合并计划 §4；IF NOT EXISTS 幂等；rel/标签名均为白名单常量）
_SCHEMA_STMTS = (
    "CREATE CONSTRAINT exercise_id IF NOT EXISTS FOR (n:Exercise) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT family_id IF NOT EXISTS FOR (n:Family) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (n:Entity) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT alias_text IF NOT EXISTS FOR (a:Alias) REQUIRE a.text IS UNIQUE",
    "CREATE CONSTRAINT term_key IF NOT EXISTS FOR (t:StandardTerm) REQUIRE (t.name, t.domain) IS UNIQUE",
)


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


class GraphStore:
    """Neo4j 图存储。构造即连 driver（惰性）；查询由上层 try/except 兜底。"""

    _instance: "GraphStore | None" = None
    _lock = threading.Lock()
    _last_fail = 0.0
    FAIL_COOLDOWN = 30.0           # 连接失败冷却窗口（秒），窗口内直接 None

    def __init__(self, uri: str, database: str, user: str, password: str,
                 conn_timeout: float = 2.0):
        from neo4j import GraphDatabase
        self._uri = uri
        self._database = database
        self._driver = GraphDatabase.driver(
            uri, auth=(user, password), connection_timeout=conn_timeout)
        self._schema_ready = False
        # 跨区边快照：clear_subgraph 写、restore_cross_links 消费。
        # 挂在实例上（GraphStore 是单例）——同一次 ingest 内 clear → 重建 → restore
        # 时序固定，不需要持久化；真丢了还有 scripts/backfill_event_muscles.py 兜底。
        self._pending_cross_links: list[dict] = []
        # W5：查询硬超时（graph_config.lookup_timeout_ms，默认 200ms），
        # 前置别名查询绝不拖慢主路径；Neo4j 秒级单位。
        try:
            self._query_timeout = (_load_config().get("lookup_timeout_ms", 200)
                                   or 200) / 1000.0
        except Exception:
            self._query_timeout = 0.2

    # ---- 降级单例 ----

    @classmethod
    def get(cls) -> "GraphStore | None":
        if cls._instance is not None:
            return cls._instance
        cfg = _load_config()
        if not cfg.get("enabled", False):
            return None
        if time.monotonic() - cls._last_fail < cls.FAIL_COOLDOWN:
            return None                     # 冷却窗口内不重连
        try:
            from neo4j import GraphDatabase  # noqa: F401  强依赖检查
        except Exception:
            cls._last_fail = time.monotonic()
            return None
        password = os.environ.get("NEO4J_PASSWORD", "")
        if not password:
            cls._last_fail = time.monotonic()
            return None
        with cls._lock:
            if cls._instance is not None:
                return cls._instance
            store = cls(cfg.get("uri", "bolt://localhost:7687"),
                        cfg.get("database", "fitmind"), "neo4j", password)
            try:
                store._driver.verify_connectivity()   # 真连一次，失败即降级
            except Exception:
                store.close()
                cls._last_fail = time.monotonic()
                return None
            store.ensure_schema()
            cls._instance = store
            return store

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:
            pass

    # ---- 约束 DDL（幂等） ----

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._driver.session(database=self._database) as s:
            for stmt in _SCHEMA_STMTS:
                s.run(stmt)
        self._schema_ready = True

    # ---- 实体子图（seed 幂等重建用） ----

    def merge_entity(self, label: str, key: str, props: dict) -> None:
        """MERGE 实体：以 (label, key 属性名=值) 唯一。props 合并写入。"""
        _validate_label(label)
        _validate_key(key, props)
        with self._driver.session(database=self._database) as s:
            s.run(
                f"MERGE (n:{label} {{{key}: $k}}) "
                "ON CREATE SET n += $p ON MATCH SET n += $p",
                k=props[key], p={k2: v for k2, v in props.items() if k2 != key})

    def merge_rel(self, label_a: str, key_a: str, val_a, rel: str,
                  label_b: str, key_b: str, val_b,
                  props: dict | None = None) -> None:
        """MERGE 有向边 (a)-[rel]->(b)；a/b 按 label+key 属性名与值定位。
        边属性仅合并 props（不含两端 key）。"""
        _validate_label(label_a); _validate_label(label_b)
        _validate_rel(rel)
        with self._driver.session(database=self._database) as s:
            s.run(
                f"MATCH (a:{label_a} {{{key_a}: $ka}}), "
                f"(b:{label_b} {{{key_b}: $kb}}) "
                f"MERGE (a)-[r:{rel}]->(b) SET r += $p",
                ka=val_a, kb=val_b, p=_prel(props or {}))

    def merge_entities_batch(self, label: str, key: str,
                             items: list[dict], batch: int = 500,
                             rels: list[dict] | None = None) -> None:
        """UNWIND 批量 MERGE 实体（单事务）。items 每项须含 key 属性。
        rels 可选：同事务合并边 {a_key, b_key, rel, props}。"""
        _validate_label(label)
        if not items:
            return
        rows = [{"k": it[key],
                 "p": {k2: v for k2, v in it.items() if k2 != key}}
                for it in items if key in it]

        def _tx(tx):
            for i in range(0, len(rows), batch):
                tx.run(
                    f"UNWIND $rows AS r "
                    f"MERGE (n:{label} {{{key}: r.k}}) "
                    f"ON CREATE SET n += r.p ON MATCH SET n += r.p",
                    rows=rows[i:i + batch])
            for r in rels or []:
                tx.run(
                    f"MATCH (a:{label} {{{key}: $ka}}), "
                    f"(b:{r['b_label']} {{{r['b_key']}: $kb}}) "
                    f"MERGE (a)-[r_:{r['rel']}]->(b) SET r_ += $p",
                    ka=r["a_key"], kb=r["b_key"], p=r.get("props", {}))
        with self._driver.session(database=self._database) as s:
            s.execute_write(_tx)

    def clear_subgraph(self, source: str = "seed") -> int:
        """清实体子图（seed 幂等重建），**先快照跨区边、待重建后接回**。返回快照条数。

        ⚠ 为什么必须快照（2026-09-16 实测事故，别删这段说明）：
        `(Event)-[:TARGETS]->(Muscle)` 的起点 Event 在**个人域**（不在 _ENTITY_LABELS），
        终点 Muscle 在实体子图。`DETACH DELETE` 掉 Muscle 会把这条边**一起删掉**，
        而 Event 完好无损 —— 症状是"对话里问得出训练记录、3D 肌肉模型全显示无记录"，
        且两处都不报错（3D 侧只是 28 项全 null，与"真没练过"完全同形，极难发现）。
        同一机制还删 `(StateFact:injury)-[:ABOUT_MUSCLE]->(Muscle)`（伤情面板的
        active_injury 因此恒为空）。

        配对用法：`clear_subgraph()` → 重建实体子图 → `restore_cross_links()`。
        漏了第三步时，下一次 clear_subgraph 会先自愈式补一次（见下方），但那只是
        兜底，别依赖。绝不动 llm_* 在线沉淀的别名子图（沿用原语义）。
        """
        # 上次快照还没被恢复（调用方漏调 restore）→ 先尽力接一次再覆盖。否则这次
        # 快照会把它顶掉，那批边就真没了。实体子图尚未重建时 MATCH 自然不中，
        # 静默跳过即可（不抛）。
        if self._pending_cross_links:
            self.restore_cross_links()
        with self._driver.session(database=self._database) as s:
            self._pending_cross_links = self._snapshot_cross_links(s)
            for lbl in _ENTITY_LABELS:
                s.run(f"MATCH (n:{lbl}) DETACH DELETE n")
        return len(self._pending_cross_links)

    def _snapshot_cross_links(self, session) -> list[dict]:
        """快照**恰好一端**落在实体子图里的边（即跨区边）。两端各记 label 与全部属性。

        白名单外的跨区边也照样收下（恢复时才会拒）——这样返回值与恢复条数的差额
        能暴露"有边没接回来"，比静默吞掉好。
        """
        ent = list(_ENTITY_LABELS)
        out: list[dict] = []
        for r in session.run(
                "MATCH (a)-[r]->(b) "
                "WHERE (ANY(l IN labels(a) WHERE l IN $ent)) <> "
                "      (ANY(l IN labels(b) WHERE l IN $ent)) "
                "RETURN labels(a) AS al, properties(a) AS ap, "
                "       type(r) AS rt, properties(r) AS rp, "
                "       labels(b) AS bl, properties(b) AS bp", ent=ent):
            out.append({"al": list(r["al"]), "ap": dict(r["ap"]),
                        "rt": str(r["rt"]), "rp": dict(r["rp"]),
                        "bl": list(r["bl"]), "bp": dict(r["bp"])})
        return out

    def restore_cross_links(self) -> int:
        """实体子图重建后调用：把快照的跨区边按**属性键**接回来。返回恢复条数。

        MERGE 幂等；快照为空 → 0。任一端在当前图里找不到、rel 不在白名单
        → 跳过该条（不抛、**不硬造节点**——硬造会污染图谱，与 memory.py 的
        "MATCH 不中静默跳过"同一取向）。跳过的条数进 diag，可查而不响。
        """
        pending, self._pending_cross_links = self._pending_cross_links, []
        if not pending:
            return 0
        ok = skipped = 0
        with self._driver.session(database=self._database) as s:
            for e in pending:
                rel = e.get("rt")
                la, ka = _node_label_key(e.get("al") or [])
                lb, kb = _node_label_key(e.get("bl") or [])
                va = (e.get("ap") or {}).get(ka) if ka else None
                vb = (e.get("bp") or {}).get(kb) if kb else None
                if rel not in _CROSS_RELS or not la or not lb \
                        or va is None or vb is None:
                    skipped += 1
                    continue
                _validate_label(la)
                _validate_label(lb)
                s.run(f"MATCH (a:{la} {{{ka}: $va}}), (b:{lb} {{{kb}: $vb}}) "
                      f"MERGE (a)-[r:{rel}]->(b) SET r += $p",
                      va=va, vb=vb, p=e.get("rp") or {})
                ok += 1
        if skipped:
            from app.core import diag      # 局部导入：store.py 不依赖 app.core 其余部分
            diag.bump("graph.cross_link_skipped", skipped)
        return ok

    def counts(self) -> dict:
        """实体子图节点/边计数（别名子图不计入）。"""
        with self._driver.session(database=self._database) as s:
            n = s.run(
                "MATCH (n) WHERE ANY(l IN $lbls WHERE l IN labels(n)) "
                "RETURN count(n) AS c", lbls=list(_ENTITY_LABELS)).single()["c"]
            e = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            return {"nodes": int(n), "edges": int(e)}

    # ---- 图检索（retriever 迁移用，签名对齐现 PG 版返回结构） ----

    def context(self, name: str, hops: int = 1,
                rels: tuple[str, ...] | None = None) -> list[dict]:
        """沿边多跳遍历。返回 [{"name","kind","rel","depth"}]；rel 限定边类型。
        Exercise 节点按 id（name=null），其余按 name 匹配。"""
        rels_ok = [r for r in (rels or ()) if r in _REL_MAP]
        with self._driver.session(database=self._database) as s:
            q = ("MATCH (start) WHERE start.name = $name OR start.id = $name "
                 "RETURN start AS d, 0 AS depth, null AS last_rel "
                 "UNION ALL "
                 "MATCH (start) WHERE start.name = $name OR start.id = $name "
                 f"MATCH path = (start)-[*1..{int(hops)}]-(d) ")
            if rels_ok:
                q += "WHERE ALL(r IN relationships(path) WHERE type(r) IN $rels) "
            q += ("RETURN d, length(path) AS depth,"
                  " type(last(relationships(path))) AS last_rel LIMIT 500")
            res = s.run(q, name=name, rels=rels_ok)
            return [{"name": (r["d"].get("name") or r["d"].get("id")),
                     "kind": _kind_of(r["d"]),
                     "rel": r["last_rel"] if r["depth"] else None,
                     "depth": r["depth"]} for r in res]

    def muscle_exercises(self, muscle: str, limit: int = 8) -> list[dict]:
        """经 targets 反查锻炼该肌肉的动作，返回 [{"name","kind"}]（name=动作 id）。"""
        with self._driver.session(database=self._database) as s:
            res = s.run(
                "MATCH (n:Exercise)-[:targets]->(m:Muscle {name: $muscle}) "
                "RETURN DISTINCT n.id AS name LIMIT $limit",
                muscle=muscle, limit=limit)
            return [{"name": r["name"], "kind": "exercise"} for r in res]

    def family_alternatives(self, exercise_id: str,
                            limit: int = _FAMILY_ALT_LIMIT) -> list[dict]:
        """同族变体（可替代）：member_of → 同族其它动作。

        返回 [{"id","name_zh","kind"}]，**共享主目标肌的排在前面**，且有条数上限。

        2026-09-14 两处修正（审计见 docs/SDD/2026-09-14-variant-family-audit.md）：
        1. 加上限 + 同主肌优先。此前返回**全部**同族：`f001 'press'` 对杠铃卧推会返回
           48 条，其中混着「杠铃坐姿过头推举」「哑铃交替侧推」这类练别的的动作——
           族是按动作**词干**划的，不等于可互换。词干族里 8 个大族只有 f001 真混了语义，
           其余 139 族经审计合理，故这里只做排序+截断，**不重划族表**。
        2. 带上可读名 `name_zh`。此前用 `name` 存动作 id（`graph_*` 的历史约定），
           消费端只能拿到 `"1309"` 这种不可读的值。
        ⚠ 因此本函数**不再返回 `name` 键**，与 `muscle_exercises` 的约定不同——
           后者目前零消费方（随 hybrid_retrieve 一起停用），保留原样未动。
        """
        with self._driver.session(database=self._database) as s:
            res = s.run(
                "MATCH (e:Exercise {id: $eid})-[:member_of]->(f:Family)"
                "<-[:member_of]-(alt:Exercise) WHERE alt.id <> $eid "
                "OPTIONAL MATCH (e)-[:targets {role: 'target'}]->(tm:Muscle)"
                "<-[:targets {role: 'target'}]-(alt) "
                "WITH alt, count(DISTINCT tm) AS shared "
                "RETURN alt.id AS id, alt.name_zh AS name_zh, shared "
                "ORDER BY shared DESC, alt.id "
                "LIMIT $limit",
                eid=exercise_id, limit=limit)
            return [{"id": r["id"], "name_zh": r["name_zh"], "kind": "exercise"}
                    for r in res]

    def muscle_peers(self, exercise_id: str,
                     limit: int = _GRAPH_PEER_LIMIT) -> list[dict]:
        """同主目标肌的**其它**动作（枚举能力的来源）。

        返回 [{"id","name_zh","kind"}]，与 `family_alternatives` 同一形状。

        ⚠ 与 `member_of` 族的区别：族是按动作**词干**划的（`2026-09-14` 审计：
        140 族里 f001 真混语义），而这里按 `targets{role:'target'}` 的实际
        目标肌走 —— 语义正确性由数据保证，不靠族表。
        ⚠ 必须带上限：这是 2 跳遍历，大肌群会返回上百条。
        """
        with self._driver.session(database=self._database) as s:
            res = s.run(
                "MATCH (e:Exercise {id: $eid})-[:targets {role: 'target'}]"
                "->(m:Muscle)<-[:targets {role: 'target'}]-(alt:Exercise) "
                "WHERE alt.id <> $eid "
                "RETURN DISTINCT alt.id AS id, alt.name_zh AS name_zh "
                "ORDER BY alt.id LIMIT $limit",
                eid=exercise_id, limit=limit)
            return [{"id": r["id"], "name_zh": r["name_zh"], "kind": "exercise"}
                    for r in res]

    def contraindications(self, exercise_id: str) -> list[dict]:
        """动作的禁忌链：Exercise -pattern_of-> Pattern <-contraindicates- Condition。

        返回 [{"pattern","condition","risk_level"}]，按严重度 **red → yellow → green**
        排序。序**只来自** `lib.screening.sort_by_risk`（`RISK_ORDER` 是全仓唯一来源，
        见该模块注释）—— 不要在别处再写一份。

        ⚠ 为什么不在 Cypher 里排：`risk_level` 是英文枚举（red/yellow/green），
        `ORDER BY risk_level DESC` 是**字符串比较**，码点序是 'yellow' > 'red' > 'green'
        —— 会把 red 排到**最后**。2026-09-23 首版即此写法（本仓实测踩过）。

        ⚠ 为什么没有 LIMIT：一个动作只有一个 `movement_pattern`，`Condition` 全集
        只 7 个节点，结果天然有界。先 LIMIT 再排序正是 red 被丢掉的方式。

        ⚠ **检索层**唯一能产出"别做这个"的能力，也是图相对向量的结构性优势
        （向量只有相似度，说不出"因为有肩伤所以别做"）。同源的禁忌块另有
        `lib/screening.py:plan_check` 与 `app/skills/guard_skill.py:_memory_intercept`
        —— 三者都从同一份 contraindications.json 出发，改一处要一起看。
        ⚠ 覆盖稀疏：`Condition` 只 7 个、`Pattern` 只 10 个，而 `Exercise` 有 1324 个
        —— 走不通是**正常结果**，不是错误（调用方不得因此报错）。见探针 P5。
        """
        with self._driver.session(database=self._database) as s:
            res = s.run(
                "MATCH (e:Exercise {id: $eid})-[:pattern_of]->(p:Pattern)"
                "<-[:contraindicates]-(c:Condition) "
                "RETURN DISTINCT p.name AS pattern, c.name AS condition, "
                "       c.risk_level AS risk_level",
                eid=exercise_id)
            rows = [{"pattern": r["pattern"], "condition": r["condition"],
                     "risk_level": r["risk_level"]} for r in res]
        return sort_by_risk(rows)

    # ---- 别名子图（在线沉淀 + 前置查询） ----

    def lookup_alias(self, text: str, domain: str) -> str | None:
        """查 Alias→StandardTerm（仅 status=approved）；命中则 hit_count+1。
        W5 前置查询：200ms 硬超时，绝不让图谱拖慢主路径。"""
        with self._driver.session(database=self._database) as s:
            res = s.run(
                "MATCH (a:Alias {text: $text})"
                "-[r:ALIAS_OF {status: 'approved'}]->(t:StandardTerm {domain: $domain}) "
                "WITH a, r, t ORDER BY r.hit_count DESC LIMIT 1 "
                "SET r.hit_count = coalesce(r.hit_count, 0) + 1 "
                "RETURN t.name AS term", text=text, domain=domain,
                timeout=self._query_timeout)
            row = res.single()
            return row["term"] if row else None

    def upsert_alias(self, alias: str, term: str, domain: str,
                     confidence: float, source: str) -> str:
        """按 §4.2 阈值写 status（≥0.8 approved；0.5~0.8 pending；<0.5 不写）。
        已存在则合并 hit_count。返回写入的 status 或 'rejected'。"""
        status = ("approved" if confidence >= 0.8
                  else "pending" if confidence >= 0.5 else None)
        if status is None:
            return "rejected"
        created_at = datetime.now(timezone.utc).isoformat()
        with self._driver.session(database=self._database) as s:
            s.run(
                "MERGE (a:Alias {text: $alias}) "
                "MERGE (t:StandardTerm {name: $term, domain: $domain}) "
                "MERGE (a)-[r:ALIAS_OF]->(t) "
                "SET r.confidence = $conf, r.source = $src, r.status = $status, "
                "    r.created_at = $created, "
                "    r.hit_count = coalesce(r.hit_count, 0)",
                alias=alias, term=term, domain=domain,
                conf=float(confidence), src=source, status=status,
                created=created_at)
        return status

    def lookup_intent(self, text: str) -> tuple[str, float] | None:
        """查 Alias→StandardTerm→IMPLIES_INTENT；返回 (task_type, confidence)。"""
        with self._driver.session(database=self._database) as s:
            res = s.run(
                "MATCH (a:Alias {text: $text})-[:ALIAS_OF {status: 'approved'}]"
                "->(t)-[r:IMPLIES_INTENT]->(i) "
                "ORDER BY r.confidence DESC LIMIT 1 "
                "RETURN i.task_type AS tt, r.confidence AS conf",
                text=text, timeout=self._query_timeout)
            row = res.single()
            return (row["tt"], float(row["conf"])) if row else None

    def pending_review(self, limit: int = 50) -> list[dict]:
        """审核队列：status=pending 按 created_at 升序。"""
        with self._driver.session(database=self._database) as s:
            res = s.run(
                "MATCH (a:Alias)-[r:ALIAS_OF {status: 'pending'}]->(t:StandardTerm) "
                "RETURN a.text AS alias, t.name AS term, t.domain AS domain, "
                "       r.confidence AS confidence, r.created_at AS created_at, "
                "       r.hit_count AS hit_count "
                "ORDER BY r.created_at LIMIT $limit", limit=limit)
            return [dict(r) for r in res]

    # ---- 种子迁移（W2：静态词表 → 图谱，source=seed） ----

    def upsert_alias_intent(self, text: str, task_type: str,
                            confidence: float = 1.0) -> None:
        """例句库 → Alias→ALIAS_OF(approved)→StandardTerm→IMPLIES_INTENT→Intent。
        意图例句不指定 domain，StandardTerm 以 intent 为主键复用。"""
        created = datetime.now(timezone.utc).isoformat()
        with self._driver.session(database=self._database) as s:
            s.run(
                "MERGE (a:Alias {text: $text}) "
                "MERGE (t:StandardTerm {name: $text, domain: 'intent'}) "
                "MERGE (i:Intent {task_type: $tt}) "
                "MERGE (a)-[r1:ALIAS_OF]->(t) "
                "SET r1.status = 'approved', r1.confidence = $conf, "
                "    r1.source = 'seed', r1.created_at = $created, "
                "    r1.hit_count = coalesce(r1.hit_count, 0) "
                "MERGE (t)-[r2:IMPLIES_INTENT]->(i) "
                "SET r2.confidence = $conf, r2.source = 'seed'",
                text=text, tt=task_type, conf=float(confidence), created=created)

    def maps_to(self, term_name: str, domain: str,
                entity_label: str, entity_key: str, entity_val) -> None:
        """StandardTerm→实体（MAPS_TO）：长尾说法一旦命中，连同实体关系带出。
        实体已存在则仅建边；不存在则跳过（不虚构实体）。"""
        _validate_label(entity_label)
        with self._driver.session(database=self._database) as s:
            s.run(
                "MATCH (t:StandardTerm {name: $term, domain: $domain}), "
                f"(e:{entity_label} {{{entity_key}: $ev}}) "
                "MERGE (t)-[:MAPS_TO]->(e)",
                term=term_name, domain=domain, ev=entity_val)

    def upsert_intent_rewrite(self, alias: str, standard: str, task_type: str,
                              confidence: float, source: str = "llm_auto") -> str:
        """意图归一化一次性写回：Alias(原句)→ALIAS_OF→StandardTerm(改写,domain=intent)
        →IMPLIES_INTENT→Intent{task_type}。status 按 §4.2 阈值分流；<0.5 不写。
        返回 status（approved/pending/rejected）。"""
        status = ("approved" if confidence >= 0.8
                  else "pending" if confidence >= 0.5 else None)
        if status is None:
            return "rejected"
        created = datetime.now(timezone.utc).isoformat()
        with self._driver.session(database=self._database) as s:
            s.run(
                "MERGE (a:Alias {text: $alias}) "
                "MERGE (t:StandardTerm {name: $std, domain: 'intent'}) "
                "MERGE (i:Intent {task_type: $tt}) "
                "MERGE (a)-[r1:ALIAS_OF]->(t) "
                "SET r1.status = $status, r1.confidence = $conf, "
                "    r1.source = $src, r1.created_at = $created, "
                "    r1.hit_count = coalesce(r1.hit_count, 0) "
                "MERGE (t)-[r2:IMPLIES_INTENT]->(i) "
                "SET r2.confidence = $conf, r2.source = $src",
                alias=alias, std=standard, tt=task_type,
                status=status, conf=float(confidence), src=source,
                created=created)
        return status


def _validate_label(label: str) -> None:
    if not label or not label.replace("_", "").isalnum():
        raise ValueError(f"非法 label: {label!r}")


def _validate_rel(rel: str) -> None:
    if rel not in _REL_MAP:
        raise ValueError(f"非法 rel（白名单外）: {rel!r}")


def _validate_key(key: str, props: dict) -> None:
    if key not in props:
        raise ValueError(f"merge_entity 需 props 含 key 属性 {key!r}")


def _prel(props: dict | None) -> dict:
    """边属性（merge_rel 用：无需剔除 key——key 值由调用方显式传参）。"""
    return dict(props or {})


def _node_label_key(labels_: list[str]) -> tuple[str | None, str | None]:
    """从 Neo4j `labels()` 里认出 (业务 label, 唯一键属性名)；认不出 → (None, None)。

    返回 label **本身**（而不是取 labels()[0]）——labels 顺序不保证，直接取首个
    会拿到 'Entity' 这类无名键的标签，恢复时 MATCH 必不中。
    """
    for lbl in labels_ or ():
        if lbl in _NODE_KEYS:
            return lbl, _NODE_KEYS[lbl]
    return None, None


def _kind_of(node) -> str:
    for lbl in _ENTITY_LABELS:
        if lbl in node.labels:
            return lbl.lower()
    return next(iter(node.labels), "entity").lower()