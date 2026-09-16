# FitMind

一个数据驱动的个人健身 / 饮食决策智能体：**对话式教练 + 3D 肌群恢复视图 + 训练计划闭环**。

它不是"把问题转发给大模型"的壳子——动作、营养、训练科学、运动医学四类知识都先落成可审计的
本地数据集，再由 `lib/` 消费层和检索层供 Agent 使用；大模型只负责**理解你说的话**和**组织措辞**，
数字一律来自查库求和。

```
你：我今天练了胸，明天能练背吗
Agent：（查图谱里 28 块肌群的恢复状态）胸大肌还在恢复窗口内，背阔肌已恢复——
      明天练背没问题。给你排了 4 个动作，第一个是高位下拉。

你：[拍一张午餐照片]
Agent：识别到「番茄炒蛋盖饭」，约 480 克 → 热量 612 kcal / 蛋白 22 g / 脂肪 21 g / 碳水 78 g

你：我有肩周炎，还能推举吗
Agent：（命中运动医学禁忌表）急性期不建议过顶推举。替代方案：……
```

---

## 目录

- [核心能力](#核心能力)
- [架构](#架构)
- [快速开始](#快速开始)
- [HTTP 接口](#http-接口)
- [目录结构](#目录结构)
- [数据资产与许可](#数据资产与许可)
- [几条设计取向](#几条设计取向)
- [已知限制](#已知限制)
- [已知限制](#已知限制)

---

## 核心能力

| 能力 | 说明 | 入口 |
|---|---|---|
| **对话教练** | 7 个技能经注册表路由，LangGraph 编排（Direct / ReAct / PlanExec / ReWOO 四种执行模式按问题动态切换） | `POST /v1/chat`、`/v1/chat/stream` |
| **3D 肌群恢复视图** | 28 块肌群按恢复度着色，几何全部代码生成（零第三方 3D 资产） | `http://<host>:8000/app/` |
| **训练计划** | 周期化编排（分化循环 + 分期），支持对话修改与整计划删除 | `GET /v1/plan`、`POST /v1/plan/delete` |
| **打卡与消歧** | 记录训练；动作名歧义时出候选让用户点选，支持自定义输入 | `POST /v1/checkin/resolve` |
| **食物拍照识别** | VLM 只判"是什么 / 多少克"，营养数字由 `lib/dish_repo` 查库求和——**模型被禁止算热量** | `POST /v1/vision/food` |
| **语音输入** | whisper 转写（可选依赖，见下文） | `POST /v1/asr` |
| **长期记忆** | 双时态用户记忆图谱（Neo4j），偏好 / 伤病 / 目标可写入、可查询、可遗忘 | 随对话自动 |
| **伤痛 guard** | 前置拦截高风险提问，查运动医学禁忌与回归赛场（RTP）阶梯，只做风险分级不做医学结论 | 随对话自动 |

---

## 架构

```
┌─ 呈现层 web/ ──────────────────────────────────────────────┐
│  Vite + TypeScript + three.js（无 UI 框架）                │
│  3D 肌群视图 · 对话面板 · 计划抽屉 · 建档窗口 · 拍照上传    │
│  构建产物 dist/ 由 app/server.py 挂在 /app                 │
├─ 编排层 app/ ──────────────────────────────────────────────┤
│  core/agent.py   Agent 门面                                │
│  core/graph.py   LangGraph StateGraph                      │
│                   guard → classify →                        │
│                     ├─ clarify / clarify_exercise → END    │
│                     ├─ execute ──────────────┐              │
│                     ├─ plan_node → step{i} → aggregate       │
│                     └─ think → act ──────────┘              │
│                                 → validate → render → END  │
│  core/nodes.py   图节点（闭包工厂）+ 条件边路由             │
│  core/router.py  执行模式动态变换 + 意图语义层              │
│  skills/         guard / qa / plan / teach / progress /     │
│                  smalltalk / preference                     │
│  rag/            PG+pgvector 向量检索原语（调用方自行组合   │
│                  图/向量）+ cross-encoder rerank（可降级）  │
│  graph/          Neo4j 记忆图谱（双时态）+ 知识图谱         │
│  runtime/        asr / vision / pipeline / validator        │
│  storage/        私有 SQLite（gitignore，不入库）           │
│  bus/            MessageBus 多智能体契约（留位，未启用）    │
├─ 消费层 lib/ ──────────────────────────────────────────────┤
│  exercise_repo · foods_repo · dish_repo · muscle_map        │
│  progression · recovery · screening · split_cycle           │
│  serving_units · portion_reference · negation               │
├─ 数据层 data/ ─────────────────────────────────────────────┤
│  exercises-dataset · nutrition-dataset · china-food         │
│  dishes · training-science · sports-medicine                │
│  各包含 data/（成品）+ scripts/（幂等构建）+ docs/（数据字典）│
└────────────────────────────────────────────────────────────┘
```

**外部依赖**

| 服务 | 用途 | 是否必需 |
|---|---|---|
| DeepSeek API | 意图分类 / 计划生成 / 措辞渲染 / 食物识别 | 必需 |
| PostgreSQL + pgvector | RAG 向量库（schema `fitness`） | 必需 |
| Neo4j 5 | 记忆图谱 + 知识图谱 | 必需（不可用时相关能力降级） |
| Ollama + `bge-m3` | 查询与语料的稠密向量（1024 维） | 必需 |
| `bge-reranker-base` 权重 | cross-encoder 重排，把 top-1 准确率从 80% 往上抬 | 可选，缺失自动降级 |
| whisper + torch | `POST /v1/asr` 语音转写 | 可选，~4.6 GB 显存 |

---

## 快速开始

### 0. 前置

- Python 3.12+（开发环境实测 3.14）
- Node 20+（仅前端）
- PostgreSQL 16 + [pgvector](https://github.com/pgvector/pgvector)
- [Ollama](https://ollama.com/)，并拉取嵌入模型：`ollama pull bge-m3`
- Docker（跑 Neo4j 用）

### 1. 装依赖

```bash
pip install -r app/requirements.txt
pip install "psycopg[binary]"        # ⚠ 见下方「已知限制」
cd web && npm install && cd ..
```

### 2. 配环境变量

```bash
cp .env.example .env
```

`.env` 已被 gitignore（**绝不入库**）。需要填：

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 密钥 |
| `NEO4J_PASSWORD` | Neo4j 密码，与 `docker-compose.yml` 联动 |
| `DATABASE_URL` | 可选，缺省 `postgresql://postgres@127.0.0.1:5432/postgres` |
| `OLLAMA_BASE_URL` / `OLLAMA_EMBED_MODEL` | 可选，缺省 `http://127.0.0.1:11434` / `bge-m3` |
| `RAG_RERANKER_DIR` | 可选，reranker 权重目录 |

### 3. 起服务

```bash
docker compose up -d          # Neo4j（数据落 D:\vm\docker\neo4j，持久化）
python -m app.rag.ingest      # 建向量库 + 知识图谱（幂等，可重跑）
cd web && npm run build       # 前端产物 → web/dist
python scripts/serve.py       # 后端 127.0.0.1:8000
```

打开 **`http://127.0.0.1:8000/app/`**。

> 注意是 `/app/` 不是 `/`——前端 `base` 与后端挂载点一致（`web/vite.config.ts`）。
>
> 日常验证走构建产物：`web/dist` 由 StaticFiles 按请求读盘，**重建后不用重启后端**，刷新即可。
> `npm run dev`（Vite dev，`:5173`）只在需要热更新时用；它的 CSS 由 JS 注入，首帧有一个
> "整页无样式"的窗口，**这是 dev 的固有行为，不是 bug**。

### 4. 验活

```bash
curl http://127.0.0.1:8000/health
curl "http://127.0.0.1:8000/v1/muscle-map?user_id=local"
```

---

## 检索质量评估

检索质量有一条独立的评估链：五格混淆（TP / WRONG / MISS / TN / LEAK），
标注集在 [`data/rag_eval/queries.jsonl`](data/rag_eval/queries.jsonl)。

```bash
python scripts/eval_rag.py       # 需 Ollama 在线；不在线会 FAIL 而不是 skip（故意的）
```

核心看**证据准（evidence_precision）与错证率**，因为消费点是 `top_k=1`——
挂了错证据比没证据更糟。脚本 docstring 里记着几轮实测结论（含被否决的方案
与"修语料比调阈值有效"的根因），改检索前先读它。

---

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/v1/muscle-map` | 28 项定长肌群状态；无记录显式 `null`；图谱不可用 → 200 + 全 null + `degraded: true` |
| GET | `/v1/muscle-exercises` | 按肌群取动作 |
| GET | `/v1/plan` | 取训练计划 |
| POST | `/v1/plan/delete` | 整计划删除 |
| POST | `/v1/checkin/resolve` | 打卡消歧点选后的补记（也可记偏好） |
| POST | `/v1/chat` | 对话（无状态 + `session_id`） |
| POST | `/v1/chat/stream` | 对话（流式） |
| GET | `/v1/asr/status` | 语音链路探活（零代价，不加载模型） |
| POST | `/v1/asr` | 语音转写（multipart） |
| GET | `/v1/vision/food/status` | 视觉链路探活 |
| POST | `/v1/vision/food` | 食物照片 → 营养数字 |
| POST | `/v1/profile` | 写入用户档案 |
| GET | `/v1/profile/{session_id}` | 读取用户档案 |

---

## 目录结构

```
app/            Agent 编排层（core / skills / rag / graph / runtime / storage / bus）
lib/            消费层：纯读取、只依赖数据、无编排逻辑
data/           数据集：各包含 data/ scripts/ docs/，构建脚本幂等、配套 validate
web/            3D 前端（Vite + TS + three.js）
scripts/        运维脚本：serve / eval_rag / stress_test / 数据回填与审计
examples/       演示：quickstart_*.py、planner_demo.py、cli_chat.py
```

---

## 数据资产与许可

**本仓库的代码与数据是两套不同的授权，请分开看。**

| 资产 | 来源 / 授权 |
|---|---|
| 动作数据集（含 1324 个动作的图片与 GIF） | 代码 MIT（© Hasan Emir Yıldırım）；**媒体版权属 [Gym visual](https://gymvisual.com/)**，经其书面许可按 **180×180 分辨率**再分发。详见 [`data/exercises-dataset/NOTICE.md`](data/exercises-dataset/NOTICE.md)——二次使用请先阅读 Gym visual 的使用条款，并**保留 `© Gym visual — https://gymvisual.com/` 署名** |
| 营养数据集 | 含 USDA 等公开来源，另见 `data/nutrition-dataset/docs/` |
| 中国食物成分表 | 出版社授权 |
| Open Food Facts 中国区 | ODbL（署名 + 共享衍生） |
| 运动医学 / 训练科学 | 未定稿，**全部标记【待审】**，不得作为医学结论使用 |
| **本项目代码** | ⚠ **尚未添加根目录 LICENSE** |

医学内容定位：系统做**风险分级**（红 / 黄 / 绿）与**风险提示**，不做诊断，不给医学结论。
`lib/screening.py` 的 PAR-Q+ 门检问卷取自训练科学数据包，**标记【待审】**，正式发布前需核对官方文本。

---

## 几条设计取向

写在这里是因为它们解释了代码里很多看起来"多余"的防御：

- **诚实数据**：单位统一、QC 钳制、脏值修复；缺数据返回 `None` + 原因，不猜。
- **数字不信模型**：食物识别的 VLM 被 prompt 显式禁止输出热量，营养数字一律查库求和。
  这是硬不变式——`app/runtime/vision.py` 的 prompt 里明写了禁令，是它的第一道防线。
- **措辞与百分比单源**：肌群的 `describe` / `pct` 全部由后端算好，前端只做呈现。
- **全降级**：reranker 权重缺失、Neo4j 掉线、Ollama 抖动——各自退到可用路径并显式标记
  (`degraded: true`)，绝不抛出，绝不静默给错值。
- **数据私有**：用户训练 / 饮食记录落本地私有存储，不入 git。
- **修语料 > 调阈值**：检索质量问题的根因优先在语料侧解决；实测调 `MIN_SCORE` 在
  n=37 的标注集上立不住（结论写在 `scripts/eval_rag.py` 的模块 docstring 里）。

---

## 已知限制

- **`app/requirements.txt` 缺 `psycopg`**：`app/rag/store.py` 依赖它，干净环境
  `pip install -r` 后会在导入期就报错。暂按上文手动补装。
- **根目录无 LICENSE**：推到公开仓库前需要决定授权（数据授权已在各包内单独声明）。
- **语音转写不在 requirements 里**：`openai-whisper` + `torch` 是数 GB 的重依赖，
  故意不拖累纯文本链路；且 8 GB 显存下**同机只能跑一个实例**。
- **reranker 权重不入库**（1.1 GB），缺失时静默降级为稠密排序。
- **多智能体未启用**：`app/bus/` 目前只是契约。
- **运动医学数据全部【待审】**，沿用现有版本。
- **仓库体积约 249 MB**（其中动作媒体 123 MB / 1324 个 GIF），首次 clone 与 push 会比较慢。
