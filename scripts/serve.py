# -*- coding: utf-8 -*-
"""启动后端 API（供 web/ 的 3D 视图联调）。

**端口必须与 `web/vite.config.ts` 的 proxy 一致**（现为 8000）——vite dev 把 `/v1`
代理到这里，浏览器同源访问，因此不需要 CORS 配置。

用法：
    python scripts/serve.py                 # 127.0.0.1:8000
    python scripts/serve.py --port 8001     # 自定义（记得同步改 vite proxy）
    python scripts/serve.py --reload        # 开发热重载

联调自检（另开一个终端）：
    curl "http://127.0.0.1:8000/v1/muscle-map?user_id=local"
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

# 与 web/vite.config.ts 的 proxy 目标保持一致；改这里要同步改那边
DEFAULT_PORT = 8000


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--reload", action="store_true",
                    help="改动即重载（开发用；首次启动稍慢）")
    ap.add_argument("--certfile", default=None,
                    help="TLS 证书（PEM）。与 --keyfile 一起给 = 以 https 启动。"
                         "局域网下手机的麦克风/摄像头只在安全上下文可用，"
                         "见 storage_output/certs/ 的生成说明")
    ap.add_argument("--keyfile", default=None,
                    help="TLS 私钥（PEM）")
    args = ap.parse_args(argv)

    ssl_kw = {}
    if args.certfile or args.keyfile:
        if not (args.certfile and args.keyfile):
            ap.error("--certfile 与 --keyfile 必须一起给")
        ssl_kw = {"ssl_certfile": args.certfile, "ssl_keyfile": args.keyfile}

    import uvicorn
    scheme = "https" if ssl_kw else "http"
    print(f"后端启动：{scheme}://{args.host}:{args.port}"
          f"（web/vite.config.ts 的 proxy 指向此端口）")
    uvicorn.run("app.server:create_app", factory=True,
                host=args.host, port=args.port, reload=args.reload, **ssl_kw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
