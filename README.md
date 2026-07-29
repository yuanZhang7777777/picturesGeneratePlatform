# 独立批量出图平台

状态：MVP 已部署到 2 号云服务器预览环境，入口为该服务器 IP 的 `18083` 端口；2026-07-29 已根据业务反馈进入产品体验重设，当前预览只作为技术链路验证，不作为最终前端方向。

本项目面向公司内部运营人员，提供文件夹上传、商品图片集群整理、全局与集群提示词、AI Prompt 优化、APIMart 异步生图、OSS 归档、单张失败重做和历史版本保留。后端使用 Django；运营工作台已确认迁移为 React + TypeScript + Vite 前端。

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
- 2 号云服务器独立 Docker Compose 栈。
- 临时预览入口使用当前空闲的 `18083` 端口并限制来源 IP。
- 真实员工密码和商品素材正式使用前必须升级为域名 HTTPS。

当前预览边界：

- 部署目录：`/opt/independent-image-platform`。
- Compose 项目名：`independent-image-platform`。
- 当前为 `APIMART_FAKE_MODE=1`，不会产生真实 APIMart 费用。
- 已生成临时 `admin` 账号；密码保存在服务器 root-only 文件 `/opt/independent-image-platform/.admin_password`。
- 聊天中出现过的 APIMart/OSS 密钥不写入仓库或文档；真实付费生图前必须轮换并写入服务器 `.env`。

设计与调研：

- [独立批量出图平台完整设计](docs/specs/2026-07-28-independent-image-platform-design.md)
- [顶级 AI 商品出图平台调研与产品重设](docs/research/2026-07-29-top-image-platform-redesign-research.md)
- [主 Agent 协作与项目集群交付设计](docs/superpowers/specs/2026-07-29-agent-orchestrated-delivery-design.md)
- [React 前端架构设计](docs/superpowers/specs/2026-07-29-react-frontend-architecture-design.md)
- [项目控制板（角色、任务、决定与阻塞）](docs/project/STATUS.md)
- [MVP 实施计划](docs/superpowers/plans/2026-07-28-independent-image-platform-mvp.md)
- [运行与部署手册](docs/runbook.md)

历史飞书/Coze 项目只作为经验来源。本项目不调用、不依赖也不复制飞书或 Coze 工作流。
