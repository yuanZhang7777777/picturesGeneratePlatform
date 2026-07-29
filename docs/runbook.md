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
- `DJANGO_CSRF_TRUSTED_ORIGINS`，仅在同源以外的受信来源确有需要时设置

预览默认值必须保持 `APIMART_FAKE_MODE=1`，`APIMART_API_KEY` 可以为空。以下全部满足前，不得设为 `0`：已轮换的供应商凭据、主 Agent 明确的付费授权、供应商契约测试、真实任务限流/重试验收、私有存储验收和发布记录。

`MAX_ACTIVE_GENERATIONS` 表示活跃供应商异步任务的运行配置；供应商 API 上限为 **500**，有效值必须在 `1..500`。当前代码尚未完成跨进程原子认领和该上限的实际执行，因此预览仍固定使用 `2`；任何环境不得仅修改环境变量就直接提升到 500。按 2、5、8、50、100、250、500 分级压测并记录 429/5xx、P95、归档成功率和数据库资源后，才能提高下一档。公平队列的基础配额为每人 2 个活跃任务；容量空闲时可临时借用更多，出现其他待处理用户时停止继续借用。

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

竞品图可经批准的 `gpt-4-vision` 视觉观察器提炼为抽象构图或风格策略；不得作为商品参考图、事实来源、生产 Prompt、导出内容或传给 `gpt-image-2`。Prompt OS 只能把确认过的商品事实、身份锁、模板、我方参考图与受限 Style DNA 编译为生成指令。

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
