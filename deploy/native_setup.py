"""Initialize private preview accounts and install bounded user services."""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
from pathlib import Path

# Codex inside a tenant reaches only the metered endpoint its runtime bridges
# to 127.0.0.1:9011, with that tenant's own token (ARGUS_METER_TOKEN).
# requires_openai_auth = false tells Argus's readiness check not to run
# `codex login status`; without it the team daemon refuses to start.
CODEX_CONFIG = '''model = "gpt-6.1-sol"
model_provider = "meter"
web_search = "disabled"
model_auto_compact_token_limit = 300000

[model_providers.meter]
name = "Argus trial meter"
base_url = "http://127.0.0.1:9011/v1"
env_key = "ARGUS_METER_TOKEN"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 8
stream_max_retries = 8
'''


def initialize(args) -> dict:
    root = args.root.resolve()
    source = args.source.resolve()
    hosting_source = args.hosting_source.resolve()
    if any(root == checkout or checkout in root.parents for checkout in (source, hosting_source)):
        raise ValueError("Private trial state must be outside the source checkout")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path = root / 'preview.json'
    if path.exists():
        return json.loads(path.read_text())
    config = {'model': 'gpt-6.1-sol', 'revision': args.revision, 'source': str(source), 'hosting_source': str(hosting_source),
              'venv': str(args.venv.resolve()), 'python_runtime': str(args.python_runtime.resolve()),
              'node_runtime': str(args.node_runtime.resolve()), 'copilot_home': str(Path.home() / '.copilot'),
              'ledger': str(root / 'usage.sqlite3'),
              'meter_runtime': str(root / 'meter'), 'total_usd': args.total_usd, 'user_usd': args.user_usd,
              'concurrency': args.concurrency, 'name': args.name, 'port': args.port, 'title': args.title,
              'runtime': args.runtime, 'agent': args.agent,
              'relay_secret': secrets.token_hex(32), 'tenants': {}}
    for key in ('copilot_package', 'codex_package', 'argus_package'):
        if value := getattr(args, key):
            config[key] = str(value.resolve())
    if args.upstream_base_url:
        config['upstream'] = {'base_url': args.upstream_base_url, 'token_file': str(args.upstream_token_file.resolve())}
    (root / 'meter').mkdir(mode=0o700)
    for n in range(1, args.tenants + 1):
        name = f'trial{n}'
        directory = root / name
        directory.mkdir(mode=0o700)
        for part in ('home', 'home/.copilot', 'state', 'run', 'workspace', 'bin'):
            (directory / part).mkdir(mode=0o700)
        # A link, not a shell wrapper: Argus's agent sandbox reads the loader's
        # ``#!/usr/bin/env node`` and brings node and the package in with it.
        if args.agent == 'codex':
            (directory / 'bin/codex').symlink_to(Path(config['codex_package']) / 'bin/codex.js')
            (directory / 'home/.codex').mkdir(mode=0o700)
            (directory / 'home/.codex/config.toml').write_text(CODEX_CONFIG)
        else:
            (directory / 'bin/copilot').symlink_to(args.node_runtime.resolve() / 'lib/node_modules/@github/copilot/npm-loader.js')
        (directory / 'state/identity.md').write_text(
            '你是研究和编程助手。用简明中文说明做了什么、得到什么结果、还需要什么信息。'
            '每个项目有自己的项目目录；提到文件时写相对项目目录的路径（例如 figures/plot.png），不要加 workspace/ 之类的前缀。解释结果时少用内部代码、角色名和工具参数。\n')
        config['tenants'][name] = {'directory': str(directory), 'socket': str(directory / 'run/web.sock'),
                                   'invite': 'argus-' + secrets.token_urlsafe(24),
                                   'model_token': secrets.token_urlsafe(48), 'web_token': secrets.token_urlsafe(48)}
    path.write_text(json.dumps(config, indent=2))
    path.chmod(0o600)
    invitations = root / 'invitations.txt'
    invitations.write_text('\n'.join(f'{name}: {row["invite"]}' for name, row in config['tenants'].items()) + '\n')
    invitations.chmod(0o600)
    return config


def install_services(config: dict, root: Path) -> None:
    source = Path(config['hosting_source'])
    python = str(Path(config['venv']) / 'bin/python')
    units = Path.home() / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    prefix = f"argus-{config.get('name', 'preview')}-"
    commands = {
        prefix + 'meter': [python, str(source / 'deploy/native_preview.py'), 'meter', '--config', str(root / 'preview.json'), '--uds', str(root / 'meter/model.sock')],
        prefix + 'egress': [python, str(source / 'deploy/native_egress.py'), '--socket', str(root / 'meter/egress.sock')],
        prefix + 'portal': [python, str(source / 'deploy/native_preview.py'), 'portal', '--config', str(root / 'preview.json'), '--port', str(config.get('port', 18871))],
    }
    for name in config['tenants']:
        commands[prefix + name] = [python, str(source / 'deploy/native_runtime.py'), 'launch', '--config', str(root / 'preview.json'), '--tenant', name]
    # Tenants' offers of their skills to the store become pull requests, every 30
    # seconds, with this user's gh login, from the argus-verticals clone in verticals/.
    job = prefix + 'contribute'
    if config.get('runtime') == 'js':
        commands[job] = [python, str(source / 'deploy/contribute.py'), 'run', '--config', str(root / 'preview.json'),
                         '--repo', str(root / 'verticals'), '--gh', shutil.which('gh') or 'gh']
    for name, command in commands.items():
        # systemd command lines have their own quoting and percent expansion.
        executable = ' '.join(json.dumps(arg.replace('%', '%%')) for arg in command)
        # Generous ceilings that keep one runaway from taking the host down;
        # tenant limits come from preview.json "limits" (memory_gb, cpus, tasks).
        limits = config.get('limits') or {}
        tenant = name.startswith(prefix + 'trial')
        memory = f"{limits.get('memory_gb', 128)}G" if tenant else '8G'
        cpu = f"{int(limits.get('cpus', 32)) * 100}%" if tenant else '800%'
        tasks = int(limits.get('tasks', 65536)) if tenant else 8192
        # The contribute job runs to its end each time its timer fires, or is stopped after 10 minutes,
        # stuck on GitHub say, for the next to answer what it left; the rest come back when they fail.
        kind = 'oneshot\nTimeoutStartSec=10min' if name == job else 'simple\nRestart=on-failure\nRestartSec=5'
        text = f'''[Unit]
Description=Argus invitation preview {name}
After=network-online.target
StartLimitIntervalSec=0
[Service]
Type={kind}
WorkingDirectory={str(source).replace("%", "%%")}
ExecStart={executable}
Environment={json.dumps('PYTHONPATH=' + str(source))}
Environment=PYTHONUNBUFFERED=1
UMask=0077
StandardOutput=append:{root / (name + '.log')}
StandardError=append:{root / (name + '.log')}
NoNewPrivileges=yes
MemoryMax={memory}
CPUQuota={cpu}
TasksMax={tasks}
KillMode=control-group
TimeoutStopSec=15
[Install]
WantedBy=default.target
'''
        path = units / (name + '.service')
        path.write_text(text)
        path.chmod(0o600)
    if job in commands:
        (units / (job + '.timer')).write_text(f'''[Unit]
Description=Argus invitation preview {job}, every 30 seconds
[Timer]
OnActiveSec=30
OnUnitInactiveSec=30
AccuracySec=1
[Install]
WantedBy=timers.target
''')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', *(name + '.timer' if name == job else name for name in commands)],
                   check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'source', 'hosting-source', 'venv', 'python-runtime', 'node-runtime'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--copilot-package', type=Path, help="Copilot CLI's extracted packages (--agent copilot)")
    parser.add_argument('--codex-package', type=Path, help='the @openai/codex package directory (--agent codex)')
    parser.add_argument('--argus-package', type=Path, help='the installed @lbx154/argus package directory (--runtime js)')
    parser.add_argument('--name', default='preview', help='names the services argus-NAME-*')
    parser.add_argument('--port', type=int, default=18871, help='loopback port of the portal')
    parser.add_argument('--title', default='Argus 6.1 试用')
    parser.add_argument('--runtime', choices=('python', 'js'), default='python', help='the Argus each invitation runs')
    parser.add_argument('--agent', choices=('copilot', 'codex'), default='copilot', help='the CLI its roles run')
    parser.add_argument('--revision', required=True)
    parser.add_argument('--tenants', type=int, default=5)
    parser.add_argument('--user-usd', type=float, default=10)
    parser.add_argument('--total-usd', type=float, default=50)
    parser.add_argument('--concurrency', type=int, default=2, help='simultaneous upstream model requests')
    parser.add_argument('--upstream-base-url', help='OpenAI-compatible Responses endpoint instead of the Copilot login')
    parser.add_argument('--upstream-token-file', type=Path, help='file holding the upstream key (kept outside Git)')
    parser.add_argument('--install-services', action='store_true')
    args = parser.parse_args()
    if bool(args.upstream_base_url) != bool(args.upstream_token_file):
        parser.error('--upstream-base-url and --upstream-token-file go together')
    for needed, flag in ((args.agent == 'copilot', 'copilot_package'), (args.agent == 'codex', 'codex_package'),
                         (args.runtime == 'js', 'argus_package')):
        if needed and not getattr(args, flag):
            parser.error(f"--{flag.replace('_', '-')} is required here")
    config = initialize(args)
    if args.install_services:
        install_services(config, args.root.resolve())
    print(f"{len(config['tenants'])} private invitations are available in invitations.txt; credentials were not printed.")


if __name__ == '__main__':
    main()
