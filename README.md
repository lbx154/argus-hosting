# argus-hosting

这里维护 Argus 的托管功能和 PR gate。Argus 可以独立安装和运行；安装本包后，客户端通过 `argus.hosting.client`，服务端通过 `argus.hosting` 入口点接入。两者没有提供者时，Argus 使用 `NO_HOSTING`。

| 功能 | 源码与说明 |
| --- | --- |
| PR gate：描述检查及本地回归检查 | [argus_hosting/pr_gate](argus_hosting/pr_gate/README.md)、[本地回归门禁](docs/local-pr-regression-gate.md) |
| 客户端：Key 设置、网关连接、余额、账号策略 | [argus_hosting/trial](argus_hosting/trial/hosting.py)、[客户端与网关](docs/trial-gateway.md)；命令 `argus-trial` |
| 服务端：网关计量、邀请工作空间、存储暂停、算力、研究记录 | [argus_trial_server](argus_trial_server/web_portal.py)、[研究与数据](docs/hosted-research-trial.md) |
| 独立管理网页和托管用户网页 | [frontend/BUILDING.md](frontend/BUILDING.md)；分别配置 `admin_frontend_dir` 和 `frontend_dir` |
| 托管桌面：获取 Argus 源码、应用托管部分、冻结客户端、构建安装包 | [desktop/README.md](desktop/README.md)、[装配入口](desktop/assemble.py) |
| 部署、使用及训练样例 | [部署步骤](docs/deployment.md)、[浏览器邀请](docs/browser-invitations.md)、[数据示例](docs/examples/agentic-training/README.md) |

托管代码、测试及文档迁自 Argus `e5d12046472603d43c4ff8f6893f5611619c28a8`。服务端真实包名保留为 `argus_trial_server`，由这里的根 `pyproject.toml` 统一安装；客户端位于 `argus_hosting.trial`。没有独立子包安装步骤。存储限额、标记持久化和原因文案属于本仓库；Argus 只读取 `work_pause: null | {reason, since}`。

## 安装到托管机器

需要 Python 3.11+、Node.js 22.12+、Git。服务端需要 Linux 的 POSIX 账户、Unix socket 和 `/proc` 能力；容器部署另需 Docker Buildx、systemd 用户服务及已准备的 Copilot/Pi 运行工具。部署目录示例为 `/srv/src/argus`、`/srv/src/argus-hosting`、`/srv/argus-env`，私密状态放 `/srv/private-argus`，资产放 `/srv/build/<发布版本>`。先由机器管理员赋予部署账户这些目录的权限。把以下两个 ref 换成已验证、包含本次拆分的提交，不直接升级一个仍在运行的源码目录。

```sh
git clone https://github.com/lbx154/Argus.git /srv/src/argus
git -C /srv/src/argus checkout ARGUS_VERIFIED_REF
git clone https://github.com/lbx154/argus-hosting.git /srv/src/argus-hosting
git -C /srv/src/argus-hosting checkout HOSTING_VERIFIED_REF
python3 -m venv /srv/argus-env
/srv/argus-env/bin/python -m pip install -e /srv/src/argus
/srv/argus-env/bin/python -m pip install -e /srv/src/argus-hosting
/srv/argus-env/bin/argus-trial --help
/srv/argus-env/bin/argus-trial-server --help
```

先安装新 Argus，再安装托管包是新建环境的顺序。已有托管机器切换时，先在停机窗口或新环境中准备新托管包，再升级 Argus，并让同一解释器同时看到两者；旧 Argus 自带的实现已经迁走。保留现有账本、密钥、工作空间、授权和暂停标记。发布时段由操作者安排。

从两份源码生成资产，不能使用 Argus 起点跟踪的旧 dist：

```sh
export ARGUS_SOURCE_DIR=/srv/src/argus
export ARGUS_HOSTING_WEB_STAGE=/srv/build/RELEASE/hosting-web
cd /srv/src/argus-hosting
npm --prefix frontend/admin-data ci
npm --prefix frontend/admin-data run typecheck
npm --prefix frontend/admin-data test
npm --prefix frontend/admin-data run build
node frontend/workspace/build.mjs
```

管理页生成于 `/srv/src/argus-hosting/frontend/admin-data/dist`，基址 `/admin/data/`；用户网页生成于 `$ARGUS_HOSTING_WEB_STAGE/frontend/web/dist`，基址 `/`。管理资产应复制到本次发布的固定目录后配置，后续构建不能覆盖正在服务的目录。初始化、镜像构建、服务启动和升级的完整命令见 [部署步骤](docs/deployment.md)。启动顺序为凭据和私密状态、模型计量与出口服务、工作空间后端、算力与研究服务、门户，最后切换反向代理。已有工作须先收尾。

客户端使用者在安装两包后运行 `argus-trial`，在隐藏提示中输入 Key，再运行 `argus`；独立网关用 `argus-trial --url https://gateway.example`。托管桌面按 [桌面构建步骤](desktop/README.md) 获取固定 Argus 提交后装配，不在 Argus 源码里保留试用代码。

## 开发与验证

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '/path/to/Argus[dev]'
python -m pip install -e '.[dev]'
python -m ruff check argus_hosting argus_trial_server deploy tests scripts/pr-gate experiments
python -m pytest -n 4 tests --basetemp=build/pytest-temp
python -c "import shutil; shutil.rmtree('build/pytest-temp')"
python -m argus_hosting.pr_gate --help
```

CI 先从 GitHub 检出 Argus `dev`（手动运行可指定 ref）并安装，再装本包并运行 PR gate、服务端、客户端及迁入的桌面测试。管理页、托管网页和桌面构建命令分别见上表。Argus 的完整测试使用独立且未装本包的环境。

Argus 的可选 `hosting/` 子模块只供其维护脚本和 PR gate 使用，不代替安装本包，也不进入 Argus wheel。修改本仓库后，要由维护者另行决定是否更新子模块指针。只提交源码、测试、文档和 CI；依赖、dist、冻结文件、安装包及单轮验收证据不入库。

## License

MIT，见 [LICENSE](LICENSE)。
