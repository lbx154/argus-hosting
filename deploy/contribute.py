"""Open the pull requests that offer trial tenants' own skills to the skill store.

A tenant's Argus team keeps its skills in <ARGUS_HOME>/skills/<name>/. When the
user offers one to the store, Argus writes <ARGUS_HOME>/contributions/<name>.json:
{"name", "version", "requested_at"}. This job, run as the host user with that
user's gh login (argus-NAME-contribute.timer, every 30 seconds), answers each
request not yet answered: no "submitted_at", or one older than "requested_at".
It checks the folder against the store's rule, commits it as packs/<name>/ on the
branch contrib/<tenant>-<name> of an argus-verticals clone, pushes that to our
fork and opens a pull request to the store, or reuses the one already open. The
store's maintainers review it; only once merged is the skill in the store. The
answer goes into the same file: the request's fields with "url" (the pull
request) or "error" (a sentence for the user), and "submitted_at".

Both the request and the folder belong to the tenant, so nothing is read through
a link: one would carry a host file, preview.json say, into a public pull request.

The clone (--repo) is kept for this job alone: every request resets its working
tree. `gh repo clone lbx154/argus-verticals CLONE` makes one, with our fork as
"origin" and the store as "upstream".
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager, suppress
from pathlib import Path

NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
SKILL = "SKILL.md"
LIMIT = 8 * 1024 * 1024  # what the store holds of one pack
REQUEST_LIMIT = 4096
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# What no part of a path in the store may hold; lone surrogates are names that are not UTF-8.
UNSAFE = re.compile(r"[\x00-\x1f:\\\ud800-\udfff]")
SECRET = re.compile(r"token|secret|invite|password|key", re.I)  # preview.json keys whose values are secrets
AUTHOR = ("-c", "user.name=lbx154", "-c", "user.email=145820328+lbx154@users.noreply.github.com")
UNREADABLE = "贡献请求读不懂，请在页面上重新点“贡献到技能商店”。"
TOO_BIG = "这个技能合计超过 8 MiB，商店收不下。"
FAILED = "提交到技能商店时出了问题，稍后可以重试；详情记在托管日志里。"
# The title and body leave out the version, the tenant's own text: a mention in the body would
# notify whoever it names, and an open pull request keeps its first title when asked again.
BODY = """试用站 {site} 的租户 {tenant} 里，Argus 团队自创了技能 `{name}`，用户在页面上确认把它贡献到技能商店。

这个 PR 只改 `packs/{name}/` 和由 `scripts/build_packs.py` 重新生成的 `packs/catalog.json`。提交前托管端已按商店规则检查过：有 SKILL.md 且写明 description，路径合规，合计不超过 8 MiB，不含试用站的邀请码和令牌，读文件时不跟随符号链接。

维护者审核合并后，这个技能才会进入商店；合并前不会上架。
"""


class Rejected(Exception):
    """The store would not take this request or folder; the message tells the user why."""


@contextmanager
def directory(name: str, dir_fd: int | None = None):
    """An open directory, never reached through a link."""
    fd = os.open(name, DIRECTORY, dir_fd=dir_fd)
    try:
        yield fd
    finally:
        os.close(fd)


def read(name: str, dir_fd: int, limit: int) -> bytes:
    """A regular file's bytes, never through a link: ``limit + 1`` of them at most."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    with open(fd, "rb") as file:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Rejected("商店只收普通文件，不收链接或特殊文件。")
        return file.read(limit + 1)


def number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load(offers: int, filename: str) -> bytes:
    """A request file's bytes; none when it cannot be read."""
    try:
        return read(filename, offers, REQUEST_LIMIT)
    except (OSError, Rejected):
        return b""


def requests(offers: int):
    """The requests in a tenant's contributions/ still to answer: file name, bytes and
    request. A file that is no request comes as {}, so that its answer settles it; one
    asked again at a time still to come waits for it, and so is answered once."""
    for filename in sorted(os.listdir(offers)):
        if not (filename.endswith(".json") and NAME.fullmatch(filename[:-5])):
            continue
        data = load(offers, filename)
        try:
            request = json.loads(data) if len(data) <= REQUEST_LIMIT else {}
        except ValueError:
            request = {}
        request = request if isinstance(request, dict) else {}
        asked, answered = request.get("requested_at"), request.get("submitted_at")
        if answered is None or (number(asked) and number(answered) and answered < asked <= time.time()):
            yield filename, data, request


def secrets_of(config: dict) -> set[bytes]:
    """What preview.json keeps secret: every tenant's invitation and tokens, the relay
    secret, and the upstream key its token_file holds."""
    found = set()

    def visit(value, key=""):
        if isinstance(value, dict):
            for inner, item in value.items():
                visit(item, inner)
        elif isinstance(value, str) and SECRET.search(key):
            if key.endswith("_file"):
                try:
                    value = Path(value).read_text().strip()
                except OSError:
                    return
            if value:
                found.add(value.encode())
    visit(config)
    return found


def field(matter: str, key: str) -> str:
    """A front matter value read as the store reads it (scripts/build_packs.py)."""
    said = (re.search(rf"^{key}:[ \t]*(.*)$", matter, re.M) or [None, ""])[1].strip()
    with suppress(ValueError):
        if said.startswith('"'):
            return str(json.loads(said))
    return re.sub(r"^'(.*)'$", r"\1", said)


def walk(fd: int, prefix: str = ""):
    """(path, directory, name) of each file below the open directory ``fd`` that the store
    would hold: like its catalog, none in __pycache__ and no .pyc. No directory is entered
    through a link, and one that cannot be opened is an error, never a part left out."""
    for entry in os.listdir(fd):
        path, mode = prefix + entry, os.stat(entry, dir_fd=fd, follow_symlinks=False).st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise Rejected(f"{json.dumps(path, ensure_ascii=False)} 是链接或特殊文件，商店只收普通文件。")
        if entry.startswith(".") or UNSAFE.search(entry):
            raise Rejected(f"商店收不了 {json.dumps(path, ensure_ascii=False)}：路径的每一段都不能以点开头，也不能有 \\、: 或控制字符。")
        if stat.S_ISREG(mode) and not entry.endswith(".pyc"):
            yield path, fd, entry
        elif stat.S_ISDIR(mode) and entry != "__pycache__":
            with directory(entry, fd) as inner:
                yield from walk(inner, path + "/")


def folder(state: int, name: str) -> dict[str, bytes]:
    """The team skill ``name`` as the store would hold it, file by file."""
    try:
        with directory("skills", state) as skills:
            top = os.open(name, DIRECTORY, dir_fd=skills)
    except OSError:
        raise Rejected(f"团队技能库里没有 {name} 这个文件夹（链接不算）。") from None
    files, total = {}, 0
    try:
        for path, fd, entry in walk(top):
            data = read(entry, fd, LIMIT - total)
            total += len(data)
            if total > LIMIT:
                raise Rejected(TOO_BIG)
            files[path] = data
    finally:
        os.close(top)
    return files


def prepare(state: int, name: str, request: dict, builtin: Path, secrets: set[bytes]) -> tuple[dict[str, bytes], str]:
    """The files of the tenant's skill ``name`` as they go to the store, and its version."""
    if request.get("name") != name or not number(request.get("requested_at")):
        raise Rejected(UNREADABLE)
    if os.path.lexists(builtin / name):
        raise Rejected(f"{name} 和 Argus 自带的技能重名，换个名字再贡献。")
    files = folder(state, name)
    try:
        text = files[SKILL].decode()
    except (KeyError, UnicodeDecodeError):
        raise Rejected(f"技能文件夹最上层要有 UTF-8 写的 {SKILL}。") from None
    matter = re.match(r"---\n(.*?)\n---", text, re.S)
    if not matter or not field(matter[1], "description").strip():
        raise Rejected(f"{SKILL} 开头的 front matter 要用 description 写明这个技能做什么。")
    written = field(matter[1], "version")
    version = written or request.get("version")
    if not isinstance(version, str) or not version.strip() or re.search(r"[\r\n]", version):
        raise Rejected(f"{SKILL} 的 version 要是一行非空的文字。")
    if not written:  # first, where the store reads it even past an empty "version:"
        files[SKILL] = (text[:matter.start(1)] + f"version: {json.dumps(version, ensure_ascii=False)}\n" + text[matter.start(1):]).encode()
    if sum(map(len, files.values())) > LIMIT:
        raise Rejected(TOO_BIG)
    if any(secret in blob for blob in (*map(str.encode, files), *files.values()) for secret in secrets):
        raise Rejected("有文件含试用站的邀请码或访问令牌，不能公开；删掉后再贡献。")
    return files, version


def output(*command: str, cwd: Path) -> str:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True).stdout


def git(repo: Path, *args: str) -> str:
    return output("git", "-c", "gc.auto=0", *args, cwd=repo)


def slug(repo: Path, remote: str) -> str:
    """owner/name of the GitHub repository behind ``remote``, read from its URL."""
    url = git(repo, "remote", "get-url", remote).strip().removesuffix("/").removesuffix(".git")
    return "/".join(re.split(r"[/:]", url)[-2:])


def submit(repo: Path, files: dict[str, bytes], *, tenant: str, name: str, version: str, site: str,
           upstream: str, fork: str, gh: str, dry_run: bool) -> str:
    """Commit the pack on contrib/<tenant>-<name> from the store's main, push it to the
    fork and return its open pull request's address ("" in a dry run)."""
    branch, store, ours = f"contrib/{tenant}-{name}", slug(repo, upstream), slug(repo, fork).split("/")[0]
    git(repo, "fetch", "--quiet", upstream, "main")
    git(repo, "checkout", "--quiet", "--force", "-B", branch, f"{upstream}/main")
    git(repo, "clean", "--quiet", "-ffdx")
    pack = repo / "packs" / name
    shutil.rmtree(pack, ignore_errors=True)
    for path, data in files.items():
        (pack / path).parent.mkdir(parents=True, exist_ok=True)
        (pack / path).write_bytes(data)
    output(sys.executable, "-I", "scripts/build_packs.py", cwd=repo)
    # --force: the catalog lists every file written here, .gitignore or not.
    git(repo, "add", "--all", "--force", "--", f"packs/{name}", "packs/catalog.json")
    if not git(repo, "diff", "--cached", "--name-only"):
        raise Rejected("商店里的这个技能已经和你的一模一样，没有要提交的改动。")
    title = f"技能商店：{name}（试用站 {site} 的 {tenant}）"
    git(repo, *AUTHOR, "commit", "--quiet", "-m",
        f"{title}\n\n试用站 {site} 的租户 {tenant} 自创的技能 {name}，版本 {version}；用户在页面上确认贡献，由托管端提交。")
    if dry_run:
        print(git(repo, "show", "--stat", "--format=%an <%ae>%n%n%B", "HEAD"), flush=True)
        return ""
    git(repo, "push", "--quiet", "--force", fork, branch)
    for open_request in json.loads(output(gh, "pr", "list", "--repo", store, "--head", branch, "--state", "open",
                                          "--json", "url,headRepositoryOwner", cwd=repo)):
        if open_request["headRepositoryOwner"]["login"].lower() == ours.lower():
            return open_request["url"]
    return output(gh, "pr", "create", "--repo", store, "--base", "main", "--head", f"{ours}:{branch}", "--title", title,
                  "--body", BODY.format(site=site, tenant=tenant, name=name), cwd=repo).strip().splitlines()[-1]


def answer(offers: int, filename: str, data: bytes, request: dict, outcome: dict) -> None:
    """Write the outcome over the request, atomically and never through a link; but not
    over a new request that took its place meanwhile: the next run answers that one."""
    kept = {key: value for key, value in request.items() if key not in ("url", "error", "submitted_at")}
    temporary = f".{filename}.tmp"
    with suppress(FileNotFoundError):
        os.unlink(temporary, dir_fd=offers)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=offers)
    with open(fd, "w") as file:
        json.dump({**kept, **outcome, "submitted_at": time.time()}, file)
    if load(offers, filename) == data:
        os.rename(temporary, filename, src_dir_fd=offers, dst_dir_fd=offers)
    else:
        os.unlink(temporary, dir_fd=offers)


def run(config: dict, repo: Path, *, upstream: str = "upstream", fork: str = "origin", gh: str = "gh",
        dry_run: bool = False) -> None:
    """Answer every tenant's requests still to answer, one run at a time."""
    secrets, builtin, site = secrets_of(config), Path(config["argus_package"]) / "skills", config.get("name", "preview")

    def settle(tenant: str, state: int, name: str, request: dict) -> dict:
        try:
            files, version = prepare(state, name, request, builtin, secrets)
            return {"url": submit(repo, files, tenant=tenant, name=name, version=version, site=site,
                                  upstream=upstream, fork=fork, gh=gh, dry_run=dry_run)}
        except Rejected as refusal:
            return {"error": str(refusal)}
        except Exception as error:
            traceback.print_exc()
            if stderr := getattr(error, "stderr", None):
                print(stderr, flush=True)
            return {"error": FAILED}

    with open(repo / ".git/contribute.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another run is answering contributions", flush=True)
            return
        for tenant, row in config["tenants"].items():
            try:
                with directory(row["directory"]) as home, directory("state", home) as state:
                    if not dry_run:  # contributions/ tells the tenant's Argus that its offers are answered here
                        with suppress(FileExistsError):
                            os.mkdir("contributions", 0o700, dir_fd=state)
                    with directory("contributions", state) as offers:
                        for filename, data, request in requests(offers):
                            outcome = settle(tenant, state, filename.removesuffix(".json"), request)
                            print(f"{tenant}/{filename}: {outcome}", flush=True)
                            if not dry_run:
                                answer(offers, filename, data, request, outcome)
            except OSError:  # no state yet, or contributions/ is not a directory of the tenant's own
                continue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["run"])
    parser.add_argument("--config", type=Path, required=True, help="the trial's preview.json")
    parser.add_argument("--repo", type=Path, required=True, help="the argus-verticals clone kept for this job")
    parser.add_argument("--upstream", default="upstream", help="the clone's remote for the store")
    parser.add_argument("--fork", default="origin", help="the clone's remote for our fork, which gets the branches")
    parser.add_argument("--gh", default="gh", help="the gh executable, logged in as the fork's owner")
    parser.add_argument("--dry-run", action="store_true", help="commit in the clone and show it; push, open and answer nothing")
    args = parser.parse_args()
    if not (args.repo / ".git").is_dir():
        parser.error(f"{args.repo} is not a clone of argus-verticals")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):  # git works on the clone, whatever the caller's shell says
        os.environ.pop(name, None)
    run(json.loads(args.config.read_text()), args.repo, upstream=args.upstream, fork=args.fork, gh=args.gh, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
