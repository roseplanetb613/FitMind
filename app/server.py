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
        return {"status": "ok",
                "semantic": getattr(agent.classifier, "semantic_status", "unknown")}

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

    return app


app = create_app()