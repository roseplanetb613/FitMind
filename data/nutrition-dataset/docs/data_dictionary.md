# FitMind 营养数据字典

> 版本：v1.0（2026-09-06）
> 数据源：[Kaggle — Global Food & Nutrition Database 2026](https://www.kaggle.com/datasets/ahsanneural/global-food-and-nutrition-database-2026)（Muhammad Ahsan，**CC BY-SA 4.0**）
> 生成方式：`scripts/build_nutrition.py` 一键重建；`scripts/validate_nutrition.py` 校验（13+ 项，失败退出码 1）

***

## 1. 文件清单

| 文件                                       | 说明                                                              |
| ---------------------------------------- | --------------------------------------------------------------- |
| `data/comprehensive_foods_usda.csv`      | 原始：40,000 条 USDA FoodData Central（含微量/健康评分）                     |
| `data/foods_health_scores_allergens.csv` | 原始：4,997 条 Open Food Facts 品牌食品（Nutri-Score/NOVA/Eco-Score/过敏原） |
| `data/foods_allergens.csv`               | 原始：3,332 条 过敏原专项                                                |
| `data/foods_dietary_restrictions.csv`    | 原始：4,997 条 饮食限制                                                 |
| `data/healthy_foods_database.csv`        | 原始：9,028 条 健康食品子集                                               |
| **`data/foods_core.json`**               | **★统一核心集（44,613 条，所有消费入口**）                                     |
| **`data/name_zh.json`**                  | **★食物名中文词表（35,099 个唯一英文名全覆盖，键=小写英文名）**                          |
| `scripts/build_nutrition.py`             | 归一化构建（合并/主键/钳制/幂等）                                              |
| `scripts/build_name_zh.py`               | 中文名生成（术语组合，可复现）                                                 |
| `scripts/validate_nutrition.py`          | 一致性校验                                                           |
| `scripts/inspect.py`                     | 原始 CSV 盘点                                                       |

## 2. `name_zh.json` — 中文食物名

**生成策略（术语组合，`scripts/build_name_zh.py`）**：名称按 `,()/` 分段 → 段优先整段短语（PHRASES）→ 未中按 3/2/1 词最长匹配（TERMS 约 700 词）→ 制备态词前置。

| 覆盖率类 | 占比    | 形态示例                                        |
| ---- | ----- | ------------------------------------------- |
| 全译   | 7.4%  | `kale, raw → 生羽衣甘蓝`、`soy sauce → 酱油`        |
| 部分译  | 82.0% | 主词中文 + 未识词括号附英文：`abiyuch, raw → 生(abiyuch)` |
| 保持英文 | 10.6% | 品牌名/数字/非英文条目（OFF 长尾），按"拿不准不翻"原则保留           |

消费约定：**键 =** **`name.lower()`**，查表未命中回退英文名；不强行翻译品牌与长尾（避免误导）。

## 3. `foods_core.json` — 统一核心集

**统一口径：所有数值按「每 100g」**；原始 CSV 保持不动。

| 字段                       | 类型           | 说明                                                     | <br />        |
| ------------------------ | ------------ | ------------------------------------------------------ | ------------- |
| `food_id`                | string       | 主键：`fdc_<USDA id>` / \`off\_\<md5(name                 | brands) 8位>\` |
| `name`                   | string       | 食品名（英文）                                                | <br />        |
| `source`                 | string       | 来源：`usda` / `openfoodfacts`                            | <br />        |
| `category` / `food_type` | string\|null | 分类（OFF 为 `en:` 标签原文）                                   | <br />        |
| `data_type`              | string       | USDA: Foundation/SR Legacy/Branded；OFF: branded        | <br />        |
| `per_100g`               | object       | 12 项营养素（见 §3.1），越界值已钳制为 null                           | <br />        |
| `serving`                | object       | `{size, unit, household}` 食用份参考（USDA 有）                | <br />        |
| `health_score`           | float\|null  | **作者自定义评分 0-100，仅作参考**（USDA 40,000 条覆盖）                | <br />        |
| `health_score_note`      | string\|null | 评分来源备注                                                 | <br />        |
| `brand` / `ingredients`  | string\|null | OFF 品牌食品才有                                             | <br />        |
| `allergens`              | string\|null | OFF 原始过敏原标签（如 `en:milk, en:nuts`）                      | <br />        |
| `labels`                 | object       | OFF 独有：`{nutriscore, nova, ecoscore}`（A-E / 1-4 / A-E） | <br />        |
| `flags`                  | object       | 6 个过敏布尔（见 §3.2），USDA 记录恒为 false（未审计）                   | <br />        |

### 3.1 per\_100g（12 项）

`calories_kcal` / `protein_g` / `fat_g` / `saturated_fat_g` / `carbs_g` / `fiber_g` / `sugar_g` / `sodium_mg` / `cholesterol_mg` / `vitamin_c_mg` / `calcium_mg` / `iron_mg`

- USDA：含胆固醇与维C/钙/铁；**缺 MUFA/PUFA 细分、缺 GI/GL**

- OFF：无微量营养素；带盐/钠

### 3.2 flags（过敏原布尔）

`contains_gluten` / `contains_dairy` / `contains_nuts` / `contains_soy` / `contains_eggs` / `contains_fish`

> ⚠️ 仅 OFF 品牌食品有真实标记（约 4.6K 条）；USDA 生鲜食品未做过敏标注，flags 全为 false，**不得当作"无过敏原"结论**。

## 3. 数据口径与质量

- 条数：44,613（USDA 40,000 + OFF 4,613，OFF 三文件已按 name|brands 合并去重）

- 热量有效值 41,910 条（93.9%）；QC 钳制掉 273 条越界热量（如 37,600 kcal 脏值 → null）及少量其他越界

- 构建幂等（同一输入重复运行输出一致）；数据可复现

## 4. 已知缺口（对健身智能体的影响）

| 缺口                       | 影响           | 建议                |
| ------------------------ | ------------ | ----------------- |
| `health_score` 作者自定义、无审计 | 引用需标注口径      | 标记为参考值；或自建评分模型    |
| 无 GI/GL、无脂肪细分            | 深度营养方案不足     | 需要时叠加中国食物成分表      |
| USDA 生鲜无过敏数据             | 过敏过滤仅覆盖品牌食品  | 文档已注明；设计上别误读      |
| 中文名 10.6% 保持英文           | 品牌/长尾名混合     | 词表可增量扩充；品牌名不翻是刻意的 |
| 版权                       | CC BY-SA 4.0 | 商用可，须署名 + 衍生品同协议  |

