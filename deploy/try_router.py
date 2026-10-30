"""One public address for several invitation previews, each under its own path.

Each preview portal (native_preview.py) expects to own the site root. The router
strips a version's prefix (/py, /js) on the way in and puts it back on the way
out: in redirects, in cookie names (so two sessions in one browser do not
overwrite each other) and, in pages, for the two browser calls that carry no
Referer or set the address bar (WebSocket, history). Anything else a page asks
for at an absolute path arrives with a Referer under its prefix, or, lacking
one, goes to the version this browser opened last.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from urllib.parse import urlsplit, urlunsplit

import httpx
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

LAST = "argus_try_version"
HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer", "proxy-connection",
       "accept-encoding", "cookie"}

LANDING = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Argus 试用</title>
<style>
:root{--bg:#fbfbfa;--card:#fff;--ink:#141414;--dim:#6b6b6b;--line:#d9d9d6;--gold:#a8842c}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--card:#1b1b1b;--ink:#f1f1ef;--dim:#9a9a96;--line:#333331;--gold:#d2ad55}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 system-ui,-apple-system,"PingFang SC","Noto Sans SC",sans-serif}
main{max-width:720px;margin:0 auto;padding:12vh 16px 64px}h1{font-size:30px;font-weight:600;letter-spacing:.01em;margin:0 0 8px}
p{color:var(--dim);margin:0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:40px 0 28px}
a.card{display:block;padding:24px;background:var(--card);border:1px solid var(--line);border-radius:14px;color:inherit;text-decoration:none;transition:border-color .15s}
a.card:hover,a.card:focus-visible{border-color:var(--gold);outline:none}a.card b{display:block;font-size:19px;font-weight:600;margin-bottom:6px}
a.card span{display:block;margin-top:18px;color:var(--gold);font-size:14px}small{color:var(--dim);font-size:13px}
@media (max-width:560px){.grid{grid-template-columns:1fr}}
</style></head><body><main>
<h1>Argus 试用</h1><p>告诉它要做什么，它会查资料、写代码，并把结果验证给你看。</p>
<div class="grid">
<a class="card" href="/py/"><b>Python 版</b><p>现行版本，功能最全。</p><span>进入 →</span></a>
<a class="card" href="/js/"><b>JS 版</b><p>新的 TypeScript 核心，更轻、更快。</p><span>进入 →</span></a>
</div>
<small>邀请码只能在发给你的那个版本使用。每个码有独立的工作区和 {user} 美元额度，用完即止，不会重置。</small>
</main></body></html>"""

NOT_FOUND = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>找不到页面 · Argus 试用</title><body style="font:16px/1.7 system-ui;margin:12vh auto;max-width:420px;padding:0 16px">
<p>没有这个页面。<a href="/">回到首页选择版本</a></p></body></html>"""

# Prefixes the two calls a page makes that the router cannot place by Referer.
SHIM = """<script>(()=>{const P=%s,H=location.host,ours=p=>p===P||p.startsWith(P+"/");
const fix=(u,ws)=>{const x=new URL(String(u),location.href);if(x.host!==H)return u;if(!ours(x.pathname))x.pathname=P+x.pathname;
if(ws&&x.protocol.startsWith("http"))x.protocol=x.protocol==="https:"?"wss:":"ws:";return x.href};
const W=window.WebSocket;function S(u,p){return p===undefined?new W(fix(u,1)):new W(fix(u,1),p)}
S.prototype=W.prototype;for(const k of["CONNECTING","OPEN","CLOSING","CLOSED"])S[k]=W[k];window.WebSocket=S;
for(const k of["pushState","replaceState"]){const f=history[k];history[k]=function(s,t,u){return f.call(this,s,t,u==null?u:fix(u))}}})()</script>"""


class Router:
    def __init__(self, versions: dict[str, int], user_usd: float):
        self.versions = versions
        self.landing = LANDING.replace("{user}", f"{user_usd:g}").encode()
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10), trust_env=False,
                                        limits=httpx.Limits(max_connections=400, max_keepalive_connections=100))

    def place(self, scope: dict, headers: dict[str, str]) -> tuple[str | None, str | None, str]:
        """The version a request belongs to, the path its portal sees, and how
        it was known: the request's own path, its Referer, or the last visit."""
        path = scope["path"]
        if (match := re.fullmatch(r"/([a-z]+)(/.*)?", path)) and match[1] in self.versions:
            return match[1], match[2], "path"
        if (match := re.match(r"/([a-z]+)(/|$)", urlsplit(headers.get("referer", "")).path)) and match[1] in self.versions:
            return match[1], path, "referer"
        last = cookies(headers).get(LAST)
        return (last if last in self.versions else None), path, "last"

    def inward_cookies(self, headers: dict[str, str], version: str) -> str:
        own = version + "__"
        kept = []
        for name, value in cookies(headers).items():
            if name.startswith(own):
                kept.append(f"{name[len(own):]}={value}")
            elif name != LAST and not any(name.startswith(other + "__") for other in self.versions):
                kept.append(f"{name}={value}")
        return "; ".join(kept)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            await self.http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self.websocket(scope, receive, send)

    async def http(self, scope, receive, send):
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        version, inner, known = self.place(scope, headers)
        named = known == "path"
        query = scope.get("query_string", b"").decode("latin-1")
        navigation = headers.get("sec-fetch-mode") == "navigate" or (
            "sec-fetch-mode" not in headers and "text/html" in headers.get("accept", ""))
        if scope["path"] == "/" and known != "referer":
            return await respond(send, 200, self.landing, "text/html; charset=utf-8")
        if version is None:
            return await respond(send, 404, NOT_FOUND.encode(), "text/html; charset=utf-8")
        if named and inner is None:
            return await redirect(send, f"/{version}/" + ("?" + query if query else ""))
        if not named and navigation and scope["method"] in ("GET", "HEAD"):
            # Keep the address bar under the version's prefix; a form post is answered in place.
            return await redirect(send, f"/{version}{scope['path']}" + ("?" + query if query else ""))
        upstream_headers = [(k, v) for k, v in headers.items() if k not in HOP]
        if cookie := self.inward_cookies(headers, version):
            upstream_headers.append(("cookie", cookie))
        upstream_headers.append(("accept-encoding", "identity"))
        has_body = "content-length" in headers or "transfer-encoding" in headers

        async def body():
            while True:
                message = await receive()
                if message.get("body"):
                    yield message["body"]
                if message["type"] == "http.disconnect" or not message.get("more_body"):
                    return

        url = f"http://127.0.0.1:{self.versions[version]}{inner}" + ("?" + query if query else "")
        request = self.client.build_request(scope["method"], url, headers=upstream_headers,
                                            content=body() if has_body else None)
        try:
            response = await self.client.send(request, stream=True)
        except httpx.HTTPError:
            return await respond(send, 502, "试用服务暂时连不上，请稍后刷新。".encode(), "text/plain; charset=utf-8")
        try:
            out = []
            for key, value in response.headers.multi_items():
                key = key.lower()
                if key in ("transfer-encoding", "connection", "keep-alive"):
                    continue
                if key == "location":
                    value = outward_location(value, version, headers.get("host", ""))
                elif key == "set-cookie":
                    value = outward_cookie(value, version)
                out.append((key, value))
            if named and navigation:
                out.append(("set-cookie", f"{LAST}={version}; Path=/; Max-Age=2592000; Secure; HttpOnly; SameSite=Lax"))
            if response.headers.get("content-type", "").startswith("text/html") and response.status_code == 200:
                page = await response.aread()
                page = inject(page, SHIM % json.dumps("/" + version))
                out = [(k, v) for k, v in out if k != "content-length"] + [("content-length", str(len(page)))]
                await send({"type": "http.response.start", "status": 200, "headers": encode(out)})
                await send({"type": "http.response.body", "body": page})
                return
            await send({"type": "http.response.start", "status": response.status_code, "headers": encode(out)})
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await response.aclose()

    async def websocket(self, scope, receive, send):
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        version, inner, _known = self.place(scope, headers)
        await receive()  # websocket.connect
        if version is None or inner is None:
            return await send({"type": "websocket.close", "code": 4404})
        query = scope.get("query_string", b"").decode("latin-1")
        forwarded = [("cookie", self.inward_cookies(headers, version))]
        forwarded += [(k, headers[k]) for k in ("user-agent", "accept-language") if k in headers]
        uri = f"ws://{headers.get('host', 'localhost')}{inner}" + ("?" + query if query else "")
        try:
            upstream = await connect(uri, host="127.0.0.1", port=self.versions[version], origin=headers.get("origin"),
                                     additional_headers=forwarded, subprotocols=scope.get("subprotocols") or None,
                                     max_size=None, ping_interval=None, open_timeout=15, user_agent_header=None)
        except (OSError, InvalidHandshake, TimeoutError) as exc:
            code = exc.response.status_code if isinstance(exc, InvalidStatus) else 1013
            return await send({"type": "websocket.close", "code": 4000 + code if code < 1000 else code})
        await send({"type": "websocket.accept", "subprotocol": upstream.subprotocol})

        async def outward():
            async for message in upstream:
                key = "text" if isinstance(message, str) else "bytes"
                await send({"type": "websocket.send", key: message})

        async def inward():
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    return
                await upstream.send(message["text"] if message.get("text") is not None else message["bytes"])

        tasks = [asyncio.create_task(outward()), asyncio.create_task(inward())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await upstream.close()
            try:
                await send({"type": "websocket.close", "code": upstream.close_code or 1000})
            except (RuntimeError, ConnectionClosed, OSError):
                pass


def cookies(headers: dict[str, str]) -> dict[str, str]:
    found = {}
    for part in headers.get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name:
            found[name] = value
    return found


def outward_location(location: str, version: str, host: str) -> str:
    parts = urlsplit(location)
    if (parts.netloc and parts.netloc != host) or not (parts.netloc or location.startswith("/")) or location.startswith("//"):
        return location
    if parts.path == f"/{version}" or parts.path.startswith(f"/{version}/"):
        return location
    return urlunsplit(parts._replace(path=f"/{version}{parts.path}"))


def outward_cookie(value: str, version: str) -> str:
    """Name a portal's cookie for its version and send it with every path,
    since a page under /py also requests absolute paths outside it."""
    first, *attributes = [part.strip() for part in value.split(";")]
    attributes = [part for part in attributes if not part.lower().startswith("path=")] + ["Path=/"]
    return "; ".join([f"{version}__{first}", *attributes])


def inject(page: bytes, script: str) -> bytes:
    text = page.decode("utf-8", "replace")
    match = re.search(r"<head[^>]*>", text, re.I) or re.search(r"<html[^>]*>", text, re.I)
    at = match.end() if match else 0
    return (text[:at] + script + text[at:]).encode()


def encode(headers: list[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers]


async def respond(send, status: int, body: bytes, kind: str):
    await send({"type": "http.response.start", "status": status,
                "headers": encode([("content-type", kind), ("content-length", str(len(body))), ("cache-control", "no-store")])})
    await send({"type": "http.response.body", "body": body})


async def redirect(send, location: str):
    await send({"type": "http.response.start", "status": 302,
                "headers": encode([("location", location), ("content-length", "0"), ("cache-control", "no-store")])})
    await send({"type": "http.response.body", "body": b""})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18870)
    parser.add_argument("--version", action="append", required=True, metavar="NAME=PORT",
                        help="a path prefix and the loopback port of its portal, e.g. py=18871")
    parser.add_argument("--user-usd", type=float, default=50, help="the per-invitation allowance the landing page states")
    args = parser.parse_args()
    versions = {name: int(port) for name, _, port in (item.partition("=") for item in args.version)}
    uvicorn.run(Router(versions, args.user_usd), host="127.0.0.1", port=args.port, lifespan="off",
                access_log=False, proxy_headers=False, ws_max_size=16 * 1024 * 1024)


if __name__ == "__main__":
    main()
