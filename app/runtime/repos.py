# -*- coding: utf-8 -*-
"""lib 检索引擎共享单例：pipeline/qa/teach 共用一份装配结果，
避免 FoodsRepo（4.4 万条）/ExerciseRepo 多实例占内存。"""
from __future__ import annotations

_EX = None
_FR = None


def exercise_repo():
    global _EX
    if _EX is None:
        from exercise_repo import ExerciseRepo
        _EX = ExerciseRepo()
    return _EX


def foods_repo():
    global _FR
    if _FR is None:
        from foods_repo import FoodsRepo
        _FR = FoodsRepo()
    return _FR