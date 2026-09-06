# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class Session:
    id: str
    history: list = field(default_factory=list)
    profile: dict = field(default_factory=dict)
    last_structured: dict | None = None