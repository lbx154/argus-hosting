# 托管安装、启动与升级

使用 [README 的两包安装和资产构建步骤](../README.md#安装到托管机器)。以下命令从 `/srv/src/argus-hosting` 执行，解释器固定为 `/srv/argus-env/bin/python`。服务、测试和 deploy 全部属于本仓库。用固定的 Argus 与托管提交组合发布，并在部署记录中保存两个完整提交、镜像标签及两份资产目录。

## 路径和前置准备

```sh
export PATH=/srv/argus-env/bin:$PATH
export ARGUS_SOURCE_DIR=/srv/src/argus
export ARGUS_HOSTING_SOURCE=/srv/src/argus-hosting
export ARGUS_TRIAL_ROOT=/srv/private-argus
export ARGUS_HOSTING_WEB_STAGE=/srv/build/RELEASE/hosting-web
export ARGUS_ADMIN_ASSETS=/srv/build/RELEASE/admin-data
mkdir -p "$ARGUS_ADMIN_ASSETS"
cp -a frontend/admin-data/dist/. "$ARGUS_ADMIN_ASSETS/"
```

`RELEASE` 是本次发布标识，用户网页暂存目录必须由构建脚本新建。准备一份独立管理员后端的访问 token 文件，例如 `/srv/private-admin/backend-token`，权限 0600；该 token 与门户生成的管理登录凭据分开。管理员后端可以是已有 Argus API；它的地址通过 `--admin-url` 显式指定。若要把已有管理员工作空间作为普通邀请工作空间纳入计量，参照 [研究指南](hosted-research-trial.md#eleventh-workspace-and-data-administrator)，保留原状态和工作目录。

## 私密状态及两份资产

以下初始化用于新部署。已有部署保留原目录，备份私密配置后只修改资产路径及发布参数；初始化会重写 portal/compute 配置，不能拿它替代升级。

```sh
python -m argus_trial_server.web_admin init \
  --root "$ARGUS_TRIAL_ROOT" \
  --admin-token-file /srv/private-admin/backend-token \
  --admin-url http://127.0.0.1:8896 \
  --frontend-dir "$ARGUS_HOSTING_WEB_STAGE/frontend/web/dist" \
  --admin-frontend-dir "$ARGUS_ADMIN_ASSETS"
argus-trial-server login \
  --state-dir "$ARGUS_TRIAL_ROOT/meter" \
  --key-file "$ARGUS_TRIAL_ROOT/secrets/master.key"
```

也可用 `import-copilot-login` 显式导入该机器选中并已登录的 Copilot 账户。输入及生成的密钥不进入 Git 或构建上下文。检查 `portal.json` 的 `public_origin`，设为反向代理的准确 HTTPS origin。`frontend_dir` 与 `admin_frontend_dir` 必须分别指向已完成的用户和管理资产。管理入口为 `/admin/login`、`/admin/data/`，管理资源为 `/admin/data/assets/*`。缺少管理构建会返回 503，不回退到用户页面。

初始化生成十个工作空间、私密邀请、计量、算力和 analytics 配置。按机器实际容量检查 `compute.json` 的 GPU、CPU、内存、镜像和用户 ID，以及相关存储卷。初始化默认容量不是该机器已具备资源的证明。研究授权和训练用途按 [研究指南](hosted-research-trial.md) 配置。

## 构建镜像

Docker Buildx 的上下文必须分别指定。`/srv/runtime-tools/copilot` 是已准备的独立可执行 Copilot；Pi 的 `argus-pi` 上下文包含 package.json、node_modules 和 packages，`node-runtime` 包含名为 node 的可执行文件。使用已验证的工具版本，工具获取与私密授权分开。

```sh
ARGUS_REV=$(git -C "$ARGUS_SOURCE_DIR" rev-parse HEAD)
HOSTING_REV=$(git -C "$ARGUS_HOSTING_SOURCE" rev-parse HEAD)
export ARGUS_WEB_IMAGE="argus-web-trial:$ARGUS_REV-$HOSTING_REV"
export ARGUS_PI_IMAGE="argus-web-trial:pi-$ARGUS_REV-$HOSTING_REV"
docker buildx build --load -f deploy/web.Dockerfile \
  --build-context argus-source="$ARGUS_SOURCE_DIR" \
  --build-context runtime-tools=/srv/runtime-tools \
  --build-arg ARGUS_SKILL_BUILD_REVISION="$ARGUS_REV" \
  --build-arg TRIAL_UID="$(id -u)" --build-arg TRIAL_GID="$(id -g)" \
  -t "$ARGUS_WEB_IMAGE" .
docker buildx build --load -f deploy/pi.Dockerfile \
  --build-context argus-source="$ARGUS_SOURCE_DIR" \
  --build-context argus-pi=/srv/runtime/argus-pi \
  --build-context node-runtime=/srv/runtime/node \
  --build-arg WEB_BASE_IMAGE="$ARGUS_WEB_IMAGE" \
  --build-arg ARGUS_SKILL_BUILD_REVISION="$ARGUS_REV" \
  -t "$ARGUS_PI_IMAGE" .
docker buildx build --load -f deploy/compute.Dockerfile \
  --build-arg WEB_BASE_IMAGE="$ARGUS_PI_IMAGE" \
  -t "argus-web-compute:$ARGUS_REV-$HOSTING_REV" .
```

Argus 构建上下文仅放源码及按发布流程生成的必要资产，不带个人 home、账本或凭据。先按 Argus 的发布工具生成其打包所需资产。Pi 观察器使用它的已验证版本，详情见研究指南。把 compute 镜像标签写入 `compute.json`。

## 启动顺序

存储文件系统挂载与所有权须由机器管理员先准备，具体磁盘及配额由其确定。`python -m argus_trial_server.web_admin mount-storage --root "$ARGUS_TRIAL_ROOT" --uid "$(id -u)" --gid "$(id -g)"` 会按配置挂载卷；运行前核对该机器挂载权限与已有卷，不能对旧数据重新格式化。

安装服务单元后依次启动。服务安装器只生成并 enable 单元，启动是显式步骤：

```sh
python deploy/web_services.py --root "$ARGUS_TRIAL_ROOT" --portal-port 8898
systemctl --user start argus-web-trial-meter argus-web-trial-egress
python -m argus_trial_server.web_admin start-containers \
  --root "$ARGUS_TRIAL_ROOT" --image "$ARGUS_PI_IMAGE"
systemctl --user start argus-web-trial-compute argus-web-trial-relay-guardian
systemctl --user start argus-web-trial-portal
curl --fail http://127.0.0.1:8898/invite
```

确认后再将 HTTPS 反向代理切到门户。检查普通邀请登录、管理员独立登录、两份网页资产、任务 API、下载、研究授权和受支持的工具。原生 `trial-11` 应在门户之前启动，并核对 `/api/meta` 的 Argus 提交。Docker/systemd/真实公网验收需在部署机器执行；本仓库的单元测试不代表它们已经运行。

## 已有部署升级

先备份配置、账本和密钥，安排活跃任务收尾；在新环境中准备两包和全部资产，再切换门户、管理员后端及工作空间使用的解释器/源码。旧机器应先准备新托管包再升级 Argus，避免升级后找不到已迁出的入口点。源码目录与两份资产都使用固定发布目录。

```sh
python -m argus_trial_server.web_admin release \
  --root "$ARGUS_TRIAL_ROOT" --image "$ARGUS_PI_IMAGE" \
  --source "$ARGUS_HOSTING_SOURCE" \
  --frontend-dir "$ARGUS_HOSTING_WEB_STAGE/frontend/web/dist" \
  --admin-frontend-dir "$ARGUS_ADMIN_ASSETS"
```

`--source` 在这里是托管仓库，用于版本记录；容器镜像同时记录 Argus revision。release 先替换工作空间容器，再更新两份资产并重启门户，在私密根目录写 release.json；门户进程须事先使用新托管环境。`start-containers` 不替换已有容器。容器替换会中断工作，旧容器保留为 `<name>-rollback`。`--no-restart-portal` 可先滚动并记录，再由操作者切换门户。回退时恢复原镜像、解释器/两份源码和两份资产配置，保留原状态数据；不要删除账本。

## 原生预览与桌面

不使用容器的原生预览由 [deploy/README-native-preview.md](../deploy/README-native-preview.md) 说明，必须同时传入 `--source`（Argus）和 `--hosting-source`（托管），已有 preview.json 须补上 hosting_source 并重新安装服务单元。桌面由 [desktop/README.md](../desktop/README.md) 从固定 Argus 提交装配。两者各有独立的资源与验证要求。
