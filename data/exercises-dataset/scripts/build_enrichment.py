# -*- coding: utf-8 -*-
"""
build_enrichment.py — FitMind 数据集增强引擎
=============================================
读取 data/exercises.json，输出 4 个独立映射文件（不修改原文件）：
  1. muscle_ontology.json   正规肌肉本体库（规范 id + 中英文名 + 区域 + 别名）
  2. muscle_mapping.json    原始肌肉术语 -> 本体 id 的归一化映射
  3. exercise_metadata.json 每个动作的训练元数据
                            (difficulty 1-3 / movement_pattern / exercise_type
                             / suggested 处方 / met_range / met_typical / family)
  4. variant_families.json  动作变体族（同源动作分组，用于替换建议）

生成原则：机器可推导的（器械、部位、动作模式、处方、MET）用确定性规则；
难度分级用"规则打底 + 人工(LLM)抽检调优"的方式标定，保证可复现。
运行：python scripts/build_enrichment.py
"""

import json
import os
import re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SRC = os.path.join(DATA_DIR, "exercises.json")

# ----------------------------------------------------------------------------
# 1. 肌肉本体（单一数据源：别名 -> 本体 id）
# ----------------------------------------------------------------------------
CANONICAL = [
    dict(id="pectorals",            name_en="Pectorals",            name_zh="胸大肌",    region="chest",
         aliases=["pectorals", "chest", "upper chest"]),
    dict(id="serratus_anterior",    name_en="Serratus Anterior",    name_zh="前锯肌",    region="chest",
         aliases=["serratus anterior"]),
    dict(id="trapezius",            name_en="Trapezius",            name_zh="斜方肌",    region="back",
         aliases=["traps", "trapezius"]),
    dict(id="latissimus_dorsi",     name_en="Latissimus Dorsi",     name_zh="背阔肌",    region="back",
         aliases=["lats", "latissimus dorsi"]),
    dict(id="rhomboids",            name_en="Rhomboids",            name_zh="菱形肌",    region="back",
         aliases=["rhomboids"]),
    dict(id="upper_back",           name_en="Upper Back",           name_zh="上背部",    region="back",
         aliases=["upper back", "back"]),
    dict(id="lower_back",           name_en="Lower Back",           name_zh="下背部（竖脊肌区）", region="back",
         aliases=["lower back"]),
    dict(id="spine",                name_en="Spine",                name_zh="脊柱肌群",  region="back",
         aliases=["spine"]),
    dict(id="levator_scapulae",     name_en="Levator Scapulae",     name_zh="肩胛提肌",  region="back",
         aliases=["levator scapulae"]),
    dict(id="deltoids",             name_en="Deltoids",             name_zh="三角肌",    region="shoulders",
         aliases=["delts", "deltoids", "rear deltoids", "shoulders"]),
    dict(id="rotator_cuff",         name_en="Rotator Cuff",         name_zh="肩袖肌群",  region="shoulders",
         aliases=["rotator cuff"]),
    dict(id="biceps",               name_en="Biceps",               name_zh="肱二头肌",  region="upper_arms",
         aliases=["biceps", "brachialis"]),
    dict(id="triceps",              name_en="Triceps",              name_zh="肱三头肌",  region="upper_arms",
         aliases=["triceps"]),
    dict(id="forearms",             name_en="Forearms",             name_zh="前臂肌群",  region="forearms",
         aliases=["forearms", "wrist flexors", "wrist extensors", "wrists", "hands", "grip muscles"]),
    dict(id="glutes",               name_en="Glutes",               name_zh="臀肌",      region="glutes",
         aliases=["glutes"]),
    dict(id="quadriceps",           name_en="Quadriceps",           name_zh="股四头肌",  region="upper_legs",
         aliases=["quads", "quadriceps"]),
    dict(id="hamstrings",           name_en="Hamstrings",           name_zh="腘绳肌",    region="upper_legs",
         aliases=["hamstrings"]),
    dict(id="calves",               name_en="Calves",               name_zh="小腿后群",  region="lower_legs",
         aliases=["calves", "soleus", "ankles"]),
    dict(id="tibialis_anterior",    name_en="Tibialis Anterior",    name_zh="胫骨前肌",  region="lower_legs",
         aliases=["shins"]),
    dict(id="abductors",            name_en="Hip Abductors",        name_zh="髋外展肌",  region="hips",
         aliases=["abductors"]),
    dict(id="adductors",            name_en="Hip Adductors",        name_zh="髋内收肌",  region="hips",
         aliases=["adductors", "groin", "inner thighs"]),
    dict(id="hip_flexors",          name_en="Hip Flexors",          name_zh="髋屈肌",    region="hips",
         aliases=["hip flexors"]),
    dict(id="rectus_abdominis",     name_en="Rectus Abdominis",     name_zh="腹直肌",    region="core",
         aliases=["abs", "abdominals", "lower abs"]),
    dict(id="obliques",             name_en="Obliques",             name_zh="腹斜肌",    region="core",
         aliases=["obliques"]),
    dict(id="core",                 name_en="Core",                 name_zh="核心肌群",  region="core",
         aliases=["core"]),
    dict(id="ankle_stabilizers",    name_en="Ankle Stabilizers",    name_zh="踝部稳定肌", region="lower_legs",
         aliases=["ankle stabilizers", "feet"]),
    dict(id="sternocleidomastoid",  name_en="Sternocleidomastoid",  name_zh="胸锁乳突肌", region="neck",
         aliases=["sternocleidomastoid"]),
    dict(id="cardio_system",        name_en="Cardiovascular System", name_zh="心肺系统", region="cardio",
         aliases=["cardiovascular system"]),
]

# 校验别名唯一性
_all_aliases = [a for m in CANONICAL for a in m["aliases"]]
assert len(_all_aliases) == len(set(_all_aliases)), "别名重复，请检查本体定义"

# ----------------------------------------------------------------------------
# 2. 动作模式 / 复合孤立 关键词规则
# ----------------------------------------------------------------------------
PATTERN = {
    "push":  ["bench press", "bench-press", "push press", "shoulder press", "overhead press", "push-up",
              "pushup", "push up", "dip", "flyes", "fly", "cable fly", "pec deck", "chest press",
              "military press", "arnold press", "crossover", "cross-over", "pressdown", "pushdown",
              "skull", "french press", "kickback", "tricep extension", "triceps extension", "chest dip",
              "handstand push", "clean and press", "clean & press"],
    "pull":  ["pull-up", "pull up", "chin-up", "chin up", "lat pulldown", "pulldown", "row", "rows",
              "pullover", "face pull", "rear delt", "reverse fly", "curl", "shrug", "upright row",
              "high pull", "clean", "snatch", "back fly", "hammer curl", "preacher", "concentration", "wrist"],
    "squat": ["squat", "sissy", "leg press", "hack"],
    "hinge": ["deadlift", "rdl", "romanian", "hip thrust", "back extension", "hip lift", "bridge",
              "good morning", "pull through", "pull-through", "reverse hyper", "trap bar deadlift",
              "stiff leg", "straight leg", "swing", "kettlebell swing", "hyperextension"],
    "lunge": ["lunge", "split squat", "step-up", "step up", "climb"],
    "core":  ["crunch", "sit-up", "sit up", "plank", "leg raise", "leg lift", "twist", "russian",
              "bird dog", "dead bug", "side bend", "v-up", "hollow", "rotation", "ab wheel",
              "cable crunch", "pallof", "supermans", "superman", "flutter", "mountain climber", "tuck"],
    "carry": ["carry", "farmer", "suitcase", "waiter", "bear hug"],
    "cardio":["run", "walk", "jog", "bike", "cycling", "jump rope", "skipping", "elliptical",
              "stepper", "stepmill", "skierg", "rowing", "burpee", "jumping jack", "high knees", "stair"],
}

# 复合(compound)判定：先按模式再按关键词覆盖
COMPOUND_BY_PATTERN = {"push": True, "pull": True, "squat": True, "hinge": True,
                       "lunge": True, "carry": True, "cardio": True, "core": False, "other": False}
ISOLATION_KW = ["curl", "fly", "raise", "kickback", "extension", "calf", "crunch", "sit-up", "sit up",
                "side bend", "leg raise", "wrist", "shrug", "front raise", "lateral raise",
                "leg extension", "leg curl", "incline curl", "preacher", "concentration",
                "pushdown", "pressdown"]
COMPOUND_OVERRIDES = ["back extension", "pullover", "shrug", "reverse hyper", "reverse extension"]

# 难度：规则打底（可在下方 TUNE 区人工调整）
BEGINNER_KW = ["march", "step touch", "step touch", "bird dog", "dead bug", "glute bridge", "wall sit",
               "knee push-up", "knee push up", "wall push", "chair squat", "sit-to-stand", "modified",
               "plank", "walk", "jog", "step up", "calf raise", "stationary bike", "elliptical",
               "assisted", "scapula", "cat", "cow", "child's pose", "cobra", "march in place"]
ADVANCED_KW = ["pistol", "muscle-up", "muscle up", "handstand", "pull-up", "pull up", "chin-up",
               "chin up", "snatch", "clean and jerk", "clean & jerk", "hang clean", "thruster",
               "jerk", "deficit", "ring", "planche", "l-sit", "l sit", "dragon flag", "iron cross",
               "dip", "windmill", "front lever", "back lever", "human flag", "weighted",
               "turkish get-up"]

DIFFICULTY_BASE = {
    "barbell": 2, "olympic barbell": 2, "trap bar": 1, "ez barbell": 1, "dumbbell": 1,
    "kettlebell": 2, "weighted": 2, "smith machine": 1, "leverage machine": 1, "cable": 1,
    "band": 1, "resistance band": 1, "stability ball": 1, "bosu ball": 2, "medicine ball": 1,
    "body weight": 1, "sled machine": 1, "rope": 1, "roller": 1, "assisted": 1,
    "wheel roller": 2, "upper body ergometer": 1, "skierg machine": 1, "hammer": 2, "tire": 2,
    "stationary bike": 1, "elliptical machine": 1, "stepmill machine": 1, "other": 1,
}
# body weight 下 高门槛徒手仍需要加档：pull-up/dip 等在 ADVANCED_KW +1

# 关键词匹配：词边界 + 复数变体，避免 narrow/butterfly/squatting 等子串误杀
def kw(text, keywords):
    t = text.lower()
    for k in keywords:
        if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?:s|es|ed|d)?(?![a-z0-9])", t):
            return True
    return False

# 拉伸/柔韧类：单独类型，不参与力量训练推荐
STRETCH_KW = ["stretch", "yoga", "mobilization", "mobility", "circle", "pose", "roll"]

def classify(text, category):
    t = text.lower()
    if kw(t, STRETCH_KW):
        return "stretch"
    if "good morning" in t or "back extension" in t:
        return "hinge"
    for pat in ("push", "pull", "squat", "hinge", "lunge", "core", "carry", "cardio"):
        if kw(t, PATTERN[pat]):
            return pat
    # 无关键词命中时的分类兜底
    return {"cardio": "cardio", "waist": "core", "chest": "push", "back": "pull",
            "shoulders": "push", "upper arms": "pull", "lower arms": "pull",
            "upper legs": "squat", "lower legs": "other", "neck": "other",
            "glutes": "hinge", "hips": "hinge"}.get(category, "other")

def is_compound(text, pattern):
    t = text.lower()
    if t.strip() in ("back extension", "pullover") or any(k in t for k in COMPOUND_OVERRIDES):
        return True
    if kw(t, ISOLATION_KW):
        return False
    return COMPOUND_BY_PATTERN[pattern]

def difficulty_score(text, pattern, equipment, category):
    t, eq = text.lower(), equipment.lower()
    d = {"body weight": 1}.get(eq, DIFFICULTY_BASE.get(eq, 1))
    # 高级动作 +1（限幅 3）；kw() 词边界匹配，避免 barbe[l sit]ted 之类的跨词误伤
    if kw(t, ADVANCED_KW):
        d += 1
    # 新手动作 -1（turkish get-up 由 ADVANCED +1 抵消，不降；其余含 beginner 词的动作降档）
    if kw(t, BEGINNER_KW) and "turkish get-up" not in t:
        d -= 1
    # 平衡要求高的动作 +1
    if eq in ("bosu ball", "wheel roller", "tire", "hammer"):
        d += 1
    # 器械孤立支撑类降档
    if eq in ("smith machine", "leverage machine", "cable", "assisted", "stability ball"):
        d -= 1
    # 模式修正：hinge/squat 大重量复合默认 2 档
    if pattern in ("hinge", "squat") and eq in ("barbell", "olympic barbell", "weighted", "kettlebell"):
        d = max(d, 2)
    # 显式人工覆盖（LLM 评审调整区，最高优先级）
    MANUAL_DIFF = {
        "weighted front raise": 2, "weighted standing curl": 2, "weighted crunch": 2,
        "weighted hyperextension (on stability ball)": 2, "weighted drop push up": 2,
        "weighted knee raise on parallel bars": 2, "sledge hammer": 2,
        "weighted kneeling step with swing": 2, "weighted lunge with swing": 2,
    }
    d = MANUAL_DIFF.get(text.strip().lower(), d)
    return max(1, min(3, d))

# ----------------------------------------------------------------------------
# 3. 处方 / MET 规则
# ----------------------------------------------------------------------------
def suggested(text, diff, pattern, exercise_type, category):
    if exercise_type == "stretch_mobility":
        return dict(sets="2-3", reps="20-60s 保持", rest_sec="0-30", duration_min=None)
    if exercise_type == "cardio":
        return dict(sets=None, reps=None, rest_sec="0-60", duration_min="20-40")
    if pattern in ("squat", "hinge"):
        sets = {1: "3", 2: "3-4", 3: "4-5"}[diff]
        # 徒手髋铰链（如背部伸展）是轻量高次动作
        reps = "10-20" if (pattern == "hinge" and category in ("waist", "back")
                           and "hyperextension" in text.lower()) else "3-8"
        rest = "60-120" if reps == "10-20" else "150-240"
        return dict(sets=sets, reps=reps, rest_sec=rest, duration_min=None)
    if pattern == "lunge":
        return dict(sets="3", reps="8-12", rest_sec="60-120", duration_min=None)
    if pattern == "core":
        return dict(sets="3", reps="15-30 或 30-60s", rest_sec="30-60", duration_min=None)
    if pattern in ("push", "pull"):
        comp = exercise_type == "strength_compound"
        if comp:
            return dict(sets={1: "3", 2: "3-4", 3: "4-5"}[diff], reps="5-12",
                        rest_sec="90-180", duration_min=None)
        return dict(sets="3", reps="8-15", rest_sec="45-90", duration_min=None)
    if pattern == "carry":
        return dict(sets="3-4", reps="20-40m", rest_sec="60-120", duration_min=None)
    return dict(sets="3", reps="8-12", rest_sec="60-90", duration_min=None)

def met_of(pattern, exercise_type, equipment):
    eq = equipment.lower()
    if exercise_type == "stretch_mobility":
        return (2.2, 3.5, 2.8)
    if pattern == "cardio":
        m = {"stationary bike": (5.5, 8.5, 7.0), "elliptical machine": (4.5, 6.5, 5.5),
             "stepmill machine": (8.0, 11.0, 9.5), "rope": (10.0, 12.5, 11.5),
             "skierg machine": (6.0, 9.0, 7.5), "sled machine": (4.0, 6.0, 5.0),
             "upper body ergometer": (3.5, 6.0, 4.5)}
        if eq in m: return m[eq]
        return (5.0, 9.0, 7.0)
    if exercise_type == "strength_compound":
        if eq in ("body weight",):
            return (5.0, 8.0, 6.5)
        return (4.5, 7.0, 6.0)
    # 孤立/core/carry/other
    return (3.0, 5.0, 4.0)

# ----------------------------------------------------------------------------
# 4. 变体族：规范化动作名 -> 归并同源动作
# ----------------------------------------------------------------------------
EQ_WORDS = ["barbell", "dumbbell", "cable", "ez barbell", "ez-barbell", "smith machine", "smithmachine",
            "kettlebell", "machine", "stability ball", "bosu ball", "medicine ball", "band", "resistance band",
            "assisted", "body weight", "bodyweight", "weighted", "leverage", "trap bar", "rotational",
            "sled machine", "roller", "wheel roller", "bench", "floor", "wall", "step", "ball"]
POS_WORDS = ["incline", "decline", "seated", "standing", "lying", "kneeling", "kneel", "one-arm",
             "one arm", "single-arm", "single arm", "two-arm", "two arm", "double-arm", "reverse",
             "wide-grip", "wide grip", "narrow-grip", "narrow grip", "close-grip", "close grip",
             "flat", "overhead", "behind-the-neck", "behind the neck", "front", "rear", "side",
             "lying-down", "upright", "alternating", "alternate", "hanging", "lying", "straight-arm",
             "straight arm", "bent-over", "bent over", "bradford", "smith", "high", "low", "cross",
             "inclined", "declined", "gironda", "thibaudeau", "hold", "paused", "static", "tate",
             "james", "landmine", "zottman", "zottmann", "palms-in", "palms-in", "neutral-grip",
             "neutral grip", "hammer"]

# ----------------------------------------------------------------------------
# 4. 器械归一 / 规范名
# ----------------------------------------------------------------------------
EQUIPMENT_NORM = {
    "body weight": "body weight", "assisted": "body weight",
    "dumbbell": "dumbbell",
    "barbell": "barbell", "olympic barbell": "barbell", "ez barbell": "barbell", "trap bar": "barbell",
    "kettlebell": "kettlebell",
    "cable": "cable", "rope": "cable",
    "band": "band", "resistance band": "band",
    "leverage machine": "machine", "smith machine": "machine", "sled machine": "machine",
    "upper body ergometer": "machine", "skierg machine": "machine", "stationary bike": "machine",
    "elliptical machine": "machine", "stepmill machine": "machine",
    "weighted": "weighted", "medicine ball": "weighted",
    "stability ball": "other", "bosu ball": "other", "roller": "other", "wheel roller": "other",
    "hammer": "other", "tire": "other", "other": "other",
}

def canonical_name(name):
    """去掉展示性标签得到规范名：括号、v.2、(male)/(female)、with towel 等。"""
    t = re.sub(r"\s*\([^)]*\)", "", name)
    t = re.sub(r"\s*v\.?\s*\d+(\s*[-–].*)?$", "", t.strip(), flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def normalize_name(name):
    t = name.lower().strip()
    for w in EQ_WORDS + POS_WORDS:
        t = re.sub(r"\b" + re.escape(w) + r"\b", " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    with open(SRC, "r", encoding="utf-8") as f:
        data = json.load(f)

    # ---- 肌肉映射 ----
    alias_map = {}
    for m in CANONICAL:
        for a in m["aliases"]:
            alias_map[a] = m["id"]
    raw_terms = set()
    for e in data:
        raw_terms.add(e["target"].strip().lower())
        raw_terms.add(e["muscle_group"].strip().lower())
        raw_terms.update(x.strip().lower() for x in e["secondary_muscles"])
    unmapped = sorted(t for t in raw_terms if t not in alias_map)
    if unmapped:
        print("!! 未映射肌肉术语:", unmapped)
    muscle_mapping = {t: alias_map[t] for t in raw_terms}

    # ---- 逐动作元数据 ----
    meta, families = [], {}
    for e in data:
        pattern = classify(e["name"], e["category"])
        etype = ("stretch_mobility" if pattern == "stretch"
                 else "cardio" if pattern == "cardio"
                 else "strength_compound" if is_compound(e["name"], pattern)
                 else "strength_isolation")
        diff = (1 if etype == "stretch_mobility"
                else difficulty_score(e["name"], pattern, e["equipment"], e["category"]))
        diff_label = {1: "beginner", 2: "intermediate", 3: "advanced"}[diff]
        sug = suggested(e["name"], diff, pattern, etype, e["category"])
        mlo, mhi, mtyp = met_of(pattern, etype, e["equipment"])
        stem = normalize_name(e["name"])
        key = stem if stem else "unclassified"
        families.setdefault(key, []).append(e["id"])
        meta.append(dict(
            id=e["id"], name=e["name"],
            canonical_name=canonical_name(e["name"]),
            difficulty=diff, difficulty_label=diff_label,
            movement_pattern=pattern,
            exercise_type=etype,
            equipment=e["equipment"],
            normalized_equipment=EQUIPMENT_NORM.get(e["equipment"].lower(), "other"),
            suggested=sug,
            met_range=[mlo, mhi], met_typical=mtyp,
            _stem=key,
        ))

    # ---- 变体族输出（>=2 成员） ----
    fam_list = []
    for i, (stem, ids) in enumerate(sorted(families.items(), key=lambda kv: -len(kv[1]))):
        if len(ids) < 2:
            continue
        members = [m for m in meta if m["id"] in ids]
        pat = Counter(m["movement_pattern"] for m in members).most_common(1)[0][0]
        fam_list.append(dict(
            family_id=f"f{i+1:03d}",
            stem=stem,
            canonical_name=stem,
            movement_pattern=pat,
            member_ids=ids,
            member_count=len(ids),
        ))
    fam_lookup = {f["stem"]: f["family_id"] for f in fam_list}
    for m in meta:
        # 两遍法：族归属与处理顺序无关，凡 stem 命中 multi-member 族一律回填 family_id
        m["family"] = fam_lookup.get(m["_stem"])
        del m["_stem"]

    # ---- 输出 ----
    out = lambda name, obj: json.dumps(obj, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, "muscle_ontology.json"), "w", encoding="utf-8") as f:
        f.write(out("", dict(version="1.0", description="Canonical muscle ontology. aliases are raw dataset terms.",
                             muscles=CANONICAL)))
    with open(os.path.join(DATA_DIR, "muscle_mapping.json"), "w", encoding="utf-8") as f:
        f.write(out("", dict(version="1.0", source="muscle_ontology.json",
                             mapping=muscle_mapping)))
    with open(os.path.join(DATA_DIR, "exercise_metadata.json"), "w", encoding="utf-8") as f:
        f.write(out("", dict(version="1.1", exercise_count=len(meta),
                             fields="id/name/canonical_name/difficulty(1-3,beginner|intermediate|advanced)/"
                                    "movement_pattern(push|pull|squat|hinge|lunge|core|carry|cardio|stretch|other)/"
                                    "exercise_type(strength_compound|strength_isolation|cardio|stretch_mobility)/"
                                    "equipment/normalized_equipment(9类)/"
                                    "suggested{sets,reps,rest_sec,duration_min}/met_range/met_typical/family",
                             exercises=meta)))
    with open(os.path.join(DATA_DIR, "variant_families.json"), "w", encoding="utf-8") as f:
        f.write(out("", dict(version="1.0", source="name-stem clustering (equipment/position words stripped). "
                             "Caveat: stems must match exactly, so 'row' vs 'cable row' land in different families.",
                             family_count=len(fam_list), families=fam_list)))

    # ---- 校验报告 ----
    print(f"总动作: {len(meta)}")
    print(f"未映射肌肉术语: {len(unmapped)} {unmapped}")
    print("难度分布:", dict(Counter(x['difficulty'] for x in meta)))
    print("模式分布:", dict(Counter(x['movement_pattern'] for x in meta)))
    print("类型分布:", dict(Counter(x['exercise_type'] for x in meta)))
    print(f"变体族: {len(fam_list)} 个（>=2 成员）")
    print("变体族最大10个:")
    for f_ in fam_list[:10]:
        print(f"  {f_['family_id']} {f_['stem']} x{f_['member_count']}")

if __name__ == "__main__":
    main()