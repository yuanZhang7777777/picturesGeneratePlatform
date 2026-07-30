# 运行与部署手册

## 架构与入口

Docker Compose 启动 PostgreSQL、Django Web、Generation Worker、Prompt Worker 和 Caddy。Caddy 是唯一对浏览器开放的同源入口：它在镜像构建时把 `frontend/dist` 写入 `/srv/frontend`，提供 React 静态资源，并将 `/api/`、`/auth/`、`/admin/`、登录/退出/改密、迁移期 `/batches/`、健康检查和 Django 静态资源反向代理到 Web 服务。

浏览器只持有 Django session Cookie 与 CSRF，不持有供应商或 OSS 凭据。生产容器不运行 Node 前端服务器。

## 环境变量与门禁

复制 `.env.example` 到 `.env`，`.env` 绝不提交。样例中的 `replace-with-*` 是故意不可部署的替换标记，只用于 Compose 静态解析。

必填值：

- `DJANGO_SECRET_KEY`
- `POSTGRES_PASSWORD`，并同步更新 `DATABASE_URL` 中对应的密码
- `DJANGO_ALLOWED_HOSTS`
- `ADMIN_PASSWORD`
- `ERP_LOGIN_URL`
- `CATALOG_QUERY_URL`
- `CATALOG_ALLOWED_IMAGE_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`，仅在同源以外的受信来源确有需要时设置

预览默认值必须保持 `APIMART_FAKE_MODE=1`，`APIMART_API_KEY` 可以为空。以下全部满足前，不得设为 `0`：服务器 Secret 已配置、主 Agent 明确的付费授权、供应商契约测试、真实任务限流/重试验收、私有存储验收和发布记录。

`MAX_ACTIVE_GENERATIONS` 表示活跃供应商异步任务的运行配置；供应商 API 上限为 **500**，有效值必须在 `1..500`。当前代码尚未完成跨进程原子认领和该上限的实际执行，因此预览仍固定使用 `2`；任何环境不得仅修改环境变量就直接提升到 500。按 2、5、8、50、100、250、500 分级压测并记录 429/5xx、P95、归档成功率和数据库资源后，才能提高下一档。公平队列的基础配额为每人 2 个活跃任务；容量空闲时可临时借用更多，出现其他待处理用户时停止继续借用。

登录使用 ERP：`ERP_LOGIN_URL` 接收用户输入的用户名和密码，平台只把返回的 Token 保存在服务端 session 中，不保存 ERP 密码。所有 ERP 登录成功用户都可进入平台；`PLATFORM_ADMIN_ERP_USERS` 用逗号分隔管理员 ERP 登录名，默认仅配置刘学城的登录名。

SKU 商品资料导入使用当前登录用户的 ERP Token 调用 `CATALOG_QUERY_URL`，请求体只发送 `{"skuList": [...]}`。`CATALOG_ALLOWED_IMAGE_HOSTS` 仅接受逗号分隔的公网 IPv4/IPv6 字面量，不能填主机名；初始图片链接和每次重定向都必须命中该名单。单请求默认最多 `CATALOG_MAX_SKUS_PER_REQUEST=50`；其余下载门限为超时、重定向次数、最大字节数与最大像素数。发布前用受限测试 SKU 验证登录包络、Token 字段、过期行为、图片源 IP 白名单和私有归档；不能把原始商品资料响应、图片 URL 或 Token 写进日志。

正式素材存储使用 `STORAGE_BACKEND=oss`，并配置 `OSS_ENDPOINT`、`OSS_BUCKET`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET` 和 `OSS_PREFIX=independent-image-platform`。原图、SKU 拉取图、生成结果和导出 ZIP 都写入该 OSS 私有前缀；`LOCAL_MEDIA_ROOT` 仅用于开发和假模式回退。

APIMart 中文文档与受限账户的最小契约测试是唯一接入事实来源：测试精确模型 ID、`/v1/responses` 图片输入、结构化输出封装、错误语义、限流和账务。上游模型文档仅帮助判断能力方向，不能代替 APIMart 参数或可用性结论。

## 出站网络放行申请

2 号服务器当前公网出口 IP 为 `139.224.2.166`，内网 IP 为 `192.168.0.138`。发布 smoke 已确认服务器能访问常见国内 HTTPS 站点、ERP `103.198.125.2:16777/8077` 和销售系统 `121.46.237.218:8071`；到 APIMart 仍超时，同一目标从开发机可连通，因此 APIMart 是服务器出口策略、DNS 或目标侧白名单问题。

不要把“入站访问端口”和“出站目的端口”混在一起：安全组里开放 `18000/19000` 是允许员工浏览器从公网访问本服务器的预览入口；APIMart 的 `443` 是本服务器主动访问外部 HTTPS API 时的目的端口，ERP 的 `16777/8077` 也是本服务器主动访问外部服务的目的端口。只开放入站 `18000/19000` 不能解决服务器访问 APIMart 或 ERP 超时。

2026-07-30 运行配置：ERP 登录与 SKU 商品资料查询使用 `103.198.125.2:16777`，销售系统 API 使用 `121.46.237.218:8071`。

向 IT 或网络负责人申请时，只申请当前失败的 APIMart 出站访问；ERP 与销售系统已通过 smoke，不作为本次放行目标：

- 源：2 号服务器公网出口 `139.224.2.166`，如按 VPC/安全组管理则同时标注内网 `192.168.0.138`。
- 目的：`api.apimart.ai`，TCP `443`，方向为出站；用于 APIMart OpenAI 兼容 API，Base URL 为 `https://api.apimart.ai/v1`。优先按域名/FQDN 放行，不要固定单个解析 IP。
- DNS/路由：确保服务器可解析 `api.apimart.ai` 的 IPv4 A 记录并通过 IPv4 出口访问；当前服务器无公网 IPv6，不能只返回或优先使用 IPv6。
- 若公司统一走 HTTP/HTTPS 代理，需提供代理地址、端口和认证方式，再把代理配置注入 Docker Compose 的 Web、Generation Worker 和 Prompt Worker 环境。

验收命令只输出连通状态，不得打印 `.env` 或密钥：

```bash
curl -4 -I --max-time 10 https://api.apimart.ai/v1
timeout 8 bash -lc '</dev/tcp/103.198.125.2/16777'
timeout 8 bash -lc '</dev/tcp/103.198.125.2/8077'
timeout 8 bash -lc '</dev/tcp/121.46.237.218/8071'
```

在启动真实环境前检查替换标记，但不要打印 `.env` 内容：

```bash
if grep -qE '^(DJANGO_SECRET_KEY|POSTGRES_PASSWORD|ADMIN_PASSWORD)=(|replace-with-)' .env; then
  echo 'replace environment placeholders before deployment'
  exit 1
fi
```

## 本地开发

后端：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
$env:USE_SQLITE_FOR_TESTS='1'
$env:APIMART_FAKE_MODE='1'
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py seed_admin --username admin --password <local-password>
.\.venv\Scripts\python manage.py runserver 127.0.0.1:8000
```

前端在另一终端运行。Vite 开发代理由 `frontend/` 配置负责将 Django 路径转给本地后端：

```powershell
npm --prefix frontend ci
npm --prefix frontend run dev
```

## Compose 静态验证与本地预览

先运行不启动容器的静态解析。该命令是配置改动的最低验证：

```powershell
docker compose --env-file .env.example config --quiet
```

同时保留 Django `/batches/` 页面，避免 React 根路由的 SPA 回退遮挡旧工作流。以下静态检查应通过：

```powershell
$content = Get-Content -Raw 'docker/Caddyfile'
if ($content -notmatch '(?m)^\s*@django path .*\/batches \/batches\/\*') { throw 'Caddy does not preserve Django /batches/ routes' }
```

完成 `.env` 的本地安全值替换后，构建并启动预览：

```bash
docker compose up -d --build
docker compose ps
docker compose exec -T web python manage.py seed_admin
docker compose exec -T web python manage.py check
curl -fsS http://127.0.0.1:18083/health/live
curl -fsS http://127.0.0.1:18083/health/ready
docker compose logs --tail=100 web generation-worker prompt-worker proxy
```

`web` 的 Docker health check 调用 `/health/live`；`/health/ready` 当前只验证数据库。发布验收还必须确认 `generation-worker` 和 `prompt-worker` 均为持续运行状态、日志没有重复退出或未处理异常。当前 Prompt Worker 是占位循环，因此它的存活不等同于异步 Prompt 功能已完成。

不要在常驻 `generation-worker` 已运行且队列非空时再执行 `run_generation_worker --once`。现有 worker 尚未实现跨进程任务原子认领；并发 one-shot 调试可能在真实付费模式重复提交。仅在隔离测试栈或停止常驻 worker 后使用该命令。

假模式手工验收路径：登录测试账号 → 创建项目 → 上传两张 PNG → 默认两个商品/SKU → 拖拽合并 → 预检 → 确认生成 → 等待队列完成 → 审核结果 → 验证未通过/失败项只生成新版本。不得把技术完成的图片当作可导出结果；只有审核 `accepted` 的版本可导出。

## 管理员规则、模板与审核发布

管理员通过同源 `/admin/` 登录后维护平台规则、输出模板及槽位。每次发布都必须记录平台/站点、官方来源 URL、核对日期、版本、图片用途/比例/分辨率、禁止内容和审核 checklist。正式种子不由本运维任务写入，而由独立 `template-seed` 任务提交：仅 global generic 模板可作为 `published` 基线；Shopee/TikTok 必须在官方规则发布前保持 `draft`。草稿或未核对规则不得被描述为自动合规。

竞品图可经批准的 `gpt-5-nano-2025-08-07` 视觉观察器提炼为抽象构图或风格策略；不得作为商品参考图、事实来源、生产 Prompt、导出内容或传给 `gpt-image-2`。Prompt OS 只能把确认过的商品事实、身份锁、模板、我方参考图与受限 Style DNA 编译为生成指令。

## Hermes 预览与真实发布

本任务不执行任何远端操作。需要部署时只能使用 `ssh hermes-remote`，且先声明：

```text
project_key=global
mode=deploy
target=/opt/independent-image-platform
locks=.codex_locks/global.lock
global_touch=yes
bridge_needed=no
```

持有 `.codex_locks/global.lock` 后，在目标目录执行 Compose 静态解析、构建、迁移、健康检查和人工假模式验收，并把命令输出与镜像版本写入发布记录。只有主 Agent 可以签核该发布。

HTTP 的 IP:端口入口仅供测试账号和非敏感素材。正式入口不使用来源 IP 白名单，但必须具备域名 HTTPS、账号安全、备份恢复、私有存储和真实供应商契约测试，才可向 100 名员工开放真实素材或付费生成。

## 发布证据最小清单

- `docker compose --env-file .env.example config --quiet` 成功。
- 镜像构建包含 React 静态产物，Caddy 同源入口能返回前端并代理 Django 健康检查。
- Django 检查、迁移状态、Web/数据库健康、两个 worker 的容器状态和日志已记录。
- 假模式端到端人工路径和对象权限回归已通过。
- 真实 API/OSS/HTTPS 如未启用，发布记录必须明确标注“未启用”，不能以预览结果替代生产证明。
