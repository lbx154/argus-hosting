"""A tenant's Docker gate (deploy/native_docker.py) in front of a stand-in Docker."""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from deploy import native_docker
from deploy.native_docker import LABEL, Gate, framing, read_head, whole

MINE, BOBS = {LABEL: "alice"}, {LABEL: "bob"}
# Images built by this tenant, pulled from a registry, built on the host by
# someone else, and built by another tenant.
OWN = {"Id": "sha256:1", "RepoTags": ["argus-tenant-alice/built:latest"], "RepoDigests": [], "Config": {"Labels": {LABEL: ""}}}
PUBLIC = {"Id": "sha256:2", "RepoTags": ["shared:latest"], "RepoDigests": ["shared@sha256:d"], "Config": {"Labels": None}}
PRIVATE = {"Id": "sha256:3", "RepoTags": ["private:latest"], "RepoDigests": [], "Config": {"Labels": None}}
BOBS_IMAGE = {"Id": "sha256:4", "RepoTags": ["argus-tenant-bob/app:latest"], "RepoDigests": [], "Config": {"Labels": {LABEL: ""}}}
OBJECTS = {
    "/containers/mine/json": {"Id": "c-mine", "Image": "sha256:2", "Config": {"Labels": MINE}},
    "/containers/bob-box/json": {"Id": "c-bob", "Image": "sha256:2", "Config": {"Labels": BOBS}},
    "/exec/e-mine/json": {"ID": "e-mine", "ContainerID": "c-mine"},
    "/exec/e-bob/json": {"ID": "e-bob", "ContainerID": "c-bob"},
    "/containers/c-mine/json": {"Id": "c-mine", "Image": "sha256:2", "Config": {"Labels": MINE}},
    "/containers/c-bob/json": {"Id": "c-bob", "Image": "sha256:2", "Config": {"Labels": BOBS}},
    "/networks/mine-net": {"Id": "n-mine", "Labels": MINE},
    "/networks/bob-net": {"Id": "n-bob", "Labels": BOBS},
    "/networks/argus-tenant-alice": {"Id": "n-alice", "Labels": MINE},
    "/volumes/mine-data": {"Name": "mine-data", "Labels": MINE},
    "/volumes/bob-data": {"Name": "bob-data", "Labels": BOBS},
    "/images/argus-tenant-alice/built/json": OWN, "/images/sha256:1/json": OWN, "/images/ab/json": OWN,
    "/images/shared/json": PUBLIC, "/images/sha256:2/json": PUBLIC,
    "/images/private/json": PRIVATE, "/images/sha256:3/json": PRIVATE,
    "/images/argus-tenant-bob/app/json": BOBS_IMAGE,
}


class FakeDocker:
    """Docker's API as far as the gate uses it. Inspections answer from
    OBJECTS (else 404); a request asking to upgrade is taken over and echoes
    stdin in upper case, then says BYE; everything else is 200. It records
    every request, and with ``linger`` whatever arrives after its answer.
    ``run`` leaves to asyncio.run whatever connections are still open at the
    end, so a gate that holds on to one fails a test instead of hanging it."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.requests, self.answers, self.extra, self.linger = [], {}, b"", 0.0

    async def handle(self, reader, writer):
        start, headers = await read_head(reader)
        method, target, protocol = start.split(" ")
        url = urlsplit(target)
        path = re.sub(r"^/v[0-9.]+", "", url.path)
        content = await whole(reader, framing(headers))
        payload = json.loads(content) if dict(headers).get("content-type") == "application/json" and content else content
        self.requests.append({"method": method, "path": path, "query": dict(parse_qsl(url.query)), "target": target,
                              "headers": dict(headers), "body": payload, "protocol": protocol})
        if "upgrade" in dict(headers) and (method, path) not in self.answers:
            writer.write(b"HTTP/1.1 101 UPGRADED\r\nContent-Type: application/vnd.docker.raw-stream\r\n"
                         b"Connection: Upgrade\r\nUpgrade: tcp\r\n\r\n")
            while data := await reader.read(65536):
                writer.write(data.upper())
            writer.write(b"BYE")
        else:
            status, answer = self.answer(method, path, payload)
            data = json.dumps(answer).encode()
            writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                         f"Connection: close\r\n\r\n".encode() + data)
            await writer.drain()
            if self.linger:
                with contextlib.suppress(TimeoutError):
                    self.extra += await asyncio.wait_for(reader.read(65536), self.linger)
        await writer.drain()
        writer.close()

    def answer(self, method, path, payload):
        if (method, path) in self.answers:
            return self.answers[method, path]
        if method == "GET" and path in OBJECTS:
            return 200, OBJECTS[path]
        if method == "POST" and path == "/volumes/create":
            return 201, {"Name": payload["Name"], "Options": payload.get("DriverOpts")}
        if method == "GET" and re.fullmatch(r"/(containers|exec|images)/.+/json|/(networks|volumes)/.+", path):
            return 404, {"message": "No such object"}
        return 200, {}

    def forwarded(self, method=None, path=None):
        """Requests the gate passed on for the tenant, not its own (HTTP/1.0) lookups."""
        return [r for r in self.requests if r["protocol"] == "HTTP/1.1"
                and (method is None or r["method"] == method) and (path is None or r["path"] == path)]

    def run(self, steps):
        async def main():
            gate = Gate("alice", self.directory, {"memory_gb": 4, "cpus": 2, "tasks": 100}, "docker.sock")
            servers = [await asyncio.start_unix_server(self.handle, "docker.sock"),
                       await asyncio.start_unix_server(gate.handle, "gate.sock")]
            try:
                return await steps()
            finally:
                for server in servers:
                    server.close()
        return asyncio.run(main())

    def send(self, *requests: bytes) -> list[bytes]:
        async def steps():
            return [await exchange(data) for data in requests]
        return self.run(steps)


async def exchange(data: bytes) -> bytes:
    reader, writer = await asyncio.open_unix_connection("gate.sock")
    writer.write(data)
    try:
        return await asyncio.wait_for(reader.read(), 10)
    finally:
        writer.close()


def request(method: str, target: str, payload=None, *headers: str) -> bytes:
    body = b"" if payload is None else json.dumps(payload).encode()
    lines = [f"{method} {target} HTTP/1.1", "Host: x", *headers]
    if payload is not None:
        lines += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def status(response: bytes) -> int:
    return int(response.split(b" ", 2)[1])


@pytest.fixture
def docker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # Unix socket paths stay short
    fake = FakeDocker(tmp_path / "alice")
    for part in ("run", "workspace", "tmp", "shm"):
        (fake.directory / part).mkdir(parents=True)
    return fake


def created(fake) -> dict:
    [create] = fake.forwarded("POST", "/containers/create")
    return create["body"]


ESCAPES = [
    {"HostConfig": {"Privileged": True}},
    {"HostConfig": {"privileged": True}},  # Go matches JSON field names in any case
    {"hostconfig": {"PRIVILEGED": True}},
    {"HostConfig": {"Privileged": False, "privileged": True}},  # and takes the last spelling
    {"HostConfig": {"CapAdd": ["SYS_ADMIN"]}},
    {"HostConfig": {"CapAdd": ["cap_dac_read_search"]}},
    {"HostConfig": {"CapAdd": ["ALL"]}},
    {"HostConfig": {"Devices": [{"PathOnHost": "/dev/sda", "PathInContainer": "/dev/sda"}]}},
    {"HostConfig": {"DeviceCgroupRules": ["b *:* rwm"]}},
    {"HostConfig": {"Sysctls": {"kernel.shm_rmid_forced": "1"}}},
    {"HostConfig": {"\u017fysctls": {"net.ipv4.ip_forward": "1"}}},  # long s folds to s in Go
    {"HostConfig": {"CgroupParent": "/"}},
    {"HostConfig": {"Annotations": {"org.systemd.property.DeviceAllow": "'/dev/sda rwm'"}}},
    {"HostConfig": {"SecurityOpt": ["seccomp=unconfined"]}},
    {"HostConfig": {"SecurityOpt": ["apparmor:unconfined"]}},
    {"HostConfig": {"SecurityOpt": ["label=disable"]}},
    {"HostConfig": {"MaskedPaths": []}},
    {"HostConfig": {"ReadonlyPaths": []}},
    {"HostConfig": {"PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18871"}]}}},
    {"HostConfig": {"PublishAllPorts": True}},
    {"HostConfig": {"LogConfig": {"Type": "syslog", "Config": {"syslog-address": "tcp://127.0.0.1:18871"}}}},
    {"HostConfig": {"VolumeDriver": "sshfs"}},
    {"HostConfig": {"OomScoreAdj": -1000}},
    {"HostConfig": {"Ulimits": [{"Name": "rtprio", "Soft": 99, "Hard": 99}]}},
    {"HostConfig": {"Ulimits": [{"Name": "RTPRIO", "Soft": 99, "Hard": 99}]}},  # Docker upper-cases it
    {"HostConfig": {"Ulimits": [{"Name": "rtpr\u0131o", "Soft": 99, "Hard": 99}]}},  # dotless i, too
    *({"HostConfig": {mode: "host"}} for mode in
      ("NetworkMode", "PidMode", "IpcMode", "UTSMode", "UsernsMode", "CgroupnsMode")),
    {"HostConfig": {"NetworkMode": "HOST"}},
    *({"HostConfig": {mode: "container:bob-box"}} for mode in ("NetworkMode", "PidMode", "IpcMode", "Cgroup")),
    {"HostConfig": {"Links": ["bob-box:db"]}},
    {"HostConfig": {"VolumesFrom": ["bob-box:ro"]}},
    {"HostConfig": {"NetworkMode": "bob-net"}},
    {"NetworkingConfig": {"EndpointsConfig": {"bob-net": {}}}},
    {"HostConfig": {"Binds": ["/:/host"]}},
    {"HostConfig": {"Binds": ["/var/run/docker.sock:/var/run/docker.sock"]}},
    {"HostConfig": {"Binds": ["/tenant/../etc:/etc2"]}},
    {"HostConfig": {"Binds": ["/tenantx:/x"]}},
    {"HostConfig": {"Binds": ["//tenant/x:/x"]}},
    {"HostConfig": {"Binds": ["bob-data:/data"]}},
    {"HostConfig": {"Binds": ["argus-tenant-bob:/data"]}},
    {"HostConfig": {"Mounts": [{"Type": "bind", "Source": "/etc", "Target": "/etc2"}]}},
    {"HostConfig": {"Mounts": [{"Type": "volume", "Source": "bob-data", "Target": "/x"}]}},
    {"HostConfig": {"Mounts": [{"Type": "volume", "Target": "/x", "VolumeOptions": {
        "DriverConfig": {"Name": "local", "Options": {"type": "none", "o": "bind", "device": "/"}}}}]}},
    {"HostConfig": {"Mounts": [{"Type": "npipe", "Source": "x", "Target": "/x"}]}},
    {"HostConfig": {"Mounts": [{"Type": "image", "Source": "private", "Target": "/x"}]}},
    {"Image": "private"}, {"Image": "sha256:3"}, {"Image": "argus-tenant-bob/app"},
]


@pytest.mark.parametrize("body", ESCAPES)
def test_a_container_that_would_reach_the_host_or_another_tenant_is_refused(docker, body):
    [response] = docker.send(request("POST", "/v1.55/containers/create", body))
    assert status(response) in (400, 403)
    assert not docker.forwarded("POST", "/containers/create")


@pytest.mark.parametrize("target", [
    "POST /v1.55/swarm/init", "GET /v1.55/plugins", "POST /v1.55/plugins/pull", "GET /v1.55/services",
    "GET /v1.55/nodes", "GET /v1.55/secrets", "GET /v1.55/configs", "GET /v1.55/tasks",
    "POST /v1.55/containers/prune", "POST /v1.55/images/prune", "POST /v1.55/volumes/prune",
    "POST /v1.55/networks/prune", "POST /v1.55/build/prune", "POST /v1.55/session", "POST /grpc",
    "GET /v1.55/system/df", "POST /v1.55/images/load", "PUT /v1.55/volumes/mine-data",
    "POST /v1.55/build?networkmode=host", "POST /v1.55/build?cgroupparent=/x", "POST /v1.55/build?version=2",
    "POST /v1.55/build?remote=http://127.0.0.1:39917/secret.txt",
    "POST /v1.55/build?ulimits=%5B%7B%22Name%22%3A%22rtprio%22%2C%22Soft%22%3A99%2C%22Hard%22%3A99%7D%5D",
    "POST /v1.55/images/create?fromSrc=-&repo=x", "POST /v1.55/images/create?fromSrc=http://127.0.0.1:1/x.tar",
    "POST /v1.55/images/create?fromImage=argus-tenant-bob/app", "POST /v1.55/images/shared/push",
    "GET /v1.55/images/private/json", "GET /v1.55/images/private/history", "GET /v1.55/images/private/get",
    "GET /v1.55/images/get?names=shared&names=private", "POST /v1.55/images/private/tag?repo=mine",
    # other spellings of a refused endpoint
    "POST /v1.55//swarm/init", "POST /v1.55/./swarm/init", "POST /v1.55/x/../swarm/init", "POST /v1.55/%73warm/init",
    "POST /v1.55/SWARM/init", "POST /v1.55/swarm/init/", "post /v1.55/swarm/init", "POST /v1.55/containers%2Fprune",
    "POST /v1.55/swarm/init?x=1", "POST http://docker/v1.55/swarm/init",
])
def test_endpoints_that_reach_the_host_are_refused_however_spelled(docker, target):
    method, path = target.split(" ")
    [response] = docker.send(request(method, path))
    assert status(response) in (400, 403)
    assert not docker.forwarded()


@pytest.mark.parametrize("path", [
    "/v1.55/containers/create", "/v1.55//containers/create", "//containers/./create", "/v1.55/images/../containers/create",
    "/v1.55/containers%2Fcreate", "/v1.55/%63ontainers/create", "/v1.55/containers/create/", "/v1.55/containers/create?name=x",
])
def test_every_spelling_of_an_endpoint_is_checked_and_sent_on_in_one_form(docker, path):
    refused, allowed = docker.send(request("POST", path, {"HostConfig": {"Privileged": True}}),
                                   request("POST", path, {"Image": "shared"}))
    assert status(refused) == 403 and status(allowed) == 200
    [create] = docker.forwarded()
    assert create["path"] == "/containers/create"
    assert create["body"]["Labels"] == {LABEL: "alice"}


def test_a_second_request_on_the_same_connection_never_reaches_docker(docker):
    docker.linger = 0.3
    smuggled = request("POST", "/v1.55/containers/create", {"HostConfig": {"Privileged": True}})
    [response] = docker.send(request("GET", "/v1.55/containers/json") + smuggled)
    assert status(response) == 200 and response.count(b"HTTP/1.1") == 1
    assert [r["path"] for r in docker.forwarded()] == ["/containers/json"]
    assert docker.forwarded()[0]["headers"]["connection"] == "close"
    assert docker.extra == b""


def test_a_chunked_body_is_read_whole_before_it_is_checked(docker):
    def chunked(payload):
        body = json.dumps(payload).encode()
        pieces = (body[:3], body[3:17], body[17:])
        return (b"POST /v1.55/containers/create HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n" + b"".join(b"%x\r\n%s\r\n" % (len(p), p) for p in pieces)
                + b"0\r\n\r\n")
    refused, allowed = docker.send(chunked({"HostConfig": {"Privileged": True}}), chunked({"Image": "shared"}))
    assert status(refused) == 403 and status(allowed) == 200
    [create] = docker.forwarded()
    assert create["body"]["Image"] == "shared" and "content-length" in create["headers"]


@pytest.mark.parametrize("head", [
    "Content-Length: 2\r\nTransfer-Encoding: chunked",
    "Transfer-Encoding: chunked, identity",
    "Transfer-Encoding: identity",
    "Content-Length: 2\r\nContent-Length: 30",
    "Content-Length: +2",
    "Content-Length: 2\r\nX-Smuggle: a\nContent-Length: 30",
])
def test_ambiguous_framing_is_refused(docker, head):
    [response] = docker.send(f"POST /v1.55/containers/create HTTP/1.1\r\nHost: x\r\n{head}\r\n\r\n{{}}".encode())
    assert status(response) == 400
    assert not docker.forwarded()


@pytest.mark.parametrize("content_type", ["application/x-www-form-urlencoded",
                                          "appl\u0130cation/x-www-form-urlencoded",  # Go lower-cases it to the same
                                          "multipart/form-data; boundary=B"])
def test_docker_never_reads_a_form_body_over_the_checked_query(docker, content_type):
    body = b"t=shared&version=2&networkmode=host&repo=shared&fromSrc=-"
    head = f"Content-Type: {content_type}\r\nContent-Length: {len(body)}".encode()
    targets = ("/v1.55/build?t=app", "/v1.55/images/built/tag?repo=fresh", "/v1.55/images/create?fromImage=alpine")
    docker.send(*(b"POST %s HTTP/1.1\r\nHost: x\r\n%s\r\n\r\n%s" % (target.encode(), head, body) for target in targets))
    assert len(docker.forwarded()) == 3
    for forwarded in docker.forwarded():
        assert "content-type" not in forwarded["headers"]  # so Go parses no form, only the query
    build, tag, _ = docker.forwarded()
    assert build["query"]["t"] == "argus-tenant-alice/app" and tag["query"]["repo"] == "argus-tenant-alice/fresh"


def test_a_checked_body_goes_as_json_whatever_type_the_client_named(docker):
    docker.send(request("POST", "/v1.55/containers/create", {"Image": "shared"}),
                b"POST /v1.55/containers/create HTTP/1.1\r\nHost: x\r\nContent-Type: text/plain\r\n"
                b"Content-Length: 18\r\n\r\n{\"Image\":\"shared\"}")
    assert [r["headers"]["content-type"] for r in docker.forwarded()] == ["application/json"] * 2


def test_host_paths_become_subpaths_of_the_tenants_own_volume(docker):
    body = {"Image": "shared", "HostConfig": {
        "Binds": ["/tenant/workspace/data:/data:ro", "/tmp/t:/t", "/dev/shm/s:/s:rw", "/usr/share:/u", "fresh:/n", "/anon"],
        "Mounts": [{"Type": "bind", "Source": "/tenant", "Target": "/all"}, {"Type": "tmpfs", "Target": "/scratch"},
                   {"Type": "volume", "Source": "mine-data", "Target": "/m"}]}}
    [response] = docker.send(request("POST", "/v1.55/containers/create", body))
    assert status(response) == 200
    host = created(docker)["HostConfig"]

    def subpath(path, readonly=False):
        return {"Type": "volume", "Source": "argus-tenant-alice", "Target": path[0], "ReadOnly": readonly,
                "VolumeOptions": {"Subpath": path[1], "NoCopy": True}}
    assert host["Mounts"] == [
        subpath(("/data", "workspace/data"), True), subpath(("/t", "tmp/t")), subpath(("/s", "shm/s")),
        {"Type": "bind", "Source": str(Path("/usr/share").resolve()), "Target": "/u", "ReadOnly": True},
        subpath(("/all", "")), {"Type": "tmpfs", "Target": "/scratch"},
        {"Type": "volume", "Source": "mine-data", "Target": "/m"}]
    assert host["Binds"] == ["fresh:/n", "/anon"]
    assert (docker.directory / "workspace/data").is_dir() and (docker.directory / "tmp/t").is_dir()
    volumes = [r["body"] for r in docker.requests if r["path"] == "/volumes/create"]
    assert {"Name": "argus-tenant-alice", "DriverOpts": {"type": "none", "o": "bind", "device": str(docker.directory)}} in volumes
    assert {"Name": "fresh", "Labels": MINE} in volumes  # a new named volume is made the tenant's


def test_a_symlink_is_never_followed_out_of_the_tenant(docker, tmp_path, monkeypatch):
    (tmp_path / "outside").mkdir()
    (docker.directory / "workspace/link").symlink_to(tmp_path / "outside")
    responses = docker.send(request("POST", "/v1.55/containers/create", {
        "Image": "shared", "HostConfig": {"Binds": ["/tenant/workspace/link/new:/x"]}}))
    # Docker gets the subpath, resolves it itself at every start, and refuses
    # one that leads outside; the gate created nothing through the link.
    assert status(responses[0]) == 200
    assert created(docker)["HostConfig"]["Mounts"][0]["VolumeOptions"]["Subpath"] == "workspace/link/new"
    assert not (tmp_path / "outside/new").exists()
    monkeypatch.setattr(native_docker.os.path, "realpath", lambda path: "/etc/shadow")
    [response] = docker.send(request("POST", "/v1.55/containers/create", {
        "Image": "shared", "HostConfig": {"Binds": ["/usr/lib/x:/x"]}}))
    assert status(response) == 403


def test_a_volume_that_does_not_hold_the_tenants_directory_is_not_used(docker):
    docker.answers["POST", "/volumes/create"] = (201, {"Name": "argus-tenant-alice", "Options": {"device": "/"}})
    [response] = docker.send(request("POST", "/v1.55/containers/create", {
        "Image": "shared", "HostConfig": {"Binds": ["/tenant:/t"]}}))
    assert status(response) == 500
    assert not docker.forwarded()


def test_a_tenant_touches_only_its_own_containers_execs_networks_and_volumes(docker):
    theirs = docker.send(
        request("POST", "/v1.55/containers/bob-box/start"), request("GET", "/v1.55/containers/bob-box/logs"),
        request("DELETE", "/v1.55/containers/c-bob"), request("POST", "/v1.55/exec/e-bob/start", {}),
        request("GET", "/v1.55/networks/bob-net"), request("DELETE", "/v1.55/volumes/bob-data"),
        request("POST", "/v1.55/networks/mine-net/connect", {"Container": "bob-box"}),
        request("POST", "/v1.55/containers/bob-box/exec", {"Cmd": ["sh"]}),
        request("POST", "/v1.55/commit?container=bob-box&repo=new"))
    assert [status(r) for r in theirs] == [404] * 9
    assert not docker.forwarded()
    mine = docker.send(
        request("POST", "/v1.55/containers/mine/start"), request("POST", "/v1.55/exec/e-mine/resize?h=1&w=1"),
        request("DELETE", "/v1.55/networks/mine-net"), request("GET", "/v1.55/volumes/mine-data"),
        request("POST", "/v1.55/networks/mine-net/connect", {"Container": "mine"}),
        request("POST", "/v1.55/commit?container=mine&repo=new", None))
    assert [status(r) for r in mine] == [200] * 6
    assert [r["path"] for r in docker.forwarded()] == [
        "/containers/c-mine/start", "/exec/e-mine/resize", "/networks/n-mine", "/volumes/mine-data",
        "/networks/n-mine/connect", "/commit"]
    connect, commit = docker.forwarded()[-2:]
    assert connect["body"]["Container"] == "c-mine"
    assert commit["query"] == {"container": "c-mine", "repo": "argus-tenant-alice/new"}
    assert commit["body"]["Labels"] == {LABEL: ""}  # an image passes its labels on to containers


def test_a_container_given_the_tenants_label_by_its_image_is_not_the_tenants(docker, monkeypatch):
    # Docker gives a container its image's labels: an operator's container
    # made from an image that carries this tenant's label carries it too.
    monkeypatch.setitem(OBJECTS, "/images/sha256:9/json", {"Id": "sha256:9", "Config": {"Labels": MINE}})
    monkeypatch.setitem(OBJECTS, "/containers/op/json", {"Id": "c-op", "Image": "sha256:9", "Config": {"Labels": MINE}})
    responses = docker.send(request("POST", "/v1.55/containers/op/exec", {"Cmd": ["id"]}),
                            request("GET", "/v1.55/containers/op/json"),
                            request("POST", "/v1.55/containers/create", {"Image": "shared", "HostConfig": {
                                "VolumesFrom": ["op"]}}))
    assert [status(r) for r in responses] == [404, 404, 403]
    assert not docker.forwarded()


def test_objects_a_request_names_go_to_docker_by_the_id_checked(docker):
    # Docker finds a container by ID, then name, then ID prefix: a name sent on
    # after its check could, by then, find another's container.
    docker.send(request("POST", "/v1.55/containers/create", {"Image": "shared", "HostConfig": {
        "VolumesFrom": ["mine:ro", "mine"], "Links": ["mine", "mine:db"], "PidMode": "container:mine",
        "IpcMode": "container:mine", "Cgroup": "container:mine", "NetworkMode": "container:mine"},
        "NetworkingConfig": {"EndpointsConfig": {"mine-net": {"Links": ["mine:web"]}}}}),
        request("POST", "/v1.55/containers/create", {"Image": "shared", "HostConfig": {"NetworkMode": "mine-net"}}))
    first, second = (r["body"] for r in docker.forwarded())
    host = first["HostConfig"]
    assert host["VolumesFrom"] == ["c-mine:ro", "c-mine"]
    assert host["Links"] == ["c-mine:mine", "c-mine:db"]
    assert host["PidMode"] == host["IpcMode"] == host["Cgroup"] == host["NetworkMode"] == "container:c-mine"
    assert first["NetworkingConfig"]["EndpointsConfig"]["n-mine"]["Links"] == ["c-mine:web"]
    assert second["HostConfig"]["NetworkMode"] == "n-mine"


def test_a_container_joins_only_the_networks_checked(docker):
    # Docker joins the network an endpoint's NetworkID names over the one its key names.
    docker.send(request("POST", "/v1.55/containers/create", {"Image": "shared", "HostConfig": {"NetworkMode": "mine-net"},
                "NetworkingConfig": {"EndpointsConfig": {"mine-net": {"NetworkID": "bob-net", "Aliases": ["a"]}}}}),
                request("POST", "/v1.55/networks/mine-net/connect", {"Container": "mine", "EndpointConfig": {
                    "networkid": "bridge"}}))
    create, connect = docker.forwarded()
    assert create["body"]["NetworkingConfig"] == {"EndpointsConfig": {"n-mine": {
        "NetworkID": "n-mine", "Aliases": ["a"], "Links": []}}}
    assert connect["path"] == "/networks/n-mine/connect"
    assert connect["body"] == {"Container": "c-mine", "EndpointConfig": {"NetworkID": "n-mine", "Links": []}}


def test_new_objects_carry_the_tenant_label_and_lists_show_only_them(docker):
    responses = docker.send(
        request("POST", "/v1.55/containers/create", {"Image": "shared", "Labels": {LABEL: "bob", "app": "x"}}),
        request("POST", "/v1.55/networks/create", {"Name": "n"}),
        request("POST", "/v1.55/volumes/create", {"Name": "v"}),
        request("GET", '/v1.55/containers/json?all=1&filters={"status":["running"],"label":["app=x"]}'),
        request("GET", '/v1.55/networks?filters={"name":{"n":true}}'),
        request("GET", "/v1.55/volumes"), request("GET", "/v1.55/events?since=1"))
    assert [status(r) for r in responses] == [200, 200, 201, 200, 200, 200, 200]
    create, network, volume, containers, networks, volumes, events = docker.forwarded()
    assert create["body"]["Labels"] == {LABEL: "alice", "app": "x"}
    assert network["body"]["Labels"] == volume["body"]["Labels"] == MINE
    assert json.loads(containers["query"]["filters"]) == {"status": {"running": True},
                                                          "label": {"app=x": True, f"{LABEL}=alice": True}}
    assert json.loads(networks["query"]["filters"]) == {"name": {"n": True}, "label": {f"{LABEL}=alice": True}}
    for listing in (volumes, events):
        assert json.loads(listing["query"]["filters"]) == {"label": {f"{LABEL}=alice": True}}
    assert containers["query"]["all"] == "1" and events["query"]["since"] == "1"


@pytest.mark.parametrize("body", [
    {"Name": "argus-tenant-bob"}, {"Name": "n", "Driver": "macvlan"},
    {"Name": "n", "Options": {"com.docker.network.bridge.name": "docker0"}},
    {"Name": "n", "IPAM": {"Config": [{"Subnet": "10.0.0.0/8"}]}}, {"Name": "n", "ConfigFrom": {"Network": "x"}},
])
def test_networks_are_plain_bridges(docker, body):
    [response] = docker.send(request("POST", "/v1.55/networks/create", body))
    assert status(response) == 403 and not docker.forwarded()


@pytest.mark.parametrize("body", [{"Name": "argus-tenant-bob"}, {"Name": "v", "DriverOpts": {"device": "/"}},
                                  {"Name": "v", "Driver": "sshfs"}])
def test_volumes_are_plain_local_volumes(docker, body):
    [response] = docker.send(request("POST", "/v1.55/volumes/create", body))
    assert status(response) == 403 and not docker.forwarded()


def test_containers_join_the_tenants_own_network_instead_of_the_shared_bridge(docker):
    docker.send(request("POST", "/v1.55/containers/create", {"Image": "shared",
        "HostConfig": {"NetworkMode": "default"}, "NetworkingConfig": {"EndpointsConfig": {"default": {"Aliases": ["a"]}}}}))
    body = created(docker)
    assert body["HostConfig"]["NetworkMode"] == "n-alice"
    assert body["NetworkingConfig"] == {"EndpointsConfig": {"n-alice": {"Aliases": ["a"], "NetworkID": "n-alice", "Links": []}}}
    assert {"Name": "argus-tenant-alice", "Labels": MINE} in [r["body"] for r in docker.requests if r["path"] == "/networks/create"]


@pytest.mark.parametrize("given, sent", [
    ({}, {"Memory": 4 * 1024**3, "NanoCpus": 2 * 10**9, "PidsLimit": 100}),
    ({"Memory": 1024**3, "NanoCpus": 10**9, "PidsLimit": 10}, {"Memory": 1024**3, "NanoCpus": 10**9, "PidsLimit": 10}),
    ({"Memory": 1024**4, "NanoCpus": 64 * 10**9, "PidsLimit": -1}, {"Memory": 4 * 1024**3, "NanoCpus": 2 * 10**9, "PidsLimit": 100}),
    ({"memory": 1024**4, "CpuQuota": -1, "CpuPeriod": 50000}, {"Memory": 4 * 1024**3, "CpuQuota": 100000, "PidsLimit": 100}),
])
def test_containers_get_the_tenants_resource_limits(docker, given, sent):
    docker.send(request("POST", "/v1.55/containers/create", {"Image": "shared", "HostConfig": given}))
    host = created(docker)["HostConfig"]
    assert {key: host.get(key) for key in sent} == sent and ("CpuQuota" in sent) != ("NanoCpus" in host)


def test_updates_and_builds_stay_within_the_limits(docker):
    docker.send(request("POST", "/v1.55/containers/mine/update", {"Memory": 0, "NanoCpus": 0, "PidsLimit": None}),
                request("POST", "/v1.55/containers/mine/update", {"Memory": 1024**4, "NanoCpus": 10**12, "PidsLimit": 0}),
                request("POST", "/v1.55/containers/mine/update", {"Devices": [{"PathOnHost": "/dev/sda"}]}),
                request("POST", "/v1.55/build?t=new&memory=99999999999999&labels=%7B%22a%22%3A%22b%22%7D"))
    unchanged, capped, build = docker.forwarded()
    assert unchanged["body"] == {"Memory": 0, "NanoCpus": 0, "PidsLimit": None}
    assert capped["body"] == {"Memory": 4 * 1024**3, "NanoCpus": 2 * 10**9, "PidsLimit": 100}
    assert build["query"]["memory"] == str(4 * 1024**3) and build["query"]["cpuquota"] == "200000"
    assert build["query"]["networkmode"] == "n-alice"
    # Its image does not give containers the tenant's label; its FROM is pulled
    # afresh, never read from an image built on this host, nor is one a cache.
    assert json.loads(build["query"]["labels"]) == {"a": "b", LABEL: ""}
    assert build["query"]["pull"] == "1" and build["query"]["cachefrom"] == "[]"


def test_a_workspace_names_images_only_in_its_own_namespace(docker):
    # Else it could take a name others would pull, and they would run its image.
    responses = docker.send(
        request("POST", "/v1.55/build?t=python:3.99-slim"), request("POST", "/v1.55/build?t=argus-tenant-alice/app"),
        request("POST", "/v1.55/images/shared/tag?repo=ghcr.io/laude-institute/t-bench/base&tag=1"),
        request("POST", "/v1.55/images/built/tag?repo=shared:anything&tag=v1"),  # Docker makes that shared:v1
        request("POST", "/v1.55/commit?container=mine&repo=argus-tenant-bob/app&tag=9", {}),
        request("DELETE", "/v1.55/images/shared?force=%1C"), request("DELETE", "/v1.55/images/built"))
    assert [status(r) for r in responses] == [200] * 7
    build, again, public, own, commit, untag, remove = docker.forwarded()
    assert build["query"]["t"] == "argus-tenant-alice/python:3.99-slim" and again["query"]["t"] == "argus-tenant-alice/app"
    assert (public["path"], public["query"]["repo"]) == ("/images/shared/tag", "argus-tenant-alice/ghcr.io/laude-institute/t-bench/base")
    assert (own["path"], own["query"]["repo"]) == ("/images/argus-tenant-alice/built/tag", "argus-tenant-alice/shared:anything")
    assert commit["query"]["repo"] == "argus-tenant-alice/argus-tenant-bob/app"
    assert [untag["path"], remove["path"]] == ["/images/argus-tenant-alice/shared", "/images/argus-tenant-alice/built"]


def test_a_workspace_uses_its_own_images_and_registries_but_none_built_here_by_others(docker):
    responses = docker.send(*(request("POST", "/v1.55/containers/create", {"Image": image}) for image in
                              ("built", "shared", "sha256:2", "ab", "nowhere", "private")),
                            request("GET", "/v1.55/images/built/history"), request("GET", "/v1.55/images/get?names=shared&names=ab"))
    # "nowhere" is 404, so the Docker CLI pulls it and tries again.
    assert [status(r) for r in responses] == [200, 200, 200, 200, 404, 403, 200, 200]
    *creates, history, save = docker.forwarded()
    # By a name only where it is the image's own tag; by ID where a name or an ID prefix could find another.
    assert [r["body"]["Image"] for r in creates] == ["argus-tenant-alice/built", "shared", "sha256:2", "sha256:1"]
    assert history["path"] == "/images/argus-tenant-alice/built/history"
    assert parse_qsl(urlsplit(save["target"]).query) == [("names", "shared"), ("names", "sha256:1")]


def test_the_image_list_shows_the_tenants_own_images_and_registries(docker):
    docker.answers["GET", "/images/json"] = (200, [OWN, PUBLIC, PRIVATE, BOBS_IMAGE])
    [response] = docker.send(request("GET", "/v1.55/images/json?all=1"))
    assert status(response) == 200
    assert [image["Id"] for image in json.loads(response.split(b"\r\n\r\n", 1)[1])] == ["sha256:1", "sha256:2"]


def test_attach_is_checked_then_streams_both_ways(docker):
    async def steps():
        reader, writer = await asyncio.open_unix_connection("gate.sock")
        writer.write(request("POST", "/v1.55/containers/mine/attach?stream=1&stdin=1&stdout=1",
                             None, "Connection: Upgrade", "Upgrade: tcp"))
        head = await reader.readuntil(b"\r\n\r\n")
        writer.write(b"hello ")
        writer.write(b"stdin")
        writer.write_eof()  # docker run -i: stdin ends, the output does not
        rest = await asyncio.wait_for(reader.read(), 10)
        writer.close()
        return head, rest
    head, rest = docker.run(steps)
    assert head.startswith(b"HTTP/1.1 101") and rest == b"HELLO STDINBYE"
    [attach] = docker.forwarded()
    assert attach["path"] == "/containers/c-mine/attach" and attach["headers"]["upgrade"] == "tcp"
    assert "content-type" not in attach["headers"]


def test_a_connection_docker_did_not_take_over_carries_nothing_more(docker):
    docker.linger = 0.3
    docker.answers["POST", "/exec/e-mine/start"] = (409, {"message": "container is not running"})
    smuggled = request("POST", "/v1.55/containers/create", {"HostConfig": {"Privileged": True}})
    [response] = docker.send(request("POST", "/v1.55/exec/e-mine/start", {"Detach": False},
                                     "Connection: Upgrade", "Upgrade: tcp") + smuggled)
    assert status(response) == 409
    assert [r["path"] for r in docker.forwarded()] == ["/exec/e-mine/start"]
    assert docker.forwarded()[0]["headers"]["content-type"] == "application/json"
    assert docker.extra == b""


def test_a_quiet_stream_ends_when_its_client_leaves(docker):
    async def quiet(reader, writer):  # docker events, logs -f, wait: an answer, then nothing for a while
        await read_head(reader)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
        await reader.read()
        docker.extra = b"let go"
        writer.close()
    docker.handle = quiet

    async def steps():
        reader, writer = await asyncio.open_unix_connection("gate.sock")
        writer.write(request("GET", "/v1.55/events"))
        await reader.readuntil(b"\r\n\r\n")
        writer.close()  # Ctrl-C
        for _ in range(100):
            if docker.extra:
                break
            await asyncio.sleep(0.02)
    docker.run(steps)
    assert docker.extra == b"let go"


def test_a_request_that_never_arrives_is_given_up(docker, monkeypatch):
    monkeypatch.setattr(native_docker, "DEADLINE", 0.2)

    async def steps():
        reader, writer = await asyncio.open_unix_connection("gate.sock")
        writer.write(b"POST /v1.55/containers/create HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{")
        try:
            return await asyncio.wait_for(reader.read(), 5)
        finally:
            writer.close()
    assert status(docker.run(steps)) == 502
    assert not docker.forwarded()


def test_a_tenant_holds_only_so_many_connections_open(docker, monkeypatch):
    monkeypatch.setattr(native_docker, "CONNECTIONS", 2)

    async def steps():
        held = [await asyncio.open_unix_connection("gate.sock") for _ in range(2)]
        await asyncio.sleep(0.05)
        reader, writer = await asyncio.open_unix_connection("gate.sock")
        writer.write(request("GET", "/v1.55/_ping"))
        refused = await reader.readuntil(b"\r\n")  # the answer comes before the request is read
        writer.close()
        for _, writer in held:
            writer.close()
        await asyncio.sleep(0.05)
        return refused, await exchange(request("GET", "/v1.55/_ping"))
    refused, allowed = docker.run(steps)
    assert status(refused) == 503 and status(allowed) == 200


def test_a_body_too_large_to_check_quickly_is_refused(docker):
    [response] = docker.send(request("POST", "/v1.55/containers/create", {"Image": "shared", "Env": ["x" * 1024**2]}))
    assert status(response) == 413 and not docker.forwarded()


def test_the_socket_is_made_only_in_a_real_run_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("alice", "bob"):
        (tmp_path / name).mkdir()
    (tmp_path / "alice/run").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "bob/run").symlink_to(tmp_path / "elsewhere")
    config = {"tenants": {name: {"directory": str(tmp_path / name), "docker": True} for name in ("alice", "bob")}}
    config["tenants"]["carol"] = {"directory": str(tmp_path / "carol")}

    async def main():
        task = asyncio.ensure_future(native_docker.serve(config, "docker.sock"))
        for _ in range(100):
            if (tmp_path / "alice/run/docker.sock").exists():
                break
            await asyncio.sleep(0.02)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    asyncio.run(main())
    assert (tmp_path / "alice/run/docker.sock").is_socket()
    assert not (tmp_path / "elsewhere/docker.sock").exists()


def test_docker_tenants_get_the_socket_and_the_service(tmp_path, monkeypatch):
    from deploy import native_setup
    from deploy.native_runtime import sandbox_command

    config = {"revision": "sha", "source": str(tmp_path / "source"), "hosting_source": str(tmp_path / "hosting"),
              "python_runtime": str(tmp_path / "python"), "venv": str(tmp_path / "venv"),
              "node_runtime": str(tmp_path / "node"), "meter_runtime": str(tmp_path / "meter"),
              "tenants": {name: {"directory": str(tmp_path / name), "web_token": "w", "model_token": "m"}
                          for name in ("alice", "bob")}}
    config["tenants"]["alice"]["docker"] = True
    alice, bob = (sandbox_command(config, name) for name in ("alice", "bob"))
    assert alice[alice.index("DOCKER_HOST") + 1] == "unix:///tenant/run/docker.sock"
    assert alice[alice.index("DOCKER_BUILDKIT") + 1] == "0"
    assert "DOCKER_HOST" not in bob

    monkeypatch.setattr(native_setup.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(native_setup.subprocess, "run", lambda *args, **kwargs: None)
    units = tmp_path / ".config/systemd/user"
    native_setup.install_services(config, tmp_path)
    assert "native_docker.py" in (units / "argus-preview-docker.service").read_text()
    (units / "argus-preview-docker.service").unlink()
    del config["tenants"]["alice"]["docker"]
    native_setup.install_services(config, tmp_path)
    assert not (units / "argus-preview-docker.service").exists()
