# -*- coding: utf-8 -*-
"""用户记忆组件 · 双时序记忆图谱（spec v0.3 已评审 2026-09-07）。

Neo4j 单图 user_id 分区：
  (User{user_id}) -[:HAS_STATE]-> (StateFact{user_id,type,value,
      valid_from,valid_to,recorded_at,invalidated_at,expires_at})
                  -[:LOGGED]->   (Event{user_id,type,payload,
      occurred_at,occurred_end,recorded_at,invalidated_at})
  (Event checkin) -[:UNDER]-> (PlanVersion{user_id,plan_id,content_hash,created_at})
  (StateFact injury) -[:ABOUT]-> (Condition)    # 同库一跳 → 业务子图（尽力连，缺节点即跳过）
  (StateFact preference) -[:ABOUT]-> (Exercise)

双时序：状态=valid/transaction 双区间；事件=occurred/recorded 双时间（补录 occurred<recorded）。
降级：MemoryStore.get() 复用 GraphStore.get() 单例，Neo4j 不可用 → None；
调用方按现状回落（每轮自报 + SessionManager），约束不变（图数据必须 Neo4j）。
隔离：接口强制 user_id（无默认值）；audit() 断言无 user_id 节点=0。
"""
from __future__ import annotations
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

from app.graph.store import GraphStore

# v1 单用户常量（spec §6：v1 常量 "local"，接口签名仍强制传参）
MEMORY_USER_ID = "local"

# 类型默认有效期（spec §3：过期不删除、guard 不再自动消费、转确认流程）
DEFAULT_EXPIRY_DAYS = {"injury": 30, "preference": 180}

# ABOUT 目标 label 白名单（injury→业务 Condition；preference→Exercise）
_ABOUT_LABEL = {"injury": "Condition", "preference": "Exercise"}
_TYPE_PREFIX = ("profile.", "injury", "preference")

_SCHEMA_STMTS = (
    "CREATE CONSTRAINT memory_fact_id IF NOT EXISTS "
    "FOR (f:StateFact) REQUIRE f.fact_id IS UNIQUE",
    "CREATE CONSTRAINT memory_event_id IF NOT EXISTS "
    "FOR (e:Event) REQUIRE e.event_id IS UNIQUE",
    "CREATE CONSTRAINT memory_plan_id IF NOT EXISTS "
    "FOR (pv:PlanVersion) REQUIRE pv.plan_id IS UNIQUE",
    "CREATE INDEX memory_fact_lookup IF NOT EXISTS "
    "FOR (f:StateFact) ON (f.user_id, f.type)",
    "CREATE INDEX memory_event_lookup IF NOT EXISTS "
    "FOR (e:Event) ON (e.user_id, e.type)",
    "CREATE INDEX memory_plan_lookup IF NOT EXISTS "
    "FOR (pv:PlanVersion) ON (pv.user_id)",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _add_days(iso: str, days: int) -> str:
    dt = datetime.fromisoformat(iso)
    return (dt + timedelta(days=days)).isoformat()


def _days_between(a: str, b: str) -> int:
    da = datetime.fromisoformat(a)
    db = datetime.fromisoformat(b)
    return max(0, int((db - da).total_seconds() // 86400))


def _fid() -> str:
    return uuid.uuid4().hex[:16]


def _validate_user_id(uid: str) -> None:
    if not uid or not isinstance(uid, str):
        raise ValueError("user_id 必须为非空字符串")


def _validate_type(t: str) -> None:
    if not (t.startswith("profile.") or t in ("injury", "preference")):
        raise ValueError(f"非法记忆 type: {t!r}")


def _pl(data: dict) -> dict:
    return {k: v for k, v in data.items() if v is not None}


class MemoryStore:
    """双时序记忆图谱封装。复用 GraphStore 单例 driver；上层全部 try/except 静默降级。
    所有查询强制 user_id；无裸 Cypher 进业务层。"""

    _instance: "MemoryStore | None" = None

    def __init__(self, store: GraphStore, clock=None):
        self._g = store
        # clock 可注入（验收"过期 31 天后"mock 时钟用）
        self._now = clock or _now_iso

    @classmethod
    def get(cls) -> "MemoryStore | None":
        """单例：复用 GraphStore.get()——Neo4j 不可用/无密码 → None（调用方回落）。"""
        if cls._instance is not None:
            return cls._instance
        g = GraphStore.get()
        if g is None:
            return None
        try:
            with g._driver.session(database=g._database) as s:
                for st in _SCHEMA_STMTS:
                    s.run(st)
        except Exception:
            return None
        cls._instance = cls(g)
        return cls._instance

    # ------------------------------------------------------- 写入：状态
    def upsert_state(self, user_id: str, type_: str, value: str,
                     about: str | None = None,
                     expires_days: int | None = None) -> str | None:
        """同 type(+about) active 唯一：单事务内封口旧值 + 创建新值（并发安全）。
        expires_days=None → 按类型默认（injury 30 / preference 180 / profile 永久）。
        返回 fact_id；任何失败返回 None（调用方按现状回落）。"""
        _validate_user_id(user_id)
        _validate_type(type_)
        if expires_days is None:
            expires_days = DEFAULT_EXPIRY_DAYS.get(type_)
        now = self._now()
        fid = _fid()
        expires = (_add_days(now, expires_days) if expires_days
                   else None)
        lab = _ABOUT_LABEL.get(type_)
        try:
            def _tx(tx):
                # 1) 封口同槽旧 active（valid_to 仍 NULL 且未被失效）
                tx.run(
                    "MATCH (u:User {user_id: $uid})-[:HAS_STATE]->"
                    "(f:StateFact {type: $t}) "
                    "WHERE f.invalidated_at IS NULL AND f.valid_to IS NULL "
                    "AND ($ab IS NULL OR f.about = $ab) "
                    "SET f.valid_to = $now",
                    uid=user_id, t=type_, ab=about, now=now)
                # 2) 创建新事实（MERGE User 保证归属节点存在）
                tx.run(
                    "MERGE (u:User {user_id: $uid}) "
                    "CREATE (u)-[:HAS_STATE]->(f:StateFact $p)",
                    uid=user_id,
                    p=_pl({"fact_id": fid, "user_id": user_id, "type": type_,
                           "value": value, "about": about,
                           "valid_from": now, "valid_to": None,
                           "recorded_at": now, "invalidated_at": None,
                           "expires_at": expires}))
                # 3) ABOUT 同库一跳（目标节点存在才连；缺失静默跳过）。
                #    食物: 前缀 → Food 个人域 MERGE（T3）；其余按白名单 label MATCH
                if about and about.startswith("食物:"):
                    ab_food = about.split(":", 1)[1]
                    if ab_food:
                        tx.run(
                            "MATCH (f:StateFact {fact_id: $fid}) "
                            "MERGE (t:Food {name: $ab}) "
                            "MERGE (f)-[:ABOUT]->(t)", fid=fid, ab=ab_food)
                elif lab:          # lab 来自白名单 _ABOUT_LABEL，非用户输入
                    tx.run(
                        f"MATCH (f:StateFact {{fact_id: $fid}}), "
                        f"(t:{lab} {{name: $ab}}) MERGE (f)-[:ABOUT]->(t)",
                        fid=fid, ab=about or "")
            with self._g._driver.session(database=self._g._database) as s:
                s.execute_write(_tx)
            return fid
        except Exception:
            return None

    def upsert_profile(self, user_id: str, profile: dict[str, object]) -> int:
        """档案全量投影写入：每个字段一个 StateFact(type=profile.<key>)。
        value 以 JSON 序列化保存（投影时类型还原，防"28"变字符串破坏数值计算）。
        返回成功写入字段数（失败字段静默跳过，降级不影响主链路）。"""
        n = 0
        for k, v in (profile or {}).items():
            if v is None:
                continue
            if self.upsert_state(user_id, f"profile.{k}",
                                 json.dumps(v, ensure_ascii=False)) is not None:
                n += 1
        return n

    def invalidate(self, user_id: str, fact_id: str) -> bool:
        """纠错：打 invalidated_at（历史可查）。仅作用于该 user 的事实。"""
        _validate_user_id(user_id)
        try:
            with self._g._driver.session(database=self._g._database) as s:
                s.run("MATCH (f:StateFact {fact_id: $fid, user_id: $uid}) "
                      "SET f.invalidated_at = $now",
                      fid=fact_id, uid=user_id, now=self._now())
            return True
        except Exception:
            return False

    def close(self, user_id: str, fact_id: str) -> bool:
        """伤病失效（"我好了"）：valid_to=now，不再 active。"""
        _validate_user_id(user_id)
        try:
            with self._g._driver.session(database=self._g._database) as s:
                s.run("MATCH (f:StateFact {fact_id: $fid, user_id: $uid}) "
                      "WHERE f.valid_to IS NULL SET f.valid_to = $now",
                      fid=fact_id, uid=user_id, now=self._now())
            return True
        except Exception:
            return False

    def renew(self, user_id: str, fact_id: str, days: int = 30) -> bool:
        """伤病续期（"还疼"）：expires_at 顺延，保留 active。"""
        _validate_user_id(user_id)
        try:
            now = self._now()
            with self._g._driver.session(database=self._g._database) as s:
                s.run("MATCH (f:StateFact {fact_id: $fid, user_id: $uid}) "
                      "SET f.expires_at = $exp, f.valid_to = null",
                      fid=fact_id, uid=user_id, exp=_add_days(now, days))
            return True
        except Exception:
            return False

    # ------------------------------------------------------- 读取：状态
    def _rows(self, query: str, **params) -> list[dict]:
        with self._g._driver.session(database=self._g._database) as s:
            return [dict(r) for r in s.run(query, **params)]

    def current(self, user_id: str, type_: str | None = None,
                include_expired: bool = False) -> list[dict]:
        """active 状态（valid_to NULL 且未失效）；include_expired 时仍返回
        已过期事实（expires_at<now，由 expire_prompts 消费转确认）。"""
        _validate_user_id(user_id)
        where_t = "AND f.type = $t" if type_ else ""
        exp_f = "" if include_expired else "AND (f.expires_at IS NULL OR f.expires_at >= $now)"
        try:
            return self._rows(
                "MATCH (u:User {user_id: $uid})-[:HAS_STATE]->(f:StateFact) "
                "WHERE f.invalidated_at IS NULL AND f.valid_to IS NULL "
                f"{where_t} {exp_f} "
                "RETURN f.fact_id AS fact_id, f.type AS type, f.value AS value, "
                "f.about AS about, f.valid_from AS valid_from, "
                "f.valid_to AS valid_to, f.recorded_at AS recorded_at, "
                "f.invalidated_at AS invalidated_at, f.expires_at AS expires_at "
                "ORDER BY f.recorded_at",
                uid=user_id, t=type_, now=self._now())
        except Exception:
            return []

    def current_about(self, user_id: str, type_: str) -> list[str]:
        """active（未过期）事实的 about 去重序列（guard 主动禁忌交叉消费）。"""
        return sorted({str(r["about"]) for r in self.current(user_id, type_)
                       if r.get("about")})

    def current_profile(self, user_id: str) -> dict[str, object]:
        """profile.* active → {key: value}（SessionManager 单向投影）。
        type 存 profile.<key> 前缀，必须按前缀匹配而非全等；
        value 为 JSON 序列化，读取时类型还原（数值保持 int/float）。"""
        out: dict[str, object] = {}
        for r in self.current(user_id):
            t = str(r["type"])
            if t.startswith("profile."):
                try:
                    out[t[len("profile."):]] = json.loads(r["value"])
                except Exception:
                    out[t[len("profile."):]] = r["value"]     # 兜底原串
        return out

    def as_of(self, user_id: str, when: str,
              type_: str | None = None) -> list[dict]:
        """as-of 点查：指定时刻可见的事实（valid 区间含 when 且当时未被失效）。"""
        _validate_user_id(user_id)
        where_t = "AND f.type = $t" if type_ else ""
        try:
            return self._rows(
                "MATCH (u:User {user_id: $uid})-[:HAS_STATE]->(f:StateFact) "
                "WHERE f.valid_from <= $w "
                "AND (f.valid_to IS NULL OR f.valid_to > $w) "
                "AND f.recorded_at <= $w "
                "AND (f.invalidated_at IS NULL OR f.invalidated_at >= $w) "
                f"{where_t} "
                "RETURN f.fact_id AS fact_id, f.type AS type, f.value AS value, "
                "f.about AS about, f.valid_from AS valid_from, "
                "f.valid_to AS valid_to, f.recorded_at AS recorded_at ",
                uid=user_id, t=type_, w=when)
        except Exception:
            return []

    def range(self, user_id: str, type_: str, from_: str, to: str) -> list[dict]:
        """range 演化：有效区间与 [from_, to] 相交的记录（按 recorded_at 排序）。"""
        _validate_user_id(user_id)
        try:
            return self._rows(
                "MATCH (u:User {user_id: $uid})-[:HAS_STATE]->(f:StateFact {type: $t}) "
                "WHERE COALESCE(f.valid_to, $to) >= $from "
                "AND f.valid_from <= $to "
                "RETURN f.type AS type, f.value AS value, f.about AS about, "
                "f.valid_from AS valid_from, f.valid_to AS valid_to, "
                "f.recorded_at AS recorded_at ORDER BY f.recorded_at",
                uid=user_id, t=type_, **{"from": from_, "to": to})
        except Exception:
            return []

    def expire_prompts(self, user_id: str) -> list[dict]:
        """过期转确认：active 但 expires_at<now 的 injury（guard 确认话术消费）。"""
        _validate_user_id(user_id)
        try:
            rows = self._rows(
                "MATCH (u:User {user_id: $uid})-[:HAS_STATE]->(f:StateFact {type: 'injury'}) "
                "WHERE f.invalidated_at IS NULL AND f.valid_to IS NULL "
                "AND f.expires_at IS NOT NULL AND f.expires_at < $now "
                "RETURN f.fact_id AS fact_id, f.about AS about, "
                "f.valid_from AS valid_from, f.expires_at AS expires_at "
                "ORDER BY f.expires_at", uid=user_id, now=self._now())
            out = []
            for r in rows:
                r2 = dict(r)
                r2["days_since"] = _days_between(r["valid_from"], self._now())
                out.append(r2)
            return out
        except Exception:
            return []

    # ------------------------------------------------------- 事件与计划
    def register_plan(self, user_id: str, plan_id: str,
                      content_hash: str,
                      content: dict | None = None) -> bool:
        _validate_user_id(user_id)
        try:
            with self._g._driver.session(database=self._g._database) as s:
                s.run(
                    "MERGE (pv:PlanVersion {plan_id: $pid, user_id: $uid}) "
                    "ON CREATE SET pv.content_hash = $ch, pv.created_at = $now, "
                    "pv.content = $content",
                    pid=plan_id, uid=user_id, ch=content_hash, now=self._now(),
                    content=(json.dumps(content, ensure_ascii=False)
                             if content is not None else None))
            return True
        except Exception:
            return False

    def latest_plan(self, user_id: str) -> dict | None:
        """最新计划版本（只读）：content JSON 还原；无 → None。"""
        _validate_user_id(user_id)
        try:
            with self._g._driver.session(database=self._g._database) as s:
                r = s.run(
                    "MATCH (pv:PlanVersion {user_id: $uid}) "
                    "RETURN pv.plan_id AS pid, pv.content AS content, "
                    "pv.created_at AS created "
                    "ORDER BY pv.created_at DESC LIMIT 1",
                    uid=user_id).single()
                if r is None:
                    return None
                content = r["content"]
                try:
                    content = json.loads(content) if content else None
                except Exception:
                    pass
                return {"plan_id": r["pid"], "content": content,
                        "created_at": r["created"]}
        except Exception:
            return None

    def log_event(self, user_id: str, type_: str, payload: dict,
                  occurred_at: str | None = None,
                  plan_id: str | None = None,
                  muscles: list[str] | None = None,
                  foods: list[str] | None = None) -> str | None:
        """事件（checkin/pr/…）：occurred_at 缺省=now；补录时 occurred<recorded 保留。
        muscles/foods：枢纽挂边（Event-TARGETS→Muscle / Event-ATE→Food，MATCH
        不中静默跳过；Food 为个人域 MERGE）。"""
        _validate_user_id(user_id)
        now = self._now()
        occ = occurred_at or now
        eid = _fid()
        try:
            with self._g._driver.session(database=self._g._database) as s:
                s.run(
                    "MERGE (u:User {user_id: $uid}) "
                    "CREATE (u)-[:LOGGED]->(e:Event $p)",
                    uid=user_id,
                    p=_pl({"event_id": eid, "user_id": user_id, "type": type_,
                           "payload": json.dumps(payload, ensure_ascii=False),
                           "occurred_at": occ, "occurred_end": None,
                           "recorded_at": now, "invalidated_at": None}))
                for mn in (muscles or []):       # 挂 Muscle（MATCH 不中静默跳过）
                    s.run("MATCH (e:Event {event_id: $eid}), "
                          "(mm:Muscle {name: $mn}) "
                          "MERGE (e)-[:TARGETS]->(mm)", eid=eid, mn=mn)
                for fn in (foods or []):         # Food 个人域 MERGE
                    s.run("MATCH (e:Event {event_id: $eid}) "
                          "MERGE (f:Food {name: $fn}) "
                          "MERGE (e)-[:ATE]->(f)", eid=eid, fn=fn)
                if plan_id:
                    s.run(
                        "MATCH (e:Event {event_id: $eid}), "
                        "(pv:PlanVersion {user_id: $uid, plan_id: $pid}) "
                        "MERGE (e)-[:UNDER]->(pv)",
                        eid=eid, uid=user_id, pid=plan_id)
            return eid
        except Exception:
            return None

    def muscles_of_exercises(self, names: list[str]) -> list[str]:
        """动作名 → 图谱 Exercise-[:targets]->Muscle 肌肉名集（只读；异常→[]）。
        Exercise 属性为 name_zh/name_en 且多为复合名（'杠铃 深蹲'）→ CONTAINS 匹配。"""
        out: set[str] = set()
        try:
            with self._g._driver.session(database=self._g._database) as s:
                for n in names:
                    for r in s.run(
                            "MATCH (e:Exercise)-[:targets]->(mm:Muscle) "
                            "WHERE e.name_zh CONTAINS $n OR e.name_en CONTAINS $n "
                            "RETURN DISTINCT mm.name AS name", n=n):
                        out.add(str(r["name"]))
        except Exception:
            pass
        return sorted(out)

    def link_injury_muscle(self, user_id: str, site: str) -> bool:
        """active injury StateFact(about=部位) → ABOUT_MUSCLE 边；不中/异常 → False。"""
        _validate_user_id(user_id)
        muscle = _PART2MUSCLE.get(site)
        if not muscle:
            return False
        try:
            with self._g._driver.session(database=self._g._database) as s:
                r = s.run(
                    "MATCH (u:User {user_id: $uid})-[:HAS_STATE]->"
                    "(f:StateFact {type: 'injury'}), (mm:Muscle {name: $m}) "
                    "WHERE f.invalidated_at IS NULL AND f.valid_to IS NULL "
                    "AND (f.about = $site OR f.value = $site) "
                    "MERGE (f)-[:ABOUT_MUSCLE]->(mm) "
                    "RETURN count(f) AS c",
                    uid=user_id, m=muscle, site=site).single()
                return bool(r and r["c"] >= 1)
        except Exception:
            return False

    def muscle_summary(self, user_id: str, muscle: str,
                       part: str | None = None, days: int = 30) -> dict:
        """肌肉聚合面板（只读）：近N天次数/最近日期/active injury(ABOUT_MUSCLE)/部位偏好。"""
        _validate_user_id(user_id)
        since = _add_days(self._now(), -days)
        out = {"muscle": muscle, "trained_count": 0, "last_trained": None,
               "active_injury": [], "preference": None}
        try:
            with self._g._driver.session(database=self._g._database) as s:
                r = s.run(
                    "MATCH (u:User {user_id: $uid})-[:LOGGED]->(e:Event)"
                    "-[:TARGETS]->(mm:Muscle {name: $m}) "
                    "WHERE e.invalidated_at IS NULL AND e.occurred_at >= $since "
                    "RETURN count(e) AS c, max(e.occurred_at) AS last",
                    uid=user_id, m=muscle, since=since).single()
                if r:
                    out["trained_count"] = r["c"]
                    out["last_trained"] = r["last"]
                rows = s.run(
                    "MATCH (u:User {user_id: $uid})-[:HAS_STATE]->"
                    "(f:StateFact {type: 'injury'})-[:ABOUT_MUSCLE]->"
                    "(mm:Muscle {name: $m}) "
                    "WHERE f.invalidated_at IS NULL AND f.valid_to IS NULL "
                    "AND (f.expires_at IS NULL OR f.expires_at >= $now) "
                    "RETURN f.about AS site",
                    uid=user_id, m=muscle, now=self._now())
                out["active_injury"] = [str(x["site"]) for x in rows]
        except Exception:
            pass
        if part:
            try:
                for r in self.current(user_id, "preference"):
                    if str(r.get("about") or "") == f"部位:{part}":
                        out["preference"] = r["value"]
            except Exception:
                pass
        return out

    def log_checkin(self, user_id: str, plan_id: str,
                    occurred_at: str | None = None) -> str | None:
        """打卡 → Event(checkin) + UNDER(PlanVersion)。采纳闭环（口头声明不产生）。"""
        return self.log_event(user_id, "checkin", {"plan_id": plan_id},
                              occurred_at=occurred_at, plan_id=plan_id)

    def log_pr(self, user_id: str, payload: dict,
               occurred_at: str | None = None) -> str | None:
        return self.log_event(user_id, "pr", payload, occurred_at=occurred_at)

    def events(self, user_id: str, type_: str | None = None,
               days: int = 90) -> list[dict]:
        """事件时间线（只读）：occurred_at 倒序，days 窗口；payload JSON 还原。
        任何异常 → []（调用方按空态回落，主链路不受影响）。"""
        _validate_user_id(user_id)
        since = _add_days(self._now(), -days)
        try:
            with self._g._driver.session(database=self._g._database) as s:
                rows = s.run(
                    "MATCH (u:User {user_id: $uid})-[:LOGGED]->(e:Event) "
                    "WHERE ($t IS NULL OR e.type = $t) "
                    "AND e.invalidated_at IS NULL AND e.occurred_at >= $since "
                    "RETURN e.type AS type, e.payload AS payload, "
                    "e.occurred_at AS occurred_at "
                    "ORDER BY e.occurred_at DESC",
                    uid=user_id, t=type_, since=since)
                out = []
                for r in rows:
                    try:
                        payload = json.loads(r["payload"] or "{}")
                    except Exception:
                        payload = {"raw": r["payload"]}
                    out.append({"type": r["type"], "payload": payload,
                                "occurred_at": r["occurred_at"]})
                return out
        except Exception:
            return []

    # ------------------------------------------------------- 隔离与审计
    def forget(self, user_id: str) -> int:
        """每用户物理删除权（spec §6：优先于 append-only 审计链）。返回删除节点数。"""
        _validate_user_id(user_id)
        try:
            with self._g._driver.session(database=self._g._database) as s:
                res = s.run("MATCH (n {user_id: $uid}) DETACH DELETE n "
                            "RETURN count(n) AS c", uid=user_id)
                return int(res.single()["c"])
        except Exception:
            return 0

    def audit(self) -> dict:
        """审计：无 user_id 节点必须=0（隔离三层兜底之一）；记忆子图规模。"""
        try:
            with self._g._driver.session(database=self._g._database) as s:
                no_uid = int(s.run(
                    "MATCH (n) WHERE (n:StateFact OR n:Event OR n:PlanVersion) "
                    "AND n.user_id IS NULL RETURN count(n) AS c"
                ).single()["c"])
                counts = {
                    "state_facts": int(s.run("MATCH (n:StateFact) RETURN count(n) AS c"
                                             ).single()["c"]),
                    "events": int(s.run("MATCH (n:Event) RETURN count(n) AS c").single()["c"]),
                    "plan_versions": int(s.run("MATCH (n:PlanVersion) RETURN count(n) AS c"
                                               ).single()["c"]),
                }
                return {"no_user_id": no_uid, **counts}
        except Exception:
            return {"no_user_id": 0, "state_facts": 0, "events": 0,
                    "plan_versions": 0}


# 便捷校验（业务层用）
def contains_injury_symptom(text: str) -> bool:
    """是否有伤病症状表述（规则抽取层用）。"""
    return any(k in text for k in
               ("疼", "痛", "扭", "伤", "拉伤", "发紧", "不舒服", "酸", "麻",
                "肿", "断", "脱臼", "骨折"))


_INJURY_SITE = {
    "膝": "knee", "半月板": "knee", "十字韧带": "knee", "交叉韧带": "knee", "腰": "lower_back", "肩": "shoulder", "手腕": "wrist",
    "手肘": "elbow", "肘": "elbow", "脚踝": "ankle", "脚": "ankle",
    "脖子": "neck", "颈椎": "neck", "背": "back", "髋": "hip", "大腿": "thigh",
    "腿": "leg", "跟腱": "achilles",
}

_INJURY_SITE_ZH = {
    "膝": "膝", "半月板": "膝", "十字韧带": "膝", "交叉韧带": "膝", "腰": "腰", "肩": "肩", "手腕": "手腕", "肘": "手肘",
    "脚踝": "脚踝", "脚": "脚踝", "脖子": "颈", "颈椎": "颈", "背": "背",
    "髋": "髋", "大腿": "大腿", "腿": "腿", "跟腱": "跟腱",
}

# 部位→Muscle 名（宁缺毋滥：以图谱实际 Muscle.name 校准——英文 snake_case，
# 不中不挂；膝/肘/手腕/颈/髋/跟腱无精确肌肉节点故不入表）
_PART2MUSCLE = {"背": "latissimus_dorsi", "胸": "pectoralis", "肩": "deltoids",
                "腿": "quadriceps", "大腿": "quadriceps", "臂": "biceps",
                "腹": "rectus_abdominis", "臀": "glutes", "核心": "core",
                "腰": "lower_back", "脚踝": "ankle_stabilizers"}


def extract_injury_site(text: str) -> tuple[str, str] | None:
    """从症状句中提取部位（原词优先，返回 (canonical_zh, key)）。"""
    for key in ("脚踝", "手腕", "手肘", "脖子", "颈椎", "大腿", "跟腱",
                "膝", "半月板", "十字韧带", "交叉韧带", "腰", "肩", "肘", "脚", "背", "髋", "腿"):
        if key in text:
            return _INJURY_SITE_ZH[key], _INJURY_SITE[key]
    return None
