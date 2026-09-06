# 🆚 Differences from Original Repository

This document details all differences between this fork and the original [hasaneyldrm/exercises-dataset](https://github.com/hasaneyldrm/exercises-dataset).

> **Original commit reference**: The base version was downloaded around July 2025, when the original repository still included `images/` and `videos/` directories. The original has since removed those directories and updated its README accordingly.

---

## 📋 Summary

| Aspect | Original | This Fork |
|---|---|---|
| Media files (`images/`, `videos/`) | ⚠️ Originally included, later removed | ✅ Preserved (as originally distributed) |
| `image`/`gif_url` in `exercises.json` | ⚠️ Set to `null`, only `media_id` kept | ✅ Original paths preserved |
| UI Language (index.html) | English | Chinese (中文) |
| UI Language (setup.html) | English | Chinese (中文) |
| Exercise name Chinese translations | ❌ None | ✅ 1,319 names translated |
| `data/name_zh.json` | ❌ Not present | ✅ Added |
| `data/name_zh.js` | ❌ Not present | ✅ Added |
| `all_names.txt` | ❌ Not present | ✅ Added |
| `build_zh.js` | ❌ Not present | ✅ Added |
| README.md | Updated (media-free) | Rewritten (Chinese-oriented) |
| DIFFERENCES.md | ❌ Not present | ✅ Added (this file) |

---

## 🔧 Detailed Changes

### 1. Media Files Preserved

**Background**: The original repository **initially included** `images/` and `videos/` directories with all 1,324 thumbnail images and animation GIFs at 180×180 resolution, as well as their paths in `exercises.json` fields (`image`, `gif_url`). The original author **later removed** these directories and set the `image`/`gif_url` fields to `null` (keeping only `media_id`) due to multiple conflicting copyright ownership claims over the exercise media. The current original repository only includes the data layer and developer tooling.

**What this fork does**: This fork was created from the version of the original repository **before** the media was removed. It preserves the complete set of 1,324 thumbnail images and 1,324 animation GIFs — exactly as they were originally distributed — along with their original paths in `exercises.json`.

**File counts**:
- `images/`: 1,324 files, ~12 MB total
- `videos/`: 1,324 files, ~127 MB total

**License**: All media is © [Gym visual](https://gymvisual.com/), redistributed with attribution. See [NOTICE.md](NOTICE.md) for details.

### 2. Chinese UI Overhaul

Both `index.html` and `setup.html` have been completely redesigned with:

#### index.html (动作库浏览器)
- **Language**: Full Chinese UI (`lang="zh-CN"`)
- **Title**: "动作库" (Exercise Library)
- **Layout**: Redesigned with a sidebar + main content area layout
  - Left sidebar: search bar, category filter, equipment filter, target muscle filter
  - Right area: infinite-scroll grid of exercise cards
- **Search**: Real-time search across all 1,324 exercises
- **Exercise cards**: Show thumbnail, English name, **Chinese name** (from `name_zh.js`), category, and equipment
- **Detail modal**: Click any card to see full details including animation GIF, all metadata, and multilingual instructions
- **Design**: Modern, clean design with CSS custom properties, responsive layout

#### setup.html (开发者设置向导)
- **Language**: Full Chinese UI
- **Title**: "开发者设置 · 动作库"
- **Three tabs**:
  1. 数据库设置 (Database Setup) — SQL generation for multiple RDBMS
  2. API 集成 (API Integration) — Code examples in 7 languages
  3. LLM 提示词 (LLM Prompt) — Structured prompts for AI-assisted backend generation
- **Design**: Clean nav-based layout with sticky header

### 3. Chinese Exercise Name Translations

#### `data/name_zh.json`
- JSON object mapping English exercise names → Chinese translations
- **1,319 entries** (99.6% coverage of 1,324 exercises)
- Format: `{ "english name": "中文翻译" }`

Example:
```json
{
  "barbell bench press": "杠铃 卧推",
  "barbell deadlift": "杠铃 硬拉",
  "pull-up": "引体向上"
}
```

#### `data/name_zh.js`
- Same data as `name_zh.json`, but as a JavaScript global variable
- Defines `window.NAME_ZH` for direct browser use without AJAX
- Format: `window.NAME_ZH = { ... }`
- Used by `index.html` to display Chinese exercise names

#### Translation Coverage
- Total exercises: 1,324
- Translated names: 1,319 (99.6%)
- Untranslated: 5 exercises

### 4. Build Tooling

#### `build_zh.js`
- Node.js script for generating Chinese name translations
- Reads `all_names.txt` (list of all English exercise names)
- Uses a comprehensive term-mapping dictionary (~500+ terms) to translate exercise names
- Translation approach: term-by-term substitution with Chinese word order reordering
- Outputs both `data/name_zh.json` and `data/name_zh.js`

To run:
```bash
node build_zh.js
```

#### `all_names.txt`
- Plain text file with all 1,324 English exercise names
- One name per line
- Serves as input for `build_zh.js`
- Also useful as a reference list of all exercises

### 5. Schema Files

`data/exercises.schema.json` is **unchanged** from the original — it still validates the same exercise data structure.

---

## 🔄 What Stayed the Same

The following have **not been modified** from the original:

| File | Notes |
|---|---|
| `data/exercises.json` | Original 1,324 exercise records, unchanged |
| `data/exercises.schema.json` | Original JSON Schema, unchanged |
| `LICENSE` | Original MIT License with media exception |
| `NOTICE.md` | Original media attribution notice |
| Media file contents | All 1,324 images and GIFs are byte-identical to the originals |

---

## 📝 Migration from Original

If you previously used the original repository, here's what you need to know:

### If you only use `exercises.json`
No changes needed. The data file is identical.

### If you use `index.html`
The UI has been completely rewritten in Chinese. If you need the English version, refer to the [original repository](https://github.com/hasaneyldrm/exercises-dataset).

### If you need media files
The original repository no longer distributes `images/` and `videos/`. This fork preserves them.

### If you want Chinese exercise names
Use `data/name_zh.json` or `data/name_zh.js` — these are new additions exclusive to this fork.

---

## 🔗 Related Links

- **Original Repository**: [hasaneyldrm/exercises-dataset](https://github.com/hasaneyldrm/exercises-dataset)
- **Original Data Source**: ExerciseDB v1 by AscendAPI
- **Media Copyright**: [Gym visual](https://gymvisual.com/)
- **LogPress App**: [hasaneyldrm/logpress-public](https://github.com/hasaneyldrm/logpress-public)
