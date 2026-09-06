# Training Science 数据包 — 数据字典

> 版本 v1.0（2026-09-06）
> 定位：渐进超负荷与运动前筛查的**自建规则数据**（公共知识共识符号化，非版权素材搬运）

---

## ⚠️ 合规与免责（必读）

1. **非医学建议**：禁忌表与筛查为风险提示，`contraindications.source` 均标注"【起草待审】"——**上线前须经医师/康复师审核**
2. **PAR-Q+ 为主题意译**：正式商用建议引用加拿大运动生理学会官方中文译本（eparqmed.com）
3. 力量标准为公开基准知识共识（exrx.net 类）的参考值，不作医学/竞赛判定

## 1. 文件清单

| 文件 | 内容 |
| --- | --- |
| `data/rpe_rir.json` | RPE×次数 → %1RM 锚点表 + 递进参数（每 1RIR≈4%） |
| `data/strength_standards.json` | 三大项+推举 的 1RM/体重比 参考区间（性别×水平） |
| `data/contraindications.json` | 6 条常见疾病/损伤 → 危险模式 → 替代 → 来源（红/黄/绿分级） |
| `data/parq_plus.json` | PAR-Q+ 2021 七问门检意译（任一"是"→附表/医生） |

## 2. 消费层（lib/，与 foods/exercise 同层）

### `lib/progression.py` — 渐进超负荷引擎（纯函数）

| 函数 | 说明 |
| --- | --- |
| `e1rm(weight, reps, rir)` | Epley 1RM：`w*(1+(reps+rir)/30)` |
| `evaluate(history, target_reps, target_rir)` | 最近 3 组评估 → `up`（+2.5/5kg）/`hold`/`deload`（-10% 降次） |
| `expected_pct1rm(reps, rir)` | 锚点外推目标强度 %1RM |

单元输出：连续达标→102.5kg 建议；连续未达标→deload 94.5kg×6@RIR3 ✓

### `lib/screening.py` — 筛查 + 禁忌

| 函数 | 说明 |
| --- | --- |
| `parq_gate(answers)` | 7 题 → green（全否）/ yellow（任一"是"+引导附表） |
| `contraindication(cond)` | 按病名查禁忌条目 |
| `plan_check(conditions, patterns)` | 疾病×计划模式交叉：红/黄/绿 + 规避动作 + 替代建议 |

## 3. 设计说明与边界

- **渐进超负荷的"用户历史"不在这包**：`SetRecord` 由调用方持久化（本地 SQLite，私有，不入 git），引擎只做纯计算
- 禁忌映射属**自建人工抽取**（源码标注待审）；PAR-Q 门槛用公开问卷；力量标准表属公共基准符号化
- 引用次序建议：`screening.parq_gate` → `plan_check` → `progression.evaluate` 供计划编排