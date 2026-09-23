# 语雀协议目录（Protocol-first）

本项目的目标：所有读写操作优先走语雀 Web 协议（`requests` + 浏览器 Cookie/CSRF），
浏览器只做两件事——提供登录会话，以及在协议确实没有对应能力时做窄兜底。
DOM 模拟（DrissionPage 点击/输入）不是默认路径。

## 1. 已协议化的操作

| 操作 | 方法与端点 | 代码位置 | payload / 说明 |
| --- | --- | --- | --- |
| 常用知识库列表 | `GET /api/mine/common_used` | `src/core/client.py:YuqueClient.get_repositories` | Cookie + `X-CSRF-Token=yuque_ctoken` |
| 知识库目录 | `GET /api/catalog_nodes?book_id&format=list` | `src/core/client.py:get_catalog_nodes` | 导出树、挂载目录用 |
| 单文档增量时间戳 | `GET /api/docs/{id}?book_id` | `src/core/client.py:get_document_updated_at` | 增量导出对比 `updated_at` |
| 新建文档（Markdown 导入） | `POST /api/docs` | `src/core/client.py:create_markdown_document` | `book_id/title/body/type=Doc/format=markdown` + `insert_to_catalog/target_uuid/action=insert` 挂到目录末尾；已对照前端 bundle 验证（见该函数 docstring） |
| 新建知识库 | `POST /api/books` | `src/core/client.py:create_repository` | `name/slug/description/public(0/1)`；**无 team 字段**，传 `team` 直接抛错，见第 3 节 |
| 发起导出 + 轮询 | `POST /api/docs/{id}/export` | `src/core/client.py:export_document` | `{type,force,options}`，`pending` 轮询后取 `data.url` |
| 当前登录用户 | `GET /api/mine` | `src/core/client.py:_current_user_login` | 建库回包缺 `user` 时补 `login` |
| 知识库解析 | `GET /api/books?id=\|namespace=` | `src/core/repository_resolver.py:resolve` | 失败才回退读命名空间页面 `window.appData` |
| 收藏知识库 | `GET /api/mine/marks?limit&offset&type=all` | `src/core/favorite_repository_provider.py` | 仅识别 `type=Book` |
| 导出文件下载 | `GET <export url>` | `src/core/download_support.py:download_file` | 有界重定向、HTTPS 强制、Cookie 域隔离、2GiB/30min 上限、原子落盘 |
| 外部图片下载 | `GET <image url>`（独立 session） | `src/core/client.py:download_external_image` | 不带语雀 Cookie，IP pinning 防 SSRF，`trust_env=False` 隔离代理 |

所有协议请求共用一套会话形态：Cookie 取自 `tab.cookies()` 经 `_yuque_cookies`
过滤，请求头经 `_api_headers` 带 `User-Agent=tab.user_agent`、`Referer`、
`X-Requested-With`、`X-CSRF-Token=yuque_ctoken`（见 `client.py:_request_json/_request_api`）。

## 2. 浏览器仍保留的 2 个理由

1. **人工登录不可协议化**：`YuqueClient.login` 用 `run_cdp("Network.clearBrowserCookies/Cache")`
   + 打开 `/login` + 轮询 `tab.url`，等用户扫码/账密登录；`YuqueAuth.load_cookies`
   用 `run_cdp` + `tab.set.cookies` 恢复会话。这是最小必要的浏览器使用。
2. **`team/组织内公开`建库暂无协议**：`POST /api/books` 经静态确认只有
   `public 0/1`，模型层（`models.py:public`、`repository_resolver.py:raw_public`）
   也只接受 0/1。`RepoService.create` 对 `visibility==team` 直接走
   `YuqueBrowserWriter.create_repository` 并打印 warning + 审计 `via=browser`，
   `YuqueClient.create_repository` 遇 `team` 直接抛 `RepositoryResponseError`，
   杜绝静默降级为 private。待抓包确认语雀是否有 `team_id/group_id` 字段后再全协议化。

## 3. 已下线的模拟

- `YuqueBrowserWriter.import_markdown` 及其 `_CREATE_DOCUMENT_BUTTONS`、
  `_IMPORT_BUTTONS`、`_MARKDOWN_BUTTONS`、`_IMPORT_SUBMIT`、`_upload`、
  `_document_url_from_page` 已删除：导入统一走 `POST /api/docs`，
  比“新建→导入→选 Markdown→上传→提交”更快且 headless 安全。
  本地图片/附件缺口以后补上传协议，不复活 UI 导入。
- `browser_writer.py` 现仅保留 `create_repository`（team 专用兜底）与通用
  `_click/_input/_find` 原语，选择器集中一处，fail-closed。

## 4. 待抓包（步骤 0）

用有头浏览器手动“新建知识库 → 选组织内公开 → 创建”，抓
`POST /api/books` 及前后请求的完整 payload，重点看 `public` 是否有第三态、
有无 `team_id/group_id/type` 字段。结论分支：

- 找到 team 字段 → `client.create_repository` 透传，`browser_writer.create_repository`
  可整体下线；
- 确认没有 → 维持“仅 team 走 UI”现状，本文件即为书面结论。
