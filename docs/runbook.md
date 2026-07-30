# 运行与部署手册

## 架构与入口

Docker Compose 启动 PostgreSQL、Django Web、Generation Worker、Prompt Worker 和 Caddy。Caddy 是唯一对浏览器开放的同源入口：它在镜像构建时把 `frontend/dist` 写入 `/srv/frontend`，提供 React 静态资源，并将 `/api/`、`/auth/`、`/admin/`、登录/退出/改密、旧 `/batches/` 重定向、健康检查和 Django 静态资源反向代理到 Web 服务。

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

正式素材存储使用 `STORAGE_BACKEND=oss`，并配置 `OSS_ENDPOINT`、`OSS_BUCKET`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET` 和 `OSS_PREFIX=independent-image-platform`。原图、SKU 拉取图、生成结果和历史版本写入该 OSS 私有前缀；导出 ZIP 临时生成后由浏览器下载到员工本地，不在服务器或 OSS 长期保留。`LOCAL_MEDIA_ROOT` 仅用于开发和假模式回退。

APIMart 中文文档与受限账户的最小契约测试是唯一接入事实来源：测试精确模型 ID、`/v1/responses` 图片输入、结构化输出封装、错误语义、限流和账务。上游模型文档仅帮助判断能力方向，不能代替 APIMart 参数或可用性结论。当前服务器真实 smoke 已验证：`deepseek-v4-pro` 文本节点走非流式 Chat Completions，`gpt-5-nano-2025-08-07` 视觉观察走 Responses 并从 `output[].content[].text` 提取文本，`gpt-image-2` 先通过 `/v1/uploads/images` 上传我方参考图，再用字符串数组 `image_urls` 提交 `/v1/images/generations`，任务完成后必须下载结果并归档到受控存储；一个真实 1+8 付费项目已完成 9 张结果、人工审核通过和 ZIP 导出。

本地 APIMart 三节点 smoke 命令：

```powershell
$env:APIMART_FAKE_MODE='1'
python manage.py smoke_apimart_nodes
```

真实受限账户验证时，先由主 Agent 确认付费授权和 `.env`，再将 `APIMART_FAKE_MODE=0`。命令只允许输出节点名、状态、耗时和结果哈希；不得打印 API key、完整响应、上传 URL、任务结果 URL 或签名 URL。明显的假 key、空 key 或替换标记必须非零退出，且输出中不得回显该 key。

## 出站网络与连通性

生产服务器统一通过 SSH 别名 `hermes-remote` 操作，仓库文档不记录服务器公网 IP。2026-07-30 迁移后已确认该服务器能够解析并访问 `api.apimart.ai`，并完成 APIMart 三节点真实 smoke、OSS 写读删 smoke 和真实 1+8 付费生图 smoke。

不要把“入站访问端口”和“出站目的端口”混在一起：安全组里开放 `18000/19000` 是允许员工浏览器访问本服务器的预览入口；APIMart 的 `443` 是本服务器主动访问外部 HTTPS API 时的目的端口。当前 APIMart 出站网络阻塞已解除。

2026-07-30 运行配置：ERP 登录与 SKU 商品资料查询使用 `103.198.125.2:16777`。当前已验证 ERP 登录接口网络可达并返回 JSON；真实员工账号成功登录与 SKU 商品图导入仍需通过浏览器人工登录或服务器端专用 smoke 账号变量验证。

验收命令只输出连通状态，不得打印 `.env`、Token 或密钥：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 hermes-remote \
  "curl --noproxy '*' --connect-timeout 8 --max-time 15 -sS -o /dev/null \
  -w 'http_code=%{http_code} tls_verify=%{ssl_verify_result} total=%{time_total}\n' \
  https://api.apimart.ai/v1"
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

旧 Django `/batches/` 页面不再对运营展示；`/batches/`、`/batches/new/` 和 `/batches/<uuid>/` 只作为兼容入口重定向到 React 工作台。以下静态检查应通过，确保这些旧链接仍由 Django 做权限校验后再跳转：

```powershell
$content = Get-Content -Raw 'docker/Caddyfile'
if ($content -notmatch '(?m)^\s*@django path .*\/batches \/batches\/\*') { throw 'Caddy does not preserve Django /batches/ redirects' }
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

`web` 的 Docker health check 调用 `/health/live`；`/health/ready` 当前只验证数据库。发布验收还必须确认 `generation-worker` 和 `prompt-worker` 均为持续运行状态、日志没有重复退出或未处理异常。`run_prompt_worker --once` 在空队列应输出 `processed=0`；真实队列验收还要确认每次只领取一个待准备商品，保存 N1–N7 节点快照、推断台账和 9 槽 PromptVersion。

不要在常驻 `generation-worker` 已运行且队列非空时再执行 `run_generation_worker --once`。现有 worker 尚未实现跨进程任务原子认领；并发 one-shot 调试可能在真实付费模式重复提交。仅在隔离测试栈或停止常驻 worker 后使用该命令。

前端工作台已切到双速项目工作区和项目结果页；后端已提供生成、再生成、修订和选择式导出接口。双速手工验收路径为：登录测试账号 → 创建项目 → 上传图片/文件夹或输入 ERP SKU → 分别验证自动模式与整理模式 → 拖拽合并 → 查看推断台账和 9 槽 Prompt → 白底图完成后生成营销图 → 结果进入待审核 → 人工通过需要导出的版本 → 圈选修改单张或重做失败项 → 下载本地 ZIP。Shopee VN 普通店还须验证槽位 1 为真实来源图直通、槽位 2 白底完成后才提交槽位 3–9。未审核通过的结果不得导出。

## 管理员规则、模板与发布

管理员通过同源 `/admin/` 登录后维护平台规则、输出模板、槽位和 N1–N9 Prompt 节点模板。每次发布都必须记录平台/站点、官方来源 URL、核对日期、版本、图片用途/比例/分辨率、禁止内容和审核 checklist。`seed_platform_templates` 发布全局 9 图模板、Prompt OS v2 节点、Shopee/TikTok 官网主规则包，以及已有官方证据的站点覆盖；没有覆盖的国家复用对应平台官网主规则包并标记 fallback。草稿、未核对项或 fallback 不得被描述成该国家的完整自动合规。

ERP 登录名在 `PLATFORM_ADMIN_ERP_USERS` 中的用户会成为平台管理员并可进入 Django admin；普通员工不应进入模型节点、队列/用量或模板规则配置页。非 global 的市场覆盖规则若要在 admin 发布，必须填写官方来源 URL、站点、核对日期和版本；未逐站核实的 Shopee/TikTok 国家继续使用对应平台官网主规则包和全局 1+8 模板，不宣称该国家完整自动合规。

竞品图可经批准的 `gpt-5-nano-2025-08-07` 视觉观察器提炼为抽象构图或风格策略；不得作为商品参考图、事实来源、生产 Prompt、导出内容或传给 `gpt-image-2`。Prompt OS 可使用确认事实、直接观察和显式披露的合理推断；审核页必须显示推断的置信度、风险、证据和用途，高风险声明仍由规则闸门阻断。

## Hermes 预览与真实发布

远端部署只能使用 `ssh hermes-remote`，且先声明：

```text
project_key=global
mode=deploy
target=/opt/independent-image-platform
locks=.codex_locks/global.lock
global_touch=yes
bridge_needed=no
```

持有 `.codex_locks/global.lock` 后，在目标目录执行 Compose 静态解析、构建、迁移、健康检查和人工验收，并把命令输出与镜像版本写入发布记录。只有主 Agent 可以签核该发布。

HTTP 的 IP:端口入口仅供测试账号和非敏感素材。正式入口不使用来源 IP 白名单，但必须具备域名 HTTPS、账号安全、备份恢复、私有存储和真实供应商契约测试，才可向 100 名员工开放真实素材或付费生成。

## 发布证据最小清单

- `docker compose --env-file .env.example config --quiet` 成功。
- 镜像构建包含 React 静态产物，Caddy 同源入口能返回前端并代理 Django 健康检查。
- Django 检查、迁移状态、Web/数据库健康、两个 worker 的容器状态和日志已记录。
- 假模式端到端路径、对象权限回归、真实 APIMart 三节点 smoke、真实 OSS 写读删和真实 1+8 付费生图 smoke 已通过。
- 未审核结果导出应返回 400；人工审核通过后 ZIP 只包含通过版本。
- 真实 ERP 员工账号成功登录和 SKU 导入如未验收，发布记录必须明确标注，不能以网络可达替代业务证明。
