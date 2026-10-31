"""Launch one tenant in a filesystem, user, PID and network namespace.

The tenant gets a fresh /proc, only its own writable directory, read-only
language runtimes/source, and Unix sockets for metered models/public egress.
There is no host network, operator home, provider login or other tenant mount.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from .preview_hosts import preview_domain, preview_template
except ImportError:  # Executed as deploy/native_runtime.py inside the namespace.
    from preview_hosts import preview_domain, preview_template

MODEL = "gpt-6.1-sol"


def sandbox_command(config: dict, tenant: str, command: list[str] | None = None) -> list[str]:
    row = config["tenants"][tenant]
    source = Path(config["source"])
    hosting_source = Path(config["hosting_source"])
    python = Path(config["python_runtime"])
    venv = Path(config["venv"])
    node = Path(config["node_runtime"])
    args = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
    for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if Path(path).exists():
            args.extend(("--ro-bind", path, path))
    directory = Path(row["directory"])
    for name in ("tmp", "shm"):
        (directory / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    args.extend(("--proc", "/proc", "--dev", "/dev", "--dir", "/etc"))
    if (config.get("limits") or {}).get("gpu", True):
        # Share the host's NVIDIA GPUs. Only the device nodes are needed: the
        # driver libraries and nvidia-smi come in with /usr.
        for device in sorted(Path("/dev").glob("nvidia*")):
            args.extend(("--dev-bind", str(device), str(device)))
    for path in ("/etc/ssl", "/etc/ca-certificates", "/etc/hosts", "/etc/nsswitch.conf", "/etc/passwd", "/etc/group", "/etc/ld.so.cache"):
        if Path(path).exists():
            args.extend(("--ro-bind", path, path))
    # A tenant may keep its own Argus release (preview.json tenants.NAME.argus_package) while the rest move on.
    argus_package = row.get("argus_package", config.get("argus_package"))
    packages = [package for package in (config.get("codex_package"), argus_package) if package]
    for path in (source, hosting_source, python, venv, node, *packages):
        args.extend(("--ro-bind", str(path), str(path)))
    args.extend(("--bind", row["directory"], "/tenant", "--ro-bind", config["meter_runtime"], "/meter"))
    if "copilot_package" in config:
        args.extend(("--ro-bind", config["copilot_package"], "/tenant/home/.cache/copilot/pkg"))
    args.extend(("--bind", str(directory / "tmp"), "/tmp", "--bind", str(directory / "shm"), "/dev/shm",
                 "--remount-ro", "/dev", "--remount-ro", "/"))
    env = {
        "HOME": "/tenant/home", "PATH": f"/tenant/bin:{venv}/bin:{node}/bin:/usr/bin:/bin",
        "PYTHONPATH": os.pathsep.join((str(source), str(hosting_source))), "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8", "TMPDIR": "/tmp", "ARGUS_SKILL_HOME": "/tenant/state",
        "ARGUS_SKILL_SOURCE_ROOT": str(source), "ARGUS_SKILL_BUILD_REVISION": config["revision"],
        "ARGUS_SKILL_WEB_TOKEN": row["web_token"], "ARGUS_SKILL_COPILOT_TRIAL": "0", "ARGUS_SKILL_MODEL": MODEL,
        "HTTP_PROXY": "http://127.0.0.1:9012", "HTTPS_PROXY": "http://127.0.0.1:9012",
        "http_proxy": "http://127.0.0.1:9012", "https_proxy": "http://127.0.0.1:9012",
        "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
        # Node 22's built-in fetch ignores the variables above unless told to read them.
        "NODE_USE_ENV_PROXY": "1",
    }
    if config.get("agent") == "codex":
        env.update({"ARGUS_SKILL_RUNNER_BACKEND": "codex", "ARGUS_SKILL_RUNNER_BIN": "/tenant/bin/codex",
                    "CODEX_HOME": "/tenant/home/.codex", "ARGUS_METER_TOKEN": row["model_token"]})
    else:
        env.update({
            "ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_RUNNER_BIN": "/tenant/bin/copilot",
            "COPILOT_HOME": "/tenant/home/.copilot", "COPILOT_MODEL": MODEL, "COPILOT_PROVIDER_MODEL_ID": MODEL,
            "COPILOT_PROVIDER_WIRE_MODEL": MODEL, "COPILOT_PROVIDER_TYPE": "openai",
            "COPILOT_PROVIDER_WIRE_API": "responses", "COPILOT_PROVIDER_TRANSPORT": "http",
            "COPILOT_PROVIDER_BASE_URL": "http://127.0.0.1:9011/v1",
            "COPILOT_PROVIDER_BEARER_TOKEN": row["model_token"],
            "COPILOT_PROVIDER_MAX_PROMPT_TOKENS": "128000", "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS": "16384",
        })
    if row.get("docker"):
        # The host's Docker through deploy/native_docker.py, with its classic
        # builder: BuildKit's requests cannot be checked one by one.
        env.update({"DOCKER_HOST": "unix:///tenant/run/docker.sock", "DOCKER_BUILDKIT": "0"})
    if config.get("runtime") == "js":
        env.update({"ARGUS_HOME": "/tenant/state", "ARGUS_WEB_TOKEN": row["web_token"],
                    "ARGUS_TRIAL_PACKAGE": argus_package})
        if domain := preview_domain(config.get("preview_domain")):
            env["ARGUS_TRIAL_PREVIEW_URL"] = preview_template(tenant, domain)
    for role in ("ENGINEER", "REVIEWER", "PLANNER", "MANAGER", "LIGHT"):
        env[f"ARGUS_SKILL_{role}_MODEL"] = MODEL
    # How many of one tenant's projects may run at once (preview.json limits.daemons).
    env["ARGUS_SKILL_MAX_ACTIVE_DAEMONS"] = str(int((config.get("limits") or {}).get("daemons", 64)))
    for key, value in env.items():
        args.extend(("--setenv", key, value))
    args.extend(("--chdir", "/tenant/workspace", "--"))
    return args + (command or [str(venv / "bin/python"), str(hosting_source / "deploy/native_runtime.py"), "inside"])


async def inside():
    import resource

    import uvicorn
    from argus.webapi.server import create_app
    from native_egress import bridge

    # No file size cap (wheels such as torch and model weights run to GBs);
    # open files up to what the host grants. The workspace's total size is
    # still watched from outside (StorageWatch).
    resource.setrlimit(resource.RLIMIT_FSIZE, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    files = 65536 if hard == resource.RLIM_INFINITY else min(65536, hard)
    resource.setrlimit(resource.RLIMIT_NOFILE, (files, files))
    model = await asyncio.start_server(lambda r, w: bridge(r, w, target_socket="/meter/model.sock"), "127.0.0.1", 9011)
    proxy = await asyncio.start_server(lambda r, w: bridge(r, w, target_socket="/meter/egress.sock"), "127.0.0.1", 9012)
    socket_path = Path("/tenant/run/web.sock")
    socket_path.unlink(missing_ok=True)
    async with model, proxy:
        if package := os.environ.get("ARGUS_TRIAL_PACKAGE"):
            await serve_js(Path(package), socket_path)
            return
        app = create_app(global_root="/tenant/state")
        server = uvicorn.Server(uvicorn.Config(app, uds=str(socket_path), access_log=False, proxy_headers=False))
        await server.serve()


async def serve_js(package: Path, socket_path: Path, port: int = 8799):
    """The TypeScript Argus serves TCP inside the tenant's own network; the
    portal reaches it through the same Unix socket a Python runtime listens on."""
    async def pipe(reader, writer):
        # No idle or size bound: page streams stay open while work runs.
        try:
            while chunk := await reader.read(65536):
                writer.write(chunk)
                await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()

    async def forward(reader, writer):
        try:
            other_reader, upstream = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            writer.close()
            return
        await asyncio.gather(pipe(reader, upstream), pipe(other_reader, writer))

    # Each tenant runs one runtime, and this namespace is new: an owner left by
    # the last one is stale. Its pid is often this child's own (pids restart
    # at 1 per namespace), which Argus would read as "in use" and refuse.
    (Path(os.environ["ARGUS_HOME"]) / "owner.json").unlink(missing_ok=True)
    command = ["node", str(package / "dist/argus.mjs"), "--port", str(port), "--backend", "codex", "--model", MODEL]
    if template := os.environ.get("ARGUS_TRIAL_PREVIEW_URL"):
        command.extend(("--preview-url", template))
    child = await asyncio.create_subprocess_exec(*command)
    web = await asyncio.start_unix_server(forward, str(socket_path))
    async with web:
        code = await child.wait()
    raise SystemExit(code)


class ResourceWatchdog:
    """Fallback for hosts without delegated cgroup resource controllers.

    Account the entire sandbox process tree, including threads, and stop it
    only on runaway memory or process counts (preview.json ``limits``
    ``memory_gb`` and ``tasks``). CPU is never policed: busy work runs slower,
    it is not cancelled.
    """
    def __init__(self, pid: int, *, memory_bytes: int = 128 * 1024**3, tasks: int = 65536):
        import psutil

        self.process = psutil.Process(pid)
        self.memory_limit = memory_bytes
        self.task_limit = tasks
        self.memory_bytes = self.threads = 0

    def exceeded(self) -> str:
        import psutil

        memory = threads = 0
        try:
            processes = [self.process, *self.process.children(recursive=True)]
        except psutil.NoSuchProcess:
            return ""
        for process in processes:
            try:
                memory += process.memory_info().rss
                threads += process.num_threads()
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
            except (PermissionError, psutil.AccessDenied):
                return "resource inspection"
        self.memory_bytes, self.threads = memory, threads
        if memory > self.memory_limit:
            return "memory"
        if threads > self.task_limit:
            return "process/thread"
        return ""


STORAGE_BYTES = 2 * 1024**3
STORAGE_ENTRIES = 5_000_000  # a few unpacked datasets and environments, not a cap on ordinary work


def storage_usage(directory: str | Path) -> tuple[int, int]:
    """Bytes and entries (files and directories) under ``directory``."""
    count = size = 0
    for root, dirs, files in os.walk(directory, followlinks=False):
        count += len(dirs) + len(files)
        for name in files:
            try:
                size += os.lstat(os.path.join(root, name)).st_size
            except FileNotFoundError:
                continue
    return size, count


def cache_directories(directory: str | Path) -> list[Path]:
    """The tenant's download caches: what its agents share, and the copies
    each project's private home kept before they were shared."""
    from argus.core.sandbox import SHARED_CACHES

    state = Path(directory) / "state"
    return [state / "cache", *(
        home / inside for home in sorted(state.glob("sandbox/*/tenant/home")) for inside in SHARED_CACHES.values()
    )]


def prune_caches(directory: str | Path, size: int, count: int, *, target: int, target_entries: int) -> tuple[int, int]:
    """Delete cached files, least recently used first, until the workspace is
    within ``target`` bytes and ``target_entries`` entries or no cache is left.
    Everything in them is downloaded again when needed. Returns the new usage."""
    cached = []
    for root in cache_directories(directory):
        for parent, _dirs, files in os.walk(root, followlinks=False):
            for name in files:
                path = os.path.join(parent, name)
                try:
                    stat = os.lstat(path)
                except FileNotFoundError:
                    continue
                cached.append((max(stat.st_atime, stat.st_mtime), stat.st_size, path))
    cached.sort()
    for _used, length, path in cached:
        if size <= target and count <= target_entries:
            break
        try:
            os.unlink(path)
        except FileNotFoundError:
            continue
        size, count = size - length, count - 1
    for root in cache_directories(directory):
        for parent, dirs, files in os.walk(root, topdown=False):
            if parent != str(root) and not dirs and not files:
                try:
                    os.rmdir(parent)
                    count -= 1
                except OSError:
                    continue
    return size, count


def stop_runtime(child: subprocess.Popen, *, grace: float = 10) -> None:
    """Stop the namespace, its processes first: a SIGTERM lets each daemon
    record "stopped by host shutdown, resumable" (``daemon.life_worker``), so
    the next start resumes it. bubblewrap would end them with SIGKILL."""
    import psutil

    try:
        inside = psutil.Process(child.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        inside = []
    for process in inside:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue
    psutil.wait_procs(inside, timeout=grace)
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def stop_for_limit(child: subprocess.Popen, reason: str) -> None:
    """Release a crowded runtime and let systemd restart it.

    Memory and processes are released by stopping the namespace; exit 75
    makes Restart=on-failure bring the web workspace back without resetting
    either its files or the independent model allowance. A full workspace
    comes back paused (``argus_trial_server.storage_pause``): the page says so and no
    work starts until files are removed. Only storage that keeps growing
    while paused stays stopped for an operator.
    """
    stop_runtime(child)
    if reason != "storage growing while paused":
        raise SystemExit(75)


class StorageWatch:
    """Keep one tenant's workspace within its storage limit.

    Over the limit, the shared download caches are pruned first (down to half
    the limit), and only a workspace still over it pauses. Paused, usage under
    90% of the limit lifts the pause; the restarted runtime resumes the work
    the pause interrupted (``webapi.host_resume``).
    """

    def __init__(self, directory: str | Path, limits: dict | None = None):
        limits = limits or {}
        self.directory = Path(directory)
        self.state = self.directory / "state"
        self.limit = int(float(limits.get("storage_gb", STORAGE_BYTES / 1024**3)) * 1024**3)
        self.entries = int(limits.get("storage_entries", STORAGE_ENTRIES))

    def check(self) -> str:
        """``"pause"``, ``"resume"``, ``"stop"`` (growing while paused) or ``""``."""
        from argus_trial_server.storage_pause import (
            clear_storage_pause,
            read_storage_pause,
            record_storage_pause,
        )

        size, count = storage_usage(self.directory)
        if pause := read_storage_pause(self.state):
            if size < 0.9 * self.limit and count < 0.9 * self.entries:
                clear_storage_pause(self.state)
                return "resume"
            return "stop" if size > 1.25 * max(self.limit, pause["used_bytes"]) else ""
        if size <= self.limit and count <= self.entries:
            return ""
        size, count = prune_caches(self.directory, size, count, target=self.limit // 2, target_entries=self.entries // 2)
        print(f"Trial workspace storage over its limit; pruned download caches, now {size} bytes, {count} entries", flush=True)
        if size <= self.limit and count <= self.entries:
            return ""
        record_storage_pause(self.state, used_bytes=size, limit_bytes=self.limit)
        return "pause"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["launch", "inside", "exec"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--tenant")
    args, extra = parser.parse_known_args()
    if args.mode == "inside":
        asyncio.run(inside())
    else:
        config = json.loads(args.config.read_text())
        # The storage check imports Argus: take the source the tenants run, not
        # an older copy installed in the venv, which made every launcher fail
        # that import and leave its runtime unwatched.
        if source := config.get("source"):
            sys.path.insert(0, source)
        command = extra or None
        if command and command[0] == "--":
            command = command[1:]
        limits = config.get("limits") or {}
        with subprocess.Popen(sandbox_command(config, args.tenant, command), env={}) as child:
            directory = config["tenants"][args.tenant]["directory"]
            watchdog = ResourceWatchdog(child.pid, memory_bytes=int(float(limits.get("memory_gb", 128)) * 1024**3),
                                        tasks=int(limits.get("tasks", 65536)))
            storage = StorageWatch(directory, config.get("limits"))
            last_storage = 0.0
            while child.poll() is None:
                now = time.monotonic()
                reason = watchdog.exceeded() if args.mode == "launch" else ""
                if args.mode == "launch" and now - last_storage >= 2:
                    last_storage = now
                    outcome = storage.check()
                    if outcome == "resume":
                        print("Trial workspace storage is under its limit again; restarting runtime", flush=True)
                        stop_runtime(child)
                        raise SystemExit(75)
                    reason = {"pause": "workspace storage", "stop": "storage growing while paused"}.get(outcome, reason)
                if reason:
                    print(f"Trial {reason} limit reached; pausing runtime. RSS={watchdog.memory_bytes} threads={watchdog.threads}", flush=True)
                    stop_for_limit(child, reason)
                    return
                time.sleep(0.5 if args.mode == "launch" else 0.1)
            raise SystemExit(child.returncode)


if __name__ == "__main__":
    main()
