# FitMind

一个数据驱动的个人健身 / 饮食决策智能体：**对话式教练 + 3D 肌群恢复视图 + 训练计划闭环**。

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-StateGraph-1C3C3C)
![three.js](https://img.shields.io/badge/three.js-r186-000000?logo=threedotjs&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-5-4581C3?logo=neo4j&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)

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

- [界面](#界面)
- [核心能力](#核心能力)
- [架构](#架构)
- [编排流程](#编排流程)
- [快速开始](#快速开始)
- [检索质量评估](#检索质量评估)
- [HTTP 接口](#http-接口)
- [目录结构](#目录结构)
- [数据资产与许可](#数据资产与许可)
- [几条设计取向](#几条设计取向)
- [已知限制](#已知限制)

---

## 界面

**主界面** —— 28 块肌群按恢复度着色（冷 = 恢复好，暖 = 需休息）；左上切换视图与显示开关，
左下是恢复度图例，右侧是常驻的对话面板。

![主界面：3D 肌群恢复视图与对话教练](assets/screenshots/01-overview.png)

**打开标签** —— 每块肌群显示中文名与恢复百分比。措辞与数字全部由后端算好
（`pct` / `describe`），前端只做呈现。

![肌群标签：中文名与恢复百分比](assets/screenshots/02-labels.png)

**对话** —— 问「我今天练了胸，明天能练背吗」，答案依据图谱里各肌群的真实恢复状态；
动作卡带示例图、器械与难度中文名，卡片底部是媒体署名。

![对话：肌群恢复问答与动作卡片](assets/screenshots/03-chat.png)

**训练计划** —— 周期化编排（分化循环 + 分期）：每天的动作、组次与组间休息，
以及当日营养目标（热量与三大营养素）。

![训练计划：四天分化与营养目标](assets/screenshots/04-plan.png)

**背面视图** —— 与前面对称的另一套肌群，共享同一份恢复状态。

![背面视图](assets/screenshots/05-back.png)

> 截图取自本地运行的实例（`/app/?user_id=demo`）。其中的训练记录、恢复度、计划与营养目标
> 都是图谱里的**真实数据**，没有为了配图摆拍；`demo` 是一份 6 次训练的演示档案。
> 想复现同样的视图，把记录写进去、用 `?user_id=<你的 id>` 打开即可。
> 注意线框（无记录）与"已恢复"在界面上是两种不同的状态——这条区分是有意保留的。

---

## 核心能力

| 能力 | 说明 | 入口 |
|---|---|---|
| **对话教练** | 7 个技能经注册表路由，LangGraph 编排（Direct / ReAct / PlanExec / ReWOO 四种执行模式按问题动态切换） | `POST /v1/chat`、`/v1/chat/stream` |
| **3D 肌群恢复视图** | 28 块肌群按恢复度着色；几何来自**真实解剖模型**（Z-Anatomy，CC BY-SA 4.0，见下方许可表） | `http://<host>:8000/app/` |
| **训练计划** | 周期化编排（分化循环 + 分期），支持对话修改与整计划删除 | `GET /v1/plan`、`POST /v1/plan/delete` |
| **打卡与消歧** | 记录训练；动作名歧义时出候选让用户点选，支持自定义输入 | `POST /v1/checkin/resolve` |
| **食物拍照识别** | VLM 只判"是什么 / 多少克"，营养数字由 `lib/dish_repo` 查库求和——**模型被禁止算热量** | `POST /v1/vision/food` |
| **语音输入** | whisper 转写（可选依赖，见下文） | `POST /v1/asr` |
| **长期记忆** | 双时态用户记忆图谱（Neo4j），偏好 / 伤病 / 目标可写入、可查询、可遗忘 | 随对话自动 |
| **伤痛 guard** | 前置拦截高风险提问，查运动医学禁忌与回归赛场（RTP）阶梯，只做风险分级不做医学结论 | 随对话自动 |

---

## 架构

依赖**单向**：`web` 经 HTTP 调 `app`，`app` 调 `lib`，`lib` 只读 `data`。

![四层结构与单向依赖](assets/diagrams/04-architecture-layers.svg)

各层职责（清单按实际目录核对过）：

| 层 | 模块 | 职责 |
|---|---|---|
| **呈现** `web/` | Vite + TypeScript + three.js | 无 UI 框架；3D 肌群视图 · 对话面板 · 计划抽屉 · 建档 · 拍照上传。构建产物 `dist/` 由 `app/server.py` 挂在 `/app` |
| **编排** `app/` | `core/agent.py` | Agent 门面：会话、图外前置（记忆抽取 / 档案投影）、出图后组装结构化结果 |
| | `core/graph.py` | LangGraph `StateGraph` 装配 —— 连边见 [编排流程](#编排流程) |
| | `core/nodes.py` | 图节点（闭包工厂）+ 条件边路由 |
| | `core/router.py` | 执行模式动态变换 + 意图语义层 |
| | `skills/` | `guard` / `qa` / `plan` / `teach` / `progress` / `smalltalk` / `preference` |
| | `config/` | 各链路的 JSON 配置：路由 / 意图例句 / LLM / 图谱 / ASR / 视觉 |
| | `rag/` | PG+pgvector 检索原语（调用方自行组合图与向量）+ cross-encoder rerank（可降级） |
| | `graph/` | Neo4j 记忆图谱（双时态）+ 知识图谱 |
| | `runtime/` | asr / vision / pipeline / validator |
| | `storage/` | 私有 SQLite（gitignore，不入库） |
| | `bus/` | MessageBus 多智能体契约（留位，未启用） |
| **消费** `lib/` | 12 个模块 | 纯只读、只依赖数据、无编排逻辑。4 个仓库（`exercise_repo` / `foods_repo` / `dish_repo` / `muscle_map`）+ `parts` / `progression` / `recovery` / `screening` / `split_cycle` / `serving_units` / `portion_reference` / `negation` |
| **数据** `data/` | 9 个数据集 | `exercises-dataset` · `nutrition-dataset` · `china-food` · `dishes` · `training-science` · `sports-medicine` · `split-schemes` · `rag_eval` · `stress_v3`；各含 `data/`（成品）+ `scripts/`（幂等构建）+ `docs/`（数据字典） |

### 外部依赖

| 服务 | 用途 | 是否必需 |
|---|---|---|
| DeepSeek API | 意图分类 / 计划生成 / 措辞渲染 / 食物识别 | 必需 |
| PostgreSQL + pgvector | RAG 向量库（schema `fitness`） | 必需 |
| Neo4j 5 | 记忆图谱 + 知识图谱 | 必需（不可用时相关能力降级） |
| Ollama + `bge-m3` | 查询与语料的稠密向量（1024 维） | 必需 |
| `bge-reranker-base` 权重 | cross-encoder 重排，把 top-1 准确率从 80% 往上抬 | 可选，缺失自动降级 |
| whisper + torch | `POST /v1/asr` 语音转写 | 可选，~4.6 GB 显存 |

---

## 编排流程

下面三张图按 `app/core/graph.py` 的实际连边画。

**一次对话的完整链路** —— 注意记忆抽取与档案投影发生在图**之前**（图外前置），
出图后才组装结构化结果并写会话留档：

![一次对话请求的全链路](assets/diagrams/01-request-lifecycle.svg)

**图内的两个决策点** —— `guard` 与 `classify` 是仅有的会「提前收束」的地方：
前者命中高风险提问就直接给安全提示（reply 在 guard 里写就），
后者判不出意图、或打卡里的动作名有歧义时出反问 / 候选。
两条短路都**不经过 render** —— 省掉一次措辞生成的 token：

![图内路由与两条短路出口](assets/diagrams/02-graph-routing.svg)

**四种执行模式的形状** —— 走哪一种由 `classify` 判出的复杂度决定，
映射写在 `app/config/router_config.json`，是**数据驱动**的，不是散在代码里的分支：

| 复杂度 | 何时判为 | 模式 | 形状 |
|---|---|---|---|
| simple | 默认兜底 | `direct` | 单步执行 |
| medium | progress / teach | `react` | `think → act` 循环，直到不需要再调工具 |
| complex | plan | `plan_exec` | `plan_node` → 逐步顺序执行 → `aggregate` |
| batch | 命中「请…一周…计划」 | `rewoo` | `plan_node` → **4 槽并行** → `aggregate` |

![四种执行模式的形状](assets/diagrams/03-execution-modes.svg)

> 后两种模式**共用 `plan_node`**，分叉点在中间那一步：`plan_exec` 直接顺序跑完，
> `rewoo` 才 fan-out 到 4 个 `execute_step{i}` 槽位再汇合——两条路最后都在 `aggregate`
> 收口，再一并交给 `validate`。

---

## 快速开始

```bash
git clone https://github.com/roseplanetb613/FitMind.git
cd FitMind
```

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
app/            Agent 编排层（core / skills / config / rag / graph / runtime / storage / bus）
lib/            消费层：纯读取、只依赖数据、无编排逻辑
data/           数据集：各包含 data/ scripts/ docs/，构建脚本幂等、配套 validate
web/            3D 前端（Vite + TS + three.js）
assets/         文档配图：screenshots/（界面截图）+ diagrams/（矢量流程图）
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
| **3D 肌群模型**（`web/public/models/muscles.glb`） | 源自 **[Z-Anatomy](https://www.z-anatomy.com/)**（上游 BodyParts3D / DBCLS），授权 **CC BY-SA 4.0** —— 署名 + **相同方式共享**；署名为 `Lluís Vinent Juanico / Z-Anatomy project`。⚠ 与动作库的 MIT **不是一套授权**（copyleft），请分开看 |
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

### 上手与部署

- **`app/requirements.txt` 缺 `psycopg`**：`app/rag/store.py` 依赖它，干净环境
  `pip install -r` 后会在导入期就报错。暂按上文手动补装。
- **语音转写不在 requirements 里**：`openai-whisper` + `torch` 是数 GB 的重依赖，
  故意不拖累纯文本链路；且 8 GB 显存下**同机只能跑一个实例**。
- **reranker 权重不入库**（1.1 GB），缺失时静默降级为稠密排序。
- **语音 / 拍照需要安全上下文（已配 https）**：浏览器的麦克风与摄像头 API 只在
  **安全上下文**（https 或 localhost）提供——浏览器限制，不是 bug。服务已支持
  `--certfile/--keyfile` 以 https 启动（证书见 `storage_output/certs/`，
  其 README 有生成与**设备信任 CA** 的步骤；SAN 覆盖 localhost/127.0.0.1/192.168.9.82，
  换 IP 要重签）。设备装一次本地 CA（`ca.crt`）后手机的语音/拍照即可用。
- **仓库体积大**：已跟踪文件约 **243 MB**（`data/` 占 239 MB，其中动作媒体 131 MB ——
  GIF 1324 个共 122.8 MB、JPG 1324 个共 8.5 MB），`.git` 打包后另有约 139 MB，
  首次 clone 与 push 会比较慢。

### 范围与合规

- **根目录仍无 LICENSE**：仓库已公开，但未声明授权 —— 按默认版权即"保留所有权利"，
  别人**没有**合法复用的余地。要让人能用，得补一个 `LICENSE`（数据授权已在各包内单独声明）。
- **测试与设计文档不在公开仓库中**：`app/tests`、`web/tests`、`docs/` 是被有意剔除的
  （发布时重写历史移除），不是遗漏；开发仓完整保留它们的版本历史。
- **运动医学数据全部【待审】**，沿用现有版本。
- **3D 肌群模型只有男性版**：上游 Z-Anatomy 即单男性模型，仓内没有女性源。
  其它层早已按性别分档（`profile.sex`、BMR 分男女、力量标准四档），**三维层是唯一缺口**。
- **多智能体未启用**：`app/bus/` 目前只是契约。
