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

try:
    from .preview_hosts import preview_domain as normalize_preview_domain
    from .preview_hosts import preview_host
    from .trial_pages import entry_page
except ImportError:  # Direct deployment script entry point.
    from preview_hosts import preview_domain as normalize_preview_domain
    from preview_hosts import preview_host
    from trial_pages import entry_page

LAST = "__Host-argus_try_version"
HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer", "proxy-connection",
       "accept-encoding", "cookie"}

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
    def __init__(self, versions: dict[str, int], user_usd: float, preview_domain: str | None = None):
        self.versions = versions
        self.preview_domain = normalize_preview_domain(preview_domain)
        self.landing = entry_page(user_usd).encode()
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
        own = "__Host-" + version + "__"
        kept = []
        for name, value in cookies(headers).items():
            if name.startswith(own):
                kept.append(f"__Host-{name[len(own):]}={value}")
        return "; ".join(kept)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            await self.http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self.websocket(scope, receive, send)

    async def http(self, scope, receive, send):
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        query = scope.get("query_string", b"").decode("latin-1")
        # Over http a login can only fail (its cookie is Secure), after the invitation went out in
        # clear. cf-visitor names the browser's scheme; x-forwarded-proto may name a later hop.
        visitor = re.search(r'"scheme":\s*"(\w+)"', headers.get("cf-visitor", ""))
        if (visitor[1] if visitor else headers.get("x-forwarded-proto")) == "http":
            path = scope.get("raw_path", scope["path"].encode()).decode("latin-1")
            return await redirect(send, f"https://{headers.get('host', '')}{path}" + ("?" + query if query else ""), 301)
        preview, tenant = preview_host(headers.get("host", ""), self.preview_domain)
        if preview:
            if tenant is None or "js" not in self.versions:
                return await respond(send, 404, b"", "text/plain")
            if scope["method"] not in ("GET", "HEAD"):
                await send({"type": "http.response.start", "status": 405,
                            "headers": encode([("allow", "GET, HEAD"), ("content-length", "0")])})
                return await send({"type": "http.response.body", "body": b""})
            version, inner, known = "js", scope.get("raw_path", scope["path"].encode()).decode("ascii"), "preview"
        else:
            version, inner, known = self.place(scope, headers)
        named = known == "path"
        navigation = headers.get("sec-fetch-mode") == "navigate" or (
            "sec-fetch-mode" not in headers and "text/html" in headers.get("accept", ""))
        if not preview and scope["path"] == "/" and known != "referer":
            return await respond(send, 200, self.landing, "text/html; charset=utf-8")
        if version is None:
            return await respond(send, 404, NOT_FOUND.encode(), "text/html; charset=utf-8")
        if named and inner is None:
            return await redirect(send, f"/{version}/" + ("?" + query if query else ""))
        if not preview and not named and navigation and scope["method"] in ("GET", "HEAD"):
            # Keep the address bar under the version's prefix; a form post is answered in place.
            return await redirect(send, f"/{version}{scope['path']}" + ("?" + query if query else ""))
        upstream_headers = [(k, v) for k, v in headers.items() if k not in HOP and
                            (not preview or k not in ("authorization", "proxy-authorization", "x-argus-public-host"))]
        if not preview and (cookie := self.inward_cookies(headers, version)):
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
                if key == "location" and not preview:
                    value = outward_location(value, version, headers.get("host", ""))
                elif key == "set-cookie" and not preview:
                    value = outward_cookie(value, version)
                out.append((key, value))
            if named and navigation:
                out.append(("set-cookie", f"{LAST}={version}; Path=/; Max-Age=2592000; Secure; HttpOnly; SameSite=Lax"))
            if not preview and response.headers.get("content-type", "").startswith("text/html") and response.status_code == 200:
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
        if preview_host(headers.get("host", ""), self.preview_domain)[0]:
            await receive()
            return await send({"type": "websocket.close", "code": 4404})
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
    name, separator, content = first.partition("=")
    if name.startswith("__Host-"):
        name = name[len("__Host-"):]
    attributes = [part for part in attributes if not part.lower().startswith("domain=")]
    if not any(part.lower() == "secure" for part in attributes):
        attributes.append("Secure")
    return "; ".join([f"__Host-{version}__{name}{separator}{content}", *attributes])


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


async def redirect(send, location: str, status: int = 302):
    await send({"type": "http.response.start", "status": status,
                "headers": encode([("location", location), ("content-length", "0"), ("cache-control", "no-store")])})
    await send({"type": "http.response.body", "body": b""})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18870)
    parser.add_argument("--version", action="append", required=True, metavar="NAME=PORT",
                        help="a path prefix and the loopback port of its portal, e.g. py=18871")
    parser.add_argument("--user-usd", type=float, default=50, help="the per-invitation allowance the landing page states")
    parser.add_argument("--preview-domain", help="same preview_domain as the JS portal's preview.json")
    args = parser.parse_args()
    versions = {name: int(port) for name, _, port in (item.partition("=") for item in args.version)}
    uvicorn.run(Router(versions, args.user_usd, args.preview_domain), host="127.0.0.1", port=args.port, lifespan="off",
                access_log=False, proxy_headers=False, ws_max_size=16 * 1024 * 1024,
                headers=[("strict-transport-security", "max-age=31536000")])  # browsers heed it over https only


if __name__ == "__main__":
    main()
