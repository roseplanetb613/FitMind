# -*- coding: utf-8 -*-
"""FastAPI 入口：POST /v1/chat（无状态处理+session_id）。"""
from __future__ import annotations
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    user_id: str | None = None                     # F4：记忆图谱归属（缺省 local）


class ProfileRequest(BaseModel):
    session_id: str
    profile: dict
    user_id: str | None = None


class CheckinResolveRequest(BaseModel):
    """消歧选择框点定后的补记请求。

    ⚠ **必须定义在模块级。** 本文件有 `from __future__ import annotations`，
    所有注解都是字符串，FastAPI 靠**模块全局**去解析它们；定义在 `create_app()`
    里的模型解析不到，会被当成 query 参数，实测报 422 `missing query req`。
    """
    user_id: str = "local"
    exercise_id: str
    name_zh: str | None = None
    raw: str | None = None            # 用户原话里的说法，留作审计
    sets: int | None = None
    reps: int | None = None
    occurred_at: str | None = None


def create_app() -> FastAPI:
    from app.core.agent import Agent
    from app.core.llm import build_provider
    from app.skills import build_default_registry

    app = FastAPI(title="FitMind Agent")
    agent = Agent(registry=build_default_registry(), llm=build_provider())
    app.state.agent = agent   # 测试/调用方可经此建档（Profile 进 Session）

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
        rec = ex.get(req.exercise_id) if req.exercise_id else None
        if not rec:
            return JSONResponse(status_code=404, content={"ok": False,
                                                          "error": f"动作不存在：{req.exercise_id}"})
        name = req.name_zh or rec.get("name_zh")
        mus = rec.get("muscles_canonical") or {}
        # 肌群与角色走单源：与打卡写入口径一致（target 主练 / 其余协同）
        roles = {}
        if mus.get("target"):
            roles[str(mus["target"])] = "target"
        for mm in (mus.get("muscle_group"), *(mus.get("secondary") or [])):
            if mm:
                roles.setdefault(str(mm), "synergist")
        item = {"name": name, "exercise_id": req.exercise_id}
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
        return {"ok": True, "event_id": eid, "exercise_id": req.exercise_id,
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
    def get_profile(session_id: str):
        profile = agent.get_profile(session_id)
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