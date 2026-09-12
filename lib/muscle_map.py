# -*- coding: utf-8 -*-
"""肌群状态图（确定性 SVG，纯 stdlib，零依赖）。

**为什么先做 2D 网格而非 3D**：真正的难点是恢复模型、数据一致性、视觉语义三件事，
都与渲染无关。用网格即可验证；解剖图/3D 网格只是最后换一层皮，且会引入资产授权问题。

**三条可验证的视觉约束（不是审美偏好）**：

1. **单色相明度**（`HUE` 单一色相 + 明度随恢复度变化）——**不用红→绿渐变**：
   红绿色盲是最常见的色觉障碍类型，"红=累/绿=恢复"对这批人完全失效。
2. **数值常驻**：每格都印恢复度数字——颜色不可依赖时（色觉障碍/黑白打印/深色模式）
   信息仍在，也避免"看起来红就是坏了"的误读。
3. **"未知"必须与"恢复 100%"可区分**：没有记录 ≠ 完全恢复。未知态不填色且走虚线边框
   + `is-unknown` class，与"恢复满"的浅色实心格在形状上就不同（不只靠颜色）。

另：输出**逐字确定**（无时间戳、无随机、键排序），可快照测试——与全仓 stub 可复现一致。
"""
from __future__ import annotations

# 单一色相（蓝）。改色相即可整体换主题，但**不得引入第二个色相做红绿对照**。
HUE = 210
# 明度区间：恢复满 → 浅；刚练完 → 深（同一色相内变化，色觉障碍下仍可辨明暗）
LIGHT_MAX, LIGHT_MIN = 90, 46

_CELL_W, _CELL_H, _COLS, _PAD = 132, 54, 4, 12
_LABEL_H = 34


def _fill(recovery: float) -> str:
    """恢复度 → 单色相明度填充。"""
    r = max(0.0, min(1.0, float(recovery)))
    light = LIGHT_MIN + (LIGHT_MAX - LIGHT_MIN) * r
    return f"hsl({HUE}, 58%, {light:.0f}%)"


def _text_color(recovery: float) -> str:
    """深底用白字、浅底用深字（保证对比度，不依赖色觉）。"""
    return "#0b1b2b" if float(recovery) >= 0.55 else "#ffffff"


def labels_from_repo() -> dict:
    """肌群 id → 中文名（取本体 `name_zh`，如 pectorals→胸大肌）。

    仓不可用 → {}（调用方回落显示 id），与本仓"降级不阻断主链路"一致。"""
    try:
        from app.runtime.repos import exercise_repo
        return {m["id"]: (m.get("name_zh") or m["id"])
                for m in exercise_repo().ontology}
    except Exception:
        return {}


def display_name(label: str) -> str:
    """展示名：**去掉括号补充**（'下背部（竖脊肌区）'→'下背部'）——格宽有限，
    括号里的解剖细分放在 aria/title 里，不挤占主标签。"""
    for ch in ("（", "("):
        i = label.find(ch)
        if i > 0:
            label = label[:i]
    return label.strip()


def render(states: dict, *, labels: dict | None = None,
           title: str = "肌肉状态（编排参考）") -> str:
    """{muscle: state|None} → SVG 字符串。state 见 `lib/recovery.recovery_map`。

    `labels`：{muscle_id: 中文名}，缺省回落到肌群 id（见 `labels_from_repo`）。
    未出现在 states 里的肌群**不臆造**；显式传 None（或 `has_record=False`）渲染为未知态。"""
    labels = labels or {}
    items = sorted((states or {}).items(), key=lambda kv: kv[0])
    rows = (len(items) + _COLS - 1) // _COLS or 1
    w = _PAD * 2 + _COLS * _CELL_W
    h = _LABEL_H + _PAD * 2 + rows * _CELL_H
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
           f'viewBox="0 0 {w} {h}" role="img" aria-label="{_esc(title)}">',
           f'<rect width="{w}" height="{h}" fill="#f7f9fc"/>',
           f'<text x="{_PAD}" y="{_LABEL_H - 8}" font-family="sans-serif" '
           f'font-size="15" fill="#0b1b2b">{_esc(title)}</text>']
    for i, (muscle, st) in enumerate(items):
        cx = _PAD + (i % _COLS) * _CELL_W
        cy = _LABEL_H + _PAD + (i // _COLS) * _CELL_H
        known = bool(st) and bool(st.get("has_record", True))
        if known:
            rec = float(st.get("recovery", 1.0))
            fill, tcol = _fill(rec), _text_color(rec)
            stroke = ' stroke="#0b1b2b" stroke-width="0.6"'
            cls, value = "is-known", f"{int(round(rec * 100))}%"
        else:
            fill, tcol = "none", "#5b6b7c"      # 不填色 → 与"恢复满"的浅色实心格形状不同
            stroke = ' stroke="#8a99a8" stroke-width="1.2" stroke-dasharray="5 3"'
            cls, value = "is-unknown", "无记录"
        full = labels.get(muscle) or muscle
        out.append(f'<rect class="{cls}" data-muscle="{_esc(muscle)}" x="{cx}" '
                   f'y="{cy}" width="{_CELL_W - 8}" height="{_CELL_H - 8}" rx="6" '
                   f'fill="{fill}"{stroke}><title>{_esc(full)}</title></rect>')
        out.append(f'<text class="{cls}-label" x="{cx + 10}" y="{cy + 21}" '
                   f'font-family="sans-serif" font-size="11" fill="{tcol}">'
                   f'{_esc(display_name(full))}</text>')
        out.append(f'<text class="{cls}-value" x="{cx + 10}" y="{cy + 39}" '
                   f'font-family="sans-serif" font-size="14" font-weight="bold" '
                   f'fill="{tcol}">{_esc(value)}</text>')
    out.append('<text x="%d" y="%d" font-family="sans-serif" font-size="10" '
               'fill="#5b6b7c">深浅=恢复度（同一色相明度，非红绿）；'
               '虚框=无记录，与"恢复满"不同</text>'
               % (_PAD, h - 6))
    out.append("</svg>")
    return "\n".join(out)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
