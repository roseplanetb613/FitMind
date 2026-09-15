# -*- coding: utf-8 -*-
"""菜品库：菜品 = 引用现有食材的配方。营养由求和算出，不存数字。

**核心不变式：营养数字只有一个来源——配方求和。** 菜品库命中的路径与 VLM 现场
拆食材的兜底路径走的是**同一个** `nutrition_from_recipe()`：一个菜品是一份已知
配方，VLM 拆食材是现场生成一份配方，二者同构。

对齐 `lib/portion_reference.py` 的先例：业务层算出来的值用 `method` 与库中实测值
**严格区分**，不冒充实测。

**舍入策略：一律 `round(x, 2)`（Python 内置，IEEE754 就近舍入），不做十进制进位。**
这条必须写明是因为它踩过一次：测试里手算 `99.9 × 15 / 100 = 14.985` 写成期望值
`14.99`，而 `round` 给出 `14.98`（该乘积的 float64 值略低于 14.985）。当时没人
规定过按哪种舍入，于是"实现错了"和"测试写错了"看起来一模一样。卡片只显示到整数
或一位小数，两位精度远超需要，故取内置 `round`，不为它引 Decimal。

**落点只有一个：`_round2()`。** 所有需要两位精度的地方都走它（`_scale` 也委托
给它），所以改精度只需要改这一处——想改精度的人请先确认这里仍是唯一出口。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DISH_DIR = Path(__file__).resolve().parent.parent / "data" / "dishes" / "data"

MACROS = ("calories_kcal", "protein_g", "fat_g", "carbs_g")
ALLERGEN_KEYS = ["contains_gluten", "contains_dairy", "contains_nuts",
                 "contains_soy", "contains_eggs", "contains_fish"]


def _present(values):
    """求和：全缺返回 None；否则跳过缺值相加。

    **缺值不能当 0**（spec §2 实测三）：`cn_1920xx` 植物油 19 条的
    `calories_kcal` 全是 None，当 0 会让一道红烧肉静默少算 ~130kcal ——
    这比"算不出"更坏，因为它安静地给出一个偏低的数字。

    ⚠ **判据是"列表非空"，不是"和是否为真"**。`_present([0.0, None])` 必须返回
    `0.0` 而不是 `None` —— 食盐的热量是**真 0**，不是缺值，两者在卡片上显示
    不同（`0` vs `—`）。写成 `sum(got) or None` 就会把这个真 0 吞成缺值。
    """
    got = [v for v in values if v is not None]
    return sum(got) if got else None


def _round2(value):
    """None 穿透 + 一律 `round(x, 2)`。**唯一的舍入落点**。"""
    return None if value is None else round(value, 2)


def _scale(value, factor):
    """按比例缩放再舍入；None 穿透。舍入统一委托给 `_round2`。"""
    return None if value is None else _round2(value * factor)


def nutrition_from_recipe(recipe, foods_repo) -> dict:
    """配方 → 营养。**唯一实现**，菜品库与 VLM 兜底共用。

    recipe 每项：`{"food_id": str|None, "name_zh": str, "grams": float}`。
    `food_id` 为 None 表示未命中的食材，进 `missing` 且**不计入总重**
    （计入会稀释 per_100g）。

    `grams` 的契约：**调用方保证已是数值**。本函数只做 `float(...)` 取用，不再
    校验——Task 3 的 `recipe_from_ingredients` 负责在构造配方时把克数转成数值
    并滤掉拿不到克数的项。传进非数值（如字符串）会 `ValueError`，那是调用方的
    契约破坏，不在这里兜。
    """
    breakdown, missing, incomplete = [], [], []
    totals = {m: [] for m in MACROS}
    allergens: set[str] = set()
    total_grams = 0.0

    for it in recipe:
        grams = float(it.get("grams") or 0)
        fid = it.get("food_id")
        rec = foods_repo.get(fid) if fid else None
        if rec is None:
            missing.append({"name": it.get("name_zh") or fid or "未知食材",
                            "grams": grams})
            continue

        total_grams += grams
        per100 = rec.get("per_100g") or {}
        row = {"food_id": fid,
               "name_zh": it.get("name_zh") or rec.get("name_zh") or fid,
               "grams": grams}
        lack = []
        for m in MACROS:
            v = per100.get(m)
            if v is None:
                lack.append(m)
                totals[m].append(None)
                row[m] = None
            else:
                val = v * grams / 100.0
                totals[m].append(val)
                row[m] = _round2(val)
        row["incomplete"] = lack
        if lack:
            incomplete.append({"food_id": fid, "name_zh": row["name_zh"],
                               "missing_fields": lack})
        for k in ALLERGEN_KEYS:
            if (rec.get("flags") or {}).get(k):
                allergens.add(k)
        breakdown.append(row)

    per_serving = {m: _round2(_present(v)) for m, v in totals.items()}
    # per_100g 按**配方总重**算，不按 serving_g —— validate 允许二者 ≤20% 偏差，
    # 用 serving_g 会引入一个说不清来源的误差（spec §4.2）。
    # 总重为 0（空配方 / 全部未命中）时没有"每 100g"可言，给 None；去掉这个
    # 守卫就是 100.0 / 0 的 ZeroDivisionError，而 Task 3 的
    # recipe_from_ingredients 在食材全没解析出来时正好返回 []。
    factor = 100.0 / total_grams if total_grams > 0 else None
    per_100g = {m: (_scale(_present(v), factor) if factor else None)
                for m, v in totals.items()}

    return {"per_serving": per_serving, "per_100g": per_100g,
            "breakdown": breakdown, "missing": missing,
            "incomplete": incomplete, "allergens": sorted(allergens),
            "total_grams": _round2(total_grams),
            "method": "recipe_estimated"}


def _norm_dish(s) -> str:
    """菜品名归一：去括号内容、去空白、小写。

    归一存在的理由是**查询侧**：库里的 10 个菜名与 92 个别名键**都不带括号**，
    但用户/VLM 报上来的菜名常带限定词——"红烧肉（家常版）""兰州牛肉面【清汤】"。
    不把括号内容削掉，这些名字就永远匹配不上库里的"红烧肉""兰州牛肉面"。

    （反例预警：带括号的"猪肉（奶面）［硬五花］"是**食材表**记录，不是菜名。
    别拿它给本函数当理由——本函数只作用在菜名上，那是条查不到证据的借口。）

    括号开闭都收全角（`［］` U+FF3B/U+FF3D）：VLM 输出用哪种宽度无从预知，
    多收一种不花钱，漏掉却会让同一句话时灵时不灵。"""
    s = re.sub(r"[（(\[【［][^）)\]】］]*[）)\]】］]", "", str(s or ""))
    return re.sub(r"\s+", "", s).strip().lower()


class DishRepo:
    """菜品库。只读，无状态（除装配结果）。

    `resolve_ingredient` 的优先级是实测逼出来的：别名表 > search。
    反过来的话「食用油」会落空（实测 0 命中）。
    """

    def __init__(self, foods_repo, dish_dir: Path = DISH_DIR):
        self.foods_repo = foods_repo
        self._dir = Path(dish_dir)

        def _read(name):
            with open(self._dir / name, encoding="utf-8") as f:
                return json.load(f)

        self.by_id = {d["dish_id"]: d for d in _read("dishes_core.json")["dishes"]}
        self.ingredient_aliases = _read("ingredient_aliases.json")["aliases"]
        # 归一索引：一次建好，匹配是 O(1)
        self._name_index = {}
        for d in self.by_id.values():
            for n in [d["name_zh"], *(d.get("aliases") or [])]:
                self._name_index.setdefault(_norm_dish(n), d)

    def get(self, dish_id: str):
        return self.by_id.get(dish_id)

    def match_exact(self, name: str):
        """只认**逐字相等**（去括号去空白）。不做子串、不做前缀。

        理由见 spec §2 实测一：模糊匹配在菜品上会静默错配（"红烧排骨"命中
        "康师傅红烧排骨面"），错配比落空更坏——落空会走空结果分支，错配会
        安静地给出错数字。宁可走兜底路径，也不猜一个相近菜名。
        """
        key = _norm_dish(name)
        return self._name_index.get(key) if key else None

    def nutrition(self, dish_id: str) -> dict:
        return nutrition_from_recipe(self.by_id[dish_id]["recipe"], self.foods_repo)

    @staticmethod
    def _usable(rec) -> bool:
        """这条记录能不能拿来算营养。

        **`calories_kcal` 为 None 的条目一律判不可用**，而不是"算了但标记缺值"。
        实测（Task 1 评审发现）：`cn_1920xx` 植物油 19 条的 `calories_kcal` 全是
        None，而别名表是按**口语名**匹配的——「豆油」「玉米油」「色拉油」都会落到
        那里。一条 15g 的油按 0 kcal 计入却照样进 `total_grams`，会让番茄炒蛋从
        349 掉到 216（−38%），而且卡片上看不出任何异常。

        宁可判 `missing`（"这部分没算进去，总热量是下界"），也不要给一个安静偏低
        的数字 —— 后者是比"算不出"更坏的失败。

        **闸门只卡 `calories_kcal` 一项，是刻意的，别扩到四宏量**：另有 751 条
        （1.59%）记录能过这道闸，但 protein/fat/carbs 里有一个是 None。扩成四项
        会连「鸡胸肉」一起拒掉（它的碳水本就≈0，故为 None），整份食材直接丢失
        —— 那比少一个宏量字段更坏。
        """
        return rec is not None and (rec.get("per_100g") or {}).get(
            "calories_kcal") is not None

    def resolve_ingredient(self, name: str):
        """口语食材名 → `(food_id, 记录)`。查不到返回 `(None, None)`，**不猜**。

        ① 别名单源（实测必需：「食用油」在 `search()` 里 0 命中）
        ② `search(score_floor=4)` —— 只收精确/前缀，**不收子串**。默认
           `score_floor=1` 会放进子串命中，实测会把「食盐」匹到「烘烤豌豆零食盐」
        ③ 都没有 → `(None, None)`，调用方记 `missing`

        ①② 命中的条目都要过 `_usable`。②取 `limit=3` 是为了让排在前面的
        不可用条目能被跳过，而不是直接放弃这个食材。

        注：`hits[0]` 不可用时，这里接受的是**排名更后**的命中。实测探过 69 例，
        被接受的替代项无一例外是**同一食材的另一个来源**（「油花生」→ 花生油、
        「椰子油」→ 椰子油），故不必为此加宽或收紧 `limit`。
        """
        key = str(name or "").strip()
        if not key:
            return None, None
        fid = self.ingredient_aliases.get(key)
        if fid:
            rec = self.foods_repo.get(fid)
            if self._usable(rec):
                return fid, rec
        for hit in self.foods_repo.search(key, limit=3, score_floor=4):
            if self._usable(hit):
                return hit["food_id"], hit
        return None, None

    def recipe_from_ingredients(self, ingredients) -> list:
        """VLM 拆出的食材 → 配方（`food_id` 可解析的填上，解析不了的置 None）。"""
        out = []
        for it in ingredients or []:
            name = (it or {}).get("name") or ""
            grams = (it or {}).get("grams")
            try:
                grams = float(grams)
            except (TypeError, ValueError):
                continue
            if grams <= 0:
                continue
            fid, _ = self.resolve_ingredient(name)
            out.append({"food_id": fid, "name_zh": name, "grams": grams})
        return out
