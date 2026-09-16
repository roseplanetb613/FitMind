# -*- coding: utf-8 -*-
"""Agent 门面：LangGraph 编排 + Session 管理 + ChatResponse 组装。
领域层（skills/runtime/storage）框架中立；本模块仅为图的门面。"""
from __future__ import annotations
from dataclasses import dataclass, field
from app.core.graph import build_graph, build_structured
from app.core.llm import LLMProvider
from app.core.registry import SkillRegistry
from app.core.render_util import bind_items_to_reply
from app.core.router import RouteClassifier
from app.core import diag                     # 降级可观测（静默失败可查）
from app.core.session import Session, SessionManager


@dataclass
class ChatResponse:
    session_id: str
    reply: str
    structured: dict
    mode_used: str
    provenance: list[str]
    guard: dict | None = None


class Agent:
    def __init__(self, registry: SkillRegistry, llm: LLMProvider,
                 sessions: SessionManager | None = None,
                 classifier: RouteClassifier | None = None,
                 checkpointer=None):
        self.registry = registry
        self.llm = llm
        self.sessions = sessions or SessionManager()
        self.classifier = classifier or RouteClassifier(llm)
        self._checkpointer = checkpointer   # 仅显式注入时才挂 thread 配置
        self.graph = build_graph(registry=registry, llm=llm,
                                 classifier=self.classifier,
                                 checkpointer=checkpointer)

    def run(self, message: str, session_id: str | None = None,
            user_id: str | None = None) -> ChatResponse:
        sess = self.sessions.get(session_id) if session_id else None
        if sess is None:
            sess = self.sessions.create(session_id, user_id=user_id)
        elif user_id and user_id != sess.user_id:
            sess.user_id = user_id               # 显式注入覆盖归属（F4）
        # 记忆规则抽取（偏好/档案陈述句；静默，宁缺毋滥——失败绝不影响主链路）。
        # 先于投影：抽取落图谱后当轮即可投影回会话（"我只有60kg"同轮生效）。
        acks: list[str] = []
        # 没解析成库内动作的片段（如"练完腿"里的"完腿"）——不静默丢，带进图里
        # 触发消歧节点问用户。见 memory_extract._resolve_exercise 的说明。
        exercise_clarify: list = []
        forgotten: list = []
        try:
            from app.graph.memory_extract import apply_memory_extract
            acks = apply_memory_extract(message, sess.user_id,
                                        unresolved=exercise_clarify,
                                        forgotten=forgotten) or []
        except Exception:
            pass
        # **清除要两头都清。** `forget` 只删图谱节点，而档案查询读的是会话缓存
        # （`nodes._ctx` 用 sess.profile）—— 只清图谱的话，回复说"已清除"、
        # 用户再问却还看得见（实测复现）。
        if forgotten:
            sess.profile.clear()
        # 记忆图谱单向投影：图谱 current 态为真相源（v1 单用户 local 或注入 uid），
        # 新会话建档/记忆变更后投影回会话缓存（写只写图谱，读投影重建）
        self._project_memory_profile(sess)
        # 无 checkpointer（默认）：每轮独立 invoke，不传 thread 配置——
        # 挂了检查点才会按 thread 累积 state（内存随轮次只增不减）。
        state_in = {"session_id": sess.id, "message": message,
                    "profile": dict(sess.profile), "user_id": sess.user_id,
                    "memory_ack": acks,
                    # 有没对上的动作 → classify 会把 mode 改成 clarify_exercise
                    "exercise_clarify": exercise_clarify,
                    # 最近 3 轮对话（classify 上下文消解："确认"承接上轮提议）
                    "history": list(sess.history[-6:]),
                    # 上一轮结果的结构化留档（同上，省略式追问要读"刚才那批是哪一档"）
                    "last_structured": sess.last_structured}
        if self._checkpointer is not None:
            thread = f"{sess.id}-{len(sess.history)}"   # 每轮独立 thread
            result = self.graph.invoke(
                state_in, config={"configurable": {"thread_id": thread}})
        else:
            result = self.graph.invoke(state_in)

        # structured 单源组装（与 render 节点同一份逻辑，防双写漂移）；
        # 顶层 provenance 传入，guard 短路时仍保留守卫来源标注
        structured = build_structured(result.get("intent"), result.get("outcome"),
                                      sources=result.get("provenance", []))
        if result.get("memory_ack"):      # API structured 与 render 节点同源透出
            structured["memory_ack"] = list(result["memory_ack"])
        reply = result.get("reply", "")
        # 动作卡片顺序跟正文（2026-09-16 用户报「卡片和文字对应不上」）：卡片按
        # items 原序渲染、正文语序由 LLM 自定，是同一批动作的两次排序；不绑的话
        # 用户认不出哪句话配哪张卡。见 render_util.bind_items_to_reply。
        bind_items_to_reply(structured, reply)
        sess.history.append({"role": "user", "text": message})
        sess.history.append({"role": "assistant", "text": reply})
        # 上一轮结果的**结构化**留档（Session.last_structured 至此才有写点）。
        # 会话里只存渲染后的文本，而省略式追问（"最难的是哪些""没有中级的吗"，
        # 见 qa_skill._followup）要回读"刚才那批是什么档位"——读文本只能靠猜。
        # 存的就是 return 出去的那一份，不再复制，免得两处漂。
        sess.last_structured = structured
        return ChatResponse(session_id=sess.id, reply=reply,
                            structured=structured,
                            mode_used=result.get("mode_used", "direct"),
                            provenance=result.get("provenance", []),
                            guard=result.get("guard_data"))

    # ------------------------------------------------------------ 建档
    @staticmethod
    def _project_memory_profile(sess: Session) -> None:
        """记忆图谱 current 态 → 会话 profile 单向投影（写只写图谱，读投影重建）。

        **必须是"替换"而不是"只增不减"**：原先写的是
        `if p: sess.profile.update(p)`，图谱为空时整段跳过，于是会话缓存里的档案
        **永远不会消失** —— 用户清除数据后，回复说"已清除"、他再问却还看得见
        （实测复现）。同一个漏洞还有一个更隐蔽的门：在会话 B 里清除，会话 A 的
        缓存从没被清过，A 里永远看得到。

        用 `current_profile_checked` 区分"空"与"没读到"：
          · None（读失败）→ **保持现状**，绝不能用空值覆盖（那会把用户档案抹掉）
          · {}（读到且确实为空）→ 权威值就是空，必须清
        """
        try:
            from app.graph.memory import MemoryStore
            m = MemoryStore.get()
            if m is None:
                return                          # 图谱不可用 → 会话独立建档（降级链不变）
            p = m.current_profile_checked(sess.user_id)
            if p is None:
                return                          # 没读到 ≠ 没有，保持现状
            sess.profile = dict(p)
        except Exception:
            diag.bump("agent.project_profile")   # 投影失败 → 会话用旧档案

    def get_profile(self, session_id: str,
                    user_id: str | None = None) -> dict | None:
        """读档案。**与对话路径同源**：记忆图谱的 current 态是真相源。

        `user_id` 只在会话缓存缺失时才用得上（那时没有 `sess.user_id` 可依，
        只能由调用方告知归属）。**不传就照旧返回 None** —— 猜一个 'local'
        在多用户下等于读错人的档案，宁可不显示也不显示错的。
        """
        sess = self.sessions.get(session_id)
        if sess is not None:
            # 读路径**也要投影**。原先这里直接 `dict(sess.profile)`，而投影只在
            # `run` 里发生 —— 于是"会话缓存没了、图谱还在"时读出来是空档案，
            # 聊一句又冒出来。用户实测报的正是这个（2026-09-15）。
            #
            # `SessionManager` 是**纯内存**（无任何持久化），图谱是落盘的，
            # 两者不同步的窗口很宽：后端重启 / 换浏览器 / 清 localStorage / 换设备。
            # 用户看到的不是"档案丢了"，是**读不出来** —— 而且他自己说句话就能
            # "修好"，所以很容易被当成偶发。
            self._project_memory_profile(sess)
            return dict(sess.profile)

        if not user_id:
            return None
        # 会话不在缓存里（刚重启 / 换浏览器 / 新 id）：直接从图谱读一次。
        #
        # **刻意不创建会话** —— GET 若能建会话，任何人拿任意 id 反复打这个端点
        # 都能让内存表无上限增长，那是个不用认证的 DoS 面。多查一次图谱便宜得多。
        try:
            from app.graph.memory import MemoryStore
            m = MemoryStore.get()
            if m is None:
                return None                    # 图谱不可用 → 降级为"没有"
            p = m.current_profile_checked(user_id)
            # None（没读到）与 {}（确实为空）在这里都算"没有档案"：
            # 前者不能当空（那会误导），后者本来就是空。两种都回 404，
            # 前端据此开一张空表单。
            return dict(p) if p else None
        except Exception:
            diag.bump("agent.profile_read")    # 读失败 → 当作没有，绝不抛
            return None

    def update_profile(self, session_id: str, profile: dict,
                       user_id: str | None = None) -> dict:
        """合并更新会话档案（未传字段保留）；非法字段抛 ValueError（details 列表）。
        记忆图谱为真相源：写路径写图谱（默认 local / 注入 uid），会话缓存为投影。"""
        errors = validate_profile(profile)
        if errors:
            raise ValueError("; ".join(errors))
        sess = self.sessions.get(session_id) or self.sessions.create(
            session_id, user_id=user_id)
        if user_id:
            sess.user_id = user_id
        # **先投影再合并，顺序不能反。** 新会话的缓存是空的，直接 `update` 的话
        # 缓存里就只剩这次提交的字段，返回值与后续 GET 都缺其余项 —— 用户的感受是
        # "我只改了个体重，其他信息怎么全没了"。
        # 先投影把图谱里的完整档案拉回来，合并之后 `upsert_profile` 写回去的
        # 也是完整那一份。
        self._project_memory_profile(sess)
        sess.profile.update(profile)
        try:
            from app.graph.memory import MemoryStore
            m = MemoryStore.get()
            if m is not None:
                m.upsert_profile(sess.user_id, dict(sess.profile))
        except Exception:
            # 图谱不可用 → 会话独立建档（现状）。**但这会与图谱分叉**：会话有、
            # 图谱没有，下一轮的投影（替换语义）会把它抹掉。留个痕，好在
            # /health 的 degraded 里看见 —— 此前是裸 pass，分叉了也没人知道。
            diag.bump("agent.profile_write")
        return dict(sess.profile)


def _split_known(v) -> bool:
    """split 须在数据包 id/名称/别名内；数据包缺失/损坏（兜底生效）或
    加载失败 → 放行（与 resolve_scheme 静默回落默认的降级方向一致）。"""
    if not isinstance(v, str):
        return False
    try:
        import split_cycle
        names = split_cycle.scheme_names()
        if not names or not split_cycle.schemes_available():
            return True
    except Exception:
        return True
    return v in names or v.lower() in {str(n).lower() for n in names}


def validate_profile(profile: dict) -> list[str]:
    """建档字段校验（系统入口边界）：返回错误列表，空=合法。"""
    errors: list[str] = []
    rules = {
        "sex": lambda v: v in ("male", "female"),
        "age": lambda v: isinstance(v, int) and 1 <= v <= 120,
        "height_cm": lambda v: isinstance(v, (int, float)) and 50 <= v <= 250,
        "weight_kg": lambda v: isinstance(v, (int, float)) and 5 <= v <= 400,
        "activity": lambda v: isinstance(v, (int, float)) and 1.0 <= v <= 3.0,
        "goal": lambda v: v in ("lose_fat", "maintain", "build_muscle"),
        "deficit_kcal": lambda v: isinstance(v, (int, float)) and 0 <= v <= 2000,
        "protein_g_per_kg": lambda v: isinstance(v, (int, float)) and 0 <= v <= 4,
        "fat_g_per_kg": lambda v: isinstance(v, (int, float)) and 0 <= v <= 3,
        "conditions": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
        "patterns": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
        "split": _split_known,
    }
    for key, ok in rules.items():
        if key in profile and not ok(profile[key]):
            errors.append(f"{key} 取值非法: {profile[key]!r}")
    return errors