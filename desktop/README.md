# Hosting desktop

Argus owns the ordinary desktop. This directory owns the trial module, its
small source patch and tests, frozen client packaging, and installer workflow.
`assemble.py` fetches an Argus revision and applies the additions in a disposable
checkout. It fails on patch conflicts. Update the patch when upstream changes;
do not keep another copy of the complete desktop here.

The imported trial implementation comes from Argus
`e5d12046472603d43c4ff8f6893f5611619c28a8`. Assemble an Argus revision containing
the Python, web and desktop splits, then install both packages into the same environment:

```sh
python -m venv .venv
# Windows: .venv/Scripts/activate
. .venv/bin/activate
python desktop/assemble.py --argus-ref dev --destination build/argus-src
python -m pip install -e './build/argus-src[dev]' -e '.[dev]' 'pyinstaller>=6.11,<7' tzdata
npm --prefix build/argus-src/desktop-tauri ci
npm --prefix build/argus-src/frontend/web ci
npm --prefix build/argus-src/frontend/tui ci
npm --prefix build/argus-src/desktop-tauri run ui:typecheck
cargo check --locked --manifest-path build/argus-src/desktop-tauri/src-tauri/Cargo.toml
cargo test --locked --manifest-path build/argus-src/desktop-tauri/src-tauri/Cargo.toml --test core_tests
python -m pytest tests/desktop --basetemp=build/pytest-temp
python -c "import shutil; shutil.rmtree('build/pytest-temp')"
python desktop/build.py --source build/argus-src
```

A local source repository is also accepted: pass `--argus-repository /path/to/Argus`
and `--argus-ref <commit-or-branch>`. The script always fetches committed source.
Native packages must be built on their target OS and architecture. Use Rust
compatible with Argus's lockfile, Node 22.12 or newer, and Python 3.11 or newer.
On Linux install the same GTK/WebKit libraries as the workflow. Windows builds
also compile Argus's first-party native adapter; macOS builds use Xcode tools.

`build.py` rebuilds the web and terminal assets before freezing. Assembly uses
the shared `frontend/workspace/hosting.patch` and its matching test sources,
just like the workspace build; the builder sets
`VITE_ARGUS_HOSTED_TRIAL=1` on every platform, including the isolated Windows
build. The independent management frontend is excluded.

`build.py --backend-only` builds these assets and validates the frozen backend
without creating an installer. It uses
Argus's existing `ARGUS_FROZEN_APPLICATION` build hook, adding client Python
sources and distribution metadata with the `argus.hosting.client` entry point.
The server entry point and console scripts are excluded from this desktop
metadata because the tenant service is not part of the frozen client.
`verify-frozen.py` runs the executable outside the checkouts with Python import
paths cleared, proves discovery from its own payload, then checks private
preparation, status and explicit resume against a local gateway with a synthetic Key. Preparation uses
test doubles for the native download and model reply. It makes no paid
model call. The hosted desktop patch also adds the client mode to its own
backend configuration response, so the shared web UI follows trial and own
account settings. Argus's configuration producer stays generic. Private Key
setup uses native IPC and subprocess stdin; the Key is cleared from the input field and never stored in browser storage or argv.

Set `ARGUS_TEST_NATIVE_COPILOT=1` to also download and verify the pinned native
Copilot executable through the frozen backend. CI enables this check. It uses
an isolated home and makes no model call. Run the combined workspace browser
check from the assembled `desktop-tauri` directory with
`ARGUS_HOSTING_FROZEN_BACKEND` set to the absolute frozen executable path:
`npx playwright test tests/hosted-workspace.spec.ts --workers=1`. This serves
the newly frozen web assets and checks the hosted build flag, client mode and
Key-settings message to the desktop shell; native IPC is simulated there.

The workflow builds Linux, Windows and both macOS architectures. Its optional
live smoke installs the actual native package, downloads/verifies Copilot,
checks the authenticated cockpit, and sends a real model request. Enable
`run_live_smoke` and provide `ARGUS_TRIAL_SMOKE_KEY` as a repository secret. This
consumes trial allowance. A successful build without that option does not
claim the public trial or native installation smoke passed.
