# -*- coding: utf-8 -*-
"""RAG 入湖管线（W1：图数据；W2 补 embedding 块——bge-m3 向量）。
幂等：clear_all 后重建。数据源：lib 只读 repo + data/ 只读知识表（均不写）。
无网络降级：Ollama/embedder 不可达 → 跳过 embedding（embeddings 保持 0），不中断图写入。"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import psycopg                              # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
for p in ("lib",):                       # ingest 内自足（脚本/import 两用）
    if str(ROOT / p) not in sys.path:
        sys.path.insert(0, str(ROOT / p))

from app.rag.store import PgStore          # noqa: E402
from app.rag.embedder import OllamaEmbedder  # noqa: E402
from app.graph.store import GraphStore     # noqa: E402
from exercise_repo import ExerciseRepo      # noqa: E402
from screening import CONDITIONS            # noqa: E402

_SCIENCE_DIR = ROOT / "data" / "training-science" / "data"
_SPORTS_DIR = ROOT / "data" / "sports-medicine" / "data"
MAX_CHUNK = 200                            # 块上限（字）


def _cap(text: str) -> str:
    return text if len(text) <= MAX_CHUNK else text[:MAX_CHUNK]


def _exercise_cue(e: dict) -> str:
    """动作要领中文文本块模板。"""
    name_zh = e.get("name_zh") or ""
    name_en = e.get("name") or ""
    pat = e.get("movement_pattern") or "综合"
    diff = e.get("difficulty") or "—"
    sug = e.get("suggested") or {}
    sets, reps = sug.get("sets") or "—", sug.get("reps") or "—"
    rest = sug.get("rest_sec") or "—"
    eq = e.get("normalized_equipment") or e.get("equipment") or "自重/器械"
    return _cap(f"{name_zh}（{name_en}）：{pat}模式，难度{diff}，"
                f"建议{sets}组×{reps}次，组间休息{rest}s，器械{eq}。")


def _science_blocks() -> list[tuple[str, str, str]]:
    """知识表（训练科学 + 运动医学）→ (id, 标题, 文本块) 列表。医学/训练科学材料待审。

    2026-09-14 加标题：`id` 是 `anchor_4` / `cn03` 这种内部标识，直接渲染给用户不可读。
    标题随 `source_ref.title` 落库，消费端（`qa._rag_science`）据此渲染"来源：…"。
    `add()` 强制三参，新增块不会漏标题。"""
    blocks: list[tuple[str, str, str]] = []

    def add(tid: str, title: str, text: str):
        if text.strip():
            blocks.append((tid, title, _cap(text)))

    # 中英模块名 → 中文，供 FITT 三节标题用
    _SEC_ZH = {"cardiorespiratory": "有氧", "resistance": "抗阻",
               "flexibility": "柔韧"}

    # ---- training-science/data ----
    rpe = json.loads((_SCIENCE_DIR / "rpe_rir.json").read_text(encoding="utf-8"))
    add("method", "RPE×次数→%1RM 换算",
        f"RPE×次数→%1RM：{rpe.get('method')} {rpe.get('note')}")
    for i, a in enumerate(rpe.get("anchors", [])):
        add(f"anchor_{i}", f"{a['reps']} 次 @RPE{a['rpe']} 的 %1RM 锚点",
            f"{a['reps']} 次@RPE{a['rpe']} ≈ {a['pct_1rm']}%1RM")

    ss = json.loads((_SCIENCE_DIR / "strength_standards.json").read_text(encoding="utf-8"))
    add("lift_header", "力量标准（1RM/体重kg）",
        f"力量标准（1RM/体重kg 比值，单位：{ss.get('unit')}）。{ss.get('note')}")
    for lift, m in (ss.get("lift") or {}).items():
        mm, fm = m.get("male") or {}, m.get("female") or {}
        def _fmt(d): return " / ".join(f"{k} {v}" for k, v in d.items())
        add(f"lift_{lift}", f"{lift} 力量标准",
            f"{lift} 力量标准：男 {_fmt(mm)}；女 {_fmt(fm)}。")
    dm = (ss.get("difficulty_mapping") or {})
    add("difficulty_mapping", "力量标准 ↔ 动作难度映射",
        f"力量标准 4 档→动作难度 3 档映射：{ {k: v for k, v in dm.items() if k != 'note'} }。{dm.get('note')}")

    ci = json.loads((_SCIENCE_DIR / "contraindications.json").read_text(encoding="utf-8"))
    add("ci_header", "训练禁忌与替代（总述）", ci.get("disclaimer"))
    for c in ci.get("conditions", []):
        add(c["id"], c["condition_zh"],
            f"{c['condition_zh']}（风险等级 {c.get('risk_level')}）："
            f"{c.get('danger_notes')}。安全处方：{'；'.join(c.get('safe_prescriptions', []))}。"
            f"替代动作：{'、'.join(c.get('alternatives', []))}。来源{c.get('source')}。")

    pq = json.loads((_SCIENCE_DIR / "parq_plus.json").read_text(encoding="utf-8"))
    add("pq_header", "PAR-Q+ 2021 门检（总述）", f"PAR-Q+ 2021 门检。{pq.get('note')}")
    for n, q in enumerate(pq.get("questions", []), 1):
        add(q["id"], f"PAR-Q+ 门检第 {n} 题", q["text_zh"])
    for k, v in (pq.get("rules") or {}).items():
        add(f"rule_{k}", f"PAR-Q+ 处置规则（{k}）", v)

    # ---- sports-medicine/data ----
    fitt = json.loads((_SPORTS_DIR / "fitt_prescription.json").read_text(encoding="utf-8"))
    add("fitt_header", "FITT 运动处方（总述）",
        f"{fitt.get('disclaimer')} 来源{fitt.get('source')}")
    for sec in ("cardiorespiratory", "resistance", "flexibility"):
        d = (fitt.get(sec) or {})
        if d:
            add(sec, f"{_SEC_ZH.get(sec, sec)} FITT 参数",
                f"{sec} FITT：频率 {d.get('frequency')}；强度 {d.get('intensity')}；"
                f"时间 {d.get('time') or d.get('volume')}；组/次数 {d.get('volume') or '—'}；"
                f"休息 {d.get('rest') or '—'}；类型 {d.get('type') or '—'}。"
                f"{d.get('progression_note') or d.get('sets_weekly_note') or ''}")
    add("fitt_intensity", "运动强度尺度",
        f"运动强度尺度：有氧 RPE4-7 {fitt.get('intensity_scale', {}).get('cardio', {}).get('rpe_10')}；"
        f"抗阻 RPE5-9，新手 40-50%1RM/中级 60-70%/高级 70-85% "
        f"{fitt.get('intensity_scale', {}).get('resistance', {})}。")

    ep = json.loads((_SPORTS_DIR / "injury_epidemiology.json").read_text(encoding="utf-8"))
    add("ep_header", "运动损伤流行病学（总述）", ep.get("disclaimer"))
    for c in ep.get("conditions", []):
        add(c["id"], c["injury_zh"],
            f"{c['injury_zh']}（{c.get('injury_en')}）：{c.get('incidence_note')}。"
            f"风险因素：{'、'.join(c.get('risk_factors', []))}。"
            f"预防：{'、'.join(c.get('prevention', []))}。")

    rtp = json.loads((_SPORTS_DIR / "recovery_rtp.json").read_text(encoding="utf-8"))
    add("rtp_header", "伤后恢复时间线（总述）", rtp.get("disclaimer"))
    for c in rtp.get("conditions", []):
        add(f"{c['id']}_rtp", f"{c['injury_zh']} 恢复时间线",
            f"{c['injury_zh']}：{c.get('rtp_note')}")
        for i, s in enumerate(c.get("stages", [])):
            add(f"{c['id']}_{i}", f"{c['injury_zh']}·{s.get('phase')}",
                f"{c['injury_zh']}·{s.get('phase')}：目标 {'、'.join(s.get('goals', []))}；"
                f"允许 {'、'.join(s.get('allowed', []))}；避免 {'、'.join(s.get('avoid', []))}。")

    return blocks


def _write_embeddings(store: PgStore, ex: ExerciseRepo,
                      embedder) -> bool:
    """生成并写入 embedding 块。返回是否成功写入（False=跳过，图数据不受影响）。"""
    try:
        if not embedder.healthy():
            return False
        rows: list[tuple[str, dict, str, bool]] = []
        for e in ex.by_id.values():
            rows.append(("exercise_cue",
                         {"table": "exercise", "id": e["id"], "field": "cue",
                          "title": e.get("name_zh") or e["id"]},
                         _exercise_cue(e), False))
        for tid, title, text in _science_blocks():
            rows.append(("science_doc", {"table": "science", "id": tid,
                                         "field": "text", "title": title},
                         text, True))
        texts = [r[2] for r in rows]
        vecs = embedder.embed(texts)
    except Exception:
        return False                       # 网络/embedder 出错 → 跳过，不中断图

    with psycopg.connect(store.dsn, autocommit=False) as conn, conn.cursor() as cur:
        for (ctype, sref, content, pending), vec in zip(rows, vecs):
            cur.execute(
                "INSERT INTO fitness.embeddings"
                "(chunk_type,source_ref,content,embedding,pending_review) "
                "VALUES(%s,%s,%s,%s,%s)",
                (ctype, psycopg.types.json.Jsonb(sref), content, vec, pending))
        conn.commit()
    return True


def build(store: PgStore | None = None, fill_embeddings: bool = True,
          graph: "GraphStore | None" = None) -> dict:
    """幂等重建：向量 → PG（store）；图 → Neo4j（graph，默认 GraphStore.get()）。
    Neo4j 不可用 → 跳过图（nodes/edges=0），向量照常（双降级）。"""
    store = store or PgStore()
    store.apply_schema()
    store.clear_all()
    ex = ExerciseRepo()

    graph = graph if graph is not None else GraphStore.get()
    graph_counts = {"nodes": 0, "edges": 0}
    if graph is not None:
        graph.clear_subgraph()
        _build_graph(graph, ex)
        graph_counts = graph.counts()
        graph_counts["graph_store"] = "neo4j"

    embedder = OllamaEmbedder()
    embedding_skipped = False
    if fill_embeddings:
        if not _write_embeddings(store, ex, embedder):
            embedding_skipped = True

    counts = store.counts()
    counts.update(graph_counts)
    if embedding_skipped:
        counts["embedding_skipped"] = True
    return counts


def _build_graph(graph: "GraphStore", ex: ExerciseRepo) -> None:
    """图写入（Neo4j）：动作/肌群/器械/模式/禁忌/同族 + 5 类边。"""
    # 动作节点：id 唯一，meta 存中英文名
    for e in ex.by_id.values():
        graph.merge_entity("Exercise", "id", {
            "id": e["id"],
            "name_zh": e.get("name_zh"),
            "name_en": e.get("name")})
        # 肌群角色（2026-09-11）：此前 target/muscle_group/secondary 被并成一个集合，
        # 边全等权 → 消费方无法区分"主要练胸、三头只是协同"。改为逐肌群带 role：
        # target（主动肌）/ synergist（协同）。已核实 muscle_group 恒为 secondary 子集
        # （0/1324 例外），故并入 synergist 不丢边、不引入第三态。
        mus = e.get("muscles_canonical") or {}
        roles: dict = {}
        if mus.get("target"):
            roles[mus["target"]] = "target"
        for m in (mus.get("muscle_group"), *(mus.get("secondary") or [])):
            if m:
                roles.setdefault(m, "synergist")
        for m, role in roles.items():
            graph.merge_entity("Muscle", "name", {"name": m})
            graph.merge_rel("Exercise", "id", e["id"], "targets",
                            "Muscle", "name", m, {"role": role})
        eq = e.get("normalized_equipment")
        if eq:
            graph.merge_entity("Equipment", "name", {"name": eq})
            graph.merge_rel("Exercise", "id", e["id"], "uses",
                            "Equipment", "name", eq)
        pat = e.get("movement_pattern")
        if pat:
            graph.merge_entity("Pattern", "name", {"name": pat})
            graph.merge_rel("Exercise", "id", e["id"], "pattern_of",
                            "Pattern", "name", pat)

    # 禁忌（疾病名 → 危险动作模式）
    for ci in CONDITIONS:
        head = ci["condition_zh"].split("（")[0].split("/")[0]
        graph.merge_entity("Condition", "name", {
            "name": head, "condition_zh": ci["condition_zh"],
            "risk_level": ci["risk_level"], "source": ci.get("source")})
        for dp in ci.get("danger_patterns", []):
            graph.merge_entity("Pattern", "name", {"name": dp})
            graph.merge_rel("Condition", "name", head, "contraindicates",
                            "Pattern", "name", dp)

    # 同族（family）：节点 + 动作→member_of→family 边（同族可替代变体）
    for family_id, meta in ex.families.items():
        graph.merge_entity("Family", "id", {"id": family_id, **(meta or {})})
    for e in ex.by_id.values():
        fid = e.get("family")
        if fid:
            graph.merge_rel("Exercise", "id", e["id"], "member_of",
                            "Family", "id", fid)


if __name__ == "__main__":
    print("ingest:", build())