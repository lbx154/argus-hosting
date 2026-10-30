# 管理页与用户工作空间

本目录的管理页来自 Argus `e5d12046472603d43c4ff8f6893f5611619c28a8`，随后在这里独立维护。构建需要 Node >= 22.12.0 和一个 Argus 源码目录；不需要在该 Argus 目录安装网页依赖。这里只借用已有的外壳、基础组件、Markdown、Modal、主题和 i18n，没有要求 Argus 增加管理页出口。

## 独立管理页

先将 `ARGUS_SOURCE_DIR` 设置为 Argus 源码根目录的绝对路径，例如 `/srv/src/argus`，不是 `frontend/web`。下面所有命令从托管仓库根目录执行：

```sh
export ARGUS_SOURCE_DIR=/srv/src/argus
cd frontend/admin-data
npm ci
npm run typecheck
npm test
npm run build
```

每个命令会在自己的 `node_modules` 下建立到该源码目录的链接。依赖和锁文件属于管理应用。它不会构建或复制 Argus 主网页；构建产物也不需要该源码目录。`npm run dev` 的地址是 `http://localhost:5174/admin/data/`，服务端代理目标通过 `ARGUS_WEB_API` 指定，默认 `http://127.0.0.1:8799`。

输出是 `frontend/admin-data/dist`，基址固定为 `/admin/data/`。将托管服务配置的 `admin_frontend_dir` 指向这一完整目录。`/admin/data` 与 `/admin/data/` 应返回管理入口（也可将前者重定向至后者）；`/admin/data/assets/*` 提供该目录的资源，不能使用普通用户网页的 dist。管理页面和资源沿用服务端管理员认证；登录仍由 `/admin/login` 提供，身份读取 `/admin/status`，数据、目录、导出和审计读取 `/admin/api/*`。用户空间跳转独立查询 `/invite/status`，匹配账号后才跳到 `/?project=...`，不匹配时提示使用 `/invite` 登录。

## 托管用户网页

`workspace/hosting.patch` 只保存托管策略相对纯 Argus 网页的变化：试用模型及 Key 设置、算力入口、隐藏服务方管理的控件、路由不开放时的说明、旧租户 HTML 预览兼容，以及存储暂停的本地化文案。独立管理页不依赖此覆盖层。

通用 snapshot 使用 `work_pause: null | { reason: string, since: number }`。Argus 原样显示原因，托管覆盖层承接原存储提示的翻译；存储限额、字节与暂停标记仍由托管服务端负责。覆盖层没有恢复 `storage_pause` 字段。

以下命令使用合入网页拆分后的 Argus 源码。暂存目录必须不存在且在 Argus 源码目录之外：

```sh
export ARGUS_SOURCE_DIR=/srv/src/argus
export ARGUS_HOSTING_WEB_STAGE=/srv/build/argus-hosting-web
node frontend/workspace/build.mjs
```

脚本复制构建所需源码到暂存目录，检查并应用补丁，安装 Argus 网页锁定的依赖，运行托管网页测试，再以 `VITE_ARGUS_HOSTED_TRIAL=1` 构建。输出路径是 `$ARGUS_HOSTING_WEB_STAGE/frontend/web/dist`，将服务端普通用户资产配置 `frontend_dir` 指向它。Argus 源目录不被修改，补丁不匹配时构建停止。要重跑，请另给一个新的暂存目录。托管桌面装配也可在它自己的 Argus 源码副本中、构建网页前应用同一补丁，无须把策略放回 Argus。

`admin-data/dist` 与托管用户网页 dist 是两份资产，发布时分别配置。两者都不提交仓库；具体安装、服务启动和发布步骤见最终部署文档。
