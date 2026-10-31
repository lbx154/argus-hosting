"""The installed client connects over HTTP to the installed gateway."""
from __future__ import annotations

import socket
import threading

import pytest
import uvicorn
from argus.core import hosting as boundary
from cryptography.fernet import Fernet

from argus_hosting.trial import client, hosting
from argus_trial_server import hosting as runtime
from argus_trial_server.gateway import Settings, create_app
from argus_trial_server.secrets import Vault, write_private
from argus_trial_server.storage_pause import clear_storage_pause, record_storage_pause


def test_installed_entry_points_and_service_pause(tmp_path):
    boundary.hosting.cache_clear()
    boundary.hosted_runtime.cache_clear()
    assert boundary.hosting() is hosting
    assert boundary.hosted_runtime() is runtime
    assert boundary.work_pause(tmp_path) is None
    record_storage_pause(tmp_path, used_bytes=34 * 1024**3, limit_bytes=30 * 1024**3)
    pause = boundary.work_pause(tmp_path)
    assert set(pause) == {"reason", "since"}
    assert "34.0 GB of 30.0 GB" in pause["reason"]
    assert pause["since"] > 0
    clear_storage_pause(tmp_path)
    assert boundary.work_pause(tmp_path) is None


def test_client_connects_to_gateway_and_rejects_an_unknown_credential(tmp_path):
    state, key = tmp_path / "meter", tmp_path / "key"
    state.mkdir()
    write_private(key, Fernet.generate_key())
    Vault(key, state / "github-token.enc").save("synthetic-upstream-credential")
    app = create_app(Settings(state, key))
    ready = threading.Event()

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = Server(uvicorn.Config(app, access_log=False, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        assert ready.wait(10), "gateway did not start"
        credential = app.state.store.issue("client-test")
        assert client.connect(url, non_interactive=True, api_key=credential) == (url + "/v1", credential)
        assert client.query_status(url, credential)["tokens_remaining"] > 0
        with pytest.raises(ValueError, match="Key 未被识别"):
            client.query_status(url, "argus_trial_" + "0" * 64)
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
    assert not thread.is_alive()
