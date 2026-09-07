# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import threading
import uuid


@dataclass
class Session:
    id: str
    history: list = field(default_factory=list)
    profile: dict = field(default_factory=dict)
    last_structured: dict | None = None


class SessionManager:
    """内存会话表；接口可换 Redis 等后端。
    线程安全：FastAPI sync 端点在线程池执行，create/get 需加锁。
    （会话内 history/profile 的单轮追加在 Agent.run 内串行发生，暂不加锁）"""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, given_id: str | None = None) -> Session:
        sid = given_id or uuid.uuid4().hex[:12]
        with self._lock:
            if sid in self._sessions:
                return self._sessions[sid]
            sess = Session(id=sid)
            self._sessions[sid] = sess
            return sess

    def get(self, sid: str) -> Session | None:
        with self._lock:
            return self._sessions.get(sid)