# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import uuid


@dataclass
class Session:
    id: str
    history: list = field(default_factory=list)
    profile: dict = field(default_factory=dict)
    last_structured: dict | None = None


class SessionManager:
    """内存会话表；接口可换 Redis 等后端。"""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create(self, given_id: str | None = None) -> Session:
        sid = given_id or uuid.uuid4().hex[:12]
        if sid in self._sessions:
            return self._sessions[sid]
        sess = Session(id=sid)
        self._sessions[sid] = sess
        return sess

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)