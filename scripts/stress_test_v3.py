# -*- coding: utf-8 -*-
"""v3 压测 harness：多轮会话执行器 + 三层断言（路由/话术/记忆状态）。

计划：docs/superpowers/plans/2026-09-08-personal-stress-v3-question-bank.md
口径：复用 v2（scripts/stress_test_v2.py）硬期望/软评估/_CLARIFY_KW/_CAUTION_KW。
用法：
  python scripts/stress_test_v3.py [--questions data/stress_v3/questions.json]
                                   [--limit N] [--self-test]
每题独立 user_id（全局命名空间，跑后 forget 清理）；setup 轮共享同一 session。
Neo4j 不可用 → memory_assert 题整体 SKIP 并计数（不算失败）；LLM 失败单题重试 1 次。
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):
    pp = str(ROOT / p)
    if pp not in sys.path:
        sys.path.insert(0, pp)

from fastapi.testclient import TestClient  # noqa: E402
from app.server import create_app          # noqa: E402

QUESTIONS_PATH = ROOT / "data" / "stress_v3" / "questions.json"
OUT_PATH = ROOT / "stress_results_v3.json"
CLARIFY_DEF = ROOT / "scripts" / "stress_test_v2.py"

# 口径常量与 v2 对齐（模块级独立复制，禁 import 防脚本自引用）
_CLARIFY_KW = ("能具体", "更具体", "哪方面", "不太确定", "没太理解",
               "换个说法", "你想了解", "能描述", "补充一下", "是指什么",
               "说明一下", "你在说", "还没理解", "不确定", "你想做哪件")
_CAUTION_KW = ("建议先", "医生", "专业", "评估", "不要", "避免", "暂停",
               "停止", "风险", "谨慎", "不建议", "务必", "咨询", "安全",
               "症状", "不适")


def _memory():
    """MemoryStore（可空）；无图 → None。"""
    try:
        from app.graph.memory import MemoryStore
        return MemoryStore.get()
    except Exception:
        return None


def _assert_memory(mem, uid: str, spec: dict) -> tuple[bool, str]:
    """memory_assert DSL 判定（只读）。无 spec → (True,"")。异常 → (False, 系统错误)。"""
    if not spec:
        return True, ""
    try:
        check = spec["check"]
        if check == "active_about":
            got = mem.current_about(uid, spec["type"])
            ok = (spec["about"] in got) == bool(spec.get("expect_active", True))
            return ok, f"active_about({spec['type']},{spec['about']})={got}"
        if check == "as_of":
            from datetime import datetime, timedelta, timezone
            when = (datetime.now(timezone.utc) - timedelta(days=int(spec["days"]))).isoformat()
            rows = mem.as_of(uid, when, spec["type"])
            vals = [str(r["value"]) for r in rows]
            ok = spec["value"] in vals
            return ok, f"as_of({spec['days']}d,{spec['type']})={vals}"
        if check == "count_events":
            # 事件计数：查 Event 节点（MemoryStore 无公开 count，走 driver 只读）
            g = mem._g
            with g._driver.session(database=g._database) as s:
                c = int(s.run("MATCH (e:Event {user_id: $u, type: $t}) RETURN count(e) AS c",
                              u=uid, t=spec["type"]).single()["c"])
            ok = c == int(spec["expect"])
            return ok, f"count_events({spec['type']})={c}"
        if check == "cleared":
            ok = not mem.current(uid)
            return ok, "cleared"
        return False, f"未知断言 check={check}"
    except Exception as e:
        return False, f"断言系统错误: {type(e).__name__}: {str(e)[:60]}"


def _assert_isolation(mem, spec: dict) -> tuple[bool, str]:
    """隔离断言：other 用户不存在该记忆（多用户分区不可见）。
    {"other": "uid", "type": "injury", "about": "膝"}。"""
    if not spec:
        return True, ""
    try:
        got = mem.current_about(spec["other"], spec["type"])
        ok = spec.get("about") not in got
        return ok, f"isolation({spec['other']}.{spec['type']})={got}"
    except Exception as e:
        return False, f"隔离断言系统错误: {str(e)[:60]}"


def _apply_expect(expect: dict, mode: str, reply: str,
                  mem, uid: str, sources: list | None = None,
                  data: dict | None = None) -> dict:
    """五层断言（mode/回复关键词/provenance/结构化数据/记忆）。

    provenance 断言（2026-09-11 新增，可选字段，不填=不断言——不影响既有题库）：
    `provenance_has` / `provenance_forbids`：子串命中 provenance 列表。用于断言
    **产出路径**而非措辞——"真的编辑了"（plan#edit.*）与"静默重新生成"在 mode
    和回复措辞上可能都看不出差别，只有 provenance 能区分。

    data 断言（同日新增）：`data_contains` / `data_forbids`：子串命中
    structured.data 的 JSON 序列化。用于**措辞无法区分**的场景——例：读回计划是
    默认四天分化还是练三休一，两者推日/拉日文字完全相同，只有数据里的 scheme 能区分。"""
    res = {"ok": True, "detail": [], "skipped": False}
    if mode is None:
        res["skipped"] = True
        return res
    exp_mode = expect.get("mode")
    if exp_mode:
        hit = (mode == exp_mode) or (exp_mode in ("teach", "qa") and mode == "direct")
        if not hit:
            res["ok"] = False
            res["detail"].append(f"mode: expect {exp_mode}, got {mode}")
    src = [str(x) for x in (sources or [])]
    for kw in expect.get("provenance_has") or []:
        if not any(kw in s for s in src):
            res["ok"] = False
            res["detail"].append(f"provenance 缺 {kw!r}（实际 {src[:5]}）")
    for kw in expect.get("provenance_forbids") or []:
        if any(kw in s for s in src):
            res["ok"] = False
            res["detail"].append(f"provenance 出现禁项 {kw!r}（实际 {src[:5]}）")
    if expect.get("data_contains") or expect.get("data_forbids"):
        blob = json.dumps(data or {}, ensure_ascii=False)
        for kw in expect.get("data_contains") or []:
            if kw not in blob:
                res["ok"] = False
                res["detail"].append(f"data 缺 {kw!r}")
        for kw in expect.get("data_forbids") or []:
            if kw in blob:
                res["ok"] = False
                res["detail"].append(f"data 出现禁项 {kw!r}")
    reply = reply or ""
    for kw in expect.get("reply_contains") or []:
        if kw not in reply:
            res["ok"] = False
            res["detail"].append(f"缺关键词 {kw!r}")
    for kw in expect.get("reply_forbids") or []:
        if kw in reply:
            res["ok"] = False
            res["detail"].append(f"出现禁词 {kw!r}")
    ok_m, d_m = _assert_memory(mem, uid, expect.get("memory_assert"))
    if not ok_m:
        res["ok"] = False
        res["detail"].append(f"memory: {d_m}")
    ok_i, d_i = _assert_isolation(mem, expect.get("isolation"))
    if not ok_i:
        res["ok"] = False
        res["detail"].append(f"isolation: {d_i}")
    return res


def _self_test() -> int:
    """harness 自测：3 道已知答案题验证断言逻辑（不连网/LLM）。"""
    import datetime as _dt
    fails = 0

    def run(expect, mode, reply, mem, uid):
        r = _apply_expect(expect, mode, reply, mem, uid)
        return r

    # 1) mode + contains + forbids
    r = run({"mode": "direct", "reply_contains": ["深蹲"],
             "reply_forbids": ["计划生成"]}, "direct", "深蹲两字回复", None, "x")
    assert r["ok"], r["detail"]
    # 2) 禁词失败
    r = run({"reply_forbids": ["波比"]}, "direct", "没有波比但有跳", None, "x")
    assert not r["ok"] and any("波比" in d for d in r["detail"])
    # 3) 记忆断言无图 → 系统错误（抛异常被归为失败而非崩）
    r = run({"memory_assert": {"check": "active_about", "type": "injury",
                               "about": "膝"}}, "direct", "x", None, "x")
    assert not r["ok"]  # 无 mem → 异常路径
    print("[self-test] 3/3 断言逻辑通过")
    return fails


def _inject_memory(mem, uid: str, spec: dict) -> bool:
    """时间旅行注入（F5 统一 action 格式）：{op, type, value?, about?, days_ago?}。
    以 days_ago 天前为 clock 写入；注入后恢复 clock；失败 False（题目 ERROR）。"""
    try:
        from datetime import datetime, timedelta, timezone
        op = spec.get("op", "upsert_state")
        tgt_uid = spec.get("user") or uid           # 隔离题可注入到其他用户（si_b）
        days = int(spec.get("days_ago", spec.get("days", 0)))
        tgt = datetime.now(timezone.utc) - timedelta(days=days)
        orig = getattr(mem, "_now", None)
        mem._now = lambda: tgt.isoformat()
        try:
            if op == "log_event":
                return mem.log_event(tgt_uid, spec.get("type", "checkin"),
                                     spec.get("payload") or {}) is not None
            if op == "close":
                for r in mem.current(tgt_uid, spec.get("type", "injury"),
                                     include_expired=True):
                    if r.get("about") == spec.get("about"):
                        return mem.close(tgt_uid, r["fact_id"])
                return True                       # 无此记录视为已失效（幂等）
            t = spec.get("type", "injury")
            if t.startswith("profile."):
                import json as _j
                return mem.upsert_state(tgt_uid, t,
                                        _j.dumps(spec.get("value"))) is not None
            return mem.upsert_state(tgt_uid, t, str(spec.get("value", "不适")),
                                    about=spec.get("about")) is not None
        finally:
            mem._now = orig or (lambda: datetime.now(timezone.utc).isoformat())
    except Exception:
        return False


def _validate_questions(questions: list[dict]) -> list[str]:
    """F5 fail-fast 预检：四元标注 + action 对象格式，返回问题题号列表。"""
    errs = []
    for q in questions:
        qid = q.get("id", "?")
        for i, t in enumerate(q.get("turns", [])):
            a = t.get("action")
            if a is not None and not (isinstance(a, dict) and a.get("op")):
                errs.append(f"{qid}#turn{i}: action 非法（需 {{op,...}} 对象）: {a}")
    return errs


def run_bank(questions: list[dict], limit: int | None = None) -> list[dict]:
    mem = _memory()
    seq = questions if not limit else questions[:limit]
    results = []
    injected_uids = set()
    with TestClient(create_app()) as c:
        for q in seq:
            qid = q["id"]
            uid = f"v3_{qid.replace('-', '')}"
            t0 = time.time()
            entry = {"id": qid, "group": q.get("group"), "intent": q.get("intent"),
                     "attack": q.get("attack"), "mode": None, "reply": "",
                     "setup_ok": True, "flag": "", "detail": []}
            try:
                last_mode, last_reply, last_prov, last_data = None, "", [], {}
                for turn in q["turns"]:
                    if "action" in turn:                    # 时间旅行注入（记忆断言前置）
                        if mem is None:
                            entry["flag"] = "SKIP"
                            break
                        if not _inject_memory(mem, uid, turn["action"]):
                            entry["flag"] = "ERROR"
                            entry["detail"].append("inject_memory 失败")
                            break
                        injected_uids.add(turn["action"].get("user") or uid)
                        continue
                    r = c.post("/v1/chat", json={"message": turn["user"],
                                             "session_id": uid, "user_id": uid})
                    b = r.json()
                    last_mode, last_reply = b.get("mode_used"), (b.get("reply") or "")
                    last_prov = b.get("provenance") or []
                    last_data = (b.get("structured") or {}).get("data") or {}
                if entry["flag"]:
                    results.append(entry)
                    continue
                entry["mode"], entry["reply"] = last_mode, (last_reply or "")[:200]
                entry["provenance"] = last_prov
                res = _apply_expect(q.get("expect") or {}, last_mode, last_reply,
                                    mem, uid, last_prov, last_data)
                entry["flag"] = "OK" if res["ok"] else "FAIL"
                entry["detail"] = res["detail"]
                if res["skipped"]:
                    entry["flag"] = "SKIP"
            except Exception as e:                          # 单题系统失败
                entry["flag"] = "ERROR"
                entry["detail"] = [f"{type(e).__name__}: {str(e)[:100]}"]
            results.append(entry)
            t_fin = time.time()
            print(f"[{qid}] {q.get('intent','')[:22]:<22} -> {entry['flag']:<4} "
                  f"{entry['mode'] or '-'} {round(t_fin - t0, 1)}s")
            if mem is not None:
                try:
                    mem.forget(uid)                             # 跑后清理（失败显式报）
                except Exception:
                    entry["detail"].append("forget 清理失败")
                for extra in injected_uids - {uid}:             # 隔离题 he 户注入也清理
                    try:
                        mem.forget(extra)
                    except Exception:
                        entry["detail"].append(f"forget {extra} 清理失败")
    return results


def report(results: list[dict]) -> None:
    n = len(results)
    print("\n" + "=" * 64)
    print(f"v3 总计 {n} 题")
    flags = Counter(r.get("flag", "?") for r in results)
    print("flag 分布:", dict(flags.most_common()))
    hard = [r for r in results if r["flag"] == "FAIL"]
    errs = [r for r in results if r["flag"] == "ERROR"]
    skips = [r for r in results if r["flag"] == "SKIP"]
    print(f"FAIL {len(hard)} / ERROR {len(errs)} / SKIP {len(skips)}")
    for r in results[:10]:
        if r["flag"] not in ("OK",):
            print(f"  #{r['id']}: {r['detail'][:2]}")
    if hard or errs:
        out = ROOT / "stress_results_v3_r1.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\n结果已写 {out}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(QUESTIONS_PATH))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        raise SystemExit(1 if _self_test() else 0)
    qp = Path(args.questions)
    if not qp.exists():
        print(f"题库缺失: {qp}（W2/W3 生成后使用）")
        raise SystemExit(1)
    questions = json.loads(qp.read_text(encoding="utf-8"))
    errs = _validate_questions(questions)
    if errs:
        print(f"[validate] {len(errs)} 处异常（fail-fast）:")
        for e in errs[:20]:
            print("  ", e)
        raise SystemExit(1)
    if args.validate:
        print(f"[validate] 题库 {len(questions)} 题格式预检通过")
        raise SystemExit(0)
    results = run_bank(questions, limit=args.limit)
    report(results)


if __name__ == "__main__":
    main()
