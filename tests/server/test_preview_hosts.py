from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from deploy.native_preview import COOKIE, MODEL, Ledger, load_config, portal_app
from deploy.native_runtime import sandbox_command
from deploy.preview_hosts import preview_domain, preview_host, preview_template
from deploy.try_router import LAST, Router, outward_cookie


@pytest.mark.parametrize("host,tenant", [
    ("js-trial1-abc123.argusbot.cn", "trial1"),
    ("JS-trial-one-ABC123.argusbot.cn:443", "trial-one"),
    ("js-trial1-abc123.argusbot.cn.", "trial1"),
    ("JS-trial-one-ABC123.argusbot.cn.:443", "trial-one"),
    ("js-trial1-a_b.argusbot.cn.", None),
    ("js-trial1-a.b.argusbot.cn.:443", None),
    ("js-trial1-a_b.argusbot.cn", None),
    ("js-trial1-a.b.argusbot.cn", None),
    ("js--a.argusbot.cn", None),
    ("js-trial1-" + "a" * 60 + ".argusbot.cn", None),
])
def test_preview_host_boundaries(host, tenant):
    assert preview_host(host, "argusbot.cn") == (True, tenant)
    assert preview_host(host + ".evil.example", "argusbot.cn") == (False, None)
    assert preview_host(host, "") == (False, None)


@pytest.mark.parametrize("domain", ["https://argusbot.cn", "argusbot.cn:443", "*.argusbot.cn", "argusbot.cn/", "a..cn"])
def test_invalid_preview_domain(domain):
    with pytest.raises(ValueError):
        preview_domain(domain)


@pytest.mark.parametrize("tenant", ["Upper", "a_b", "../x", "-x", "x-", "a" * 28])
def test_invalid_preview_tenant(tenant):
    with pytest.raises(ValueError):
        preview_template(tenant, "argusbot.cn")


def test_a_tenant_name_leaves_room_for_a_project_identifier(tmp_path):
    host = preview_template("a" * 27, "argusbot.cn").replace("*", "0123456789abcdef" * 2)
    assert preview_host(host.removeprefix("https://"), "argusbot.cn") == (True, "a" * 27)
    config = tmp_path / "preview.json"
    config.write_text(json.dumps({"model": MODEL, "runtime": "js", "preview_domain": "argusbot.cn",
                                  "tenants": {"a" * 28: {}}}))
    with pytest.raises(ValueError, match="at most 27 characters"):
        load_config(config)  # the portal and meter refuse it before serving anyone


def test_launcher_preview_is_opt_in_and_js_only(tmp_path):
    config = {key: str(tmp_path / key) for key in (
        "source", "hosting_source", "python_runtime", "venv", "node_runtime", "meter_runtime", "argus_package")}
    config.update(revision="test", tenants={"trial-one": {
        "directory": str(tmp_path / "tenant"), "web_token": "fake-web", "model_token": "fake-model"}})
    for runtime, domain, expected in [("js", None, None), ("py", "argusbot.cn", None),
                                     ("js", "argusbot.cn", "https://js-trial-one-*.argusbot.cn")]:
        config.update(runtime=runtime, preview_domain=domain)
        command = sandbox_command(config, "trial-one")
        if expected:
            assert command[command.index("ARGUS_TRIAL_PREVIEW_URL") + 1] == expected
        else:
            assert "ARGUS_TRIAL_PREVIEW_URL" not in command


@pytest.mark.parametrize("template", [None, "https://js-alice-*.argusbot.cn"])
def test_js_child_receives_preview_argument(template, tmp_path, monkeypatch):
    from deploy import native_runtime

    seen = []
    monkeypatch.setenv("ARGUS_HOME", str(tmp_path))
    if template:
        monkeypatch.setenv("ARGUS_TRIAL_PREVIEW_URL", template)
    else:
        monkeypatch.delenv("ARGUS_TRIAL_PREVIEW_URL", raising=False)

    class Child:
        async def wait(self):
            return 0

    class Web:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def spawn(*command):
        seen.extend(command)
        return Child()

    async def unix_server(*args):
        return Web()

    monkeypatch.setattr(native_runtime.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(native_runtime.asyncio, "start_unix_server", unix_server)
    with pytest.raises(SystemExit) as result:
        asyncio.run(native_runtime.serve_js(tmp_path / "package", tmp_path / "web.sock"))
    assert result.value.code == 0
    assert seen[:2] == ["node", str(tmp_path / "package/dist/argus.mjs")]
    if template:
        assert seen[-2:] == ["--preview-url", template]
    else:
        assert "--preview-url" not in seen


def test_router_cookie_prefix_round_trip():
    router = Router({"js": 1, "py": 2}, 50)
    cookie = outward_cookie(f"{COOKIE}=js-token; Secure; HttpOnly; Path=/js; Domain=argusbot.cn; SameSite=Lax", "js")
    assert cookie.startswith("__Host-js__argus_preview_session=js-token;")
    assert "Domain=" not in cookie and "Path=/" in cookie and "Secure" in cookie
    headers = {"cookie": "js__argus_preview_session=old; argus_preview_session=old; "
               "__Host-js__argus_preview_session=js-token; __Host-py__argus_preview_session=py-token; "
               f"{LAST}=py; argus_try_version=js"}
    assert router.inward_cookies(headers, "js") == f"{COOKIE}=js-token"
    assert router.inward_cookies(headers, "py") == f"{COOKIE}=py-token"
    scope = {"path": "/api/projects"}
    assert router.place(scope, headers)[0] == "py"
    assert router.place(scope, {"cookie": "argus_try_version=js"})[0] is None
    asyncio.run(router.client.aclose())


@pytest.mark.parametrize("root_dot", ["", "."])
def test_router_preview_never_uses_portal_pages_or_websockets(root_dot):
    router = Router({"py": 1}, 50, "argusbot.cn")
    client = TestClient(router)
    try:
        for path in ("/", "/py/", "/login", "/healthz"):
            response = client.get(path, headers={"Host": "js-alice-a.argusbot.cn" + root_dot})
            assert response.status_code == 404 and response.content == b""
        for path in ("/api/x", "/_relay", "/trial/stream"):
            with pytest.raises(WebSocketDisconnect), client.websocket_connect("ws://js-alice-a.argusbot.cn" + root_dot + path):
                pass
    finally:
        client.close()
        asyncio.run(router.client.aclose())


def test_portal_rejects_old_cookie_name(tmp_path):
    config = {"revision": "fake", "tenants": {"alice": {"invite": "fake-invite"}}}
    app = portal_app(config, Ledger(tmp_path / "ledger"))
    with TestClient(app, base_url="https://try.example") as client:
        response = client.post("/login", data={"invite": "fake-invite"}, follow_redirects=False)
        assert response.headers["set-cookie"].startswith(COOKIE + "=")
        assert "Path=/" in response.headers["set-cookie"] and "Domain=" not in response.headers["set-cookie"]
        token = client.cookies.get(COOKIE)
        client.cookies.clear()
        assert client.get("/trial/budget", headers={"Cookie": "argus_preview_session=" + token}).status_code == 401
        assert client.get("/trial/budget", headers={"Cookie": COOKIE + "=" + token}).status_code == 200


@pytest.mark.parametrize("root_dot", ["", "."])
def test_preview_host_blocks_every_portal_endpoint_and_websocket(tmp_path, root_dot):
    config = {"runtime": "js", "preview_domain": "argusbot.cn", "revision": "fake",
              "tenants": {"alice": {"invite": "fake", "socket": str(tmp_path / "absent.sock")}}}
    with TestClient(portal_app(config, Ledger(tmp_path / "ledger")), base_url="https://js-missing-a.argusbot.cn" + root_dot) as client:
        for path in ("/", "/login", "/healthz", "/trial/budget", "/trial-quota.js", "/trial-transport.js", "/api/projects", "/docs"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 404 and response.content == b""
            assert "set-cookie" not in response.headers
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
            response = client.request(method, "/login", headers={"Host": "js-alice-a.argusbot.cn" + root_dot})
            assert response.status_code == 405 and response.headers["allow"] == "GET, HEAD"
        for path in ("/api/x", "/_relay", "/trial/stream"):
            with pytest.raises(WebSocketDisconnect), client.websocket_connect("wss://js-missing-a.argusbot.cn" + root_dot + path):
                pass


@pytest.mark.integration
@pytest.mark.parametrize("root_dot", ["", "."])
def test_local_router_portal_and_tenant_isolation(tmp_path, root_dot):
    """Real loopback router/portals and two Unix-socket tenants; no host secrets."""
    async def verify():
        seen = []
        servers = []
        tasks = []
        sockets = []

        async def start(app, *, uds=None):
            if uds:
                settings = uvicorn.Config(app, uds=str(uds), lifespan="off", access_log=False, log_level="error", proxy_headers=False)
                port = None
                bound = None
            else:
                bound = socket.socket()
                bound.bind(("127.0.0.1", 0))
                port = bound.getsockname()[1]
                sockets.append(bound)
                settings = uvicorn.Config(app, lifespan="off", access_log=False, log_level="error", proxy_headers=False)
            server = uvicorn.Server(settings)
            servers.append(server)
            task = asyncio.create_task(server.serve(sockets=[bound] if bound else None))
            tasks.append(task)
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise AssertionError("local service failed to start")
                    await asyncio.sleep(0.01)
            return port

        def probe(name):
            async def app(scope, receive, send):
                headers = dict(scope["headers"])
                seen.append((name, scope["path"], scope["query_string"], headers))
                page = f"<html><head></head><body>{name}</body></html>".encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"text/html"), (b"content-length", str(len(page)).encode())]})
                await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else page})
            return app

        # Use short socket paths under this workspace's pytest basetemp, not /tmp.
        base = Path(tmp_path.parents[1]) / "preview-sockets"
        base.mkdir(exist_ok=True)
        config = {"runtime": "js", "preview_domain": "argusbot.cn", "revision": "isolation-probe",
                  "relay_secret": "fake-relay-only", "tenants": {}}
        router = None
        try:
            for name in ("alice", "bob"):
                uds = base / (name + ".sock")
                await start(probe(name), uds=uds)
                config["tenants"][name] = {"invite": "fake-" + name, "web_token": "fake-web-" + name, "socket": str(uds)}
            js_port = await start(portal_app(config, Ledger(tmp_path / "js-ledger")))
            py_config = {**config, "runtime": "py", "preview_domain": None}
            py_port = await start(portal_app(py_config, Ledger(tmp_path / "py-ledger")))
            router = Router({"js": js_port, "py": py_port}, 50, "argusbot.cn")
            port = await start(router)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                for version in ("js", "py"):
                    response = await client.post(f"/{version}/login", content="invite=fake-alice", headers={"Host": "try.argusbot.cn"})
                    session = response.headers["set-cookie"].split(";", 1)[0]
                    assert session.startswith(f"__Host-{version}__argus_preview_session=")
                    response = await client.get(f"/{version}/", headers={"Host": "try.argusbot.cn", "Cookie": session, "Accept": "text/html"})
                    assert response.status_code == 200 and "alice" in response.text
                    assert "trial-quota.js" in response.text and "window.WebSocket" in response.text
                    assert seen[-1][3][b"authorization"] == b"Bearer fake-web-alice"
                    assert seen[-1][3][b"host"] == b"localhost"
                for name in ("alice", "bob"):
                    host = f"JS-{name}-abc123.argusbot.cn{root_dot}:443"
                    response = await client.get("/js/assets/a%20b.html?x=%2F", headers={"Host": host, "Accept": "text/html",
                        "Cookie": "__Host-js__argus_preview_session=forged", "Authorization": "Bearer fake-relay-only",
                        "Origin": "https://try.argusbot.cn", "Range": "bytes=0-"})
                    assert response.status_code == 200
                    assert response.text == f"<html><head></head><body>{name}</body></html>"
                    assert "set-cookie" not in response.headers and "location" not in response.headers
                    assert seen[-1][:3] == (name, "/js/assets/a b.html", b"x=%2F")
                    forwarded = seen[-1][3]
                    assert forwarded[b"host"] == host.encode()
                    assert forwarded[b"origin"] == b"https://try.argusbot.cn" and forwarded[b"range"] == b"bytes=0-"
                    assert b"cookie" not in forwarded and b"authorization" not in forwarded
                    response = await client.head("/", headers={"Host": host})
                    assert response.status_code == 200 and response.content == b""
                before = len(seen)
                for path in ("/", "/login", "/healthz", "/trial/budget", "/api/projects"):
                    response = await client.get(path, headers={"Host": "js-missing-abc.argusbot.cn" + root_dot})
                    assert response.status_code == 404 and response.content == b""
                for method in ("POST", "OPTIONS", "TRACE"):
                    assert (await client.request(method, "/login", headers={"Host": "js-alice-abc.argusbot.cn" + root_dot})).status_code == 405
                assert len(seen) == before
        finally:
            if router:
                await router.client.aclose()
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks)
            for bound in sockets:
                bound.close()
            for uds in base.glob("*.sock"):
                uds.unlink(missing_ok=True)
            base.rmdir()
    asyncio.run(verify())
