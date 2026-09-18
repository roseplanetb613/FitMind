# -*- coding: utf-8 -*-
"""FastAPI 入口：POST /v1/chat（无状态处理+session_id）。"""
from __future__ import annotations
import sys
from pathlib import Path
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

_LOGIN_PAGE_PATH = ROOT / "web" / "login.html"
"""登录/注册页。免构建静态页（不走 Vite）—— 它是**进入应用的门**，
不能依赖"应用已经加载"才能显示。"""


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    user_id: str | None = None                     # F4：记忆图谱归属（缺省 local）


class ProfileRequest(BaseModel):
    session_id: str
    profile: dict
    user_id: str | None = None


class PlanDeleteRequest(BaseModel):
    """计划表卡片「删除计划」按钮的请求（user_id 归属同其它端点）。"""
    user_id: str = "local"


class AuthRequest(BaseModel):
    """登录 / 注册共用。**字段名沿用 user_id** —— 登录名即记忆图谱的归属键，
    不是另起一套账号体系（那样"账号 → 数据"就要多一张映射表）。"""
    user_id: str
    password: str


class CheckinResolveRequest(BaseModel):
    """消歧选择框点定后的补记请求。

    ⚠ **必须定义在模块级。** 本文件有 `from __future__ import annotations`，
    所有注解都是字符串，FastAPI 靠**模块全局**去解析它们；定义在 `create_app()`
    里的模型解析不到，会被当成 query 参数，实测报 422 `missing query req`。
    """
    user_id: str = "local"
    # 二选一：点候选给 id；自己打字给 text（后端再解析一次）。
    # **都必填会挡住自定义输入**，都选填又会让空请求变成 422 之外的另一种静默 ——
    # 所以两个都选填，在端点里显式判"至少给一个"。
    exercise_id: str | None = None
    text: str | None = None           # 自定义输入的动作名
    name_zh: str | None = None
    raw: str | None = None            # 用户原话里的说法，留作审计
    sets: int | None = None
    reps: int | None = None
    occurred_at: str | None = None
    # 这次点选是为什么而点：`checkin`（补记训练）还是 `preference`（记偏好）。
    # 缺省 checkin —— 老前端不带这个字段时行为逐字不变。
    kind: str = "checkin"
    value: str | None = None          # kind=preference 时的极性：喜欢 / 不喜欢


def create_app() -> FastAPI:
    from app.core.agent import Agent
    from app.core.llm import build_provider
    from app.skills import build_default_registry

    app = FastAPI(title="FitMind Agent")
    # 压缩静态资源（2026-09-13）：手机首屏要下 670 KiB 的 JS + 543 KiB 的模型，
    # 而 StaticFiles **默认一个字节都不压**（实测响应里没有 Content-Encoding）。
    # 开 gzip 后 JS 约 179 KiB（3.7×），是首屏"白/半截"窗口里最大的单一杠杆。
    # minimum_size=1024：小于 1 KiB 的不值得压（压完反而更大）。
    # ⚠ 只压文本类；GLB 是二进制，收益很小但无害。
    from fastapi.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    agent = Agent(registry=build_default_registry(), llm=build_provider())
    app.state.agent = agent   # 测试/调用方可经此建档（Profile 进 Session）

    # ---- 访问网关（局域网多用户：登录注册 + user_id 改写隔离） ----
    # 放在 GZip **之后** add → AuthGate 是最外层：未登录的请求在 gzip 之前就被
    # 302/401 掉，不浪费压缩。⚠ 纯 ASGI 中间件，不要换成 BaseHTTPMiddleware ——
    # 它会缓冲响应体，/v1/chat/stream 的 SSE 会被整套拖死（见 app/auth.py）。
    from app.auth import (AccountStore, AuthGate, CookieSession,
                          validate_password as auth_validate_password,
                          validate_user_id as auth_validate_user_id)
    accounts = AccountStore()
    cookie_session = CookieSession()
    app.state.auth = {"accounts": accounts, "session": cookie_session}
    app.add_middleware(AuthGate, session=cookie_session, accounts=accounts)

    @app.get("/health")
    def health():
        # semantic：L1 例句库状态（on / off(unreachable) / disabled）。全静默降级时
        # "enabled=true 但其实没在工作"无法从外部发现，故在此显式暴露。
        # degraded：静默降级计数（见 app/core/diag.py）。降级是有意策略，但"静默"
        # 曾让整条链路不可用而表面正常（食物库构建失败 → 27 个测试连锁挂而无迹可查）。
        from app.core.diag import counts as _deg, details as _degd
        return {"status": "ok",
                "semantic": getattr(agent.classifier, "semantic_status", "unknown"),
                "degraded": _deg(),
                # 只记次数等于"知道坏了但不知道为什么"——实测食物库间歇 OOM 就是靠
                # 这里的异常摘要才从"4 个断言莫名失败"定位到 MemoryError
                "degraded_detail": _degd()}

    # ------------------------------------------------------------ 登录注册
    # 端点写在 app.mount 之前（mount 会吞掉后续路由）。认证逻辑在 app/auth.py。
    # ⚠ 数据隔离真正的 enforcement 点在 AuthGate（改写 user_id），不在这四个端点 ——
    #   就算端点全对，处理器拿到什么样的 user_id 仍由网关决定。

    @app.post("/v1/auth/register")
    def auth_register(req: AuthRequest):
        """注册。**开放注册**：局域网内任何人可建号（可信圈子的取舍，已在 SDD 注明）。"""
        try:
            uid = auth_validate_user_id(req.user_id)
            auth_validate_password(req.password)
        except ValueError as e:
            return JSONResponse(status_code=422, content={"ok": False, "error": str(e)})
        if not accounts.create(uid, req.password):
            return JSONResponse(status_code=409,
                                content={"ok": False, "error": f"「{uid}」已被注册"})
        resp = JSONResponse({"ok": True, "user_id": uid,
                             "ack": f"已创建账号 {uid}"})
        resp.headers["Set-Cookie"] = cookie_session.set_cookie_header(uid)
        return resp

    @app.post("/v1/auth/login")
    def auth_login(req: AuthRequest):
        uid = auth_validate_user_id(req.user_id)     # 顺带把大小写归一
        if not accounts.verify(uid, req.password or ""):
            # 不区分"用户不存在"与"密码错"（后端已做耗时对齐）——前端文案同理
            return JSONResponse(status_code=401,
                                content={"ok": False, "error": "用户名或密码不对"})
        resp = JSONResponse({"ok": True, "user_id": uid, "ack": f"欢迎回来，{uid}"})
        resp.headers["Set-Cookie"] = cookie_session.set_cookie_header(uid)
        return resp

    @app.post("/v1/auth/logout")
    def auth_logout():
        resp = JSONResponse({"ok": True, "ack": "已退出"})
        resp.headers["Set-Cookie"] = cookie_session.clear_cookie_header()
        return resp

    @app.get("/v1/auth/me")
    def auth_me(user_id: str | None = None):
        # user_id 由 AuthGate 注入（网关把 query 里的 user_id 改写成登录者本人），
        # 所以这里拿到的**一定是登录者** —— 前端想冒充别人也改不动它。
        return {"ok": True, "user_id": user_id}

    @app.get("/login")
    def login_page():
        """登录/注册页（免构建的静态页，不走 Vite）。"""
        return FileResponse(_LOGIN_PAGE_PATH, media_type="text/html; charset=utf-8")

    @app.get("/")
    def root(request: Request):
        """根路径按登录态分流：已登录进应用，未登录去登录页。

        ⚠ 不要在这里读 `request.query_params` 的 user_id 来决定去哪 ——
        那个值是客户端可伪造的；登录态只认 Cookie（网关同样只认 Cookie）。"""
        user = cookie_session.verify(request.cookies.get(cookie_session.cookie_name))
        return RedirectResponse(f"/app/?user_id={user}" if user else "/login")

    @app.get("/v1/muscle-map")
    def muscle_map(user_id: str = "local", days: int = 7):
        """肌群恢复状态（3D 视图数据源）。

        契约与 `web/src/data/types.ts` **一一对应**（改一处须改两处）：
          · `muscles` 为**定长 28 项**，无记录显式 `null`（**不是缺键**）
          · `describe` 由后端 `recovery.describe` 算好（措辞单源，前端只显示）
          · `pct` 也由后端算好，且与 `describe` **同口径**（下方注释）
          · 降级（图谱不可用）→ **200 + degraded=true + 全 null**，不是错误——
            前端据此画未知态，与"没有记录"同样处理
          · `labels` 降级时可为 {}，前端回落显示 muscle_id（不得因缺标签丢块）
        """
        import recovery
        import muscle_map as _mm      # 注意别名：本路由函数同名，直接 import 会遮蔽自己
        from datetime import datetime, timezone
        # 注意别写 `days or 7`：显式传 0 是 falsy 会被静默吞成默认值（实测踩过）
        try:
            days = max(1, min(int(days), 90))
        except (TypeError, ValueError):
            days = 7
        now = datetime.now(timezone.utc)
        ids = recovery.muscle_ids()                 # 不依赖图谱的定长枚举
        base = {"generated_at": now.isoformat(), "days": days,
                "labels": _mm.labels_from_repo()}
        try:
            from app.graph.memory import MemoryStore
            m = MemoryStore.get()
            if m is None:
                raise RuntimeError("记忆图谱不可用")
            raw = recovery.recovery_map(m.muscle_load_map(user_id, days=days), now)
            # describe 由后端算好（措辞单源：含"约"与非精确口吻、低置信标注），
            # 前端只显示——契约 web/src/data/types.ts 明确要求该字段
            #
            # pct 同理，也由后端算好（整数百分比的**单源**）：下面这行必须与
            # `recovery.describe()` 里那行**逐字同口径**——Python 的 round() 是
            # half-even、JS 的 Math.round 是 half-up，0.245 会得出 24 vs 25。
            # 前端标签此前自己 Math.round，导致同一屏上标签 25% 而详情 24%
            # （实测 0.245 命中的正是 latissimus_dorsi / trapezius / upper_back）。
            # tests/test_muscle_map_api.py 把两者钉在一起：
            # test_pct_matches_describe（一般值）+ test_pct_tie_is_half_even（平局值
            # 0.245 → 24；上面那条对非平局值恒真，只有这条能发现口径漂移）。
            # 前端 labels.test.ts 的"平局值不漂"那条守着另一侧。
            states = {mid: {**st,
                            "pct": int(round(float(st.get("recovery", 1.0)) * 100)),
                            "describe": recovery.describe(mid, st)}
                      for mid, st in raw.items()}
        except Exception:
            return {**base, "muscles": {mid: None for mid in ids},
                    "degraded": True}
        return {**base, "muscles": {mid: states.get(mid) for mid in ids}}

    @app.get("/v1/muscle-exercises")
    def muscle_exercises(muscle: str, limit: int = 3, user_id: str = "local"):
        """练这块肌肉的推荐动作（供 3D 视图"点肌肉 → 看该练什么"）。

        **复用 `ExerciseRepo.recommend`**，不另写推荐逻辑 —— 它已有变体族去重
        （避免推荐 3 个深蹲变体）、主目标优先、逐级放宽条件。

        `count` 不足是常态，不是异常：实测 tibialis_anterior 全库只有 1 个动作、
        levator_scapulae 2 个。响应里的 `fallback=true` 就表示"放宽过条件"
        （含最后一步放开拉伸类）。**不要给空位补占位**。
        """
        from app.runtime.repos import exercise_repo

        ex = exercise_repo()
        # 按用户**最近 30 天练过的**排序：点肌肉看到的先是自己常练的。
        # 取不到偏好时是空集 → recommend 的顺序完全不变（见其 prefer 说明）。
        prefer: set[str] = set()
        try:
            from app.graph.memory import MemoryStore
            store = MemoryStore.get()
            if store is not None:
                prefer = set(store.recent_exercises(user_id, days=30))
        except Exception:
            prefer = set()          # 偏好是锦上添花，取不到就按原顺序给
        r = ex.recommend(muscle, count=max(1, min(int(limit), 20)),
                         prefer=prefer or None)
        out = []
        for x in r["recommendations"]:
            out.append({
                "id": x.get("id"),
                "name": x.get("name"),
                "name_zh": x.get("name_zh"),
                # 媒体路径是相对 data/exercises-dataset/ 的；前端拼 /media 前缀。
                # ⚠ 版权：素材 © Gym visual，商用需另行取授权（见 docs/HANDOVER.md）。
                "gif_url": x.get("gif_url"),
                "image": x.get("image"),
                "exercise_type": x.get("exercise_type"),
                "difficulty": x.get("difficulty"),
                # 该动作对这块肌肉的角色：主练 or 协同 —— 前端据此标注
                "role": ("target" if x["muscles_canonical"]["target"] == ex._norm_muscle(muscle)
                         else "synergist"),
            })
        return {"muscle": muscle, "count": len(out),
                "fallback": r["fallback"], "exercises": out}

    @app.get("/v1/plan")
    def plan(user_id: str = "local"):
        """最新训练计划（计划表数据源）。

        数据来自**图谱里的 PlanVersion**（`plan_skill._register_plan` 产出计划时
        登记），不是重新算一遍——同一份计划在聊天、网页、后续编辑里必须是同一份，
        重算会漂。取最新一条（`latest_plan` 已按 created_at 倒序）。

        契约：
          · 无计划 → **200 + plan:null**，不是 404——前端据此显示"还没有计划"，
            与"接口坏了"区分开（404 会让前端把它当错误处理）
          · 降级（图谱不可用）→ 200 + degraded:true + plan:null，同样是可渲染状态
          · `content` **原样透传**，不重塑：计划内容是 plan_skill 的产物，在这里
            重新拼一遍就会多一个会漂的平行结构（见 web/src/data/plan.ts 的说明）
        """
        try:
            from app.graph.memory import MemoryStore
            m = MemoryStore.get()
            if m is None:
                raise RuntimeError("记忆图谱不可用")
            got = m.latest_plan(user_id)
        except Exception:
            return {"user_id": user_id, "plan": None, "degraded": True}
        if not got or not got.get("content"):
            # 有计划记录但 content 为空（旧版本只存 hash）→ 同样按"没有计划"处理
            return {"user_id": user_id, "plan": None, "degraded": False}
        # ⚠ content **逐字透传，不在这里补动作 id**（2026-09-18 考虑过，否决）。
        # 理由：计划的 `name` 本来就来自 `search_zh` 命中的 `name_zh`，所以老计划
        # 按名字走 `POST /v1/checkin/resolve` 时 `norm_zh` 精确匹配**必然命中**，
        # 不会弹消歧 —— 补 id 的收益几乎为零，代价却是打破"端点是纯透传"这条契约
        # （test_plan_api.test_plan_content_is_passthrough 明确钉着它，且理由是
        # "别在这里造一个会跟后端漂的平行结构"）。新计划由写入侧
        # `plan_skill.attach_exercise_ids` 落 id，那才是唯一该负责的地方。
        return {"user_id": user_id, "degraded": False,
                "plan": {"plan_id": got.get("plan_id"),
                         "created_at": got.get("created_at"),
                         "content": got["content"]}}

    @app.post("/v1/plan/delete")
    def plan_delete(req: PlanDeleteRequest):
        """删除当前训练计划（计划表卡片「删除计划」按钮）。

        **软删**：supersede_plan 给 PlanVersion 打 deleted_at 标——打卡历史
        （log_checkin 引用 plan_id）与版本链保留，latest_plan 过滤后自然为空。
        与对话句式「删掉所有训练计划」（plan_skill._PLAN_CLEAR_RE）同一存储
        口径；独立端点是因为按钮语义就是"删除计划"，无需句式解析。
        无计划 → 200 + deleted:false + reason:no_plan（与 GET /v1/plan 的
        "无计划不是 404" 同一契约）。"""
        try:
            from app.graph.memory import MemoryStore
            m = MemoryStore.get()
            if m is None:
                raise RuntimeError("记忆图谱不可用")
            # 按**用户**删净全部未删版本（PlanVersion 是版本链，只删最新会
            # 回落旧版——实测「已删除」后 GET 仍返回 9/13 的旧计划）。
            n = m.supersede_plans(req.user_id)
            if n < 0:
                return {"deleted": False, "reason": "degraded"}
            return {"deleted": True, "superseded": n}
        except Exception:
            return {"deleted": False, "reason": "degraded"}

    @app.post("/v1/checkin/resolve")
    def checkin_resolve(req: CheckinResolveRequest):
        """用户在消歧选择框里点定了一个动作 → 补记这次训练。

        **为什么不复用 /v1/chat**：这时我们已经确知用户点了哪个动作，
        再跑一遍 agent 既慢（2~5s）又可能再次落回消歧 —— 自找的。
        这里直接建事件 + TARGETS 边，并把 exercise_id 落进 payload
        （记为"偏好"，供后续按常练排序）。
        """
        from app.graph.memory import MemoryStore
        from app.runtime.repos import exercise_repo
        m = MemoryStore.get()
        if m is None:
            return JSONResponse(status_code=503, content={"ok": False,
                                                          "error": "记忆图谱不可用"})
        ex = exercise_repo()
        if not req.exercise_id and not req.text:
            return JSONResponse(status_code=422, content={
                "ok": False, "error": "需要 exercise_id 或 text 之一"})

        eid_in = req.exercise_id
        if not eid_in and req.text:
            # 自定义输入：**只认完全同名**，否则把近似结果当选项回去让用户确认。
            #
            # 为什么不直接取 search_zh 的首条：它按难度升序返回，拿到的是"包含这个词
            # 的最简单的动作"。实测输入"深蹲"会落到 `弹力带 单臂 单腿 分腿深蹲` ——
            # 器械都不一样，**记的是一个用户没做过的动作**。而库里根本没有叫"深蹲"
            # 的动作（都是"弹力带…分腿深蹲"这类复合名），所以只做模糊匹配一定出错。
            from lib.exercise_repo import norm_zh
            want = norm_zh(req.text)
            exact = next((r for r in ex.by_id.values()
                          if (r.get("norm_name_zh") or "") == want), None)
            if exact:
                eid_in = exact.get("id")
                if not req.name_zh:
                    req.name_zh = exact.get("name_zh")
            else:
                # 捞候选的宽度和字段组装都与 nodes.clarify_exercise 共用
                # （**同一份**）：此前这里只捞 3 条且 `recent_count` 硬编码 0，
                # 于是"你常练、但检索排第 4"的动作在这条路径上永远冒不上来，
                # 而同一屏上面那张卡片却有「练过 N 次」角标。
                from app.core.clarify_options import (CLARIFY_CANDIDATE_POOL,
                                                     build_clarify_options)
                cands = ex.search_zh(req.text, limit=CLARIFY_CANDIDATE_POOL)
                if not cands:
                    return JSONResponse(status_code=404, content={
                        "ok": False,
                        "error": f"动作库里没有「{req.text}」——换个说法试试"})
                return {"ok": False, "need_pick": True, "raw": req.text,
                        "reply": f"库里没有正好叫「{req.text}」的动作——是下面哪个？",
                        "options": build_clarify_options(cands, req.user_id)}
        rec = ex.get(eid_in) if eid_in else None
        if not rec:
            # 如实说没找到，**不硬记**：库里没有的动作记下去也建不出肌群边，
            # 3D 上照样看不见 —— 那正是这次要消灭的"以为记上了"。
            return JSONResponse(status_code=404, content={
                "ok": False,
                "error": f"动作库里没有「{req.text or req.exercise_id}」——换个说法试试"})
        name = req.name_zh or rec.get("name_zh")
        # —— 偏好点选：写**偏好**而不是打卡 ——
        # 用户在"你想避开的「深蹲」是下面哪个？"里点定了一个动作，语义是
        # "我（不）喜欢这个动作"，不是"我今天练了它"。写成打卡会凭空多出一次
        # 训练记录，污染恢复度（这是最长的那根线：肌群恢复全靠打卡算）。
        if req.kind == "preference":
            val = req.value or "不喜欢"
            hit = m.upsert_state(req.user_id, "preference", val, about=name)
            if hit is None:
                return JSONResponse(status_code=500, content={"ok": False,
                                                              "error": "写入失败"})
            # ack 文案走单源（memory_extract._ack_text）——另写一份必然与
            # 自动抽取那条路漂移（同一个偏好，两种说法）
            from app.graph.memory_extract import _ack_text
            return {"ok": True, "kind": "preference", "exercise_id": eid_in,
                    "name_zh": name, "value": val,
                    "ack": "已记下：" + _ack_text(
                        {"op": "preference", "value": val, "about": name})}
        mus = rec.get("muscles_canonical") or {}
        # 肌群与角色走单源：与打卡写入口径一致（target 主练 / 其余协同）
        roles = {}
        if mus.get("target"):
            roles[str(mus["target"])] = "target"
        for mm in (mus.get("muscle_group"), *(mus.get("secondary") or [])):
            if mm:
                roles.setdefault(str(mm), "synergist")
        item = {"name": name, "exercise_id": eid_in}
        if req.raw:
            item["raw"] = req.raw
        if req.sets is not None:
            item["sets"] = int(req.sets)
        if req.reps is not None:
            item["reps"] = int(req.reps)
        eid = m.log_event(req.user_id, "checkin",
                          {"about": name, "verb": "点选补记", "items": [item]},
                          occurred_at=req.occurred_at or None,
                          muscles=list(roles) or None, muscle_roles=roles or None)
        if not eid:
            return JSONResponse(status_code=500, content={"ok": False,
                                                          "error": "写入失败"})
        return {"ok": True, "event_id": eid, "exercise_id": eid_in,
                "name_zh": name, "muscles": sorted(roles),
                "ack": f"已记下：{name}"}

    def _payload(resp) -> dict:
        # structured 由 build_structured 单源组装：失败原因已透传 error 字段。
        # guard 有史以来算好了却没序列化（agent.py:83），于是前端拿不到结构化的
        # 风险负载（level_label / advice / blocks），"不建议练"只能退化成一段
        # 和普通回答长得一样的文字。补上。
        return {"session_id": resp.session_id, "reply": resp.reply,
                "mode_used": resp.mode_used, "provenance": resp.provenance,
                "structured": dict(resp.structured),
                "guard": resp.guard}

    @app.post("/v1/chat")
    def chat(req: ChatRequest):
        return _payload(agent.run(req.message, req.session_id, user_id=req.user_id))

    @app.post("/v1/chat/stream")
    async def chat_stream(req: ChatRequest):
        """阶段进度 + 最终整包。

        **只流阶段，不流 token。** 后置护栏会在 LLM 渲染之后整条丢弃并替换 reply
        （见 progress.py 的模块注释），按 token 流出去等于先把编造的数字给用户看一遍。

        SSE 规范上 EventSource 只支持 GET，所以这里是 POST —— 前端用
        fetch + res.body.getReader() 手动解帧，不能用 EventSource。

        ⚠ **端点与生成器都必须是 `async def`，不能写成同步生成器。**
        实测（Windows + uvicorn 0.52，h11 与 httptools 都一样）：同步生成器会被
        Starlette 走 `iterate_in_threadpool` 那条路，**整个响应被缓冲到结束才发出去**
        —— 生成器明明在 +0.001s 就 yield 了，客户端却要等到 ~1.5s（全跑完）才见到
        第一个字节，阶段流等于白做。改 async 之后 TTFB 从 1.5s 掉到 0.000s。
        排查时用最小复现对照过：同样的后台线程 + queue 结构，只把生成器换成同步的
        就复现，换回 async 就好。**不要"顺手"把它改回同步生成器。**

        ⚠ **已知残留：第一个字节仍会晚 ~2s**（实测与工作量无关，恒定）。原因是工人
        线程里 agent.run 的**前置 CPU 密集段**（记忆抽取 + 分类）长时间持有 GIL，
        事件循环拿不到执行权去 `send()`，于是前三段阶段事件堆在一起、等工人线程撞上
        第一次网络等待时才一次性发出。已排除：uvicorn 的 h11/httptools、同步生成器、
        纯 sleep 的工人线程（这三样单独都能流到 TTFB≈2ms）。真要消掉得把 agent 挪到
        独立进程，代价与本次改动不成比例 —— 现状下用户仍是"2s 后开始有反馈"，
        而不是"全程空白到 7s"。
        """
        import json as _json
        import queue as _queue
        import threading as _threading
        from fastapi.responses import StreamingResponse
        from app.core import progress

        q: _queue.Queue = _queue.Queue()
        # 立刻给一条"已收到"：认清意图之前还有 ~1.4s 死区（记忆抽取 + guard），
        # 不先反馈的话用户面对的是一个毫无反应的输入框。
        q.put(("stage", {"stage": progress.STAGE_RECEIVED}))

        def worker() -> None:
            # contextvar 在本线程内注册，agent.run 的整条同步调用链都看得见
            # —— 于是 agent.py / 各节点签名一行都不用改。
            # 发射器收到的是 dict（{"stage":..., "skill":...}），原样转发给前端
            progress.set_emitter(lambda ev: q.put(("stage", ev)))
            try:
                resp = agent.run(req.message, req.session_id, user_id=req.user_id)
                q.put(("done", _payload(resp)))
            except Exception as e:                      # noqa: BLE001
                # 端点本身不抛：异常要作为一条事件送达前端，否则连接静默断开，
                # 前端只能看到一个没有任何解释的空白。
                q.put(("error", {"message": f"{type(e).__name__}: {e}"}))
            finally:
                progress.clear()
                q.put(("__end__", {}))

        _threading.Thread(target=worker, daemon=True).start()

        async def gen():
            import asyncio as _aio
            while True:
                kind, payload = await _aio.to_thread(q.get)
                if kind == "__end__":
                    break
                data = {"type": kind}
                data.update(payload)
                yield f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            # 反代（nginx 等）默认会缓冲整个响应，那样"流式"就白做了
            "X-Accel-Buffering": "no",
        })

    # ------------------------------------------------------------ 语音输入
    # 端点定义写在 `app.mount("/app", ...)` **之前** —— mount 会把后续路由吞掉。
    @app.get("/v1/asr/status")
    def asr_status():
        """转写能力探活。前端据此决定要不要显示麦克风按钮。

        **不触发模型加载**（见 app/runtime/asr.py 的 status 说明）：探活本身
        代价必须是零，否则"看一眼有没有麦克风"就等于把 4.6GB 模型拉进显存。
        """
        from app.runtime import asr
        st = asr.status()
        # 前端只需要一个二值判断，但排障需要知道**为什么**不可用，
        # 所以把原始状态一起给出去，别让前端从 enabled 反推。
        return {"available": st["enabled"] and st["model_exists"], **st}

    @app.post("/v1/asr")
    async def asr_transcribe(file: UploadFile = File(...)):
        """音频 → 文本。前端把文本回填输入框，由用户确认后再走 /v1/chat。

        ⚠ **必须是 async def**：`await file.read()` 是协程，写成同步 def 会被
        Starlette 丢进线程池并把整个响应缓冲住（同 /v1/chat/stream 那条实测
        结论，见上文）。转写本身是阻塞的，用 `to_thread` 让出事件循环 ——
        否则一次转写会把 /v1/chat/stream 的阶段流一起卡住。
        """
        import asyncio
        import os
        import tempfile

        from app.runtime import asr

        if not asr.status()["enabled"]:
            return JSONResponse(status_code=503, content={
                "ok": False, "error": "语音转写未启用（asr_config.json 的 enabled=false）"})

        data = await file.read()
        if not data:
            # 空录音是**用户操作问题**（按了开始没说话就按结束），不是服务错误。
            # 422 + 一句人话，前端要把这句原样显示出来，别吞成"失败了"。
            return JSONResponse(status_code=422, content={
                "ok": False, "error": "没收到音频内容——是不是没说话就结束了？"})

        # whisper 从**路径**读文件（内部再交给 ffmpeg），所以先落盘。
        # 后缀保留原扩展名：ffmpeg 靠它判断容器格式，写成 .tmp 会解不出来。
        from pathlib import Path as _Path
        suffix = _Path(file.filename or "").suffix or ".webm"
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        try:
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
            except Exception:
                # 写盘失败（磁盘满等）：fdopen 没接手 fd 时得自己关，
                # 否则每次失败都漏一个文件描述符。
                os.close(fd)
                raise
            try:
                out = await asyncio.to_thread(asr.transcribe, tmp)
            except FileNotFoundError as e:
                return JSONResponse(status_code=503, content={"ok": False, "error": str(e)})
            except Exception as e:                        # noqa: BLE001
                # **只回类型名，不回异常文本**。异常消息里常带服务器绝对路径
                # （权重不存在时那句 FileNotFoundError 就含完整盘符路径），
                # 对用户毫无意义却把内部结构透出去了 —— 实测这条被测试逮到过。
                # 类型名够定位（显存不足=RuntimeError/OOM、权重坏=BadZipFile），
                # 细节进服务端日志。
                import logging
                logging.getLogger("fitmind.asr").exception("转写失败")
                return JSONResponse(status_code=503, content={
                    "ok": False, "error": f"转写失败（{type(e).__name__}），详见服务端日志"})
            return {"ok": True, "text": out["text"], "language": out["language"],
                    "duration": out["duration"]}
        finally:
            # 临时音频必须删：里面是用户的语音，留在盘上没有任何理由。
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ---------------- 食物识别（拍照 → 营养卡片） ----------------
    @app.get("/v1/vision/food/status")
    def vision_status():
        """食物识别探活。前端据此决定要不要渲染相机按钮。

        **零代价**：不构造 client、不发请求（见 app/runtime/vision.status）。
        探活要是会调模型，"看一眼有没有这个功能"就等于花钱。
        """
        from app.runtime import vision
        st = vision.status()
        return {"available": st["enabled"] and st["key_configured"], **st}

    @app.post("/v1/vision/food")
    async def vision_food(file: UploadFile = File(...)):
        """餐食照片 → 营养卡片。**无状态，不落库。**

        ⚠ **必须是 async def + to_thread**：`await file.read()` 是协程（同
        /v1/asr 的理由），而 VLM 调用是阻塞的——直接调会把 /v1/chat/stream
        的阶段流一起卡住，用户会觉得整个面板死了。
        """
        import asyncio

        from app.runtime import vision

        # 先查可用性、再读文件（对齐 /v1/asr 的做法）。
        # ⚠ 这一步**不能省**、也不能只靠下面那个 "vision#error" 判断：
        # 未启用 / 缺 key 时 `recognize()` 是**早返回**，provenance 为空，
        # 只判 "vision#error" 会把"服务不可用"当成正常结果回 200 ——
        # 计划 Task 7 Step 3 的原稿正是如此，被 test_disabled_returns_503 逮住。
        st = vision.status()
        if not (st["enabled"] and st["key_configured"]):
            return JSONResponse(status_code=503, content={
                "ok": False, "error": st["error"] or "食物识别不可用"})

        data = await file.read()
        if not data:
            # 空文件是**用户操作问题**（选错了/取消了一半），不是服务错误。
            # 422 + 一句人话，前端原样显示，别吞成"失败了"。
            return JSONResponse(status_code=422, content={
                "ok": False, "error": "没收到图片内容——重新选一张试试？"})

        mime = (file.content_type or "image/png").split(";")[0].strip()
        if not mime.startswith("image/"):
            return JSONResponse(status_code=422, content={
                "ok": False,
                "error": f"这个格式（{mime}）认不了，发张图片（JPG/PNG/WebP）吧"})

        result = await asyncio.to_thread(vision.recognize, data, mime)

        if not result.get("ok"):
            # 「这不是食物」是**有效判断**（200），与「调用失败」（503）是两回事：
            # 前者是识别结果，后者是服务故障 —— 前端给的提示完全不同。
            failed = result.get("provenance", [])[-1:] == ["vision#error"]
            if failed:
                return JSONResponse(status_code=503, content=result)
        return result

    @app.post("/v1/profile")
    def set_profile(req: ProfileRequest):
        from fastapi.responses import JSONResponse
        try:
            profile = agent.update_profile(req.session_id, req.profile,
                                           user_id=req.user_id)
        except ValueError as e:
            return JSONResponse(status_code=422,
                                content={"error": "profile 校验失败",
                                         "details": [s.strip() for s in str(e).split(";")]})
        return {"session_id": req.session_id, "profile": profile}

    @app.get("/v1/profile/{session_id}")
    def get_profile(session_id: str, user_id: str | None = None):
        """读档案。

        `user_id` 是**可选**的，只在会话缓存缺失时起作用：`SessionManager` 是纯内存，
        后端一重启（或用户换浏览器 / 清 localStorage）缓存就没了，而档案在落盘的
        图谱里。那时后端没有 `sess.user_id` 可依，只能由前端告知归属 ——
        不带的话这一步回 404，用户点开「我的信息」看到空表单、聊一句又冒出来
        （实测复现过的 bug）。
        """
        profile = agent.get_profile(session_id, user_id=user_id)
        if profile is None:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"error": "会话不存在"})
        return {"session_id": session_id, "profile": profile}

    # 动作演示媒体（GIF/图片）。⚠ 版权：素材 © Gym visual，商用需另行取授权
    # （见 data/exercises-dataset/docs/HANDOVER.md）。当前仅用于非商业演示。
    # 只在目录存在时挂载，不阻断启动。
    _media = ROOT / "data" / "exercises-dataset"
    if _media.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/media", StaticFiles(directory=str(_media)), name="media")

    # 3D 视图静态资源；dist 不存在（未构建）时静默跳过，不阻断后端启动
    _dist = ROOT / "web" / "dist"
    if _dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/app", StaticFiles(directory=str(_dist), html=True), name="web")

    return app


app = create_app()