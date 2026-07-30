# 独立批量出图平台

状态：MVP 已部署到 2 号云服务器预览环境，入口为该服务器 IP 的 `18083` 端口；2026-07-29 已根据业务反馈进入产品体验重设，当前预览只作为技术链路验证，不作为最终前端方向。

本项目面向公司内部运营人员，提供文件夹上传、商品图片分组、结构化 Brief、AI Prompt、异步生图、审核、失败项重做和历史版本保留。Django 负责登录、权限、任务和数据；`frontend/` 中的 React + TypeScript + Vite 工作台由 Caddy 同源提供静态文件，并由 Caddy 代理 Django API、认证、后台、健康检查和迁移期的 `/batches/` 页面。

SKU 是一级后端入口：`POST /api/projects/{id}/sku-import/` 接受最多 50 条 SKU，服务端使用当前登录用户的 ERP Token 查询商品名和产品图，并把图片归档到私有存储；失败项逐条审计，不阻塞同批其他 SKU。商品资料查询地址、图片源公网 IP 字面量白名单、ERP 登录和 OSS 配置见 [运行手册](docs/runbook.md)，真实目录服务的 Token 过期契约仍需在发布环境验证。

核心任务模型：

```text
上传批次
  └─ 商品集群（每张源图默认一个，拖拽合并多角度图）
      └─ 输出图片任务
          └─ 重做版本
```

首版规划容量：

- 最多约 100 名内部员工。
- 组织级每日最多 2,000 次生图提交尝试。
- 供应商 API 活跃异步任务上限为 500；当前预览默认并发为 2，必须完成原子队列、限流与分级压测后才可逐步提高。
- 2 号云服务器独立 Docker Compose 栈。
- 临时预览入口使用当前空闲的 `18083` 端口；正式使用不做来源 IP 白名单，但必须通过域名 HTTPS 登录。

当前预览边界：

- 部署目录：`/opt/independent-image-platform`。
- Compose 项目名：`independent-image-platform`。
- 服务器已配置真实 ERP、OSS 与 APIMart Secret；OSS smoke 已通过。ERP 与 APIMart smoke 因 2 号服务器出站网络阻断未通过，尚无服务器侧成功付费生图证据。
- 已生成临时 `admin` 账号；密码保存在服务器 root-only 文件 `/opt/independent-image-platform/.admin_password`。
- APIMart、OSS 与 ERP 凭据只写入服务器 root-only `.env` 或 Secret；不进入仓库、文档、前端、数据库、日志、Prompt 或导出包。
- 模型名称、端点、参数、响应和限流以 [APIMart 中文文档](https://docs.apimart.ai/cn) 与受限账户契约测试为准；不以模型原厂文档直接推断可接入性。
- `APIMART_FAKE_MODE=1` 仍是本地和新环境的唯一安全默认值。2 号服务器切到真实 APIMart 前，必须先修复出站网络、完成供应商契约测试、限流验收、HTTPS 和主 Agent 发布签核。
- HTTP 的 IP:端口入口只允许测试账号与非敏感素材；正式入口不做来源 IP 白名单，但在域名 HTTPS 与账号安全就绪前，不得面向 100 名员工开放。

## 本地运行与静态验证

先用环境样例验证 Compose 形状；它含有替换标记，只用于解析，不可直接启动服务：

```powershell
docker compose --env-file .env.example config --quiet
```

实际本地预览时，复制 `.env.example` 为 `.env`，把所有 `replace-with-*` 替换为本地安全值，再执行：

```powershell
docker compose up -d --build
docker compose ps
curl.exe -fsS http://127.0.0.1:18083/health/ready
```

React 开发模式与 Docker 预览是两条路径：前者运行 Django 与 `npm --prefix frontend run dev`；后者由 `docker/Caddy.Dockerfile` 构建 `frontend/dist`，不保留 Node 前端服务器。完整命令、健康要求和发布门禁见 [运行与部署手册](docs/runbook.md)。

## 规则、模板与审核

- 平台规则和套图模板只能由管理员在同源 `/admin/` 路径维护和发布，并记录官方来源、核对日期、适用平台/站点和版本。
- 正式种子由独立 `template-seed` 任务处理：仅全局通用模板可作为 `published` 基线；Shopee/TikTok 规则和模板在官方规则完成核对、来源和版本写入前必须保持 `draft`，不能宣称自动合规。
- 未发布或未核对的规则不能被宣称为自动合规。竞品图只能经批准的 `gpt-5-nano-2025-08-07` 视觉观察器形成抽象策略，不能作为生成参考图、商品事实、生产 Prompt、导出内容或上传至 `gpt-image-2`。
- 生成完成不等于业务验收；只有人工标记 `accepted` 的版本可以进入平台导出。失败重做和人工修改均保留旧版本。

## Worker 边界

- `generation-worker` 提交、轮询并归档图片任务。
- `prompt-worker` 为结构化 Brief/Prompt 预留；当前实现仍是占位循环，不能作为异步 Prompt 工作已交付的证据。
- 健康检查目前验证 Web 与数据库；发布时还必须确认两个 worker 容器持续运行且日志无重复退出。

设计与调研：

- [独立批量出图平台完整设计](docs/specs/2026-07-28-independent-image-platform-design.md)
- [顶级 AI 商品出图平台调研与产品重设](docs/research/2026-07-29-top-image-platform-redesign-research.md)
- [Shopee/TikTok 商品图规则官方来源登记册](docs/research/2026-07-29-platform-image-rule-source-register.md)
- [主 Agent 协作与项目集群交付设计](docs/superpowers/specs/2026-07-29-agent-orchestrated-delivery-design.md)
- [React 前端架构设计](docs/superpowers/specs/2026-07-29-react-frontend-architecture-design.md)
- [项目控制板（角色、任务、决定与阻塞）](docs/project/STATUS.md)
- [MVP 实施计划](docs/superpowers/plans/2026-07-28-independent-image-platform-mvp.md)
- [运行与部署手册](docs/runbook.md)

历史飞书/Coze 项目只作为经验来源。本项目不调用、不依赖也不复制飞书或 Coze 工作流。
