# -*- coding: utf-8 -*-
"""检索层问题表评测：data/rag_eval/exercise_retrieval.jsonl → 逐条判 PASS/FAIL。

回答的问题：**词法动作检索（lib/exercise_repo.py::search_zh）在真实用户措辞下，
给出来的东西对不对**——不是"返回了没有"。

与 scripts/eval_rag.py 的分工（两个通道、两张表、别混）：
    eval_rag.py              向量通道（PG+pgvector），表 = data/rag_eval/queries.jsonl
    eval_retrieval.py        词法通道（纯 Python），表 = data/rag_eval/exercise_retrieval.jsonl
本表**不重复** queries.jsonl：那张表的 exercise_cue 行是 auto_name（query≈动作名原词），
量的是"原词命中"；本表量的是**改写/口语/部位/场景**措辞，正是 retriever.py 注释里
点名缺的那一类。

判据（每族一个谓词，全部**独立于 search_zh 的实现**——从 data/ 与 lib/parts.py 推，
否则就是拿系统测自己）：
    region_expand        top_k 里每条必须"属于"期望肌群集——口径同
                         `_part_target_rank`（target 或 muscle_group，即 rank ≤1；
                         仅次要肌群 rank=2 不算），且不同肌群数 ≥ min_distinct（覆盖）
    region_expand_guard  同上；专钉"刻意不展开"的词（腰）
    alias                至少一条 name_zh 含 NAME_ALIASES 的目标片段
    scene_equip          top_k 里每条 normalized_equipment 必须等于期望器械
    verbatim             top1 id 必须等于标注 id（对照组，防"为了新功能弄坏老功能"）
    no_fabrication       必须空（无关查询 / 判定类检索的 part_fallback=False 边界）
    gap_negative         必须空（数据集确实没有该动作）

用法：
    python scripts/eval_retrieval.py            # 全部 + 通道状态
    python scripts/eval_retrieval.py -v         # 逐条打印返回了什么
    python scripts/eval_retrieval.py --family alias
退出码：有 FAIL → 1（可作门禁）。
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

TABLE = ROOT / "data" / "rag_eval" / "exercise_retrieval.jsonl"


def load_table(path: Path = TABLE) -> list[dict]:
    rows = []
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path.name}:{ln} JSON 解析失败: {e}") from e
    return rows


def validate_table(rows: list[dict], valid_muscles: set[str]) -> list[str]:
    """问题表自身的体检：id 唯一、必填字段在、肌群 id 真实、query 非空。"""
    errs: list[str] = []
    seen: set[str] = set()
    for r in rows:
        rid = r.get("id")
        if not rid:
            errs.append(f"缺 id: {r.get('query')!r}")
            continue
        if rid in seen:
            errs.append(f"id 重复: {rid}")
        seen.add(rid)
        if not (r.get("query") or "").strip():
            errs.append(f"{rid}: query 为空")
        if not r.get("expect"):
            errs.append(f"{rid}: 缺 expect")
        want = (r.get("expect") or {}).get("primary_target_in") or []
        for m in want:
            if m not in valid_muscles:
                errs.append(f"{rid}: 期望肌群 {m!r} 不在本体里（写错字会假通过）")
    return errs


def _target_of(rec: dict) -> str | None:
    return (rec.get("muscles_canonical") or {}).get("target")


def _relevant_muscles(rec: dict, want: set[str]) -> tuple[int, str] | None:
    """(rank, 命中的期望肌群)；None = 与期望集无关。

    rank 口径**必须**与 lib/exercise_repo.py::_part_target_rank 一致
    （0=主目标 / 1=肌群归属 / 2=仅次要），否则会误判一整类词：
    全库有 **9 个肌群一条主目标动作都没有**（lower_back / core / obliques /
    rhomboids / rotator_cuff / hip_flexors / tibialis_anterior /
    ankle_stabilizers / sternocleidomastoid）。只看 `target` 的话，
    「腰怎么练」在任何实现下都不可能通过——那不是在测系统，是在测我的表。
    实测 lower_back: target=0 / muscle_group=6 / secondary_only=64。"""
    c = rec.get("muscles_canonical") or {}
    if c.get("target") in want:
        return 0, c["target"]
    if c.get("muscle_group") in want:
        return 1, c["muscle_group"]
    return None


def judge(row: dict, hits: list[dict]) -> tuple[bool, str]:
    """返回 (passed, 人话原因)。"""
    exp = row.get("expect") or {}
    fam = row.get("family")

    if "expect_empty" in exp:
        if not hits:
            return True, "空（符合预期）"
        got = ", ".join(f"{h.get('id')} {h.get('name_zh')}" for h in hits[:3])
        return False, f"不该有结果，却返回 {len(hits)} 条：{got}"

    if "top1_id" in exp:
        if not hits:
            return False, f"空结果（期望 top1={exp['top1_id']}）"
        got = hits[0].get("id")
        return (got == exp["top1_id"]), f"top1={got}（期望 {exp['top1_id']}）"

    if "name_contains_any" in exp:
        want = exp["name_contains_any"]
        if not hits:
            return False, f"空结果（期望名字含 {want}）"
        for h in hits:
            n = h.get("name_zh") or ""
            if any(w in n for w in want):
                return True, f"{h.get('id')} {n}"
        names = ", ".join(f"{h.get('id')} {h.get('name_zh')}" for h in hits[:3])
        return False, f"top{len(hits)} 无一条名字含 {want}：{names}"

    if "all_equipment" in exp:
        want = exp["all_equipment"]
        if not hits:
            return True, f"空（无结果，约束无从违反）"
        bad = [(h.get("id"), h.get("name_zh"), h.get("normalized_equipment"))
               for h in hits if h.get("normalized_equipment") != want]
        if not bad:
            return True, f"{len(hits)} 条全是 {want}"
        b = "; ".join(f"{i} {n}({e})" for i, n, e in bad[:3])
        return False, f"期望全 {want}，混入 {len(bad)} 条：{b}"

    if "primary_target_in" in exp:
        want = set(exp["primary_target_in"])
        if not hits:
            return False, f"空结果（期望主目标 ∈ {sorted(want)}）"
        bad = [(h.get("id"), h.get("name_zh"), _target_of(h))
               for h in hits if _relevant_muscles(h, want) is None]
        distinct = {m for h in hits
                    if (r := _relevant_muscles(h, want)) is not None
                    for m in [r[1]]}
        need = int(exp.get("min_distinct", 1))
        if bad:
            b = "; ".join(f"{i} {n}(target={t})" for i, n, t in bad[:3])
            return False, f"主目标跑到期望集外 {len(bad)} 条：{b}"
        if len(distinct) < need:
            return False, (f"只覆盖 {len(distinct)} 个肌群（要 ≥{need}）："
                           f"{sorted(distinct)}")
        return True, f"命中肌群 {sorted(distinct)}（{len(hits)} 条）"

    return False, f"未知判据族: {fam} / expect={exp}"


# ---------------- 通道状态（诚实探测，别只构造对象） ----------------

def channel_status() -> list[tuple[str, str, str]]:
    """(通道, 状态, 说明)。**PG 必须真连一次**——
    2026-09-20 前 tmp/probe_vector_channel.py 只 `PgStore()`（构造函数不连接）
    就报了「PG 可达: True」，那个检查是空的。"""
    out: list[tuple[str, str, str]] = []

    # 词法：纯 Python，无外部依赖
    try:
        from exercise_repo import ExerciseRepo
        n = len(ExerciseRepo().exercises)
        out.append(("lexical", "OK", f"lib/exercise_repo.py，{n} 条动作，无外部依赖"))
    except Exception as e:  # pragma: no cover
        out.append(("lexical", "FAIL", f"{type(e).__name__}: {e}"))

    # 向量：真连 PG
    try:
        from app.rag.store import PgStore
        st = PgStore()
        with st.conn() as c, c.cursor() as cur:
            cur.execute("SELECT chunk_type, count(*) FROM fitness.embeddings GROUP BY 1")
            rows = cur.fetchall()
        out.append(("vector", "OK", f"PG {len(rows)} 类 chunk: {dict(rows)}"))
    except Exception as e:
        msg = str(e).splitlines()[0][:110]
        env = os.environ.get("DATABASE_URL")
        out.append(("vector", "BLOCKED",
                    f"{type(e).__name__}: {msg}"
                    f" | DATABASE_URL={'已设' if env else '未设'}，默认 DSN 无密码"))

    # 图：Neo4j 需要 uri/database/user/password
    try:
        from app.graph import store as gs
        cfg = None
        for attr in ("GraphConfig", "load_config", "DEFAULT_CONFIG"):
            cfg = getattr(gs, attr, None)
            if cfg is not None:
                break
        out.append(("graph", "MANUAL",
                    "app.graph.store.GraphStore 需 uri/database/user/password 四参；"
                    "本脚本不猜配置，请用既有 graph 测试跑"))
    except Exception as e:  # pragma: no cover
        out.append(("graph", "BLOCKED", f"{type(e).__name__}: {e}"))

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=str(TABLE))
    ap.add_argument("--family", default=None, help="只跑某一族")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每条返回了什么")
    args = ap.parse_args()

    from exercise_repo import ExerciseRepo
    repo = ExerciseRepo()

    rows = load_table(Path(args.table))
    if args.family:
        rows = [r for r in rows if r.get("family") == args.family]
    if not rows:
        print("没有可跑的用例")
        return 2

    errs = validate_table(rows, set(repo.muscle_meta.keys()))
    if errs:
        print("[问题表体检不通过]")
        for e in errs:
            print("   -", e)
        return 2

    print(f"问题表 {Path(args.table).name}：{len(rows)} 条")
    print("=" * 78)
    by_fam: dict[str, list[bool]] = defaultdict(list)
    fails: list[tuple[str, str, str]] = []

    for r in rows:
        q = r["query"]
        pf = bool(r.get("part_fallback", True))
        k = int(r.get("top_k", 5))
        try:
            hits = repo.search_zh(q, limit=k, part_fallback=pf)
        except Exception as e:
            hits, crash = [], f"{type(e).__name__}: {e}"
            ok, why = False, f"抛异常 {crash}"
        else:
            ok, why = judge(r, hits)
        by_fam[r["family"]].append(ok)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {r['id']:>4} {r['family']:<20} {q:<18} pf={int(pf)}  {why}")
        if args.verbose and hits:
            for h in hits:
                print(f"          · {h.get('id')} {h.get('name_zh')} "
                      f"target={_target_of(h)} eq={h.get('normalized_equipment')}")
        if not ok:
            fails.append((r["id"], r["family"], why))

    print("=" * 78)
    print("按族汇总：")
    for fam in sorted(by_fam):
        res = by_fam[fam]
        print(f"  {fam:<22} {sum(res)}/{len(res)}")

    print()
    print("通道状态：")
    for name, status, detail in channel_status():
        print(f"  {name:<9} {status:<8} {detail}")

    if fails:
        print(f"\n[FAIL] {len(fails)}/{len(rows)} 条不通过 → 退出码 1")
        return 1
    print(f"\n[OK] {len(rows)}/{len(rows)} 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
