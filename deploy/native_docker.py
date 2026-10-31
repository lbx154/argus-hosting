"""The host's Docker for tenants, through a gate that refuses only what leaves the tenant.

A tenant whose preview.json row has ``"docker": true`` finds Docker at
/tenant/run/docker.sock and uses it as usual: build, run, exec, attach, pipes,
logs, GPUs (``--gpus``), bridge networks, compose. Refused is what would hand it
the host or another tenant: privileged containers, extra capabilities, devices,
host namespaces and ports, host paths, other tenants' containers, networks and
volumes (they carry the label argus.tenant), swarm, plugins and prune.

Each connection carries one request, read whole and sent on in the canonical
form it was checked in: decoded and normalized path, re-encoded query, rebuilt
headers, re-serialized JSON. Nothing a client sends after it reaches Docker,
except the raw stream of an attach or exec Docker has taken over.

Host paths are mounted as subpaths of a per-tenant volume bound to the tenant's
directory: Docker resolves those itself at every start and refuses one a symlink
leads out of, which no check made here, before the start, could promise.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import posixpath
import re
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode

try:
    from .native_egress import refuse
except ImportError:  # Executed as deploy/native_docker.py.
    from native_egress import refuse

LABEL = "argus.tenant"
RESERVED = "argus-tenant-"  # the gate's own volume and network for each tenant
DOCKER = "/var/run/docker.sock"
# Docker's default capabilities, plus tracing and network administration,
# which act only inside the container's own namespaces.
CAPABILITIES = frozenset("AUDIT_WRITE CHOWN DAC_OVERRIDE FOWNER FSETID KILL MKNOD NET_BIND_SERVICE NET_RAW "
                         "SETFCAP SETGID SETPCAP SETUID SYS_CHROOT NET_ADMIN SYS_PTRACE".split())
# Where the tenant's sandbox shows parts of its directory (native_runtime.sandbox_command).
VIEWS = (("/tenant", ""), ("/tmp", "tmp"), ("/dev/shm", "shm"))
HOST_RULES = (  # HostConfig fields a tenant may not set, and why
    ("Privileged", bool, "a privileged container owns the host"),
    ("CapAdd", lambda caps: any(cap.upper().removeprefix("CAP_") not in CAPABILITIES for cap in caps or ()),
     "these capabilities reach past the container"),
    ("Devices", bool, "host devices"),
    ("DeviceCgroupRules", bool, "host devices"),
    ("Sysctls", bool, "kernel parameters"),
    ("CgroupParent", bool, "host cgroups"),
    ("Annotations", bool, "runtime annotations configure the host"),
    ("SecurityOpt", lambda options: any(not option.startswith("no-new-privileges") for option in options or ()),
     "seccomp, AppArmor and the rest stay on"),
    ("MaskedPaths", lambda paths: paths is not None, "/proc and /sys stay masked"),
    ("ReadonlyPaths", lambda paths: paths is not None, "/proc and /sys stay read-only"),
    ("PortBindings", bool, "ports on the host's network"),
    ("PublishAllPorts", bool, "ports on the host's network"),
    ("LogConfig", lambda log: (field(log or {}, "Type") or "json-file") not in ("json-file", "local", "none"),
     "log drivers send from the host's network"),
    ("VolumeDriver", lambda driver: driver not in (None, "", "local"), "volume plugins"),
    ("OomScoreAdj", lambda score: (score or 0) < 0, "the host's OOM killer would spare it"),
    ("Ulimits", lambda limits: any(field(limit, "Name") == "rtprio" for limit in limits or ()),
     "real-time priority starves the host"),
)
CONTAINER, EXEC, NETWORK, VOLUME = (rf"(?P<{kind}>[^/]+)" for kind in ("container", "exec", "network", "volume"))
ROUTES = [(methods.split(), re.compile(path), rule) for methods, path, rule in (
    # Everything else is refused. A named object must be this tenant's (images are shared).
    ("GET HEAD", r"/_ping", None),
    ("GET", r"/version|/info|/images/json|/images/search|/images/get|/images/.+/(json|history|get)"
            r"|/distribution/.+/json", None),
    ("POST", r"/auth|/images/.+/push", None),
    ("POST", r"/images/create", "pull"),
    ("POST", r"/build", "build"),
    ("POST", r"/images/(?P<image>.+)/tag", "tag"),
    ("DELETE", r"/images/(?P<image>.+)", "untag"),
    ("POST", r"/commit", "commit"),
    ("GET", r"/events|/containers/json|/networks|/volumes", "listing"),
    ("POST", r"/containers/create", "create"),
    ("GET", rf"/containers/{CONTAINER}/(json|top|logs|changes|export|stats|archive|attach/ws)", None),
    ("HEAD PUT", rf"/containers/{CONTAINER}/archive", None),
    ("POST", rf"/containers/{CONTAINER}/(start|stop|restart|kill|pause|unpause|rename|resize|wait|attach)", None),
    ("POST", rf"/containers/{CONTAINER}/update", "update"),
    ("POST", rf"/containers/{CONTAINER}/exec", "exec"),
    ("DELETE", rf"/containers/{CONTAINER}", None),
    ("GET", rf"/exec/{EXEC}/json", None),
    ("POST", rf"/exec/{EXEC}/(start|resize)", None),
    ("POST", r"/networks/create", "network_create"),
    ("GET DELETE", rf"/networks/{NETWORK}", None),
    ("POST", rf"/networks/{NETWORK}/(connect|disconnect)", "connect"),
    ("POST", r"/volumes/create", "volume_create"),
    ("GET DELETE", rf"/volumes/{VOLUME}", None),
)]
JSON_BODIES = {"create", "update", "exec", "connect", "network_create", "volume_create", "commit"}
HIJACKED = re.compile(r"/containers/[^/]+/attach(/ws)?|/exec/[^/]+/start")
TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
HOP = {"connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
       "content-length", "host", "expect"}
BODY_LIMIT = 16 * 1024**2


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def field(obj: dict, name: str):
    """``obj[name]`` as Docker reads it: Go matches JSON field names regardless
    of case, so whichever spelling a client used becomes ``name``."""
    for key in [key for key in obj if key.casefold() == name.casefold() and key != name]:
        obj[name] = obj.pop(key)
    return obj.get(name)


def unique(pairs: list) -> dict:
    """A JSON object, refused when two keys differ only in case: Go would take
    the last of them, which need not be the one checked."""
    if len({key.casefold() for key, _ in pairs}) < len(pairs):
        raise Refused(400, "a JSON object repeats a key")
    return dict(pairs)


def check_host(host: dict) -> None:
    for key, refused, reason in HOST_RULES:
        if refused(field(host, key)):
            raise Refused(403, f"{key}: {reason}")


def labels(data: dict) -> dict:
    return (data.get("Config") or data).get("Labels") or {}


def one(query: list, key: str) -> str | None:
    """The value Docker reads (Go's ``Form.Get``: the first)."""
    return next((value for name, value in query if name == key), None)


def put(query: list, key: str, value: str) -> None:
    query[:] = [(name, old) for name, old in query if name != key] + [(key, value)]


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() not in ("", "0", "no", "false", "none")


def within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def bound(resources: dict, limits: dict, fill: bool) -> None:
    """Cap memory, CPU and processes at preview.json ``limits``. ``fill`` (a new
    container, a build) also caps what is unset or unlimited; an update leaves
    what it does not change (0, or no PidsLimit) alone."""
    cpus = float(limits.get("cpus", 32))
    caps = {"Memory": (int(float(limits.get("memory_gb", 128)) * 1024**3), fill or field(resources, "Memory")),
            "PidsLimit": (int(limits.get("tasks", 65536)), fill or field(resources, "PidsLimit") is not None)}
    if field(resources, "CpuQuota") or field(resources, "CpuPeriod"):
        caps["CpuQuota"] = (int(cpus * (resources.get("CpuPeriod") or 100_000)), True)
    else:
        caps["NanoCpus"] = (int(cpus * 1e9), fill or field(resources, "NanoCpus"))
    for key, (cap, applies) in caps.items():
        if applies and not 0 < (resources.get(key) or 0) <= cap:
            resources[key] = cap


def make_directories(root: str, subpath: str) -> None:
    """Create a missing mount source as Docker would for a bind, never through
    a symlink; Docker itself refuses a subpath that resolves outside."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in filter(None, subpath.split("/")):
            made = False
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, dir_fd=fd)
                made = True
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            if made:
                os.fchmod(fd, 0o755)
    except OSError:
        pass  # a symlink or a file: Docker decides
    finally:
        os.close(fd)


async def read_head(reader) -> tuple[str, list]:
    lines = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")[:-2]
    headers = []
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if not colon or not TOKEN.fullmatch(name) or re.search(r"[\r\n\0]", value):
            raise Refused(400, "malformed header")
        headers.append((name.lower(), value.strip()))
    return lines[0], headers


async def exactly(reader, size: int):
    while size:
        piece = await reader.read(min(size, 65536))
        if not piece:
            raise asyncio.IncompleteReadError(b"", size)
        size -= len(piece)
        yield piece


def framing(headers: list) -> int | str:
    """How a request's body is delimited: its length (0 for none) or "chunked"."""
    lengths = {value for name, value in headers if name == "content-length"}
    encodings = [value.lower() for name, value in headers if name == "transfer-encoding"]
    if encodings == ["chunked"] and not lengths:
        return "chunked"
    if not encodings and len(lengths) <= 1 and all(re.fullmatch(r"[0-9]{1,15}", length) for length in lengths):
        return int(next(iter(lengths), 0))
    raise Refused(400, "ambiguous request framing")


async def body(reader, size: int | str):
    """The request body, decoded, in pieces."""
    if size == "chunked":
        while True:
            size = (await reader.readuntil(b"\r\n"))[:-2].split(b";")[0].strip()
            if not re.fullmatch(rb"[0-9a-fA-F]{1,8}", size):
                raise Refused(400, "malformed chunk")
            if not int(size, 16):
                break
            async for piece in exactly(reader, int(size, 16)):
                yield piece
            if await reader.readexactly(2) != b"\r\n":
                raise Refused(400, "malformed chunk")
        while await reader.readuntil(b"\r\n") != b"\r\n":
            pass  # trailers
    else:
        async for piece in exactly(reader, size):
            yield piece


async def whole(reader, size: int | str) -> bytes:
    data = bytearray()
    async for piece in body(reader, size):
        data += piece
        if len(data) > BODY_LIMIT:
            raise Refused(413, "request body too large")
    return bytes(data)


async def pipe(reader, writer) -> None:
    """Copy until EOF, then pass the EOF on: stdin closed by ``docker run -i``
    still lets the container's output come back."""
    try:
        while piece := await reader.read(65536):
            writer.write(piece)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except OSError:
        pass


class Request:
    """One client request, in the canonical form it is checked and forwarded in."""

    def __init__(self, start: str, headers: list):
        self.method, target, protocol = start.split(" ")
        if protocol not in ("HTTP/1.1", "HTTP/1.0") or not target.startswith("/"):
            raise Refused(400, "malformed request line")
        if any(value.split(";")[0].strip().lower() == "application/x-www-form-urlencoded"
               for name, value in headers if name == "content-type"):
            raise Refused(400, "Docker would read a form body over the query")
        raw, _, query = target.partition("?")
        path = posixpath.normpath("/" + unquote(raw, errors="strict").lstrip("/"))
        version = re.match(r"/v[0-9.]+(?=/)", path)
        self.version = version.group() if version else ""
        self.path = path[len(self.version):]
        self.query = parse_qsl(query, keep_blank_values=True)
        self.headers, self.size = headers, framing(headers)
        self.json = None
        for methods, pattern, rule in ROUTES:
            if self.method in methods and (match := pattern.fullmatch(self.path)):
                self.match, self.rule = match, rule
                return
        raise Refused(403, f"{self.method} {self.path} is not available in a workspace")

    def head(self, content: bytes | None) -> bytes:
        """The request line and headers as Docker gets them: a checked body
        goes with its length, any other with the client's own framing."""
        target = quote(self.version + self.path, safe="/:@!$&'()*+,;=-._~")
        if self.query:
            target += "?" + urlencode(self.query)
        # Docker closes the connection after one answer; one it takes over
        # (attach, exec) it upgrades regardless.
        connection = "Connection: close"
        upgrade = [value for name, value in self.headers if name == "upgrade"]
        if HIJACKED.fullmatch(self.path) and upgrade:
            connection = f"Connection: Upgrade, close\r\nUpgrade: {upgrade[0]}"
        headers = [(name, value) for name, value in self.headers if name not in HOP]
        if self.json is not None:
            headers = [(name, value) for name, value in headers if name != "content-type"]
            headers.append(("content-type", "application/json"))
        if content is not None:
            headers.append(("content-length", str(len(content))))
        elif self.size == "chunked":
            headers.append(("transfer-encoding", "chunked"))
        elif self.size:
            headers.append(("content-length", str(self.size)))
        lines = [f"{self.method} {target} HTTP/1.1", "Host: docker", connection,
                 *(f"{name}: {value}" for name, value in headers)]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


class Gate:
    """One tenant's Docker: what it may do, and which objects are its own."""

    def __init__(self, name: str, directory: str | Path, limits: dict | None, docker: str = DOCKER):
        self.name, self.directory, self.limits, self.docker = name, str(directory), limits or {}, docker
        # The volume holding the tenant's directory, and its default network.
        self.plumbing = RESERVED + name

    async def call(self, method: str, path: str, payload=None) -> tuple[int, object]:
        """Ask Docker directly; HTTP/1.0 so the answer is never chunked."""
        reader, writer = await asyncio.open_unix_connection(self.docker)
        try:
            data = b"" if payload is None else json.dumps(payload).encode()
            writer.write(f"{method} {path} HTTP/1.0\r\nHost: docker\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
            head, _, content = (await reader.read()).partition(b"\r\n\r\n")
        finally:
            writer.close()
        try:
            status = int(head.split(b" ")[1])
        except (IndexError, ValueError):
            raise ConnectionError("Docker gave no answer") from None
        with contextlib.suppress(ValueError):
            return status, json.loads(content)
        return status, None

    async def owned(self, kind: str, ref: str, status: int = 404) -> str:
        """The full ID of this tenant's container, exec, network or volume ``ref``.
        A request body naming another's is refused with 403, as a 404 there
        would send the Docker CLI to pull the image again."""
        path = {"container": "/containers/{}/json", "exec": "/exec/{}/json",
                "network": "/networks/{}", "volume": "/volumes/{}"}[kind]
        found, data = await self.call("GET", path.format(quote(ref.lstrip("/"), safe="")))
        if found == 200 and kind == "exec":
            await self.owned("container", data["ContainerID"])
            return data["ID"]
        if found != 200 or labels(data).get(LABEL) != self.name:
            raise Refused(status, f"No such {kind}: {ref}")
        return data.get("Id") or data["Name"]

    async def image(self, reference: str) -> str | None:
        """Who built image ``reference``: a tenant, "" for anyone else, None if there is none."""
        status, data = await self.call("GET", f"/images/{quote(reference, safe='/:@')}/json")
        return labels(data).get(LABEL, "") if status == 200 else None

    async def may_name(self, reference: str) -> None:
        if await self.image(reference) not in (None, self.name):
            raise Refused(403, f"{reference} names a shared image; a workspace names only images it built")

    def claim(self, body: dict) -> None:
        if (field(body, "Name") or "").startswith(RESERVED):
            raise Refused(403, f"names beginning {RESERVED} are reserved")
        body["Labels"] = {**(field(body, "Labels") or {}), LABEL: self.name}

    async def network(self, name: str) -> str:
        """The network a container or build joins: Docker's default is replaced
        by this tenant's own bridge, which no other tenant's containers share."""
        if name in ("", "default", "bridge", self.plumbing):
            await self.call("POST", "/networks/create", {"Name": self.plumbing})  # 409 once it exists
            return self.plumbing
        if name.casefold() == "host":
            raise Refused(403, "the host's network is not available")
        if name.startswith("container:"):
            await self.owned("container", name.removeprefix("container:"), 403)
        elif name != "none":
            await self.owned("network", name, 403)
        return name

    async def volume(self, name: str) -> None:
        """A named volume a container mounts: this tenant's, or a new one made its own."""
        if name.startswith(RESERVED):
            raise Refused(403, f"names beginning {RESERVED} are reserved")
        status, data = await self.call("GET", f"/volumes/{quote(name, safe='')}")
        if status == 404:
            await self.call("POST", "/volumes/create", {"Name": name, "Labels": {LABEL: self.name}})
        elif status != 200 or labels(data).get(LABEL) != self.name:
            raise Refused(403, f"No such volume: {name}")

    async def bind(self, source: str, target: str, readonly) -> dict:
        """A host path as the sandbox names it: a subpath of the tenant's own
        directory, or /usr read-only."""
        path = posixpath.normpath(source)
        for inside, outside in VIEWS:
            if within(path, inside):
                subpath = posixpath.join(outside, path[len(inside) + 1:]).strip("/")
                _, volume = await self.call("POST", "/volumes/create", {"Name": self.plumbing, "DriverOpts": {
                    "type": "none", "o": "bind", "device": self.directory}})
                if ((volume or {}).get("Options") or {}).get("device") != self.directory:
                    raise Refused(500, f"volume {self.plumbing} does not hold this workspace")
                make_directories(self.directory, subpath)
                return {"Type": "volume", "Source": self.plumbing, "Target": target, "ReadOnly": bool(readonly),
                        "VolumeOptions": {"Subpath": subpath, "NoCopy": True}}
        if within(path, "/usr") and within(real := os.path.realpath(path), "/usr"):
            return {"Type": "bind", "Source": real, "Target": target, "ReadOnly": True}
        raise Refused(403, f"{source}: a workspace mounts its own files (/tenant, /tmp, /dev/shm) and /usr read-only")

    async def host_config(self, host: dict) -> None:
        check_host(host)
        for key in ("PidMode", "IpcMode", "UTSMode", "UsernsMode", "CgroupnsMode", "Cgroup"):
            mode = field(host, key) or ""
            if mode.casefold() == "host":
                raise Refused(403, f"{key}: the host's namespaces are not available")
            if mode.startswith("container:"):
                await self.owned("container", mode.removeprefix("container:"), 403)
        for ref in [*(field(host, "Links") or ()), *(field(host, "VolumesFrom") or ())]:
            await self.owned("container", ref.split(":")[0], 403)
        binds, mounts = [], []
        for spec in field(host, "Binds") or ():
            source, *rest = spec.split(":")
            if len(rest) > 2:
                raise Refused(400, f"invalid volume specification: {spec}")
            if rest and source.startswith("/"):
                mounts.append(await self.bind(source, rest[0], "ro" in (rest[1:] or [""])[0].split(",")))
                continue
            if rest:
                await self.volume(source)
            binds.append(spec)
        for mount in field(host, "Mounts") or ():
            kind = field(mount, "Type")
            if kind == "bind":
                mount = await self.bind(field(mount, "Source") or "", field(mount, "Target"), field(mount, "ReadOnly"))
            elif kind == "volume":
                driver = field(field(mount, "VolumeOptions") or {}, "DriverConfig") or {}
                if field(driver, "Options") or field(driver, "Name") not in (None, "", "local"):
                    raise Refused(403, "volume driver options can mount any host path")
                if source := field(mount, "Source"):
                    await self.volume(source)
            elif kind not in ("tmpfs", "image"):
                raise Refused(403, f"{kind} mounts are not available")
            mounts.append(mount)
        host["Binds"], host["Mounts"] = binds, mounts

    async def check(self, request: Request) -> None:
        for kind, ref in request.match.groupdict().items():
            if kind != "image":  # images are shared
                start, end = request.match.span(kind)
                request.path = request.path[:start] + await self.owned(kind, ref) + request.path[end:]
        if request.rule:
            await getattr(self, request.rule)(request)

    # The rules named in ROUTES.

    async def create(self, request: Request) -> None:
        body = request.json
        body["HostConfig"] = host = field(body, "HostConfig") or {}
        await self.host_config(host)
        bound(host, self.limits, fill=True)
        host["NetworkMode"] = await self.network(field(host, "NetworkMode") or "")
        endpoints = field(field(body, "NetworkingConfig") or {}, "EndpointsConfig") or {}
        body["NetworkingConfig"] = {"EndpointsConfig": {
            await self.network(name): endpoint for name, endpoint in endpoints.items()}}
        body["Labels"] = {**(field(body, "Labels") or {}), LABEL: self.name}

    async def update(self, request: Request) -> None:
        check_host(request.json)
        bound(request.json, self.limits, fill=False)

    async def exec(self, request: Request) -> None:
        if field(request.json, "Privileged"):
            raise Refused(403, "Privileged: a privileged exec owns the host")

    async def connect(self, request: Request) -> None:
        await self.owned("container", field(request.json, "Container") or "")

    async def network_create(self, request: Request) -> None:
        body = request.json
        ipam = field(body, "IPAM") or {}
        if (field(body, "Driver") not in (None, "", "bridge") or field(body, "Options") or field(body, "ConfigFrom")
                or field(body, "ConfigOnly") or field(body, "Ingress") or field(ipam, "Config")
                or field(ipam, "Options") or field(ipam, "Driver") not in (None, "", "default")):
            raise Refused(403, "a workspace creates plain bridge networks; drivers, options and subnets reach the host's")
        self.claim(body)

    async def volume_create(self, request: Request) -> None:
        body = request.json
        if field(body, "Driver") not in (None, "", "local") or field(body, "DriverOpts") or field(body, "ClusterVolumeSpec"):
            raise Refused(403, "volume drivers and their options can mount any host path")
        self.claim(body)

    async def listing(self, request: Request) -> None:
        # Docker's own format, {"label": {"a=b": true}}; it reads [] lists only when no value is a map.
        filters = {key: dict.fromkeys(values, True) for key, values in json.loads(one(request.query, "filters") or "{}").items()}
        filters.setdefault("label", {})[f"{LABEL}={self.name}"] = True
        put(request.query, "filters", json.dumps(filters))

    async def pull(self, request: Request) -> None:
        if one(request.query, "fromSrc") is not None:
            raise Refused(403, "importing could replace a shared image's name; pull or build instead")

    async def build(self, request: Request) -> None:
        query = request.query
        if one(query, "version") not in (None, "", "1"):
            raise Refused(403, "BuildKit's requests cannot be checked one by one; build with DOCKER_BUILDKIT=0")
        if one(query, "cgroupparent"):
            raise Refused(403, "cgroupparent: host cgroups")
        for name, value in query:
            if name == "t":
                await self.may_name(value)
        put(query, "networkmode", await self.network(one(query, "networkmode") or ""))
        put(query, "labels", json.dumps({**json.loads(one(query, "labels") or "{}"), LABEL: self.name}))
        resources = {"Memory": int(one(query, "memory") or 0), "CpuPeriod": int(one(query, "cpuperiod") or 100_000),
                     "CpuQuota": int(one(query, "cpuquota") or 0)}
        bound(resources, self.limits, fill=True)
        for key in ("Memory", "CpuPeriod", "CpuQuota"):
            put(query, key.lower(), str(resources[key]))

    async def tag(self, request: Request) -> None:
        repository, tag = one(request.query, "repo") or "", one(request.query, "tag")
        await self.may_name(f"{repository}:{tag}" if tag else repository)

    async def untag(self, request: Request) -> None:
        if truthy(one(request.query, "force")):
            raise Refused(403, "force would remove an image other containers use")
        if await self.image(request.match["image"]) != self.name:
            raise Refused(403, "a workspace removes only images it built")

    async def commit(self, request: Request) -> None:
        put(request.query, "container", await self.owned("container", one(request.query, "container") or ""))
        if repository := one(request.query, "repo"):
            tag = one(request.query, "tag")
            await self.may_name(f"{repository}:{tag}" if tag else repository)
        request.json["Labels"] = {**(field(request.json, "Labels") or {}), LABEL: self.name}

    async def handle(self, reader, writer) -> None:
        upstream = upward = None
        answered = False
        try:
            start, headers = await asyncio.wait_for(read_head(reader), 60)
            try:
                request = Request(start, headers)
            except ValueError as exc:
                raise Refused(400, f"malformed request: {exc}") from None
            hijacked = HIJACKED.fullmatch(request.path)
            content = None
            if request.rule in JSON_BODIES or hijacked:
                content = await whole(reader, request.size)
            if request.rule in JSON_BODIES:
                request.json = json.loads(content or b"{}", object_pairs_hook=unique) or {}
            try:
                await self.check(request)
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                raise Refused(400, f"malformed request: {exc!r}") from None
            if request.json is not None:
                content = json.dumps(request.json).encode()
            other, upstream = await asyncio.open_unix_connection(self.docker)
            upstream.write(request.head(content) + (content or b""))
            if content is None:
                chunked = request.size == "chunked"
                async for piece in body(reader, request.size):
                    upstream.write(b"%x\r\n%s\r\n" % (len(piece), piece) if chunked else piece)
                    await upstream.drain()
                if chunked:
                    upstream.write(b"0\r\n\r\n")
            await upstream.drain()
            response = await other.readuntil(b"\r\n\r\n")
            writer.write(response)
            answered = True
            # Only once Docker has taken the connection over is the rest of it
            # a raw stream rather than requests.
            status = response.split(b" ", 2)[1]
            if hijacked and (status == b"101" or b"\ncontent-type: application/vnd.docker." in response.lower()):
                upward = asyncio.ensure_future(pipe(reader, upstream))
            await pipe(other, writer)
        except Refused as exc:
            if not answered:
                refuse(writer, f"{exc.status} {HTTPStatus(exc.status).phrase}", str(exc))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            if not answered:
                refuse(writer, "400 Bad Request", f"malformed request: {exc}")
        except (OSError, EOFError, asyncio.LimitOverrunError, TimeoutError) as exc:
            if not answered:
                refuse(writer, "502 Bad Gateway", f"{type(exc).__name__}: {exc}")
        finally:
            if upward:
                upward.cancel()
            writer.close()
            if upstream:
                upstream.close()


async def serve(config: dict, docker: str = DOCKER) -> None:
    """Listen at run/docker.sock in each Docker tenant's directory. The socket
    is made through an open handle on run/, never through a symlink there."""
    servers = []
    for name, row in config["tenants"].items():
        if not row.get("docker"):
            continue
        gate = Gate(name, row["directory"], config.get("limits"), docker)
        try:
            run = os.open(Path(row["directory"]) / "run", os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
            with contextlib.suppress(FileNotFoundError):
                os.unlink("docker.sock", dir_fd=run)
            servers.append(await asyncio.start_unix_server(gate.handle, f"/proc/self/fd/{run}/docker.sock",
                                                           limit=65536))
        except OSError as exc:
            print(f"{name}: no Docker socket: {exc}", flush=True)
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    asyncio.run(serve(json.loads(parser.parse_args().config.read_text())))
