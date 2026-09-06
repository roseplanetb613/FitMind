# FitMind 数据字典

> 版本：v1.1（2026-09-06）
> 覆盖对象：`exercises-dataset/` 下全部数据与增强文件
> 生成方式：`scripts/build_enrichment.py` 一键重建；`scripts/validate_enrichment.py` 校验口径

***

## 1. 文件清单

| 文件                            | 类型          | 大小     | 说明                                  |
| ----------------------------- | ----------- | ------ | ----------------------------------- |
| `data/exercises.json`         | 主数据集        | 17 MB  | 1,324 条动作原始记录（**上游数据，勿手改**）         |
| `data/exercise_metadata.json` | 增强元数据       | 784 KB | 每条动作的训练元数据（难度/模式/处方/MET/变体族）        |
| `data/variant_families.json`  | 变体族         | 38 KB  | 140 个同源动作族                          |
| `data/muscle_ontology.json`   | 肌肉本体        | 5 KB   | 28 个规范肌肉（中英文名/区域/别名）                |
| `data/muscle_mapping.json`    | 归一映射        | 1 KB   | 50 条原始肌肉术语 → 本体 id                  |
| `data/name_zh.json`           | 中文名映射       | 81 KB  | 1,318 条 英文动作名 → 中文名（键为小写英文名）        |
| `data/name_zh.js`             | 中文名映射       | 75 KB  | 同 JSON，`window.NAME_ZH` 供浏览器直接引用    |
| `data/exercises.schema.json`  | JSON Schema | 5 KB   | 上游提供的 exercises 字段定义（Draft 2020-12） |

> 根目录另有中文增强配套：`all_names.txt`（1,324 个英文名清单）、`build_zh.js`（中文名生成脚本，Node.js）、`DIFFERENCES.md`（与上游差异说明）、中文版 `index.html`/`setup.html`。

***

## 2. `exercises.json` — 原始字段

| 字段                  | 类型        | 说明                                          | 示例                                      |
| ------------------- | --------- | ------------------------------------------- | --------------------------------------- |
| `id`                | string    | 唯一编号，4 位数字                                  | `"0001"`                                |
| `name`              | string    | 动作全名（含展示标签）                                 | `"3/4 sit-up"`                          |
| `category`          | string    | 身体部位分类（同 `body_part`）                       | `"waist"`                               |
| `body_part`         | string    | = `category`                                | `"waist"`                               |
| `equipment`         | string    | 所需器械（原始值，27 种）                              | `"body weight"`                         |
| `instructions`      | object    | 10 语言整段说明，键：`en/es/it/tr/ru/zh/hi/pl/ko/fr` | `instructions.zh`                       |
| `instruction_steps` | object    | 同语言键，值为有序分步数组                               | `instruction_steps.zh[0]`               |
| `muscle_group`      | string    | 主要协同肌群（原始术语）                                | `"hip flexors"`                         |
| `secondary_muscles` | string\[] | 其他参与肌群（原始术语）                                | `["hip flexors","lower back"]`          |
| `target`            | string    | 目标肌肉（原始术语）                                  | `"abs"`                                 |
| `media_id`          | string    | 媒体素材引用 id                                   | `"2gPfomN"`                             |
| `image`             | string    | 缩略图相对路径                                     | `images/0001-2gPfomN.jpg`               |
| `gif_url`           | string    | 动画相对路径                                      | `videos/0001-2gPfomN.gif`               |
| `attribution`       | string    | 媒体版权声明                                      | `© Gym visual — https://gymvisual.com/` |
| `created_at`        | string    | ISO 8601 创建时间                               | `2026-03-18T12:31:32Z`                  |

> ⚠️ `target` / `muscle_group` / `secondary_muscles` 是**非规范原始术语**（存在 `traps`/`trapezius` 混用），消费时须经 `muscle_mapping.json` 归一。

***

## 3. `exercise_metadata.json` — 增强字段

记录结构（每条动作一个对象）：

| 字段                     | 类型             | 说明                                                 |
| ---------------------- | -------------- | -------------------------------------------------- |
| `id`                   | string         | 对应 `exercises.json` 的 `id`                         |
| `name`                 | string         | 原始动作名（冗余便于联查）                                      |
| `canonical_name`       | string         | 规范名：剥离括号、`v.2`、`(male)/(female)` 等展示标签             |
| `difficulty`           | int            | 难度 1=新手 / 2=进阶 / 3=高级                              |
| `difficulty_label`     | string         | 难度英文标签（beginner/intermediate/advanced）             |
| `movement_pattern`     | string         | 动作模式，取值见 §3.1                                      |
| `exercise_type`        | string         | 训练类型，取值见 §3.2                                      |
| `equipment`            | string         | 原始器械值（冗余）                                          |
| `normalized_equipment` | string         | 器械归一值（9 类），映射见 §3.3                                |
| `suggested`            | object         | 训练处方：`{sets, reps, rest_sec, duration_min}`，见 §3.4 |
| `met_range`            | \[float,float] | 代谢当量区间 \[低, 高]                                     |
| `met_typical`          | float          | 典型 MET 值                                           |
| `family`               | string\|null   | 变体族 id（`f001`…）；无变体为 null                          |

> 检索层（`lib/exercise_repo.py`）在装配记录时另追加 `name_zh`（中文动作名，来自 `name_zh.json`，缺翻译回退英文原名）与 `muscles_canonical`（`target`/`muscle_group`/`secondary` 归一后的本体 id）。

### 3.1 movement\_pattern 取值（10 个）

| 值         | 含义                      | 判定要点                                                  | 数量  |
| --------- | ----------------------- | ----------------------------------------------------- | --- |
| `push`    | 推类（卧推/推举/俯卧撑/臂屈伸/飞鸟/下压） | press/dip/push-up/fly 等                               | 382 |
| `pull`    | 拉类（划船/引体/下拉/二头弯举）       | row/pull-up/curl/pulldown 等                           | 439 |
| `squat`   | 蹲类（深蹲/腿举/哈克）            | squat/leg press/hack                                  | 116 |
| `hinge`   | 髋铰链（硬拉/臀推/早安/摆动/背伸展）    | deadlift/hip thrust/good morning/swing/hyperextension | 64  |
| `lunge`   | 分腿蹲类（弓步/保加利亚/上台阶）       | lunge/split squat/step-up                             | 19  |
| `core`    | 核心（卷腹/平板/举腿/旋转）         | crunch/plank/leg raise/twist                          | 168 |
| `carry`   | 负重行走                    | carry/farmer/suitcase                                 | 2   |
| `cardio`  | 有氧（跑/跳/划船机/爬楼机）         | run/jump rope/cycling/rowing 等                        | 29  |
| `stretch` | 拉伸柔韧（拉伸/瑜伽/滚轴/活动度）      | stretch/yoga/pose/circle/roll 等关键词                    | 61  |
| `other`   | 其他（提踵/颈/踝等）             | 兜底                                                    | 44  |

### 3.2 exercise\_type 取值（4 个）

| 值                    | 说明                    | 数量  |
| -------------------- | --------------------- | --- |
| `strength_compound`  | 力量复合动作（多关节）           | 663 |
| `strength_isolation` | 力量孤立动作（单关节，弯举/飞鸟/提踵等） | 571 |
| `cardio`             | 有氧                    | 29  |
| `stretch_mobility`   | 拉伸柔韧                  | 61  |

### 3.3 normalized\_equipment（9 类，由原始 27 种归一）

| 归一值           | 覆盖的原始值                                                                   |
| ------------- | ------------------------------------------------------------------------ |
| `body weight` | body weight, assisted                                                    |
| `dumbbell`    | dumbbell                                                                 |
| `barbell`     | barbell, olympic barbell, ez barbell, trap bar                           |
| `kettlebell`  | kettlebell                                                               |
| `cable`       | cable, rope                                                              |
| `machine`     | leverage/smith/sled/skierg/stationary bike/elliptical/stepmill/ergometer |
| `band`        | band, resistance band                                                    |
| `weighted`    | weighted, medicine ball                                                  |
| `other`       | stability ball, bosu ball, roller, wheel roller, hammer, tire, other     |

### 3.4 suggested 处方结构

| 键              | 类型           | 说明             | 示例                    |
| -------------- | ------------ | -------------- | --------------------- |
| `sets`         | string\|null | 建议组数（有氧为 null） | `"3-4"`               |
| `reps`         | string\|null | 建议次数；拉伸为保持时长   | `"3-8"`/`"20-60s 保持"` |
| `rest_sec`     | string       | 组间休息秒数         | `"150-240"`           |
| `duration_min` | string\|null | 有氧建议时长（分钟）     | `"20-40"`             |

处方规则按 模式+类型+难度 推定，属**建议值**，业务层可覆盖。

***

## 4. `muscle_ontology.json` — 肌肉本体（28 条）

字段：`id`（规范标识）/ `name_en` / `name_zh` / `region`（12 个身体区域）/ `aliases`（原始术语别名）

| id                  | 中文        | 英文                    | 区域          |
| ------------------- | --------- | --------------------- | ----------- |
| pectorals           | 胸大肌       | Pectorals             | chest       |
| serratus\_anterior  | 前锯肌       | Serratus Anterior     | chest       |
| trapezius           | 斜方肌       | Trapezius             | back        |
| latissimus\_dorsi   | 背阔肌       | Latissimus Dorsi      | back        |
| rhomboids           | 菱形肌       | Rhomboids             | back        |
| upper\_back         | 上背部       | Upper Back            | back        |
| lower\_back         | 下背部（竖脊肌区） | Lower Back            | back        |
| spine               | 脊柱肌群      | Spine                 | back        |
| levator\_scapulae   | 肩胛提肌      | Levator Scapulae      | back        |
| deltoids            | 三角肌       | Deltoids              | shoulders   |
| rotator\_cuff       | 肩袖肌群      | Rotator Cuff          | shoulders   |
| biceps              | 肱二头肌      | Biceps                | upper\_arms |
| triceps             | 肱三头肌      | Triceps               | upper\_arms |
| forearms            | 前臂肌群      | Forearms              | forearms    |
| glutes              | 臀肌        | Glutes                | glutes      |
| quadriceps          | 股四头肌      | Quadriceps            | upper\_legs |
| hamstrings          | 腘绳肌       | Hamstrings            | upper\_legs |
| calves              | 小腿后群      | Calves                | lower\_legs |
| tibialis\_anterior  | 胫骨前肌      | Tibialis Anterior     | lower\_legs |
| abductors           | 髋外展肌      | Hip Abductors         | hips        |
| adductors           | 髋内收肌      | Hip Adductors         | hips        |
| hip\_flexors        | 髋屈肌       | Hip Flexors           | hips        |
| rectus\_abdominis   | 腹直肌       | Rectus Abdominis      | core        |
| obliques            | 腹斜肌       | Obliques              | core        |
| core                | 核心肌群      | Core                  | core        |
| ankle\_stabilizers  | 踝部稳定肌     | Ankle Stabilizers     | lower\_legs |
| sternocleidomastoid | 胸锁乳突肌     | Sternocleidomastoid   | neck        |
| cardio\_system      | 心肺系统      | Cardiovascular System | cardio      |

**归一约定**（易混点）：`shoulders→deltoids`、`chest→pectorals`、`back→upper_back`、`groin/inner thighs→adductors`、`soleus/ankles→calves`、`hands→forearms`、`lower abs→rectus_abdominis`、`brachialis→biceps`。

***

## 5. `variant_families.json` — 变体族（140 个）

| 字段                 | 类型        | 说明                   |
| ------------------ | --------- | -------------------- |
| `family_id`        | string    | `f001`…按成员数降序编号      |
| `stem`             | string    | 归一化干名（剥离器械/体势词后的动作名） |
| `canonical_name`   | string    | = stem               |
| `movement_pattern` | string    | 族内多数成员的配类            |
| `member_ids`       | string\[] | 成员动作 id              |
| `member_count`     | int       | 成员数                  |

归并规则：动作名剥离 器械词（barbell/dumbbell/cable…）+ 体势词（incline/seated/reverse/one-arm…）后精确匹配。示例族：`f001 press` ×49、`f002 row` ×31、`f003 curl` ×27。

> ⚠️ 限制：干名须**完全一致**才归并，`row` 与 `cable row` 分属不同族；括号标签（`with towel`）不参与剥除会各自成族。

***

## 6. 数据口径与质量基线

- 覆盖：1,324 条动作，10 语言指令，图片/GIF 各 1,324 张，无空值字段

- 肌肉术语：`target`/`muscle_group`/`secondary_muscles` 共 50 个原始术语全部归一，0 遗漏

- 交叉引用：metadata.family ↔ variant\_families 有效；family 内 id 不重复跨族

- MET：区间合法（0 < lo ≤ hi，typical 在区间内）

- 校验命令：`python scripts/validate_enrichment.py`（13 项检查，失败退出码 1）

