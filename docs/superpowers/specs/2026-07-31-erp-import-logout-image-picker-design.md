# ERP 导入、退出登录与图片选择设计

## 目标

- 修复正式环境 ERP 商品查询成功、但图片因源地址未进入白名单而全部导入失败的问题。
- 工作台右上角提供“退出登录”，服务端同时清除 Django 登录态和 session 中的 ERP Token。
- 上传区明确区分“选择单张 / 多张图片”和“选择整个文件夹”；图片入口原生支持 `multiple`，两种入口继续复用现有上传、预览和防重复逻辑。

## 设计

ERP 继续使用当前登录人的 session Token 查询商品，不改认证链路。正式环境仅将已确认的 ERP 图片源 IP `180.167.156.35` 加入 `CATALOG_ALLOWED_IMAGE_HOSTS`，不允许任意公网图片地址。

退出登录复用 Django 现有 `POST /logout/`。React 先从 `/api/csrf/` 取得 CSRF Token，再提交退出请求，成功后跳转 `/login/`；不在浏览器读取 Cookie 或保存 Token。

上传区保留两个独立 `<input type="file">`：图片控件带 `multiple` 且不带 `webkitdirectory`，文件夹控件带 `webkitdirectory`。用户一次选择一张或多张图片后，页面先显示缩略图，再由“导入并自动出图”或“导入后整理”提交。

## 错误与验收

- ERP 图片源不在白名单时仍安全拒绝，不降级为全公网放行。
- 退出请求失败时保留当前页面并显示错误，不伪装为已退出。
- 图片选择入口必须是可见按钮，文字明确说明支持单张和多张。
- 验收覆盖 Django 退出清除 ERP Token、React 退出调用、单/多图选择控件契约、真实 ERP SKU 图片导入和 OSS 归档。

