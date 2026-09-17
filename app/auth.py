# -*- coding: utf-8 -*-
"""局域网多用户的登录注册：账号存储 + 签名 Cookie 会话 + 访问网关。

**威胁模型**（先说清楚这东西防什么、不防什么）：
- 防：局域网里的人**误用 / 串用**别人的身份（裸 URL 落 `local` 的那类事故），
      以及"完全没有任何门槛"的裸奔。
- 不防：公网攻击者。HTTP 明文传输 + 无防爆破限速 + Cookie 无 Secure 标志
      （LAN 走 HTTP，加 Secure 会直接失效）—— **别把 8000 端口映射到公网**。

组成：
- 账号存 **SQLite**（`user_account` 表；口令 pbkdf2_hmac 哈希，**不存明文**）。
  放 SQLite 而不是图谱：口令是"访问凭证"不是"健身事实"，混进记忆图谱
  会让图谱的导出 / 审计 / 迁移都背上一份敏感数据。
- 会话 = **签名 Cookie**（HMAC-SHA256，HttpOnly）。**无服务端状态** ——
  进程重启不掉登录，也不需要会话表。Secret 从 `AUTH_SECRET` 读，
  没配则自动生成并落 `storage_output/auth_secret.key`（gitignore 内）。
- 网关 `AuthGate`（**纯 ASGI**，不用 BaseHTTPMiddleware —— 它会缓冲响应体，
  本项目的 `/v1/chat/stream` 是 SSE，会整套被拖死）：
    · `/v1/*`（白名单除外）必须持有效 Cookie
    · 并把请求里的 `user_id`（query / JSON body）**改写成登录者** ——
      这一句才是数据隔离真正的 enforcement 点：改写之后，
      前端发什么 `user_id` 都不重要，处理器永远只看到登录者本人。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

COOKIE_NAME = "fm_session"
"""会话 Cookie 名。HttpOnly + SameSite=Lax；**没有 Secure**（LAN 走 HTTP）。"""

SESSION_TTL_SECONDS = 30 * 24 * 3600
"""登录态有效期 30 天。过期后网关视为未登录，前端重新登录即可。"""

PBKDF2_ITERATIONS = 600_000
"""OWASP 2023 对 PBKDF2-HMAC-SHA256 的建议档位。登录一次约 0.2s，可接受。"""

USER_ID_RE = re.compile(r"^[a-z0-9_-]{2,24}$")
"""user_id 即登录名。限制字符集：它要出现在 URL / Neo4j / 文件名语义里，
大小写会被 Windows 与 URL 处理得不一致，所以**只收小写**。"""

MIN_PASSWORD_LEN = 6

_DEFAULT_DB = Path(__file__).resolve().parents[1] / "storage_output" / "auth.db"
_DEFAULT_SECRET = (Path(__file__).resolve().parents[1] / "storage_output"
                   / "auth_secret.key")
# ⚠ 是 **parents[1]**（= 仓库根）。auth.py 在 app/ 下只隔一层；照抄三层深的
# db.py（app/storage/db.py）曾把账号库写到 **E:\storage_output\**（仓库外）——
# 注册"成功"、检查"没表"，两头都对不上。路径类常量改完必须实盘验证落点。


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def validate_user_id(user_id: str) -> str:
    """登录名合法性校验（注册入口用）。返回归一后的值，不合法抛 ValueError。"""
    uid = (user_id or "").strip().lower()
    if not USER_ID_RE.match(uid):
        raise ValueError("用户名限 2-24 位：小写字母 / 数字 / _ / -")
    return uid


def validate_password(pw: str) -> str:
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD_LEN:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LEN} 位")
    return pw


# ---------------------------------------------------------------------------
# 口令哈希（stdlib，零新依赖 —— 不为此引入 bcrypt）
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比较；格式不对（历史脏数据）一律 False 而不是抛。"""
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 账号存储（SQLite；连接按线程重连，同 LogStore 的做法 —— TestClient 会换线程）
# ---------------------------------------------------------------------------

class AccountStore:
    """登录账号（user_id → 口令哈希）。与用户数据（图谱）**分离**：
    这里只有"谁能登录"，没有任何健身数据 —— 图谱导出 / 审计不背口令。"""

    def __init__(self, path: Path | str | None = None):
        # ⚠ 路径在**调用时**解析（而不是默认参数绑定）：测试要 monkeypatch
        # `app.auth._DEFAULT_DB` 指到 tmp_path —— 默认参数绑定会让 patch 失效。
        self._path = Path(path) if path else _DEFAULT_DB
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._tid = None
        self._conn = None
        self._ensure()

    def _ensure(self):
        tid = threading.get_ident()
        if self._conn is not None and tid == self._tid:
            return
        self._tid = tid
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS user_account("
            " user_id TEXT PRIMARY KEY,"
            " pw_hash TEXT NOT NULL,"
            " created_at TEXT NOT NULL)")
        self._conn.commit()

    def create(self, user_id: str, password: str) -> bool:
        """注册。用户名已存在 → False（注册页据此提示，而不是 500）。"""
        self._ensure()
        try:
            self._conn.execute(
                "INSERT INTO user_account(user_id,pw_hash,created_at) VALUES(?,?,?)",
                (user_id, hash_password(password), _now()))
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def exists(self, user_id: str) -> bool:
        self._ensure()
        cur = self._conn.execute(
            "SELECT 1 FROM user_account WHERE user_id=?", (user_id,))
        return cur.fetchone() is not None

    def verify(self, user_id: str, password: str) -> bool:
        """登录校验。用户不存在与密码错误**返回同一个 False** ——
        不区分两者能让探测者分不清"这个用户名有没有注册"。"""
        self._ensure()
        cur = self._conn.execute(
            "SELECT pw_hash FROM user_account WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row is None:
            # 仍做一次哈希：让"不存在"与"密码错"耗时一致（防时序侧信道）
            hash_password(password)
            return False
        return verify_password(password, row[0])

    def count(self) -> int:
        self._ensure()
        return int(self._conn.execute("SELECT COUNT(*) FROM user_account").fetchone()[0])


# ---------------------------------------------------------------------------
# 签名 Cookie 会话（无服务端状态）
# ---------------------------------------------------------------------------

class CookieSession:
    """签名会话令牌：`user_id|过期秒|hmac`。无服务端存储 → 重启不掉登录。"""

    def __init__(self, secret: str | None = None, cookie_name: str = COOKIE_NAME):
        self.secret = self._load_secret(secret)
        self.cookie_name = cookie_name

    @staticmethod
    def _load_secret(secret: str | None) -> str:
        """优先环境变量 `AUTH_SECRET`；没有则生成并落盘（固定下来，重启不掉登录）。"""
        if secret:
            return secret
        env = os.environ.get("AUTH_SECRET")
        if env:
            return env
        try:
            if _DEFAULT_SECRET.exists():
                return _DEFAULT_SECRET.read_text(encoding="utf-8").strip()
            s = secrets.token_hex(32)
            _DEFAULT_SECRET.parent.mkdir(parents=True, exist_ok=True)
            _DEFAULT_SECRET.write_text(s, encoding="utf-8")
            return s
        except Exception:
            # 落盘失败（只读盘等）→ 用进程内随机 secret：本次运行内可用，
            # 重启后所有人需重新登录（可接受的降级，不该让服务起不来）
            return secrets.token_hex(32)

    def issue(self, user_id: str) -> str:
        expires = int(time.time()) + SESSION_TTL_SECONDS
        payload = f"{user_id}|{expires}"
        sig = hmac.new(self.secret.encode("utf-8"), payload.encode("utf-8"),
                       hashlib.sha256).hexdigest()
        return f"{payload}|{sig}"

    def verify(self, token: str | None) -> str | None:
        """校验签名与有效期，返回 user_id；无效一律 None（不区分原因，防探测）。"""
        if not token:
            return None
        parts = token.split("|")
        if len(parts) != 3:
            return None
        user_id, expires, sig = parts
        payload = f"{user_id}|{expires}"
        good = hmac.new(self.secret.encode("utf-8"), payload.encode("utf-8"),
                        hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        if int(expires) < time.time():
            return None
        # user_id 是自己签发的，但回读时仍要过一次字符集校验（防历史脏数据）
        return user_id if USER_ID_RE.match(user_id) else None

    def set_cookie_header(self, user_id: str) -> str:
        return (f"{self.cookie_name}={self.issue(user_id)}; Path=/; Max-Age={SESSION_TTL_SECONDS}; "
                "HttpOnly; SameSite=Lax")

    def clear_cookie_header(self) -> str:
        return f"{self.cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"

    @staticmethod
    def cookie_of(scope: dict) -> str | None:
        """从 ASGI scope 的请求头里取会话 Cookie 值（无 / 无效 → None）。"""
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or []}
        raw = headers.get("cookie", "")
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            if k.strip() == COOKIE_NAME:
                return v.strip()
        return None

    def user_from_scope(self, scope: dict) -> str | None:
        return self.verify(self.cookie_of(scope))


# ---------------------------------------------------------------------------
# 网关（纯 ASGI 中间件）
# ---------------------------------------------------------------------------

_EXEMPT_PATHS = ("/health", "/v1/auth/register", "/v1/auth/login")
"""免登录白名单：健康检查 + 注册 / 登录本身。
`/v1/auth/logout`、`/v1/auth/me` **不在**白名单 —— 它们的语义就是"已登录"。"""

_DOCUMENT_PATHS = ("/", "/app", "/app/", "/index.html")
"""浏览器会以"整页导航"方式打开的路径。未登录访问它们 → 302 去登录页；
其余路径（/v1/* API）→ 401 JSON，让前端 fetch 拿到明确状态码而不是一坨 HTML。"""


def _is_document(scope: dict) -> bool:
    """是不是浏览器整页导航（而不是前端 fetch）。
    用 `sec-fetch-mode` 判（现代浏览器都带）；判不出来再按路径兜底。"""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1")
               for k, v in scope.get("headers") or []}
    mode = headers.get("sec-fetch-mode")
    if mode:
        return mode == "navigate"
    return scope.get("path", "") in _DOCUMENT_PATHS


class AuthGate:
    """访问网关：/v1/* 一律要登录，并把 user_id **改写成本人**。

    为什么改写而不是"校验不一致就拒绝"：处理器里 `user_id` 的缺省是 `local`
    （**主人的数据**）。只校验不改写的话，"不带 user_id 的请求"依然落到 local，
    等于给每个登录者留了一扇看主人数据的门。改写则无论前端发什么，
    处理器只看得到登录者本人 —— 缺省漏洞被结构性堵死。

    为什么是纯 ASGI：`BaseHTTPMiddleware` 会缓冲响应体，本项目的
    `/v1/chat/stream` 是 SSE，流式会被整套拖死。
    """

    def __init__(self, app, session: CookieSession,
                 accounts: "AccountStore | None" = None,
                 login_path: str = "/login"):
        self.app = app
        self.session = session
        self.accounts = accounts
        self.login_path = login_path

    def _enabled(self) -> bool:
        """网关是否启用 —— **账号表为空即未启用**（初始化态）。

        为什么这么设计：一旦启用，所有不带 Cookie 的请求都会 401，包括
        现有的几十个测试与"第一次打开还没注册"的体验。约定成：
        **注册第一个账号的那一刻，门禁生效**；在那之前系统行为与从前逐字一致。
        SQLite 对小表 COUNT 是微秒级，每请求查一次可接受。"""
        if self.accounts is None:
            return False
        try:
            return self.accounts.count() > 0
        except Exception:
            return False        # 记不了账就别拦人 —— 沿用"全降级、绝不抛出"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in _EXEMPT_PATHS or path == self.login_path:
            return await self.app(scope, receive, send)
        if not self._enabled():
            return await self.app(scope, receive, send)

        user = self.session.user_from_scope(scope)
        if user is None:
            if path in _DOCUMENT_PATHS or _is_document(scope):
                return await _redirect(send, f"{self.login_path}?next={path}")
            return await _json_response(send, 401, {"ok": False,
                                                    "error": "未登录或登录已过期"})

        if path.startswith("/v1/"):
            scope, receive = await self._rewrite_user_id(scope, receive, user)
        await self.app(scope, receive, send)

    async def _rewrite_user_id(self, scope, receive, user: str):
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or []}
        # 1) query string（GET 类端点：muscle-map / plan / profile）
        qs = scope.get("query_string") or b""
        pairs = [(k, v) for k, v in parse_qsl(qs.decode("latin-1"), keep_blank_values=True)
                 if k != "user_id"]
        pairs.append(("user_id", user))
        scope["query_string"] = urlencode(pairs).encode("latin-1")
        # 2) JSON body（POST 类端点：chat / checkin / profile / plan-delete）
        if headers.get("content-type", "").startswith("application/json"):
            body = await _read_body(receive)
            new_body = body
            if body:
                try:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        data["user_id"] = user
                        new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                except Exception:
                    new_body = body          # 解析不动 → 原样放行，让下游如实报错
            receive = _replay_receive(new_body, receive)
        return scope, receive


# ---------------------------------------------------------------------------
# ASGI 小工具
# ---------------------------------------------------------------------------

async def _read_body(receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        msg = await receive()
        chunks.append(msg.get("body", b"") or b"")
        if not msg.get("more_body", False):
            break
    return b"".join(chunks)


def _replay_receive(body: bytes, receive):
    """把改写后的 body 交给下游，之后**原样透传真 receive**。

    ⚠ 曾经这里是"body 之后一律返回 `http.disconnect`"，理由是"请求体已在网关处
    读完并缓存"。那个理由是**错的**：下游除了读 body，还会用 receive 判断
    **客户端还在不在**——Starlette 的 `StreamingResponse` 专门起一个任务跑
    `listen_for_disconnect`，收到 disconnect 就 `cancel_scope.cancel()` 把整个响应
    取消掉（`starlette/responses.py:230-259`）。于是假造的 disconnect 让 SSE 响应
    在发出 `http.response.start` **之前**就被取消：

      · 若外层还有 `GZipMiddleware`（浏览器带 `Accept-Encoding: gzip` 即生效），
        它把 response.start 扣在 `initial_message` 里等首个 chunk
        （`starlette/middleware/gzip.py:43-46`），永远等不到 →
        uvicorn 打 `ASGI callable returned without starting response.` + **500**
      · 没有 gzip 时 → 200 但**一个 chunk 都没有**（前端空转，比 500 更难查）

    线上实盘：`POST /v1/chat/stream` 恒 500（serve8443.err.log 刷屏），
    根因就是这一句。最小复现与四臂对照：`storage_output/repro_sse_gate.py`
    （`fake+挂起+gzip` = 0/5 且 send 序列为空；本修法 = 完整 200）。

    修法即"回落"：`http.request` 之后的每一条消息都由真 receive 决定——
    "客户端断没断"这个事实只有上游知道，网关无权代答。
    """
    state = {"done": False}

    async def replayed_receive():
        if not state["done"]:
            state["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()          # ← 不代答：交还真上游

    return replayed_receive


async def _json_response(send, status: int, obj: dict) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _redirect(send, location: str) -> None:
    await send({"type": "http.response.start", "status": 302,
                "headers": [(b"location", location.encode("latin-1")),
                            (b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})
