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


_DR = None


def dish_repo():
    """DishRepo 单例。依赖 foods_repo 单例（10 道菜的配方引用它的条目）。"""
    global _DR
    if _DR is None:
        from dish_repo import DishRepo
        _DR = DishRepo(foods_repo())
    return _DR