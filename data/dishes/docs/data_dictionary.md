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

`aliases` 是「口语食材名 → food_id」的映射，消费者有三处：

1. `DishRepo.resolve_ingredient`（**主要消费者**）：拆解食材时**先查本表**，未命中才回落
   `foods_repo.search(score_floor=4)`，仍不中记 `missing`。
2. `vision.py` 的兜底接地路径：同上表的同一逻辑，用于 VLM 输出的口语食材名。
3. `validate_dishes.py`：对别名目标**只校验 food_id 存在，不校验宏量完整**（宏量完整性只对
   `recipe[]` 行检查）。

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
`ingredient_aliases.json` 里显式绑到有完整宏量的 `fdc_*` 条目。

**尚有未覆盖词形：`菜油`**（与 `菜籽油` 同义）。它目前 `search` 无命中，会落到
`missing` —— 该食材从配料表消失。注意这是**可见失败**，与上面那类**静默少算热量**
不同；补别名时请指向 `fdc_171410`（同 `菜籽油`）。

## 已知局限

- 配方是生重求和，**不含煎炸吸油与汤汁损耗**。因此 `nutrition_from_recipe()`
  **返回的字典**里 `method` 取值 `recipe_estimated`，与库中实测值严格区分
  （对齐 `lib/portion_reference.py` 的 `method='reference'` 先例）。
  注意这跟本文档上方的 `dishes_core.json` 顶层 `method` 键不是一回事——那个是
  公式字符串，描述"怎么算"，不是"这个值有多可信"。
- 10 道菜覆盖面极小，本波目的是打通链路与钉死 schema。
