# Sports Medicine 数据包 — 数据字典

> 版本 v1.0（2026-09-06）｜定位：运动医学知识符号化（FITT 处方 / 损伤流行病学 / 恢复重返），供计划编排与筛查引擎消费。

## ⚠️ 合规与免责（必读）

- 三表均基于公开指南/综述**起草**，每条 `source` 标注 **【待审】**——**上线前须专业（医师/康复师）审核**

- 流行病学数字为参考区间，非本系统实测；商用发布请引用权威综述原文

- 非医学建议：不作诊断，只做风险提示与恢复阶段参考

## 1. 文件清单

| 文件                                    | 内容                                      |
| ------------------------------------- | --------------------------------------- |
| `data/fitt_prescription.json`         | FITT 运动处方（心肺/抗阻/柔韧：频率·强度·时间·类型 + 强度换算表） |
| `data/injury_epidemiology.json`       | 6 条常见损伤：部位/高发项目/危险因素/预防                 |
| `data/recovery_rtp.json`              | 5 条常见损伤：阶段化康复路径与重返运动时间线                 |
| `scripts/validate_sports_medicine.py` | 三表结构/来源校验                               |

## 2. 设计要点

- **fitt**：可直接入计划编排（心肺 150min/周基线、抗阻 2-4×8-12+组间 2-3min、柔韧 10-30s）；强度换算与 `progression.py`/`screening.py` 的 RPE/%1RM 口径衔接

- **injury\_epidemiology**：供"风险提示"层使用（某一损伤的高发项目/预防动作）；`body_part` 可关联动作/禁忌表

- **recovery\_rtp**：供"伤了能练什么"场景——每阶段含 `allowed/avoid`，可直接渲染成用户提示

## 3. 与其他包的衔接

```
screening.py(禁忌) ← contraindications.json
planner(编排)     ← fitt_prescription.json + exercise_repo + foods_repo
伤后康复提示       ← recovery_rtp.json + injury_epidemiology.json
```

## 4. 已知限制

- 表内为"代表值/共识区间"，个体差异大；越界情况（手术类型、损伤分级）须医嘱

- 暂缺：生物力学参数、专项运动专项量表（FMS 等）——非必需品，可后补

