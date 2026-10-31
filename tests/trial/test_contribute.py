"""A tenant's offer of its own skill to the store becomes a pull request (deploy/contribute.py).

Local bare repositories stand in for the store and our fork, and a fake gh records its calls.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from deploy import contribute
from deploy.native_setup import install_services

BUILD = '''import json, pathlib
packs = pathlib.Path(__file__).resolve().parents[1] / "packs"
catalog = [{"name": p.name, "files": sorted(f.relative_to(p).as_posix() for f in p.rglob("*") if f.is_file())}
           for p in sorted(packs.iterdir()) if (p / "SKILL.md").is_file()]
(packs / "catalog.json").write_text(json.dumps({"packs": catalog}, indent=2) + "\\n")
'''
GH = '''#!{python}
import json, pathlib, sys
here, args = pathlib.Path(__file__).parent, sys.argv[1:]
with open(here / "calls.jsonl", "a") as log:
    log.write(json.dumps(args) + "\\n")
known = json.loads((here / "pulls.json").read_text()) if (here / "pulls.json").exists() else []
value = lambda flag: args[args.index(flag) + 1]
if args[:2] == ["pr", "list"]:
    print(json.dumps([{{"url": p["url"], "headRepositoryOwner": {{"login": p["owner"]}}}} for p in known if p["branch"] == value("--head")]))
elif args[:2] == ["pr", "create"]:
    owner, branch = value("--head").split(":")
    known.append({{"url": f"https://github.com/{{value('--repo')}}/pull/{{len(known) + 1}}", "owner": owner, "branch": branch}})
    (here / "pulls.json").write_text(json.dumps(known))
    print(known[-1]["url"])
else:
    sys.exit(1)
'''
HOST_ONLY = "host-only-content-7f3a"
SKILL = "---\nname: lab-notes\ndescription: keep a tidy lab notebook\nversion: 1.2.0\n---\n\n# Lab notes\n"


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", "-c", "user.name=seed", "-c", "user.email=seed@example.com", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


class Site:
    """A trial with two tenants, its argus-verticals clone, the store, our fork and gh."""

    def __init__(self, tmp: Path):
        seed = tmp / "seed"
        (seed / "scripts").mkdir(parents=True)
        (seed / "scripts/build_packs.py").write_text(BUILD)
        (seed / "packs/speedrun").mkdir(parents=True)
        (seed / "packs/speedrun/SKILL.md").write_text("---\ndescription: make it fast\nversion: 0.2.0\n---\n")
        (seed / ".gitignore").write_text("build/\n__pycache__/\n")
        subprocess.run([sys.executable, "scripts/build_packs.py"], cwd=seed, check=True)
        git("init", "--quiet", "-b", "main", cwd=seed)
        git("add", "-A", cwd=seed)
        git("commit", "--quiet", "-m", "seed", cwd=seed)
        self.store = tmp / "github/Argus-AiTeam/argus-verticals.git"
        self.fork = tmp / "github/lbx154/argus-verticals.git"
        for bare in (self.store, self.fork):
            git("clone", "--quiet", "--bare", str(seed), str(bare))
        self.root = tmp / "root"
        self.clone = self.root / "verticals"
        git("clone", "--quiet", str(self.fork), str(self.clone))
        git("remote", "add", "upstream", str(self.store), cwd=self.clone)
        self.gh = tmp / "gh/gh"
        self.gh.parent.mkdir()
        self.gh.write_text(GH.format(python=sys.executable))
        self.gh.chmod(0o755)
        (tmp / "argus/skills/research").mkdir(parents=True)
        (tmp / "upstream.key").write_text("sk-upstream-0123456789abcdef\n")
        (tmp / "host").mkdir()
        (tmp / "host/notes.md").write_text(HOST_ONLY)
        (tmp / "host/lab-notes").mkdir()
        (tmp / "host/lab-notes/SKILL.md").write_text(SKILL + HOST_ONLY)
        self.host = tmp / "host"
        self.config = {"name": "try-js", "runtime": "js", "argus_package": str(tmp / "argus"),
                       "relay_secret": "relay-secret-0123456789abcdef",
                       "upstream": {"base_url": "https://upstream.invalid/v1", "token_file": str(tmp / "upstream.key")},
                       "tenants": {}}
        for tenant in ("trial1", "trial2"):
            (self.root / tenant / "state/skills").mkdir(parents=True)
            self.config["tenants"][tenant] = {
                "directory": str(self.root / tenant), "socket": str(self.root / tenant / "run/web.sock"),
                "invite": f"argus-{tenant}-invite-0123456789", "model_token": f"{tenant}-model-0123456789",
                "web_token": f"{tenant}-web-0123456789"}
        self.preview = self.root / "preview.json"
        self.preview.write_text(json.dumps(self.config))

    def state(self, tenant: str = "trial1") -> Path:
        return self.root / tenant / "state"

    def skill(self, files: dict[str, str | bytes], name: str = "lab-notes") -> Path:
        folder = self.state() / "skills" / name
        for path, data in files.items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_bytes(data if isinstance(data, bytes) else data.encode())
        return folder

    def offer(self, name: str = "lab-notes", version: str = "1.2.0", at: float | None = None) -> Path:
        path = self.state() / "contributions" / f"{name}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"name": name, "version": version, "requested_at": at or time.time()}))
        return path

    def run(self, **options) -> None:
        contribute.run(self.config, self.clone, gh=str(self.gh), **options)

    def calls(self) -> list[list[str]]:
        log = self.gh.parent / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def branches(self) -> list[str]:
        return git("--git-dir", str(self.fork), "for-each-ref", "--format=%(refname:short)", "refs/heads").split()

    def pushed(self, path: str, branch: str = "contrib/trial1-lab-notes") -> str:
        return git("--git-dir", str(self.fork), "show", f"{branch}:{path}")

    def assert_nothing_left_the_host(self, answer: Path, *secrets: str) -> None:
        assert self.calls() == [] and self.branches() == ["main"]
        text = answer.read_text() + "".join(path.read_text(errors="replace") for path in self.clone.rglob("*")
                                            if path.is_file() and ".git" not in path.parts)
        for secret in secrets:
            assert secret not in text


@pytest.fixture
def site(tmp_path, monkeypatch) -> Site:
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return Site(tmp_path)


def test_an_offer_becomes_a_pull_request_and_its_address_comes_back(site):
    site.skill({"SKILL.md": SKILL, "scripts/tidy.py": "print('tidy')\n", "skills/engineer/notes.md": "Keep it tidy.\n",
                "build/notes.txt": "kept though .gitignore names build/\n", "scripts/__pycache__/tidy.cpython-312.pyc": b"\0"})
    offer = site.offer()
    asked = json.loads(offer.read_text())["requested_at"]

    site.run()

    answer = json.loads(offer.read_text())
    assert answer["url"] == "https://github.com/Argus-AiTeam/argus-verticals/pull/1"
    assert (answer["name"], answer["version"], answer["requested_at"]) == ("lab-notes", "1.2.0", asked)
    assert answer["submitted_at"] >= asked and "error" not in answer
    branch = "contrib/trial1-lab-notes"
    files = git("--git-dir", str(site.fork), "ls-tree", "-r", "--name-only", branch).split()
    assert [path for path in files if path.startswith("packs/lab-notes/")] == [
        "packs/lab-notes/SKILL.md", "packs/lab-notes/build/notes.txt",
        "packs/lab-notes/scripts/tidy.py", "packs/lab-notes/skills/engineer/notes.md"]
    assert site.pushed("packs/lab-notes/SKILL.md") == SKILL
    catalog = json.loads(site.pushed("packs/catalog.json"))["packs"]
    assert [pack["name"] for pack in catalog] == ["lab-notes", "speedrun"]
    commit = git("--git-dir", str(site.fork), "log", "-1", "--format=%an <%ae>%n%B", branch)
    assert commit.startswith("lbx154 <145820328+lbx154@users.noreply.github.com>")
    assert all(word in commit for word in ("trial1", "lab-notes", "1.2.0", "try-js"))
    listed, created = site.calls()
    assert listed[:2] == ["pr", "list"] and listed[listed.index("--head") + 1] == branch
    assert created[:2] == ["pr", "create"]
    for flag, value in (("--repo", "Argus-AiTeam/argus-verticals"), ("--head", f"lbx154:{branch}"), ("--base", "main")):
        assert created[created.index(flag) + 1] == value
    assert "审核合并后" in created[created.index("--body") + 1]

    site.run()  # answered: nothing more to do
    assert len(site.calls()) == 2 and json.loads(offer.read_text()) == answer


def test_asking_again_updates_the_open_pull_request(site):
    folder = site.skill({"SKILL.md": SKILL})
    offer = site.offer()
    site.run()
    first = json.loads(offer.read_text())
    (folder / "SKILL.md").write_text(SKILL.replace("1.2.0", "1.3.0") + "More.\n")
    site.offer(version="1.3.0", at=first["submitted_at"] + 1)

    site.run()

    again = json.loads(offer.read_text())
    assert again["url"] == first["url"] and again["version"] == "1.3.0" and again["submitted_at"] > first["submitted_at"]
    assert [call[:2] for call in site.calls()] == [["pr", "list"], ["pr", "create"], ["pr", "list"]]
    assert site.pushed("packs/lab-notes/SKILL.md").endswith("More.\n")


def test_asking_again_while_a_run_submits_waits_for_the_next_run(site, monkeypatch):
    folder = site.skill({"SKILL.md": SKILL})
    offer = site.offer()
    submit = contribute.submit

    def asked_again_meanwhile(*args, **kwargs):
        url = submit(*args, **kwargs)
        (folder / "SKILL.md").write_text(SKILL.replace("1.2.0", "1.3.0"))
        offer.with_suffix(".json.tmp").write_text(json.dumps({"name": "lab-notes", "version": "1.3.0", "requested_at": time.time()}))
        offer.with_suffix(".json.tmp").rename(offer)
        return url

    monkeypatch.setattr(contribute, "submit", asked_again_meanwhile)
    site.run()
    assert "submitted_at" not in json.loads(offer.read_text())
    assert [path.name for path in offer.parent.iterdir()] == ["lab-notes.json"]
    monkeypatch.setattr(contribute, "submit", submit)
    site.run()

    answer = json.loads(offer.read_text())
    assert answer["version"] == "1.3.0" and answer["url"] and "version: 1.3.0" in site.pushed("packs/lab-notes/SKILL.md")


@pytest.mark.parametrize("asked", ["1e300", "Infinity", "NaN"])
def test_a_request_from_the_future_is_answered_once(site, asked):
    site.skill({"SKILL.md": SKILL})
    offer = site.offer()
    offer.write_text('{"name": "lab-notes", "version": "1.2.0", "requested_at": %s}' % asked)

    site.run()
    answered = offer.read_text()
    site.run()

    assert json.loads(answered)["url"] and offer.read_text() == answered
    assert [call[:2] for call in site.calls()] == [["pr", "list"], ["pr", "create"]]


def test_the_version_stays_out_of_the_title_and_body(site):
    version = "1.2.0 @torvalds @github/security " + "x" * 300
    site.skill({"SKILL.md": SKILL.replace("1.2.0", version)})
    offer = site.offer()

    site.run()

    assert json.loads(offer.read_text())["url"]
    created = site.calls()[-1]
    for flag in ("--title", "--body"):
        assert "@" not in created[created.index(flag) + 1]
    assert version in git("--git-dir", str(site.fork), "log", "-1", "--format=%B", "contrib/trial1-lab-notes")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any folder")
def test_a_folder_that_cannot_be_read_fails_the_offer_rather_than_leaving_it_out(site):
    folder = site.skill({"SKILL.md": SKILL, "scripts/run.py": "print(1)\n"})
    offer = site.offer()
    (folder / "scripts").chmod(0)
    try:
        site.run()
    finally:
        (folder / "scripts").chmod(0o755)

    assert json.loads(offer.read_text())["error"] == contribute.FAILED
    site.assert_nothing_left_the_host(offer)


@pytest.mark.parametrize("layout", ["folder", "file", "inner folder", "skills"])
def test_a_link_never_carries_a_host_file_into_a_pull_request(site, layout):
    if layout == "folder":
        (site.state() / "skills/lab-notes").symlink_to(site.host / "lab-notes")
    elif layout == "file":
        folder = site.skill({"SKILL.md": SKILL})
        (folder / "notes.md").symlink_to(site.preview)
    elif layout == "inner folder":
        folder = site.skill({"SKILL.md": SKILL})
        (folder / "skills").mkdir()
        (folder / "skills/engineer").symlink_to(site.host)
    else:
        (site.state() / "skills").rmdir()
        (site.state() / "skills").symlink_to(site.host)
    offer = site.offer()

    site.run()

    assert "链接" in json.loads(offer.read_text())["error"]
    tenant = site.config["tenants"]["trial2"]
    site.assert_nothing_left_the_host(offer, HOST_ONLY, tenant["web_token"], tenant["invite"], site.config["relay_secret"])


@pytest.mark.parametrize("secret", ["invite", "model_token", "web_token", "relay", "upstream", "in a path"])
def test_a_trial_secret_is_refused_without_repeating_it(site, secret):
    tenants = site.config["tenants"]
    value = {"invite": tenants["trial1"]["invite"], "model_token": tenants["trial2"]["model_token"],
             "web_token": tenants["trial2"]["web_token"], "relay": site.config["relay_secret"],
             "upstream": "sk-upstream-0123456789abcdef", "in a path": tenants["trial1"]["web_token"]}[secret]
    path = f"skills/{value}.md" if secret == "in a path" else "skills/engineer.md"
    site.skill({"SKILL.md": SKILL, path: f"Use this: {value}\n" if secret != "in a path" else "nothing\n"})
    offer = site.offer()

    site.run()

    assert "令牌" in json.loads(offer.read_text())["error"]
    site.assert_nothing_left_the_host(offer, value)


def test_more_than_8_mib_is_refused(site):
    site.skill({"SKILL.md": SKILL, "data/weights.bin": b"\0" * contribute.LIMIT})
    offer = site.offer()

    site.run()

    assert json.loads(offer.read_text())["error"] == contribute.TOO_BIG
    site.assert_nothing_left_the_host(offer)


@pytest.mark.parametrize(("files", "name", "said"), [
    ({"README.md": "no skill here\n"}, "lab-notes", "SKILL.md"),
    ({"SKILL.md": "---\nname: lab-notes\n---\n"}, "lab-notes", "description"),
    ({"SKILL.md": SKILL, ".env": "KEY=1\n"}, "lab-notes", "以点开头"),
    ({"SKILL.md": SKILL, "notes:draft.md": "x\n"}, "lab-notes", "\\"),
    ({"SKILL.md": SKILL}, "research", "自带"),
])
def test_what_the_store_would_refuse_is_refused(site, files, name, said):
    site.skill(files, name)
    offer = site.offer(name)

    site.run()

    assert said in json.loads(offer.read_text())["error"]
    site.assert_nothing_left_the_host(offer)


def test_a_file_that_is_no_request_is_answered_once(site):
    site.skill({"SKILL.md": SKILL})
    offer = site.offer()
    offer.write_text("{not json")

    site.run()
    answered = offer.read_text()
    site.run()

    assert json.loads(answered)["error"] == contribute.UNREADABLE and offer.read_text() == answered
    site.assert_nothing_left_the_host(offer)


def test_a_missing_version_is_written_into_skill_md(site):
    site.skill({"SKILL.md": SKILL.replace("version: 1.2.0\n", "")})
    offer = site.offer(version="0.1.0")

    site.run()

    assert json.loads(offer.read_text())["url"]
    text = site.pushed("packs/lab-notes/SKILL.md")
    assert text.startswith('---\nversion: "0.1.0"\nname: lab-notes\n')
    matter = text.split("---\n")[1]
    assert contribute.field(matter, "version") == "0.1.0" and contribute.field(matter, "description")


def test_a_dry_run_commits_but_pushes_opens_and_answers_nothing(site, capsys):
    site.skill({"SKILL.md": SKILL})
    offer = site.offer()
    request = offer.read_text()

    site.run(dry_run=True)

    shown = capsys.readouterr().out
    assert "lbx154 <145820328+lbx154@users.noreply.github.com>" in shown and "packs/lab-notes/SKILL.md" in shown
    assert git("log", "-1", "--format=%s", "contrib/trial1-lab-notes", cwd=site.clone) == "技能商店：lab-notes（试用站 try-js 的 trial1）\n"
    assert offer.read_text() == request
    site.assert_nothing_left_the_host(offer)


def test_one_run_at_a_time(site, capsys):
    site.skill({"SKILL.md": SKILL})
    offer = site.offer()
    request = offer.read_text()
    with open(site.clone / ".git/contribute.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        site.run()
        assert offer.read_text() == request and site.calls() == []
        assert "Another run" in capsys.readouterr().out
    site.run()
    assert json.loads(offer.read_text())["url"]


@pytest.mark.parametrize("runtime", ["js", "python"])
def test_the_js_trial_runs_the_job_every_30_seconds(tmp_path, monkeypatch, runtime):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "operator")
    calls = []
    monkeypatch.setattr("deploy.native_setup.subprocess.run", lambda command, **kwargs: calls.append(command))
    config = {"name": "try-js", "runtime": runtime, "hosting_source": str(tmp_path / "hosting"), "venv": str(tmp_path / "venv"),
              "tenants": {"trial1": {}}}

    install_services(config, tmp_path / "root")

    units = tmp_path / "operator/.config/systemd/user"
    tenant = (units / "argus-try-js-trial1.service").read_text()
    assert "Type=simple\nRestart=on-failure\n" in tenant
    if runtime == "python":
        assert not list(units.glob("argus-try-js-contribute.*")) and "argus-try-js-contribute.timer" not in calls[-1]
        return
    job = (units / "argus-try-js-contribute.service").read_text()
    assert "Type=oneshot\nTimeoutStartSec=10min\n" in job and "Restart=" not in job
    assert f'"{tmp_path / "hosting/deploy/contribute.py"}" "run"' in job and f'"--repo" "{tmp_path / "root/verticals"}"' in job
    assert "OnUnitInactiveSec=30\n" in (units / "argus-try-js-contribute.timer").read_text()
    assert "argus-try-js-contribute.timer" in calls[-1] and "argus-try-js-contribute" not in calls[-1]
