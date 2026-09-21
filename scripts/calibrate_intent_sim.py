# -*- coding: utf-8 -*-
"""意图语义层标定脚本：对 200 题压测集中"规则未命中"子集逐条算 bge-m3 相似度，
输出各意图 sim 分布（min/p50/max 与最近邻混淆对），据此定 adopt_sim /
guard_adopt_sim / sim_floor / sim_ceil 终值（需本机 Ollama bge-m3）。

另复核 `app/config/intent_calibration.json`（按 task_type 标注的用例集，含反例）：
出现**误采纳**即以退出码 1 结束，可直接当门禁用。
⚠ 那 200 题的 EXPECTED 里 **qa 类为 0 条**，只看它的分布会漏掉整族缺陷。

用法： python scripts/calibrate_intent_sim.py
输出： 控制台统计；若 --write 则把 semantic 终值写回 app/config/router_config.json
      并同步更新 app/core/semantic.py 的 SIM_FLOOR/SIM_CEIL。
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

from app.core.llm import StubProvider        # noqa: E402
from app.core.semantic import ExemplarStore, SIM_FLOOR, SIM_CEIL  # noqa: E402
from scripts.stress_test import QUESTIONS, EXPECTED  # noqa: E402

# 期望意图（EXPECTED 用 mode 名 plan_exec/smalltalk；这里归一为任务类型）
_MODE2TT = {"plan_exec": "plan", "smalltalk": "smalltalk"}

# task_type 标注的标定用例集（见文件内 _note：stress_test 的 EXPECTED 里 qa 类为 0，
# 正是这个空洞让「动作推荐」一族的缺陷漏了出去）
CALIB = ROOT / "app" / "config" / "intent_calibration.json"


def _expected(i: int) -> str:
    return _MODE2TT.get(EXPECTED.get(i), EXPECTED.get(i))


def _runtime_semantic_cfg() -> dict:
    """读**运行时真正生效**的 semantic 阈值，而不是本脚本下面『建议』出来的那组。

    标定用例的判决必须复现线上决策，否则量的是另一套东西。
    """
    cfg_path = ROOT / "app" / "config" / "router_config.json"
    sem = json.loads(cfg_path.read_text(encoding="utf-8")).get("semantic", {})
    return {
        "adopt": float(sem.get("adopt_sim", 0.62)),
        "guard_adopt": float(sem.get("guard_adopt_sim", 0.55)),
    }


def review_cases(store) -> int:
    """按 task_type 标注复核 L1 判决，返回**误采纳条数**（设计前提：必须为 0）。

    判据：
      OK          采纳且命中期望意图
      FALSE_ADOPT 采纳但命中了别的意图 —— 最坏结果（抢走别的意图），必须为 0
      FALLTHROUGH 未达阈值 → 交给 L2；不算 L1 的错，但要看得出有多少
    """
    if not CALIB.exists():
        print(f"\n[WARN] 标定用例集不存在：{CALIB}")
        return 0
    cases = json.loads(CALIB.read_text(encoding="utf-8"))["cases"]
    thr = _runtime_semantic_cfg()

    print(f"\n=== 标定用例集（{CALIB.name}，n={len(cases)}）===")
    print(f"    阈值取运行时生效值：adopt_sim={thr['adopt']:.3f} "
          f"guard_adopt_sim={thr['guard_adopt']:.3f}")
    print(f"    {'id':>3} {'期望':<8} {'命中':<8} {'sim':>6}  判决")

    false_adopt = []
    per_family: dict = {}
    for c in cases:
        matched, sim = store.match(c["text"])
        limit = thr["guard_adopt"] if matched == "guard" else thr["adopt"]
        if sim < limit:
            verdict = "FALLTHROUGH"
        elif matched == c["task_type"]:
            verdict = "OK"
        else:
            verdict = "FALSE_ADOPT"
            false_adopt.append((c, matched, sim))
        st = per_family.setdefault(c["family"], [0, 0])
        st[0 if verdict == "OK" else 1] += 1
        print(f"    {c['id']:>3} {c['task_type']:<8} {matched:<8} {sim:>6.3f}  "
              f"{verdict:<11} {c['text']}")

    print("\n=== 按族统计（OK / 非 OK）===")
    for fam, (ok, bad) in sorted(per_family.items()):
        print(f"    {fam:<18} OK={ok}  非OK={bad}")

    if false_adopt:
        print(f"\n  ✗ 误采纳 {len(false_adopt)} 条（设计前提是 0）：")
        for c, matched, sim in false_adopt:
            print(f"    #{c['id']} sim={sim:.3f} 期望{c['task_type']} "
                  f"被抢成 {matched} | {c['text']}")
    else:
        print("\n  ✓ 零误采纳")
    return len(false_adopt)


def main() -> None:
    should_write = "write" in sys.argv or "--write" in sys.argv
    rules = StubProvider()
    store = ExemplarStore.get()
    if store is None:
        print("[WARN] ExemplarStore 不可用（缺 Ollama / 例句库）→ 无法标定。")
        sys.exit(1)

    # 规则未命中子集（rule.confidence<0.7）→ candidate
    candidates = []   # (id, text, expected_tt)
    for i, q in enumerate(QUESTIONS, 1):
        r = rules.classify(q)
        if r.confidence < 0.7 and i in EXPECTED:
            candidates.append((i, q, _expected(i)))

    print(f"规则未命中且归属明确：{len(candidates)} 题\n")

    # 逐条相似度
    rows = []   # (id, text, expected, matched, sim)
    for i, q, exp in candidates:
        matched, sim = store.match(q)
        rows.append((i, q, exp, matched, sim))

    # 按期望意图分组统计 sim
    groups: dict = {}
    for i, q, exp, matched, sim in rows:
        g = groups.setdefault(exp, [])
        g.append((sim, matched))
    print("=== 各意图 sim 分布（规则未命中子集）===")
    for tt, arr in sorted(groups.items()):
        sims = sorted(s for s, _ in arr)
        n = len(sims)
        p50 = sims[n // 2]
        ok = sum(1 for s, m in arr if m == tt)
        print(f"  {tt:<10} n={n:>2}  min={sims[0]:.3f} p50={p50:.3f} "
              f"max={sims[-1]:.3f}  同款命中 {ok}/{n}")

    print("\n=== 混淆对（matched != expected，top N）===")
    confusions = [(i, q, exp, matched, sim) for i, q, exp, matched, sim in rows
                  if matched != exp]
    for i, q, exp, matched, sim in sorted(confusions, key=lambda r: -r[4])[:10]:
        print(f"  #{i:3} sim={sim:.3f} 期望{exp:<6} 命中{matched:<6} {q[:26]}")

    print("\n=== 建议阈值 ===")
    sims = sorted(s for _, _, _, _, s in rows)
    adopt = max([s for tt, arr in groups.items() if tt != "guard"
                 for s, _ in arr], default=0.6)
    guard_max = max([s for tt, arr in groups.items() if tt == "guard"
                     for s, _ in arr], default=adopt)
    guard = min(guard_max, adopt)  # guard 阈值不得高于通用（宁误拦）
    floor = min([min(s for s, _ in arr) for arr in groups.values()] + [SIM_FLOOR])
    ceil = max([max(s for s, _ in arr) for arr in groups.values()] + [SIM_CEIL])
    print(f"  adopt_sim（通用）= {adopt:.3f}")
    print(f"  guard_adopt_sim  = {guard:.3f}  （guard 阈值 ≤ 通用）")
    print(f"  sim_floor={floor:.3f}  sim_ceil={ceil:.3f}")

    # 标注用例集复核：任何一条误采纳都会让本脚本以非零码退出，可直接当门禁用。
    n_false = review_cases(store)

    if should_write:
        cfg_path = ROOT / "app" / "config" / "router_config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["semantic"] = {
            "enabled": True,
            "adopt_sim": round(adopt, 3),
            "guard_adopt_sim": round(guard, 3),
            "sim_floor": round(floor, 3),
            "sim_ceil": round(ceil, 3),
        }
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"\n已写回 {cfg_path}")

    if n_false:
        print(f"\n[FAIL] 标定用例集有 {n_false} 条误采纳 → 退出码 1（可作门禁）")
        sys.exit(1)


if __name__ == "__main__":
    main()