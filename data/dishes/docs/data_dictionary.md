# 菜品库字段说明

## dishes_core.json

| 字段 | 含义 |
|---|---|
| `dish_id` | 唯一 id，`dish_` 前缀 |
| `name_zh` | 显示名，也是 VLM 精确匹配的键之一 |
| `aliases` | 别名，同样参与精确匹配（不含子串匹配） |
| `category` | 家常菜 / 主食 / 早餐 / 快餐 / 健身餐 |
| `serving_zh` | 人读份量（"一份"/"一碗"） |
| `serving_g` | 该份量的克数，恒等于配方总重 |
| `recipe[].food_id` | 引用 `foods_core.json` / `foods_china.json` / `off_cn.json` 的条目 |
| `recipe[].grams` | 生重克数 |
| `recipe[].name_zh` | 冗余的中文名。**故意冗余**：库里 `name_zh` 不可读（"盐(table)"/"蜂蜜味"），配料表要给人看 |

**本文件不存营养值。** 营养由 `lib/dish_repo.nutrition_from_recipe()` 在 load 期
求和算出。存了就会跟 `foods_core.json` 漂移。

## ingredient_aliases.json

`aliases` 是「口语食材名 → food_id」的映射，消费者有两处：`validate_dishes.py`
（校验引用的 food_id 存在且宏量完整）与 `vision.py` 的兜底接地路径。

## 已知局限

- 配方是生重求和，**不含煎炸吸油与汤汁损耗**，故 `method` 标 `recipe_estimated`，
  不冒充实测值。
- 10 道菜覆盖面极小，本波目的是打通链路与钉死 schema。
