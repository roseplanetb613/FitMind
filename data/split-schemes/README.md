# split-schemes 数据包

训练编排方案（分化/循环）唯一真相源。**只放数据与校验脚本，业务逻辑在 lib/split_cycle.py。**

## 新增方案

在 `data/split_schemes.json` 的 `schemes` 数组加一行：

- `id` 全局唯一；`aliases` 全局不重复（大小写不敏感）——分类/记忆链路靠它命中
- `cycle` 一个完整循环；`type` ∈ `train|rest`；`train` 必有 `pattern` ∈
  push/pull/squat/hinge/core，复合日用 `extra_patterns`；`rest` 不带 pattern
- 全包恰好一个 `"default": true`

改完跑 `python data/split-schemes/scripts/validate.py` 或
`pytest app/tests/test_split_schemes_data.py`。
