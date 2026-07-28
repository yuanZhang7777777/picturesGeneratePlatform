# 独立批量出图平台

状态：MVP 正在实现，已具备 Django 项目基线、登录、上传、集群、队列和 APIMart 客户端。

本项目面向公司内部运营人员，提供文件夹上传、商品图片集群整理、全局与集群提示词、AI Prompt 优化、APIMart 异步生图、OSS 归档、单张失败重做和历史版本保留。

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

权威设计：

- [独立批量出图平台完整设计](docs/specs/2026-07-28-independent-image-platform-design.md)
- [MVP 实施计划](docs/superpowers/plans/2026-07-28-independent-image-platform-mvp.md)
- [运行与部署手册](docs/runbook.md)

历史飞书/Coze 项目只作为经验来源。本项目不调用、不依赖也不复制飞书或 Coze 工作流。
