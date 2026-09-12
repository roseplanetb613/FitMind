# -*- coding: utf-8 -*-
"""FastAPI 入口：POST /v1/chat（无状态处理+session_id）。"""
from __future__ import annotations
import sys
from pathlib import Path
from fastapi import FastAPI
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

    @app.post("/v1/chat")
    def chat(req: ChatRequest):
        resp = agent.run(req.message, req.session_id, user_id=req.user_id)
        # structured 由 build_structured 单源组装：失败原因已透传 error 字段
        return {"session_id": resp.session_id, "reply": resp.reply,
                "mode_used": resp.mode_used, "provenance": resp.provenance,
                "structured": dict(resp.structured)}

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

    # 3D 视图静态资源；dist 不存在（未构建）时静默跳过，不阻断后端启动
    _dist = ROOT / "web" / "dist"
    if _dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/app", StaticFiles(directory=str(_dist), html=True), name="web")

    return app


app = create_app()