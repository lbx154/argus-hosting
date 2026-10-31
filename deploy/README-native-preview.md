# Invitation preview with GPT-6.1 Sol

This is a Linux operator deployment of the real Argus web application. Five
invitations select five separate runtimes and workspaces. Each invitation has
$10 of model allowance; the entire deployment has $50, with no daily reset.
Copilot CLI uses its Responses BYOK provider against an operator-owned meter.
The operator's GitHub login remains outside every runtime.

## Requirements and initialization

Use pinned Argus and argus-hosting checkouts with rebuilt release artifacts,
Python 3.11+, both packages installed in the same environment, Node, Copilot CLI with BYOK Responses support, bubblewrap and a
working systemd user manager. Warm up Copilot CLI on the operator account first;
only its extracted `~/.cache/copilot/pkg` packages are mounted into guests.
Do not mount the rest of the operator cache or home. The reference deployment
uses Copilot CLI 1.0.94 and Python 3.12.

Run `native_setup.py --help` for the required paths. For example:

```sh
/srv/argus-env/bin/python /srv/src/argus-hosting/deploy/native_setup.py \
  --root /srv/private-argus-preview \
  --source /srv/src/argus \
  --hosting-source /srv/src/argus-hosting \
  --venv /srv/argus-env \
  --python-runtime /opt/python-3.12 \
  --node-runtime /opt/node \
  --copilot-package "$HOME/.cache/copilot/pkg" \
  --revision FULL_GIT_SHA --install-services
```

Keep the private root outside Git. `preview.json` and `invitations.txt` are
operator-only files. Reinitializing an existing root preserves invitations,
charges and limits. To upgrade, stop the services, update `source` and `revision`
and `hosting_source` to checked commits, reinstall service definitions and restart.
An existing preview.json must explicitly gain `hosting_source`; initialization
preserves the file and does not update it automatically. The two source mounts
remain read-only. See [two-package installation](../README.md#安装到托管机器). Never
recreate the ledger to replenish a trial.

The meter serializes admission in SQLite, reserves conservative text input and
capped output cost before submission, and settles from terminal Copilot
`copilot_usage.total_nano_aiu` receipts. Reservations use twice the observed
GPT-6.1 tariff (input/cache-write at most $2.50/M, output $10/M). Missing receipts
or interrupted submissions retain their reservation. A single locked meter
recovers unfinished requests on restart; portal restarts do not alter charges.
Hosted tools and remote media references are rejected because their billing
cannot be bounded from text bytes. Reservations can pause a request before all
nominal balance has been spent. Confirm tariffs before enabling a new provider
version. There are at most two simultaneous upstream model requests.

## Runtime boundaries

Bubblewrap creates separate user, PID, mount, IPC, UTS and network namespaces.
Only that invitation's state/home/workspace is writable. Language runtimes and
source are read-only; the host home and other invitations are absent. Guests
have no host network. Local socket bridges provide the metered model endpoint
and an HTTP proxy that resolves, validates and pins public IP destinations on
ports 80/443, rejecting private networks and metadata endpoints. Shell tools
that use the configured HTTP/HTTPS proxies can access public internet resources.
Tools needing direct sockets are unavailable.

The user services request 8 GiB memory, one CPU and 512 task limits per runtime.
Some user managers have no delegated resource controllers. An independent
host-side watchdog always samples aggregate process-tree RSS and thread counts
at 0.5-second intervals, pausing above 8 GiB / 512 threads. A per-runtime CPU
core is assigned before startup and the watchdog reasserts every thread's CPU
affinity, allowing normal work to finish within its capacity. Memory/process
checks allow short bursts and are not equivalent to kernel cgroup quotas.
Operator-only logs are retained in the private deployment directory.
Keep at most two warm Manager contexts, expiring inactive contexts after three
minutes. Native Copilot clients for classification, tools and multiple roles
can exceed 2 GiB in ordinary use; the 8 GiB allowance accommodates this without
changing the $50 model budget. Memory/thread stops exit with temporary-failure
status 75 so systemd restarts the runtime after five seconds (up to four starts
per minute). Files and the model ledger survive these restarts. The portal displays a retrying page while the runtime
is unavailable. Root and device directories are
read-only; temporary/shared-memory files use each tenant's monitored disk
directories. Files have a 32 MiB size limit. A workspace monitor (2 GiB / 100,000 entries,
or `limits.storage_gb` / `limits.storage_entries` in preview.json) keeps each runtime within
its storage; it is not an operating-system disk quota. Projects of one invitation share their
agents' download caches (Hugging Face, pip, torch, uv, npm) under `state/cache`. Over the limit,
the monitor first deletes cached files, least recently used first, down to half the limit.
Still over, it restarts the runtime paused: the project page says the workspace is full and
that files are kept, no project starts work, and the Manager still answers. Once usage is under
90% of the limit the runtime restarts again and resumes the interrupted work. A paused
workspace that grows another 25% stops until an operator cleans it up.
Invitation sessions are random tokens in secure, HttpOnly cookies, kept as issued in
the operator-only (0600) `sessions.sqlite3` beside the ledger. HTTP mutations and
browser WebSockets check the browser origin. Runtime upgrades and provider /
budget settings are operator-managed. Model credentials inside guests authorize
only their own bounded balance.

## Cloudflare

Point a Cloudflare tunnel at the loopback portal on port 18871. Deploy
`cloudflare-preview-worker.mjs` with `ORIGIN` set to the tunnel's HTTPS URL and
`RELAY_SECRET` supplied as a Worker secret matching the private config. Capture
Wrangler's output privately because temporary deployments print a claim link.

The Worker relays HTTP through an authenticated origin WebSocket so streamed
responses work with Quick Tunnels; browser WebSockets are forwarded separately.
The HTTP body limit is 16 MiB. Verify login, assets, SSE, browser WebSockets and a
real file-producing task on the public URL. A temporary Cloudflare account
expires; a Quick Tunnel address can change after restarting cloudflared. Update
`ORIGIN` after such a change. Use an authenticated account and named tunnel for
a permanent deployment.

Check `/healthz` for the pinned revision and authenticated `/trial/budget` for
actual remaining allowance. Stop the preview with
`systemctl --user stop 'argus-preview-*'`. The private ledger survives shutdown.

### Direct Quick Tunnel access

The portal also supports using the Quick Tunnel URL directly, without a Worker
or a temporary account. `trial-transport.js` wraps only the Manager's streamed
POST endpoint in an authenticated, same-origin browser WebSocket, restoring
streamed replies despite Quick Tunnels' SSE limitation. Other browser requests
use ordinary HTTP. The portal supplies the real session cookie to the internal
request; browser frames cannot select a different tenant or an arbitrary API.
This URL stays usable while the tunnel process runs, and does not inherit the
one-hour expiry of Wrangler 4.149's temporary accounts. Restarting the tunnel
can change the address. Non-browser clients must use the authenticated
`/trial/stream` protocol or a named tunnel / Worker for streamed requests.
All main and auxiliary model routes are pinned to GPT-6.1 Sol, including front
door classification, bounded planning, plan preview and prompt rewriting.

## Several versions on one address

One host can serve several previews side by side, e.g. the Python Argus and the
TypeScript Argus at `/py/` and `/js/` of one domain. Initialize each root with its
own `--name`, `--port` and `--title`; `--runtime js --argus-package DIR` runs the
installed `@lbx154/argus` package in each tenant instead of the Python web app.
`--agent codex --codex-package DIR` gives the roles Codex instead of Copilot CLI:
each tenant's `home/.codex/config.toml` points at the metered endpoint with that
tenant's token, and the meter's `--upstream-base-url` names where it forwards.
Each root keeps its own invitations, ledger and limits.

`try_router.py --version py=18871 --version js=18872` serves the domain: its root is
a page choosing a version, and each prefix reaches that version's portal. Point the
named tunnel at the router, not at a portal. The router renames portal cookies per
version and adds the prefix back to redirects and to pages' WebSocket and history
calls; other absolute-path requests are placed by their Referer, else by the
version the browser opened last.

### JS project preview origins

With a JS Argus release supporting origin templates in `--preview-url`, add
`"preview_domain": "argusbot.cn"` to the JS root's private `preview.json` and
pass `--preview-domain argusbot.cn` to `try_router.py`. The launcher supplies
`https://js-<tenant>-*.argusbot.cn`; project identifiers contain only lowercase
letters and digits. Tenant names must be lowercase DNS labels of at most 27
characters, so that `js-<tenant>-<32-digit project id>` fits one 63-byte DNS
label; the portal, meter and launcher refuse a `preview.json` naming a longer one
before anything starts. Without the field, no preview argument is passed; Python
runtime startup is unchanged.

The operator must add a **proxied wildcard CNAME** `*.argusbot.cn` pointing to
`<TUNNEL-ID>.cfargotunnel.com`. In the named tunnel configuration, keep the
`try.argusbot.cn` ingress first, then add `hostname: '*.argusbot.cn'` with
`service: http://127.0.0.1:18870` (the router), before the final catch-all rule.
Do not override the incoming HTTP Host. Use one-level subdomains as above so
the existing `*.argusbot.cn` certificate covers them.

Deploy the router and portal changes together, prepare DNS/ingress and matching
domain configuration, then restart JS tenants on the template-capable Argus
release. Preview hosts accept only GET/HEAD, never receive portal credentials,
and bypass portal pages and path rewriting, including hosts with a trailing DNS
root dot. The original Host is preserved when forwarding. Unknown tenants return 404.
Portal session cookies and the router's version cookie now use `__Host-`
names with Secure, Path=/ and no Domain; only the new names are accepted.
**Existing users of both versions must enter their invitation once again.**
Their workspaces, invitations and balances remain intact. DNS, tunnel changes
and production rollout are operator actions, separate from this source change.
