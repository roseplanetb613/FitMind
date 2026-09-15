# 菜品库字段说明

## dishes_core.json

顶层信封：`version` / `note`（数据出处与局限）/ `source` / `method`（**公式字符串**，
说明营养怎么算）/ `dishes`（下面的数组）。

| 字段 | 含义 |
|---|---|
| `dish_id` | 唯一 id，`dish_` 前缀 |
| `name_zh` | 显示名，也是 VLM 精确匹配的键之一 |
| `aliases` | 别名，同样参与精确匹配（不含子串匹配） |
| `category` | 家常菜 / 主食 / 早餐 / 快餐 / 健身餐 |
| `serving_zh` | 人读份量（"一份"/"一碗"） |
| `serving_g` | **配方总生重（克）**，恒等于 `recipe` 各项 `grams` 之和。**≠ 成菜重量**：只算生重，不含汤水与煎炸吸油——如兰州牛肉面「一碗」= 254g 干面+牛肉+香料，实际一碗连汤 500g+ |
| `recipe[].food_id` | 引用 `foods_core.json` / `foods_china.json` / `off_cn.json` 的条目 |
| `recipe[].grams` | 生重克数 |
| `recipe[].name_zh` | 冗余的中文名。**故意冗余**：库里 `name_zh` 不可读（"盐(table)"/"蜂蜜味"），配料表要给人看 |

**本文件不存营养值。** 营养由 `lib/dish_repo.nutrition_from_recipe()` 在 load 期
求和算出。存了就会跟 `foods_core.json` 漂移。

## ingredient_aliases.json

顶层信封：`version` / `note` / `aliases`。

`aliases` 是「口语食材名 → food_id」的映射，**直接消费者有两处**：

1. `DishRepo.resolve_ingredient`（**主要消费者**）：拆解食材时**先查本表**，未命中才回落
   `foods_repo.search(score_floor=4)`，仍不中记 `missing`。
2. `validate_dishes.py`：对别名目标校验**存在且宏量完整**（⑧，错误级，与 `recipe[]` 行的
   ⑤ 同级）——指到 `calories_kcal: None` 的条目会直接报错，不会放过。

`vision.py` **不直接读本表**：它经 `DishRepo.resolve_ingredient` **间接**使用（即上面的
消费者 ①），不构成第三个消费者。

> 给未来的作者：**别照着中文表 `cn_1920xx` 抄食用油别名**。加进来会解析到 `calories_kcal:
> None` 的条目，见下节。

## 食用油的 USDA 规则

**配方与别名表里的食用油，一律引用 USDA `fdc_*` 条目，禁用中文表 `cn_1920xx`。**

理由是实测的、不是洁癖：中国数据集整个植物油类目（`cn_192004` 豆油、`cn_192014` 色拉油、
`cn_192016` 玉米油……）的 `calories_kcal` **全是 `None`**。油在配方里通常是 8–15g、约
70–130 kcal，是一份家常菜热量的可观部分；一旦解析到这类条目，宏量求和时会**静默按 0 计**，
而克数照样进 `total_grams`。结果是卡片少报约 130 kcal 且不留任何痕迹——没有报错，没有
`missing`，数字看起来完全正常。

已实测的坑：`豆油` → `cn_192004`、`玉米油` → `cn_192016`、`色拉油` → `cn_192014`，
三者 `calories_kcal` 均为 `None`；`调和油` 完全无命中。以上词形均已在
`ingredient_aliases.json` 里显式绑到有完整宏量的 `fdc_*` 条目（另有 `菜油` 同 `菜籽油`、
`葵花子油` 同 `葵花籽油`、`椰子油` → `off_645365c7`）。

**仍有无歧义可补的剩余词形**（`茶油` `辣椒油` `棕榈油` `棉籽油` `红花油` `胡麻油`
`麦胚油`）：这些 `search` 只返回那条不可用记录，**没有任何诚实的别名目标**，故不硬塞。
它们由 `DishRepo._usable` 兜住 → 记 `missing`（"这部分未计入，总热量是下界"）。
这是**可见失败**，与上面那类**静默少算热量**不同——别为了"填满表"给它们随便指一个
`None` 条目，那正好把可见失败变回静默失败。

## 已知局限

- 配方是生重求和，**不含煎炸吸油与汤汁损耗**。因此 `nutrition_from_recipe()`
  **返回的字典**里 `method` 取值 `recipe_estimated`，与库中实测值严格区分
  （对齐 `lib/portion_reference.py` 的 `method='reference'` 先例）。
  注意这跟本文档上方的 `dishes_core.json` 顶层 `method` 键不是一回事——那个是
  公式字符串，描述"怎么算"，不是"这个值有多可信"。
- 10 道菜覆盖面极小，本波目的是打通链路与钉死 schema。
