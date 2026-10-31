from __future__ import annotations

import asyncio
import json
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from deploy.native_egress import public_address
from deploy.native_preview import (
    COOKIE,
    MODEL,
    Ledger,
    model_app,
    portal_app,
    token_cost,
)
from deploy.native_runtime import sandbox_command


@pytest.fixture
def config(tmp_path):
    return {'model': MODEL, 'revision': 'test-sha', 'relay_secret': 'private-relay-key',
            'upstream': {'base_url': 'https://relay.example/v1/', 'token_file': str(tmp_path / 'upstream.key')},
            'tenants': {'alice': {'invite': 'invite-a', 'model_token': 'meter-a', 'web_token': 'web-a', 'socket': str(tmp_path / 'a.sock')},
                        'bob': {'invite': 'invite-b', 'model_token': 'meter-b', 'web_token': 'web-b', 'socket': str(tmp_path / 'b.sock')}}}


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / 'ledger.sqlite3', total_usd=50, user_usd=10)


def upstream(monkeypatch, tmp_path, reply):
    """Answer the meter's upstream calls with ``reply(request)`` and return what it sent."""
    (tmp_path / 'upstream.key').write_text('upstream-secret\n')
    sent = []
    def provider(request):
        sent.append(request)
        return reply(request)
    original = httpx.AsyncClient
    monkeypatch.setattr('deploy.native_preview.httpx.AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(provider)))
    return sent


def completed(usage):
    event = {'type': 'response.completed', 'response': {'id': 'r', 'status': 'completed', 'output': [], 'usage': usage}}
    return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
                          content=b'data: {"type": "response.output_text.delta", "delta": "hi"}\n\n'
                                  + ('data: ' + json.dumps(event) + '\n\n').encode())


def price(input_tokens, output_tokens, cached=0):
    from argus.core.pricing import quote_token_usage
    return quote_token_usage(MODEL, input_tokens=input_tokens, output_tokens=output_tokens, cached_input_tokens=cached).cost_usd


def test_allowances_refuse_once_spent_and_never_reset(tmp_path):
    ledger = Ledger(tmp_path / 'ledger', total_usd=50, user_usd=10)
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda _: ledger.charge('alice', 0.2), range(50)))
    assert '这个邀请码的 10 美元已经用完' in ledger.refusal('alice')
    assert ledger.refusal('bob') == ''
    ledger.charge('bob', 12.5)  # a receipt is charged in full, even past what was left
    assert ledger.status('bob')['used_usd'] == 12.5 and ledger.status('bob')['remaining_usd'] == 0
    for tenant in ('c', 'd', 'e'):
        ledger.charge(tenant, 9)
    assert ledger.refusal('new-user') == ''
    ledger.charge('f', 0.495)
    assert '全站共 50 美元的额度已经用完' in ledger.refusal('new-user')
    reopened = Ledger(tmp_path / 'ledger', total_usd=50, user_usd=10)
    assert reopened.status('alice')['used_usd'] == pytest.approx(10)
    with pytest.raises(ValueError, match='cannot be reset'):
        Ledger(tmp_path / 'ledger', total_usd=500, user_usd=100)


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf')])
def test_invalid_amounts(ledger, value):
    with pytest.raises(ValueError):
        ledger.charge('alice', value)


def test_login_cookie_auth_and_tenant_budgets(config, ledger):
    client = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    assert client.get('/api/projects').status_code == 401
    assert client.get('/', follow_redirects=False).headers['location'] == '/login'
    assert client.post('/login', data={'invite': 'wrong'}).status_code == 401
    assert client.post('/login', data={'invite': 'invite-a'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    response = client.post('/login', data={'invite': 'invite-a'}, follow_redirects=False)
    assert response.status_code == 303
    assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
    ledger.charge('alice', 0.1)
    assert client.get('/trial/budget').json()['used_usd'] == 0.1
    assert client.post('/api/daemons', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/projects/test/config/set', json={}).status_code == 403
    client.post('/login', data={'invite': 'invite-b'}, follow_redirects=False)
    assert client.get('/trial/budget').json()['used_usd'] == 0
    # Alone: which of two same-named cookies arrives last depends on the HTTP client.
    client.cookies.clear()
    client.cookies.set(COOKIE, 'alice:9999999999:forged')
    assert client.get('/trial/budget').status_code == 401


def test_websocket_relay_auth_and_http_body(config, ledger):
    client = TestClient(portal_app(config, ledger))
    with client.websocket_connect('/_relay', headers={'Authorization': 'Bearer private-relay-key'}) as ws:
        ws.send_json({'method': 'GET', 'path': '/healthz', 'host': 'preview.example', 'headers': {}, 'body': ''})
        assert ws.receive_json()['status'] == 200
        assert json.loads(ws.receive_bytes())['revision'] == 'test-sha'
        assert ws.receive_json()['type'] == 'end'
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect), client.websocket_connect('/_relay'):
        pass


@pytest.mark.parametrize('host', ['127.0.0.1', '169.254.169.254', '10.0.0.1', '::1', '192.168.1.1'])
def test_proxy_blocks_private_destinations(host):
    with pytest.raises(ValueError, match='Private'):
        asyncio.run(public_address(host, 443))


def test_proxy_pins_validated_address_and_rejects_mixed_dns(monkeypatch):
    async def check():
        loop = asyncio.get_running_loop()
        async def dns(*args, **kwargs):
            return [(2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', ('127.0.0.1', 443))]
        monkeypatch.setattr(loop, 'getaddrinfo', dns)
        with pytest.raises(ValueError):
            await public_address('rebind.example', 443)
    asyncio.run(check())
    with pytest.raises(ValueError):
        asyncio.run(public_address('example.com', 22))


def test_sandbox_has_no_host_network_home_or_cross_tenant_mount(config, tmp_path):
    config.update(source=str(tmp_path / 'source'), hosting_source=str(tmp_path / 'hosting'), python_runtime=str(tmp_path / 'python'),
                  venv=str(tmp_path / 'venv'), node_runtime=str(tmp_path / 'node'),
                  meter_runtime=str(tmp_path / 'meter'), copilot_package=str(tmp_path / 'public-package'))
    config['tenants']['alice']['directory'] = str(tmp_path / 'alice')
    command = sandbox_command(config, 'alice', ['/bin/true'])
    assert '--unshare-all' in command and '--share-net' not in command
    assert command[command.index('NODE_USE_ENV_PROXY') + 1] == '1'  # Node's fetch takes the egress proxy too
    assert '--proc' in command and '--cap-drop' in command
    assert str(tmp_path / 'alice') in command and str(tmp_path / 'bob') not in command
    assert str(Path.home()) not in command
    assert command[-1] == '/bin/true'


def test_meter_forwards_the_request_unchanged_and_charges_the_receipt(config, ledger, monkeypatch, tmp_path):
    """Hosted tools, item references, remote images and a 3 MB conversation all reach the upstream as sent."""
    sent = upstream(monkeypatch, tmp_path, lambda request: completed(
        {'input_tokens': 100_000, 'output_tokens': 10_000, 'input_tokens_details': {'cached_tokens': 40_000}}))
    body = json.dumps({'model': 'any-model', 'stream': True, 'store': True, 'previous_response_id': 'p',
                       'tools': [{'type': 'web_search'}, {'type': 'code_interpreter'}], 'tool_choice': {'type': 'web_search'},
                       'input': [{'type': 'item_reference', 'id': 'x'},
                                 {'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'https://x/y.png'},
                                                              {'type': 'input_text', 'text': 'x' * 3_000_000}]}]}).encode()
    r = TestClient(model_app(config, ledger)).post('/v1/responses', content=body, headers={
        'Authorization': 'Bearer meter-a', 'Content-Type': 'application/json'})
    assert r.status_code == 200 and r.text.count('data:') == 2 and 'response.completed' in r.text
    assert str(sent[0].url) == 'https://relay.example/v1/responses'
    assert sent[0].headers['authorization'] == 'Bearer upstream-secret' and sent[0].content == body
    assert ledger.status('alice')['used_usd'] == pytest.approx(price(100_000, 10_000, 40_000), abs=1e-6)
    assert ledger.status('bob')['used_usd'] == 0


def test_meter_charges_a_whole_response_too(config, ledger, monkeypatch, tmp_path):
    response = {'id': 'r', 'status': 'completed', 'output': [], 'usage': {'input_tokens': 1000, 'output_tokens': 100}}
    upstream(monkeypatch, tmp_path, lambda request: httpx.Response(200, json=response))
    r = TestClient(model_app(config, ledger)).post('/v1/responses', headers={'Authorization': 'Bearer meter-a'},
                                                   json={'model': MODEL, 'input': 'hello'})
    assert r.json() == response
    assert ledger.status('alice')['used_usd'] == pytest.approx(price(1000, 100), abs=1e-6)


def test_token_usage_is_priced_and_missing_usage_is_none():
    usage = {'input_tokens': 1_000_000, 'output_tokens': 100_000, 'input_tokens_details': {'cached_tokens': 400_000}}
    assert token_cost({'usage': usage}) == pytest.approx(price(1_000_000, 100_000, 400_000))
    assert price(1_000_000, 100_000, 400_000) > 0
    for response in (None, [], {}, {'usage': {'input_tokens': 5}}, {'usage': {'input_tokens': -1, 'output_tokens': 1}}):
        assert token_cost(response) is None


def test_upstream_answers_reach_the_tenant_as_they_are_and_cost_nothing(config, ledger, monkeypatch, tmp_path):
    upstream(monkeypatch, tmp_path, lambda request: httpx.Response(429, json={'error': {'message': 'slow down'}}))
    r = TestClient(model_app(config, ledger)).post('/v1/responses', headers={'Authorization': 'Bearer meter-a'},
                                                   json={'model': MODEL, 'input': 'hello'})
    assert r.status_code == 429 and r.json() == {'error': {'message': 'slow down'}}
    assert ledger.status('alice')['used_usd'] == 0


def test_a_stream_cut_before_its_receipt_is_not_charged(config, ledger, monkeypatch, tmp_path):
    """Estimates once charged broken streams; only what a receipt reports is charged."""
    upstream(monkeypatch, tmp_path, lambda request: httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
             content=b'data: {"type": "response.output_text.delta", "delta": "half"}\n\n'))
    r = TestClient(model_app(config, ledger)).post('/v1/responses', headers={'Authorization': 'Bearer meter-a'},
                                                   json={'model': MODEL, 'input': 'x' * 1_000_000, 'stream': True})
    assert 'half' in r.text
    assert ledger.status('alice')['used_usd'] == 0


def test_a_spent_allowance_or_an_oversized_request_never_reaches_the_upstream(config, ledger, monkeypatch, tmp_path):
    sent = upstream(monkeypatch, tmp_path, lambda request: completed({'input_tokens': 1, 'output_tokens': 1}))
    client = TestClient(model_app(config, ledger))
    assert client.post('/v1/responses', headers={'Authorization': 'Bearer meter-a'},
                       content=b'x' * (16 * 1024 * 1024 + 1)).status_code == 413
    ledger.charge('alice', 10)
    r = client.post('/v1/responses', headers={'Authorization': 'Bearer meter-a'}, json={'model': MODEL, 'input': 'hello'})
    assert r.status_code == 402 and r.json()['error']['code'] == 'trial_budget_exhausted'
    assert client.post('/v1/responses', headers={'Authorization': 'Bearer nobody'}, json={}).status_code == 401
    assert sent == []


def test_browser_stream_requires_session_and_same_origin_and_cannot_choose_other_api(config, ledger):
    from starlette.websockets import WebSocketDisconnect
    client = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    with pytest.raises(WebSocketDisconnect), client.websocket_connect('wss://preview.example/trial/stream', headers={'Origin': 'https://preview.example'}):
        pass
    client.post('/login', data={'invite': 'invite-a'}, follow_redirects=False)
    cookie = COOKIE + '=' + client.cookies.get(COOKIE)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect('wss://preview.example/trial/stream', headers={'Origin': 'https://evil.example', 'Cookie': cookie}):
        pass
    with client.websocket_connect('wss://preview.example/trial/stream', headers={'Origin': 'https://preview.example', 'Cookie': cookie}) as ws:
        ws.send_json({'method': 'POST', 'path': '/api/system/update', 'body': ''})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with client.websocket_connect('wss://preview.example/trial/stream', headers={'Origin': 'https://preview.example', 'Cookie': cookie}) as ws:
        ws.send_json({'method': 'POST', 'path': '/api/projects/s-test/message/stream', 'body': '',
                      'host': 'attacker.example', 'headers': {'cookie': 'forged'}})
        assert ws.receive_json()['status'] == 503  # It uses the authenticated tenant's unavailable socket.
        assert '工作区' in json.loads(ws.receive_bytes())['detail']
        assert ws.receive_json()['type'] == 'end'


def test_all_auxiliary_model_routes_are_pinned(config, tmp_path):
    config.update(source=str(tmp_path / 'source'), hosting_source=str(tmp_path / 'hosting'), python_runtime=str(tmp_path / 'python'),
                  venv=str(tmp_path / 'venv'), node_runtime=str(tmp_path / 'node'),
                  meter_runtime=str(tmp_path / 'meter'), copilot_package=str(tmp_path / 'package'))
    config['tenants']['alice']['directory'] = str(tmp_path / 'alice')
    args = sandbox_command(config, 'alice')
    for name in ('LIGHT_MODEL', 'MANAGER_MODEL'):
        index = args.index('ARGUS_SKILL_' + name)
        assert args[index + 1] == MODEL


def test_a_tenant_keeps_its_own_argus_release(config, tmp_path):
    config.update(source=str(tmp_path / 'source'), hosting_source=str(tmp_path / 'hosting'), python_runtime=str(tmp_path / 'python'),
                  venv=str(tmp_path / 'venv'), node_runtime=str(tmp_path / 'node'), meter_runtime=str(tmp_path / 'meter'),
                  runtime='js', argus_package=str(tmp_path / 'new'))
    config['tenants']['alice']['directory'] = str(tmp_path / 'alice')
    package = lambda args: args[args.index('ARGUS_TRIAL_PACKAGE') + 1]
    assert package(sandbox_command(config, 'alice')) == str(tmp_path / 'new')
    config['tenants']['alice']['argus_package'] = str(tmp_path / 'old')
    args = sandbox_command(config, 'alice')
    assert package(args) == str(tmp_path / 'old')
    assert str(tmp_path / 'old') in args and str(tmp_path / 'new') not in args


def test_host_watchdog_observes_real_child_memory_and_threads():
    import subprocess
    import sys
    import time

    from deploy.native_runtime import ResourceWatchdog

    child = subprocess.Popen([sys.executable, '-c', 'import time; data=bytearray(24*1024*1024); time.sleep(30)'])
    try:
        monitor = ResourceWatchdog(child.pid, memory_bytes=16 * 1024 * 1024)
        reason = ''
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not reason:
            reason = monitor.exceeded()
            time.sleep(0.05)
        assert reason == 'memory'
        assert ResourceWatchdog(child.pid, tasks=0).exceeded() == 'process/thread'
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.parametrize('reason', ['memory', 'process/thread', 'resource inspection'])
def test_transient_runtime_limits_exit_for_automatic_recovery(reason):
    import subprocess
    import sys

    from deploy.native_runtime import stop_for_limit

    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    with pytest.raises(SystemExit) as result:
        stop_for_limit(child, reason)
    assert result.value.code == 75
    assert child.poll() is not None


def test_full_storage_restarts_paused_and_only_growth_while_paused_stays_stopped():
    import subprocess
    import sys

    from deploy.native_runtime import stop_for_limit

    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    with pytest.raises(SystemExit) as result:
        stop_for_limit(child, 'workspace storage')
    assert result.value.code == 75
    assert child.poll() is not None
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    assert stop_for_limit(child, 'storage growing while paused') is None
    assert child.poll() is not None


def test_stopping_the_runtime_lets_its_daemons_record_the_stop(tmp_path):
    import subprocess
    import sys
    import time

    from deploy.native_runtime import stop_runtime

    record = tmp_path / 'host-shutdown'
    daemon = (f"import signal, sys, time\n"
              f"signal.signal(signal.SIGTERM, lambda *_: (open({str(record)!r}, 'w').write('resumable'), sys.exit(0)))\n"
              "print('ready', flush=True)\ntime.sleep(30)\n")
    # The namespace's first process ignores SIGTERM, as bubblewrap passes it on to nobody.
    child = subprocess.Popen([sys.executable, '-c', (
        "import signal, subprocess, sys, time\n"
        f"daemon = subprocess.Popen([sys.executable, '-c', {daemon!r}], stdout=subprocess.PIPE)\n"
        "daemon.stdout.readline()\nprint('ready', flush=True)\ntime.sleep(30)\n")], stdout=subprocess.PIPE)
    assert child.stdout.readline() == b'ready\n'
    started = time.monotonic()
    stop_runtime(child, grace=5)
    assert record.read_text() == 'resumable'
    assert child.poll() is not None and time.monotonic() - started < 10


def test_unavailable_workspace_shows_retry_page_and_preserves_allowance(config, ledger):
    client = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    ledger.charge('alice', 0.25)
    client.post('/login', data={'invite': 'invite-a'}, follow_redirects=False)
    response = client.get('/')
    assert response.status_code == 503
    assert response.headers['content-type'].startswith('text/html')
    assert response.headers['retry-after'] == '5'
    assert 'http-equiv="refresh" content="5"' in response.text
    assert '文件和试用余额已保留' in response.text
    assert client.get('/trial/budget').json()['remaining_usd'] == 9.75
    response = client.get('/api/projects')
    assert response.status_code == 503 and response.headers['retry-after'] == '5'
    cookie = COOKIE + '=' + client.cookies.get(COOKIE)
    with client.websocket_connect('wss://preview.example/api/projects/s-test/stream', headers={'Origin': 'https://preview.example', 'Cookie': cookie}) as ws:
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as result:
            ws.receive_json()
        assert result.value.code == 1013


def test_workspace_stream_carries_the_token_in_a_header_not_the_url(config, ledger, monkeypatch):
    calls = []

    class Backend:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    def connect(path, *, uri, **kwargs):
        calls.append((uri, kwargs))
        return Backend()

    monkeypatch.setattr('deploy.native_preview.unix_connect', connect)
    client = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    client.post('/login', data={'invite': 'invite-a'}, follow_redirects=False)
    cookie = COOKIE + '=' + client.cookies.get(COOKIE)
    with client.websocket_connect('wss://preview.example/api/projects/s-test/stream?replay=40&token=browser',
                                  headers={'Origin': 'https://preview.example', 'Cookie': cookie}):
        pass
    uri, kwargs = calls[0]
    assert uri == 'ws://localhost/api/projects/s-test/stream?replay=40'
    assert kwargs['extra_headers'] == {'Authorization': 'Bearer web-a'}


def test_watchdog_accepts_normal_multirole_copilot_memory():
    import os
    from types import SimpleNamespace

    from deploy.native_runtime import ResourceWatchdog

    class ClientProcess:
        def __init__(self, pid, memory, threads):
            self.pid, self.memory, self.thread_count = pid, memory, threads
        def children(self, recursive):
            return [ClientProcess(2, 1100 * 1024**2, 100)]
        def memory_info(self):
            return SimpleNamespace(rss=self.memory)
        def num_threads(self):
            return self.thread_count
        def cpu_times(self):
            return SimpleNamespace(user=0, system=0, children_user=0, children_system=0)

    monitor = ResourceWatchdog(os.getpid())
    monitor.process = ClientProcess(1, 1200 * 1024**2, 99)
    assert monitor.exceeded() == ''  # The previously observed 2.2 GiB / 199-thread case must remain usable.
    monitor.process.memory = 9 * 1024**3
    assert monitor.exceeded() == ''  # A 9 GiB experiment is ordinary work, not a runaway.
    monitor.process.memory = 129 * 1024**3
    assert monitor.exceeded() == 'memory'


def _sized(path, size, *, age=0):
    import os
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as file:
        file.truncate(size)
    if age:
        then = time.time() - age
        os.utime(path, (then, then))
    return path


def test_storage_limit_allows_a_normal_repository_clone(tmp_path):
    from deploy.native_runtime import StorageWatch

    checkout = _sized(tmp_path / 'workspace' / 'repository' / 'fixture', 251 * 1024**2)
    watch = StorageWatch(tmp_path)
    assert watch.check() == ''
    _sized(checkout, 2 * 1024**3 + 1)
    assert watch.check() == 'pause'


def test_full_storage_prunes_download_caches_before_pausing(tmp_path):
    from deploy.native_runtime import StorageWatch

    cache = tmp_path / 'state' / 'cache'
    old_model = _sized(cache / 'huggingface' / 'hub' / 'old-model', 1024**3, age=86400)
    wheel = _sized(cache / 'pip' / 'http' / 'wheel', 300 * 1024**2, age=60)
    copy = tmp_path / 'state' / 'sandbox' / '=2Ftenant=2Fworkspace=2Fa' / 'tenant' / 'home' / '.cache' / 'huggingface'
    kept_copy = _sized(copy / 'hub' / 'blob', 600 * 1024**2, age=3600)
    results = _sized(tmp_path / 'workspace' / 'results.bin', 400 * 1024**2, age=86400 * 7)

    assert StorageWatch(tmp_path).check() == ''  # 2.3 GB, back to 1 GB once the oldest caches are gone
    assert not old_model.exists() and not kept_copy.exists()
    assert wheel.exists() and results.exists()
    assert not (tmp_path / 'state' / 'storage-pause.json').exists()


def test_storage_pause_is_shown_and_lifts_once_files_are_removed(tmp_path):
    from argus_trial_server.storage_pause import read_storage_pause
    from deploy.native_runtime import StorageWatch

    _sized(tmp_path / 'state' / 'cache' / 'pip' / 'wheel', 100 * 1024**2)
    results = _sized(tmp_path / 'workspace' / 'results.bin', 3 * 1024**3)
    watch = StorageWatch(tmp_path, {'storage_gb': 2})
    assert watch.check() == 'pause'
    pause = read_storage_pause(tmp_path / 'state')
    assert pause['used_bytes'] == 3 * 1024**3 and pause['limit_bytes'] == 2 * 1024**3
    assert not (tmp_path / 'state' / 'cache' / 'pip' / 'wheel').exists()
    assert watch.check() == ''  # still full: stays paused, not stopped again
    _sized(results, int(1.9 * 1024**3))
    assert watch.check() == ''  # under the limit but not yet with room
    _sized(results, 1024**3)
    assert watch.check() == 'resume'
    assert read_storage_pause(tmp_path / 'state') is None


def test_storage_that_keeps_growing_while_paused_stops_for_an_operator(tmp_path):
    from deploy.native_runtime import StorageWatch

    results = _sized(tmp_path / 'workspace' / 'results.bin', 3 * 1024**3)
    watch = StorageWatch(tmp_path, {'storage_gb': 2})
    assert watch.check() == 'pause'
    _sized(results, 3 * 1024**3 + 1)
    assert watch.check() == ''
    _sized(results, 38 * 1024**3 // 10)  # 25% beyond what it held when it paused
    assert watch.check() == 'stop'


@pytest.mark.parametrize('paused', [False, True])
def test_launch_restarts_into_and_out_of_a_storage_pause(tmp_path, monkeypatch, paused):
    import sys

    from argus_trial_server.storage_pause import read_storage_pause, record_storage_pause
    from deploy import native_runtime

    directory = tmp_path / 'trial1'
    (directory / 'state').mkdir(parents=True)
    if paused:
        record_storage_pause(directory / 'state', used_bytes=3 * 1024**3, limit_bytes=1024**3)
        _sized(directory / 'workspace' / 'small', 1024)
    else:
        _sized(directory / 'workspace' / 'big', 1024**3 + 1)
    config = tmp_path / 'preview.json'
    config.write_text(json.dumps({'tenants': {'trial1': {'directory': str(directory)}}, 'limits': {'storage_gb': 1}}))
    monkeypatch.setattr(native_runtime, 'sandbox_command', lambda *_args: [sys.executable, '-c', 'import time; time.sleep(30)'])
    monkeypatch.setattr(sys, 'argv', ['native_runtime.py', 'launch', '--config', str(config), '--tenant', 'trial1'])
    with pytest.raises(SystemExit) as result:
        native_runtime.main()
    assert result.value.code == 75  # Restart=on-failure brings the runtime back either way
    assert (read_storage_pause(directory / 'state') is None) is paused


def test_a_portal_restart_keeps_signed_in_sessions(config, ledger):
    stored = ledger.path.with_name('sessions.sqlite3')
    with closing(sqlite3.connect(stored)) as db:  # the former table of hashed sessions
        db.execute('CREATE TABLE sessions (digest TEXT PRIMARY KEY, tenant TEXT NOT NULL, expiry REAL NOT NULL)')
    first = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    first.post('/login', data={'invite': 'invite-a'}, headers={'Origin': 'https://preview.example'}, follow_redirects=False)
    cookie = first.cookies.get(COOKIE)
    assert cookie
    restarted = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    restarted.cookies.set(COOKIE, cookie)
    assert restarted.get('/trial/budget').status_code == 200
    restarted.cookies.set(COOKIE, cookie[:-1] + ('A' if cookie[-1] != 'A' else 'B'))
    assert restarted.get('/trial/budget').status_code == 401
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    with closing(sqlite3.connect(stored)) as db:
        assert [name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ['logins']


def test_a_signed_out_page_returns_to_the_login(config, ledger):
    client = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    script = client.get('/trial-quota.js').text
    assert "r.status===401" in script and "location.assign('/login')" in script
    assert client.get('/', follow_redirects=False).headers['location'].endswith('/login')


def test_a_tenant_runs_as_many_projects_at_once_as_configured(config, tmp_path):
    config.update(source=str(tmp_path / 'source'), hosting_source=str(tmp_path / 'hosting'), python_runtime=str(tmp_path / 'python'),
                  venv=str(tmp_path / 'venv'), node_runtime=str(tmp_path / 'node'),
                  meter_runtime=str(tmp_path / 'meter'), copilot_package=str(tmp_path / 'public-package'))
    config['tenants']['alice']['directory'] = str(tmp_path / 'alice')
    def daemons(cmd):
        return cmd[cmd.index('ARGUS_SKILL_MAX_ACTIVE_DAEMONS') + 1]
    assert daemons(sandbox_command(config, 'alice')) == '64'  # one person's several projects all run
    config['limits'] = {'daemons': 3}
    assert daemons(sandbox_command(config, 'alice')) == '3'
