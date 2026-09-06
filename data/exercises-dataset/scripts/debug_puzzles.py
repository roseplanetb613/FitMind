# -*- coding: utf-8 -*-
"""调试：找出特定动作命中的关键词，解释误分类根源"""
import importlib.util
import re

spec = importlib.util.spec_from_file_location("be", r"e:\FitMind\exercises-dataset\scripts\build_enrichment.py")
be = importlib.util.module_from_spec(spec)
spec.loader.exec_module(be)

names = ["front lever reps", "barbell sitted alternate leg raise"]
for n in names:
    t = n.lower()
    print(f"--- {n} ---")
    print("  pattern hits:")
    for pat, kws in be.PATTERN.items():
        for k in kws:
            if be.kw(t, [k]):
                print(f"    {pat}: {k}")
    print("  advanced hits:", [k for k in be.ADVANCED_KW if k in t])
    print("  beginner hits:", [k for k in be.BEGINNER_KW if k in t])
    print("  isolation hits:", [k for k in be.ISOLATION_KW if be.kw(t, [k])])
    print("  classify =", be.classify(n, "waist"), "| is_compound =", be.is_compound(t, be.classify(n, "waist")))