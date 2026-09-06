# 中国食物成分数据包 — 数据字典

> 版本:v1.0(2026-09-06)
> 数据源:《中国食物成分表》标准版第6版(中国疾病预防控制中心营养与健康所编著,北京大学医学出版社,ISBN 9787565916991)
> 本次数据转录自 GitHub 社区解析版 [Sanotsu/china-food-composition-data](https://github.com/Sanotsu/china-food-composition-data)(manual-fixed 分支,2026-08)

***

## ⚠️ 版权与使用(必读)

- 数据本质是**出版物内容的转录**(来自原书"能量和食物一般营养成分"部分)

- **商用/对外发布须另行取得出版社与疾控中心授权**;当前仅建议用于内部研发与原型

- 来源标注方式:数据包内保留 `raw/` 原始文件与链接,避免二次传导失真

## 1. 文件清单

| 文件                                                        | 说明                                            |
| --------------------------------------------------------- | --------------------------------------------- |
| `raw/repo/json_data_v3_20260825_qwen38max_kimi_k3_fixed/` | 原始 61 个类别 JSON(**勿改**)                        |
| **`data/foods_china.json`**                               | **★标准化核心集(1,677 条,source='china')**           |
| `scripts/build_china.py`                                  | 标准化管道(幂等):`—`→None、字段映射、能量钳制                  |
| `scripts/validate_china.py`                               | 校验(结构/条数/id/范围/extra)                         |
| `scripts/probe.py`                                        | 原始结构探测                                        |
| **`data/off_cn.json`**                                    | **★OFF 中国区品牌食品(826 条,source='off\_cn',ODbL)** |
| `scripts/build_off_cn.py`                                 | OFF 拉取+归一(断点续传,需联网)                           |
| `scripts/validate_off_cn.py`                              | off\_cn 校验                                    |
| `raw/off_cn_raw/`                                         | OFF 原始分页响应(审计用)                               |

## 2. OFF 中国区(`off_cn.json`)

- **来源**:Open Food Facts 官方 API(`countries_tags_en=china`),**ODbL 许可**(商用可,须署名并共享衍生,见 <https://opendatacommons.org/licenses/odbl/>)

- **数量:826 条**(2026-09-06 拉取;OFF 全量中国区约 1,669 条,**本次受 OFF 限流仅获约 50%**)

- 字段:品牌/成分/过敏原 6 布尔推断/营养评分(Nutri-Score/NOVA)/每 100g

- **补齐方式**:限流解除后重跑 `python scripts/build_off_cn.py`(断点续传,重复全量拉取代价小)

## 3. 标准化口径

- **单位:每 100g 可食部**(另有 `extra.edible` 可食部分%供换算)

- **缺失标记** **`—`** **→ None**

- 能量 kcal/kJ **双单位自带**(数据源自带,无需 kJ 修复);按 energyKCal 取值并做 0-2000 钳制

- 主键:`cn_<foodCode>`(官方编码);少量 code 带识别字母(如 102101x)保持原样

- 中文名天然自带:`name` 与 `name_zh` 均为中文;`food_id=cn_*`

### 2.1 `per_100g`(12 项,对齐 foods\_core)

`calories_kcal`/`protein_g`/`fat_g`/`carbs_g(CHO)`/`fiber_g(dietaryFiber)`/`sugar_g(**恒为 None,无糖细分**)/`sodium\_mg(Na)`/`cholesterol\_mg`/`vitamin\_c\_mg`/`calcium\_mg(Ca)`/`iron\_mg(Fe)\`

### 2.2 `extra`(成分表独有字段)

`edible`(可食部%/100)、`water`(水分g)、`ash`(灰分g)、`energy_kj`、`vitamin_a`、`carotene`(胡萝卜素µg)、`retinol`(视黄醇µg)、`thiamin`(B1mg)、`riboflavin`(B2mg)、`niacin`(烟酸mg)、`vitamin_e`(合计mg)、`potassium_mg(K)`、`phosphorus_mg(P)`、`magnesium_mg(Mg)`、`zinc_mg(Zn)`、`selenium_ug(Se)`、`copper_mg(Cu)`、`manganese_mg(Mn)`

## 3. 数据口径与质量

- 条数:1,677(61 个类别文件合并);热量有效 1,643(98%)'

- 分类:文件名解析 `merged_<大类>-<子类>.json` → `category`/`food_type`

- 真实极端值保留(如猪脑胆固醇 2571mg/100g、高钙食物 >7000mg/100g),校验范围已按真实值放宽

- `flags` 全 false(**本表无过敏数据,不得当作"无过敏原"**);`serving` 无官方份量(可用 `extra.edible` 折算)

## 4. 已知限制

| 限制                             | 影响           | 建议              |
| ------------------------------ | ------------ | --------------- |
| 无糖细分、无 GI(TODO 有独立 GI 文件可后续并入) | 精细营养不足       | 需要时接原仓库 GI JSON |
| 47 条 foodCode 带识别标记(x)         | 极少数,影响 id 美观 | 可后续人工核对         |
| 仅"一般营养成分"表                     | 无氨基酸/脂肪酸明细   | 原书表二/表三未转录      |
| 版权未授权商用                        | 发布风险         | 商用前向出版社确认       |

