# -*- coding: utf-8 -*-
"""变体族（variant_families）审计：**"同一族" 是否等于 "可替代"？**

背景：上游 `data/exercises-dataset/data/variant_families.json` 按动作词干（press /
row / curl …）划族；而项目内多个消费方把它当作"可替代"用：
  - `ExerciseRepo.siblings()`            —— 文档写"用于替换建议"
  - `retriever.graph_family_alternatives` —— 文档写"同族变体（可替代）"
  - `ExerciseRepo.recommend()`            —— 用作**变体去重**的 key
前两者需要"可互换"，后者需要"同一动作的不同花样"。两种需求可能要不同的分组。

本脚本把事实量出来，不给结论。判据用两个可观测轴：
  - 主目标肌 `muscles_canonical.target`（可替代的下限：练的不是同一块就不算替代）
  - 器械 `normalized_equipment`（练同一块但器械不同，仍可能是好替代，故单独看）

用法：python scripts/audit_variant_families.py
"""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

from exercise_repo import ExerciseRepo                                  # noqa: E402


def _pct(a: float) -> str:
    return f"{a * 100:.1f}%"


def main() -> None:
    ex = ExerciseRepo()
    members = {eid: r for eid, r in ex.by_id.items() if r.get("family")}
    print(f"动作总数 {len(ex.by_id)}；有族的 {len(members)}"
          f"（{_pct(len(members) / len(ex.by_id))}）；无族的 {len(ex.by_id) - len(members)}")
    print(f"族总数 {len(ex.families)}")

    def tgt(r):
        return (r.get("muscles_canonical") or {}).get("target")

    print("\n== 一、族规模分布 ==")
    sizes = sorted((len(f.get("member_ids") or []) for f in ex.families.values()),
                   reverse=True)
    print(f"  max={sizes[0]} min={sizes[-1]} 中位={sizes[len(sizes) // 2]}")
    for lo, hi, label in ((20, 10 ** 9, "≥20 人"), (10, 19, "10-19"),
                          (5, 9, "5-9"), (2, 4, "2-4"), (1, 1, "1（单成员）")):
        n = sum(1 for s in sizes if lo <= s <= hi)
        print(f"  {label:12s} {n:3d} 族  {_pct(n / len(sizes))}")

    print("\n== 二、族内语义一致性（只统计 ≥2 成员的族）==")
    multi = [f for f in ex.families.values() if len(f.get("member_ids") or []) >= 2]
    single_target, mixed_eq = 0, 0
    dom_shares = []
    rows = []
    for f in multi:
        ms = [ex.by_id[i] for i in f["member_ids"] if i in ex.by_id]
        tg = Counter(t for t in (tgt(r) for r in ms) if t)
        eq = Counter(r.get("normalized_equipment") for r in ms)
        if len(tg) <= 1:
            single_target += 1
        dom = (tg.most_common(1)[0][1] / sum(tg.values())) if tg else 0.0
        dom_shares.append(dom)
        if len(eq) > 1:
            mixed_eq += 1
        rows.append((dom, f, ms, tg, eq))

    print(f"  ≥2 成员的族 {len(multi)} 个")
    print(f"  其中**主目标肌唯一**的 {single_target}"
          f"（{_pct(single_target / len(multi))}）"
          f" → 其余 {len(multi) - single_target} 个族里混了不同主目标肌")
    print(f"  混了多种器械的族 {mixed_eq}（{_pct(mixed_eq / len(multi))}）")

    print("\n== 三、逐动作视角：我这族的兄弟里，有多少和我练同一块主肌？==")
    shares = []
    zero = []
    for eid, r in members.items():
        sibs = [ex.by_id[i] for i in ex.families[r["family"]]["member_ids"]
                if i != eid and i in ex.by_id]
        if not sibs:
            continue
        me = tgt(r)
        same = sum(1 for s in sibs if tgt(s) and tgt(s) == me)
        shares.append(same / len(sibs))
        if same == 0:
            zero.append((r, len(sibs)))
    shares.sort()
    print(f"  n={len(shares)}  均值={_pct(sum(shares) / len(shares))} "
          f"中位={_pct(shares[len(shares) // 2])}")
    print(f"  **兄弟里一个同主肌都没有**的动作 {len(zero)}"
          f"（{_pct(len(zero) / len(shares))}）")

    print("\n== 四、反例：主目标肌最不一致的族（取 6 个）==")
    for dom, f, ms, tg, eq in sorted(rows, key=lambda x: x[0])[:6]:
        print(f"\n  [{f['family_id']}] stem={f.get('stem')!r} "
              f"{len(ms)} 人 · 主导主肌占比 {_pct(dom)} · "
              f"主肌 {len(tg)} 种 · 器械 {len(eq)} 种")
        for t, c in tg.most_common():
            names = [r.get("name_zh") for r in ms if tgt(r) == t][:3]
            print(f"      {t or '—':22s} ×{c:<2} 例: {'、'.join(n or '?' for n in names)}")

    print("\n== 五、大族（≥10 成员）逐个看 —— 危害集中在这里 ==")
    for f in sorted(multi, key=lambda x: -len(x["member_ids"]))[:8]:
        ms = [ex.by_id[i] for i in f["member_ids"] if i in ex.by_id]
        if len(ms) < 10:
            break
        tg = Counter(t for t in (tgt(r) for r in ms) if t)
        eq = Counter(r.get("normalized_equipment") for r in ms)
        print(f"\n  [{f['family_id']}] stem={f.get('stem')!r}  {len(ms)} 人  "
              f"主肌 {len(tg)} 种 · 器械 {len(eq)} 种")
        for t, c in tg.most_common():
            names = [r.get("name_zh") for r in ms if tgt(r) == t][:2]
            print(f"      {t or '—':22s} ×{c:<3} 例: {'、'.join(n or '?' for n in names)}")

    print("\n== 六、那 37 个「兄弟里没有一个是同主肌」的动作落在哪些族 ==")
    by_fam = Counter(r["family"] for r, _ in zero)
    for fid, c in by_fam.most_common(6):
        f = ex.families[fid]
        print(f"  [{fid}] stem={f.get('stem')!r} 族规模 "
              f"{len(f['member_ids'])} 人 · 其中 {c} 个动作如此")

    print("\n== 七、对照：若改用 (主目标肌, 器械) 分组，规模是多少？==")
    grp: dict[tuple, list] = {}
    for eid, r in members.items():
        grp.setdefault((tgt(r), r.get("normalized_equipment")), []).append(eid)
    gs = sorted((len(v) for v in grp.values()), reverse=True)
    print(f"  组数 {len(grp)}（词干族是 {len(ex.families)}）")
    print(f"  最大组 {gs[0]} 人（词干族最大 {sizes[0]} 人）；"
          f"中位 {gs[len(gs) // 2]} 人")
    big = sorted(grp.items(), key=lambda kv: -len(kv[1]))[:3]
    for (t, e), ids in big:
        print(f"    {t or '—'} / {e or '—'}: {len(ids)} 人  例: "
              f"{'、'.join((ex.by_id[i].get('name_zh') or '?') for i in ids[:4])}")


if __name__ == "__main__":
    main()
