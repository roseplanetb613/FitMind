# FitMind 健身智能体 · 数据层交接文档

> 交接日期：2026-09-06
> 交接范围：运动数据集 + 增强数据层 + 检索层（本阶段全部产出）
> 下游阶段：训练计划编排、LLM 对话教练、训练记录（**未开始**）

***

## 1. 一句话总结

数据集**完整可交付**：1,324 个动作（10 语言指令 + 动图），已补齐难度/动作模式/肌肉本体/处方/MET/变体族六维增强，带一键重建与一致性校验，检索层已就绪。**版权注意：动作媒体素材 © Gym visual，商用需另行取得授权。**

## 2. 目录结构（已合并，自包含）

```
e:\FitMind\data\exercises-dataset\      # ★整个数据集自包含，可整体拷贝/交接
├── docs\
│   ├── HANDOVER.md                # 本文档
│   └── data_dictionary.md         # 数据字典（字段/枚举/映射全表）
├── data\                          # 8 个 JSON（原始+增强+中文名映射，见数据字典 §1）
├── images\  videos\               # 1,324 缩略图 / 1,324 动图
├── scripts\
│   ├── build_enrichment.py        # ★增强管道（规则引擎，可复现）
│   ├── validate_enrichment.py     # ★一致性校验（13 项）
│   └── status.py / review.py / check.py / debug_puzzles.py  # 盘点与评审辅助
# 消费层（检索/换算/演示）位于项目顶层 e:\FitMind\lib\ 与 e:\FitMind\examples\，数据包内不含代码
├── all_names.txt / build_zh.js / DIFFERENCES.md   # 中文名配套（来自中文增强 fork）
├── index.html / setup.html        # 中文版交互浏览器 / 开发者向导（已替换）
├── README.md / NOTICE.md / LICENSE / .gitignore   # 上游自带
```

## 3. 数据管线（重要：改数据必须走这条路）

> 所有命令均在 `exercises-dataset\` 目录下执行。

```
三步回归： build_enrichment.py → validate_enrichment.py → examples/quickstart.py
```

1. `python scripts/build_enrichment.py` — 读 `exercises.json`，按规则重算增强字段，输出 4 个 JSON
2. `python scripts/validate_enrichment.py` — 13 项检查（id 一致性/术语覆盖/取值白名单/交叉引用/处方/MET），失败退出码 1
3. `python examples/quickstart.py` — 端到端回归（组合过滤/推荐/替换/中文）

**加了新动作/改了原始数据后，必须重跑第 1 步，否则增强数据与原始数据不同步。**

### 3.1 增强规则要点（build\_enrichment.py 内）

- **难度模型**：器械基础分（杠铃/壶铃=2，徒手/哑铃/器械=1）+ 关键词修正（高级词 `snatch/dip/pull-up/weighted` 等 +1，新手词 -1）+ 平衡类 +1 + 机器支撑 -1，截断到 1-3；`MANUAL_DIFF` 表为人工评审覆盖（9 条，最高优先级）

- **动作模式**：词边界关键词匹配（`kw()` 正则，防 `narrow`/`butterfly` 子串误杀），拉伸类关键词最优先

- **变体族**：名 剥器械词+体势词 后精确合并（**已知限制：干名须完全一致**）

## 4. 检索层 API（lib/exercise\_repo.py）

构造：`repo = ExerciseRepo()`（自动定位数据集，一次加载全部 JSON）。

| 方法          | 签名要点                                                                                                       | 说明                                                             |
| ----------- | ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `get`       | `get(id)`                                                                                                  | 完整增强记录（含 `name_zh` 中文名、`muscles_canonical` 归一肌肉、`family_name`） |
| `search`    | `search(text, limit=)`                                                                                     | 动作名子串搜索                                                        |
| `filter`    | `filter(muscle=, equipment=, difficulty=, pattern=, exercise_type=, family=, limit=, sort_by_difficulty=)` | 组合过滤，可缺省任一项                                                    |
| `recommend` | `recommend(muscle, equipment=, difficulty=, count=5, exclude=, include_stretch=False)`                     | 肌肉优先 + 渐进放宽 + 变体去重；默认排除拉伸                                      |
| `siblings`  | `siblings(id)`                                                                                             | 同变体族其他动作（替换建议）                                                 |
| `muscle`    | `muscle('delts')`                                                                                          | 肌肉本体信息（中英文名/区域）——中文名/别名全通                                      |

输入宽容：肌肉支持 规范 id / 原始术语 / 中文名/别名（`delts` ≡ `三角肌` ≡ `shoulders`）；`equipment` 支持 `bodyweight`/`徒手`。

**说明**：`recommend` 是启发式候选集（非个性化），无用户画像输入。

## 5. 数据质量结论与遗留事项

### 已验证 ✓

- 1,324 条全覆盖，10 语言指令完整，媒体无缺失，无空值

- 50 个原始肌肉术语 100% 归一；field 取值全在白名单；MET/处方合法；family 引用有效

### 遗留 (v1 边界，影响低)

1. 变体族按干名精确合并：`row` vs `cable row` 分族；括号标签（`with towel`）会各自成族
2. 难度为规则标定 + 9 条人工覆盖——如需更高精度可逐条 LLM 复核
3. 拉伸处方（20-60s 保持）对 "静态 hold 型" 保守适用；动态热身类可再细分
4. `category/body_part` 与肌肉本体是两套体系（部位 ≠ 肌肉），检索时优先用肌肉本体

### 风险提示

- **媒体版权**：`images/` `videos/` © Gym visual，商用须去 gymvisual.com 自行取授权

- 原始 `exercises.json` 为上游数据，本仓库增强全部落在**独立文件**，升级上游可直接替换

## 6. 交接清单

- [x] 数据文件齐全（6 JSON + 媒体目录）

- [x] 数据字典已交付（`docs/data_dictionary.md`：全部字段/枚举/映射/示例）

- [x] 校验通过可复现

- [x] 检索层 API 可用（quickstart 7 组用例全通过）

- [x] 已知限制与风险已记录

## 7. 建议的下一步

1. **训练计划编排层**：基于 难度×动作模式×器械 出周计划模板（推/拉/腿/核心均衡），输出结构化 JSON 计划
2. **LLM 教练对话**：把 `filter/recommend` 包装成工具（function calling），让大模型按用户目标检索动作 + 组织成计划
3. **FastAPI 服务化**：把检索层暴露为 REST 接口，供 APP/Agent 调用
4. **用户档案**：训练历史/目标 → 个性化处方（需要产品设计先行）

