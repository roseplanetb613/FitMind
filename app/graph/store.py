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

load_dotenv()

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "graph_config.json"

# 实体子图标签 + rel 名（与 ingest 迁移保持一致：rel 小写）
_ENTITY_LABELS = ("Exercise", "Muscle", "Equipment", "Pattern", "Condition", "Family")
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

    def clear_subgraph(self, source: str = "seed") -> None:
        """幂等重建：只清实体子图（seed 重建），绝不动 llm_* 在线沉淀的别名子图。"""
        with self._driver.session(database=self._database) as s:
            for lbl in _ENTITY_LABELS:
                s.run(f"MATCH (n:{lbl}) DETACH DELETE n")

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

    def family_alternatives(self, exercise_id: str) -> list[dict]:
        """同族变体（可替代）：member_of → 同族其它动作，返回 [{"name","kind"}]（name=动作 id）。"""
        with self._driver.session(database=self._database) as s:
            res = s.run(
                "MATCH (e:Exercise {id: $eid})-[:member_of]->(f:Family)"
                "<-[:member_of]-(alt:Exercise) WHERE alt.id <> $eid "
                "RETURN DISTINCT alt.id AS name",
                eid=exercise_id)
            return [{"name": r["name"], "kind": "exercise"} for r in res]

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


def _kind_of(node) -> str:
    for lbl in _ENTITY_LABELS:
        if lbl in node.labels:
            return lbl.lower()
    return next(iter(node.labels), "entity").lower()