"""Small invitation-only trial with isolated native runtimes and a USD meter.

Run this operator service outside tenant namespaces. Native Copilot inside a
tenant receives only that tenant's metered credential, never the provider login.
Deployment credentials, database and tenant directories stay outside Git.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from websockets.legacy.client import unix_connect

try:
    from .preview_hosts import preview_domain, preview_host, preview_template
    from .trial_pages import entry_page
except ImportError:  # Direct deployment script entry point.
    from preview_hosts import preview_domain, preview_host, preview_template
    from trial_pages import entry_page

COOKIE = "__Host-argus_preview_session"
MODEL = "gpt-6.1-sol"
MICRO = 1_000_000
# The model relay's own limit (copilot-codex-relay MAX_REQUEST_BYTES): past it the
# relay drops the connection unanswered, so the meter says why instead.
MAX_BODY = 16 * 1024 * 1024
MIN_ADMIT = 10_000  # $0.01 in micro-dollars
UPLOAD_LIMIT = 256 * 1024 * 1024  # one browser upload, buffered by the portal
RECEIPTS = ("response.completed", "response.incomplete", "response.failed")


async def bounded_body(request: Request, maximum: int) -> bytes:
    result = bytearray()
    async for chunk in request.stream():
        if len(result) + len(chunk) > maximum:
            raise ValueError("Request body limit")
        result.extend(chunk)
    return bytes(result)


class Ledger:
    def __init__(self, path: Path, *, total_usd: float = 50, user_usd: float = 10, allowances: dict[str, float] | None = None):
        self.path = path
        self.total = round(total_usd * MICRO)
        self.user = round(user_usd * MICRO)
        # An invitation may carry its own allowance (its tenant's user_usd in preview.json); the rest share user_usd.
        self.allowances = {tenant: round(float(usd) * MICRO) for tenant, usd in (allowances or {}).items()}
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS charges (
                  id TEXT PRIMARY KEY, tenant TEXT NOT NULL,
                  amount INTEGER NOT NULL CHECK(amount >= 0),
                  state TEXT NOT NULL, created REAL NOT NULL);
            """)
            db.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, total INTEGER, per_user INTEGER)")
            db.execute("INSERT OR IGNORE INTO policy VALUES (1,?,?)", (self.total, self.user))
            stored = db.execute("SELECT total,per_user FROM policy WHERE id=1").fetchone()
            if stored != (self.total, self.user):
                raise ValueError("Existing ledger limits cannot be reset by configuration")
            db.commit()
        path.chmod(0o600)

    def charge(self, tenant: str, usd: float) -> None:
        if not math.isfinite(usd) or usd < 0:
            raise ValueError("Invalid charge")
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("INSERT INTO charges VALUES(?,?,?,?,?)",
                       (secrets.token_hex(16), tenant, math.ceil(usd * MICRO), "settled", time.time()))
            db.commit()

    def usage(self, tenant: str) -> tuple[int, int, int]:
        """The tenant's allowance, what it has used and what the whole trial has used."""
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            used = db.execute("SELECT COALESCE(SUM(amount),0) FROM charges WHERE tenant=?", (tenant,)).fetchone()[0]
            total = db.execute("SELECT COALESCE(SUM(amount),0) FROM charges").fetchone()[0]
        return self.allowances.get(tenant, self.user), used, total

    def refusal(self, tenant: str) -> str:
        """Why the tenant's next call is not forwarded ("" if it is): the meter's 402 and the page's notice.
        Calls already running are charged at their receipts, so they can overdraw what was left."""
        limit, used, total = self.usage(tenant)
        if min(self.total - total, limit - used) >= MIN_ADMIT:
            return ""
        whose = (f"这一版全站共 {self.total / MICRO:g} 美元的额度" if self.total - total < limit - used
                 else f"这个邀请码的 {limit / MICRO:g} 美元")
        return f"试用额度已用完：{whose}已经用完，任务已暂停，已有文件都保留着。需要继续请联系提供邀请码的人加额度。"

    def status(self, tenant: str) -> dict:
        limit, used, total = self.usage(tenant)
        return {"model": MODEL, "limit_usd": limit / MICRO, "used_usd": used / MICRO,
                "remaining_usd": max(0, limit - used) / MICRO,
                "total_limit_usd": self.total / MICRO,
                "total_remaining_usd": max(0, self.total - total) / MICRO,
                "exhausted": self.refusal(tenant)}


def token_cost(response) -> float | None:
    """Price a Responses result from the usage its upstream reports; ``None`` when there is none."""
    from argus.core.pricing import quote_token_usage

    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None
    counts = [usage.get("input_tokens"), usage.get("output_tokens")]
    if any(type(value) is not int or value < 0 for value in counts):
        return None
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens")
    quote = quote_token_usage(MODEL, input_tokens=counts[0], output_tokens=counts[1],
                              cached_input_tokens=cached if type(cached) is int else 0)
    return quote.cost_usd if quote.status == "priced" else None


def load_config(path: Path) -> dict:
    value = json.loads(path.read_text())
    if value.get("model") != MODEL or not value.get("tenants"):
        raise ValueError("Preview requires gpt-6.1-sol and configured tenants")
    if value.get("runtime") == "js" and (domain := preview_domain(value.get("preview_domain"))):
        for tenant in value["tenants"]:
            preview_template(tenant, domain)
    return value


def model_app(config: dict, ledger: Ledger) -> FastAPI:
    """The tenants' model endpoint, a pipe to the upstream: it swaps the tenant's
    token for the upstream key, forwards the request unchanged, charges what the
    upstream's receipt reports and answers 402 once an allowance is spent."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    tokens = {row["model_token"]: name for name, row in config["tenants"].items()}
    upstream = config["upstream"]
    url = upstream["base_url"].rstrip("/") + "/responses"

    def identify(request: Request) -> str | None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        return next((name for token, name in tokens.items() if secrets.compare_digest(supplied, token)), None)

    def charge(tenant: str, usd: float | None) -> None:
        if usd is None:
            print(f"meter: {tenant} call ended without a usage receipt; not charged", flush=True)
        else:
            ledger.charge(tenant, usd)

    @app.get("/healthz")
    async def health():
        return {"status": "ready", "model": MODEL}

    @app.get("/v1/models")
    async def models(request: Request):
        if identify(request) is None:
            return JSONResponse({"error": {"message": "需要试用授权。"}}, 401)
        return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "argus-preview"}]}

    @app.post("/v1/responses")
    async def responses(request: Request):
        tenant = identify(request)
        if tenant is None:
            return JSONResponse({"error": {"message": "需要试用授权。"}}, 401)
        if refusal := ledger.refusal(tenant):
            return JSONResponse({"error": {"code": "trial_budget_exhausted", "message": refusal}}, 402)
        try:
            body = await bounded_body(request, MAX_BODY)
        except ValueError:
            return JSONResponse({"error": {"message": f"这次请求超过模型中转单次 {MAX_BODY // 1024 // 1024} MB 的上限，没有发出。"}}, 413)
        client = httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=15), follow_redirects=False, trust_env=False)
        try:
            key = Path(upstream["token_file"]).read_text().strip()
            reply = await client.send(client.build_request("POST", url, content=body, headers={
                "Authorization": "Bearer " + key,
                "Content-Type": request.headers.get("content-type", "application/json")}), stream=True)
        except Exception as exc:
            await client.aclose()
            print(f"meter: {tenant} upstream unreachable: {type(exc).__name__}: {exc}", flush=True)
            return JSONResponse({"error": {"message": f"计量连不上模型中转：{type(exc).__name__}: {exc}"}}, 502)
        kind = reply.headers.get("content-type", "application/json")
        if not kind.startswith("text/event-stream"):
            try:
                content = await reply.aread()
            finally:
                await client.aclose()
            if reply.status_code != 200:
                print(f"meter: {tenant} upstream {reply.status_code}: {content[:300].decode(errors='replace')}", flush=True)
            else:
                try:
                    usd = token_cost(json.loads(content))
                except ValueError:
                    usd = None
                charge(tenant, usd)
            return Response(content, reply.status_code, media_type=kind)

        async def relay():
            usd = None
            try:
                async for line in reply.aiter_lines():
                    if usd is None and line.startswith("data:") and '"usage"' in line:
                        try:
                            event = json.loads(line[5:])
                        except ValueError:
                            event = None
                        if isinstance(event, dict) and event.get("type") in RECEIPTS:
                            usd = token_cost(event.get("response"))
                    yield (line + "\n").encode()
            finally:
                try:
                    charge(tenant, usd)
                finally:
                    await client.aclose()

        return StreamingResponse(relay(), reply.status_code, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    return app


RECOVERING = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5">
<title>工作区暂时不可用 · Argus</title><style>body{font:16px system-ui;background:#f6f7f9;color:#172333;margin:0}main{max-width:440px;margin:12vh auto;padding:32px;background:white;border-radius:18px}p{line-height:1.7;color:#536071}a{color:#2069d1}</style>
<main><h1>正在重新连接你的工作区</h1><p>{reason}</p><p>你的文件和试用余额已保留。页面每 5 秒重试连接。</p><p>如果一直无法连接，请联系提供邀请码的人。</p><a href="/">立即重试</a></main></html>"""


def unreachable_reason(row: dict) -> str:
    """Why a tenant's workspace does not answer, in words its user can act on."""
    try:
        pause = json.loads((Path(row["directory"]) / "state" / "storage-pause.json").read_text())
        used, limit = pause["used_bytes"] / 1024**3, pause["limit_bytes"] / 1024**3
    except (OSError, ValueError, KeyError, TypeError):
        return "工作区正在重启（通常是更新或自动恢复），一般几秒钟就好。"
    return (f"这个邀请码的工作区已经用了 {used:.1f} GB，超过 {limit:.0f} GB 的存储上限，所以运行暂停了。"
            "请联系提供邀请码的人清理文件或增加空间，之后会自动接着做。")


class PreviewHosts:
    """Intercept preview hosts before any portal HTTP or WebSocket endpoint."""

    def __init__(self, app, config: dict):
        self.app = app
        self.config = config
        self.domain = preview_domain(config.get("preview_domain"))

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        reserved, tenant = preview_host(headers.get(b"host", b"").decode("latin-1"), self.domain)
        if not reserved:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await receive()
            return await send({"type": "websocket.close", "code": 4404})
        if self.config.get("runtime") != "js" or tenant not in self.config["tenants"]:
            response = Response(status_code=404)
        elif scope["method"] not in ("GET", "HEAD"):
            response = Response(status_code=405, headers={"Allow": "GET, HEAD"})
        else:
            response = await self.forward(Request(scope, receive), self.config["tenants"][tenant])
        await response(scope, receive, send)

    async def forward(self, request: Request, row: dict):
        # Allow only public file request headers; never relay browser credentials
        # or the portal's metered/runtime/relay tokens to a preview origin.
        headers = {k: v for k, v in request.headers.items() if k in (
            "host", "origin", "accept", "accept-language", "range", "if-range",
            "if-none-match", "if-modified-since", "user-agent")}
        client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=row["socket"]),
                                   timeout=httpx.Timeout(None, connect=10), trust_env=False)
        path = request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
        query = request.scope.get("query_string", b"").decode("ascii")
        try:
            response = await client.send(client.build_request(request.method, "http://localhost" + path +
                                                              ("?" + query if query else ""), headers=headers), stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return Response(status_code=503, headers={"Retry-After": "5"})
        out = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in response.headers.multi_items()
               if k not in ("transfer-encoding", "connection", "keep-alive")]

        async def chunks():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        result = StreamingResponse(chunks(), status_code=response.status_code)
        result.raw_headers = out
        return result


def portal_app(config: dict, ledger: Ledger) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(PreviewHosts, config=config)
    invites = {row["invite"]: name for name, row in config["tenants"].items()}
    def login_html(*, invite: str = "", error: bool = False) -> str:
        return entry_page(ledger.user / MICRO, runtime=config.get("runtime", "py"),
                          title=config.get("title"), invite=invite, error=error)

    # A session is a random token the portal holds, not a signed claim. It is
    # kept as issued next to the ledger so a portal restart (every deploy) does
    # not sign everyone out. Whoever can read this file (0600, the operator)
    # can sign in as any of its tenants.
    sessions_path = Path(config.get("sessions") or ledger.path.with_name("sessions.sqlite3"))
    with closing(sqlite3.connect(sessions_path)) as db:
        db.executescript("""
            DROP TABLE IF EXISTS sessions;  -- hashed sessions of earlier releases
            CREATE TABLE IF NOT EXISTS logins (token TEXT PRIMARY KEY, tenant TEXT NOT NULL, expiry REAL NOT NULL);
        """)
    sessions_path.chmod(0o600)

    def session(tenant: str) -> str:
        now = time.time()
        token = secrets.token_urlsafe(32)
        with closing(sqlite3.connect(sessions_path, timeout=10)) as db:
            db.execute("DELETE FROM logins WHERE expiry <= ?", (now,))
            db.execute("INSERT INTO logins VALUES (?,?,?)", (token, tenant, now + 7 * 86400))
            db.commit()
        return token

    def identify(cookie: str) -> str | None:
        with closing(sqlite3.connect(sessions_path, timeout=10)) as db:
            rows = db.execute("SELECT token, tenant FROM logins WHERE expiry > ?", (time.time(),)).fetchall()
        offered = cookie.encode()
        tenant = next((tenant for token, tenant in rows if secrets.compare_digest(token.encode(), offered)), None)
        return tenant if tenant in config["tenants"] else None

    def origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        return not origin or origin == "https://" + request.headers.get("host", "")

    @app.get("/healthz")
    async def health():
        return {"status": "ready", "model": MODEL, "total_limit_usd": ledger.total / MICRO,
                "revision": config["revision"]}

    @app.get("/login")
    async def login_page():
        return HTMLResponse(login_html(), headers={"Cache-Control": "no-store"})

    @app.post("/login")
    async def login(request: Request):
        if not origin_ok(request):
            return JSONResponse({"detail": "请从试用页面登录。"}, 403)
        try:
            form = parse_qs((await bounded_body(request, 4096)).decode("utf-8"), max_num_fields=2)
            token = form.get("invite", [""])[0].strip()
        except (ValueError, UnicodeError):
            return JSONResponse({"detail": "邀请码格式不正确。"}, 400)
        tenant = next((name for invite, name in invites.items() if secrets.compare_digest(token.encode("utf-8"), invite.encode("utf-8"))), None)
        if tenant is None:
            return HTMLResponse(login_html(invite=token, error=True), 401, headers={"Cache-Control": "no-store"})
        response = RedirectResponse("/", 303)
        response.set_cookie(COOKIE, session(tenant), secure=True, httponly=True, samesite="lax", max_age=7 * 86400)
        return response

    @app.get("/trial/budget")
    async def budget(request: Request):
        tenant = identify(request.cookies.get(COOKIE, ""))
        if tenant is None:
            return JSONResponse({"detail": "请先登录。"}, 401)
        return ledger.status(tenant)

    @app.get("/trial-quota.js")
    async def quota_script():
        from fastapi.responses import Response

        return Response("""(()=>{async function update(){const r=await fetch('/trial/budget');if(r.status===401){location.assign('/login');return}if(!r.ok)return;const b=await r.json();const host=document.querySelector('.composer-runtime');if(!host)return;let node=document.getElementById('trial-quota');if(!node){node=document.createElement('span');node.id='trial-quota';node.style.fontSize='11px';host.append(node)}node.style.color=b.exhausted?'#c0392b':'#2069d1';node.textContent=b.exhausted||'试用余额 $'+b.remaining_usd.toFixed(2)+' / $'+b.limit_usd.toFixed(2);node.title='全站剩余 $'+b.total_remaining_usd.toFixed(2)+'；模型 '+b.model}setInterval(update,8000);setTimeout(update,1200)})();""", media_type="text/javascript")

    @app.get("/trial-transport.js")
    async def transport_script():
        from fastapi.responses import Response

        return Response((Path(__file__).parent / "trial-transport.js").read_text(), media_type="text/javascript")

    @app.websocket("/_relay")
    @app.websocket("/trial/stream")
    async def relay(socket: WebSocket):
        browser = socket.url.path == "/trial/stream"
        public_host = socket.headers.get("host", "")
        if secrets.compare_digest(socket.headers.get("authorization", ""), "Bearer " + config["relay_secret"]):
            public_host = socket.headers.get("x-argus-public-host", public_host)
        if browser:
            if identify(socket.cookies.get(COOKIE, "")) is None:
                await socket.close(code=4401)
                return
            if socket.headers.get("origin") != "https://" + public_host:
                await socket.close(code=4403)
                return
        elif not secrets.compare_digest(socket.headers.get("authorization", ""), "Bearer " + config["relay_secret"]):
            await socket.close(code=4401)
            return
        await socket.accept()
        frame = await asyncio.wait_for(socket.receive_json(), 20)
        if browser:
            if frame.get("method") != "POST" or not re.fullmatch(r"/api/projects/[a-zA-Z0-9_-]+/message/stream", frame.get("path", "")):
                await socket.close(code=4403)
                return
            frame["host"] = public_host
            frame["headers"] = {"cookie": socket.headers.get("cookie", ""),
                                "origin": socket.headers["origin"], "content-type": "application/json"}
        raw = base64.b64decode(frame.get("body", ""), validate=True)
        if len(raw) > UPLOAD_LIMIT:
            await socket.close(code=4400)
            return
        request_headers = [(k.lower().encode(), v.encode()) for k, v in frame.get("headers", {}).items()
                           if k.lower() not in ("host", "content-length", "connection", "upgrade", "transfer-encoding")]
        request_headers.extend([(b"host", frame["host"].encode()), (b"content-length", str(len(raw)).encode())])
        path, _, query = frame["path"].partition("?")
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": frame["method"],
                 "scheme": "https", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
                 "headers": request_headers, "client": ("127.0.0.1", 0), "server": (frame["host"], 443)}
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": raw, "more_body": False}
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                await socket.send_json({"type": "start", "status": message["status"],
                                        "headers": [[k.decode(), v.decode()] for k, v in message.get("headers", [])]})
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    await socket.send_bytes(body)
                if not message.get("more_body"):
                    await socket.send_json({"type": "end"})
        try:
            await app(scope, receive, send)
        finally:
            await socket.close()

    @app.websocket("/api/{path:path}")
    async def websocket_proxy(socket: WebSocket, path: str):
        tenant = identify(socket.cookies.get(COOKIE, ""))
        if tenant is None:
            await socket.close(code=4401)
            return
        origin = socket.headers.get("origin")
        public_host = socket.headers.get("host", "")
        if secrets.compare_digest(socket.headers.get("authorization", ""), "Bearer " + config["relay_secret"]):
            public_host = socket.headers.get("x-argus-public-host", public_host)
        expected = "https://" + public_host
        if origin and origin != expected:
            await socket.close(code=4403)
            return
        row = config["tenants"][tenant]
        await socket.accept()
        query = {key: value for key, value in socket.query_params.items() if key != "token"}
        try:
            async with unix_connect(row["socket"], uri="ws://localhost/api/" + path + "?" + urlencode(query), max_size=None,
                                    extra_headers={"Authorization": "Bearer " + row["web_token"]}) as backend:
                async def outward():
                    async for message in backend:
                        await socket.send_text(message)
                async def inward():
                    while True:
                        await backend.send(await socket.receive_text())
                tasks = [asyncio.create_task(outward()), asyncio.create_task(inward())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except OSError:
            await socket.close(code=1013, reason="工作区暂时无法连接，请稍后重试")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
    async def proxy(request: Request, path: str):
        tenant = identify(request.cookies.get(COOKIE, ""))
        if tenant is None:
            return RedirectResponse("/login", 303) if not path.startswith("api/") else JSONResponse({"detail": "请先登录试用版。"}, 401)
        if request.method not in ("GET", "HEAD") and not origin_ok(request):
            return JSONResponse({"detail": "请从试用页面发送请求。"}, 403)
        if path.startswith(("api/runtime", "api/system", "api/plugins", "api/pairing-links")) and request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "试用环境由管理员维护。"}, 403)
        if request.method not in ("GET", "HEAD") and (path.endswith("/config/set") or path.endswith("/budget")):
            return JSONResponse({"detail": "试用版固定使用 GPT-6.1，额度由管理员设置。"}, 403)
        try:
            body = await bounded_body(request, UPLOAD_LIMIT)
        except ValueError:
            return JSONResponse({"detail": f"单个文件不能超过 {UPLOAD_LIMIT // 1024 // 1024} MB。更大的数据请让 Argus 从网址下载到项目里。"}, 413)
        row = config["tenants"][tenant]
        headers = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept", "accept-language", "range")}
        headers["Authorization"] = "Bearer " + row["web_token"]
        client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=row["socket"]), timeout=httpx.Timeout(None, connect=10))
        try:
            response = await client.send(client.build_request(request.method, "http://localhost/" + path +
                                                             ("?" + str(request.url.query) if request.url.query else ""),
                                                             headers=headers, content=body), stream=True)
        except httpx.HTTPError:
            await client.aclose()
            headers = {"Retry-After": "5", "Cache-Control": "no-store"}
            reason = unreachable_reason(row)
            if path == "":
                return HTMLResponse(RECOVERING.replace("{reason}", reason), 503, headers=headers)
            return JSONResponse({"detail": reason + "文件和余额都已保留。"}, 503, headers=headers)
        response_headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding")}
        if path == "" and response.status_code == 200:
            from fastapi.responses import Response

            html = (await response.aread()).decode().replace("<head>", '<head><script src="/trial-transport.js"></script>').replace("</body>", '<script src="/trial-quota.js" defer></script></body>')
            await client.aclose()
            # Never serve the shell from cache: a signed-out reader must reach /login.
            response_headers["cache-control"] = "no-store"
            return Response(html, media_type="text/html", headers=response_headers)

        async def chunks():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
        return StreamingResponse(chunks(), status_code=response.status_code, headers=response_headers)

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["portal", "meter"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--uds", type=str)
    args = parser.parse_args()
    config = load_config(args.config)
    ledger = Ledger(Path(config["ledger"]), total_usd=config["total_usd"], user_usd=config["user_usd"],
                    allowances={tenant: row["user_usd"] for tenant, row in config["tenants"].items() if "user_usd" in row})
    meter_lock = None
    if args.mode == "meter":
        import fcntl

        meter_lock = open(config["ledger"] + ".lock", "a")
        fcntl.flock(meter_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    app = model_app(config, ledger) if args.mode == "meter" else portal_app(config, ledger)
    uvicorn.run(app, host="127.0.0.1", port=args.port or 18871, uds=args.uds, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
