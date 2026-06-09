## Summary

把 Custom CMS 的“发布 contract”固化为代码可校验的规范，并通过真实后端联调（staging/测试环境）把**字段名、状态值、响应结构**对齐到完全一致，最终让 `CMSAgent.execute()` 在真实发布时做到：

- 请求体字段与后端契约一致（包括 content_html/content_md/meta/featured_image/status/publish_date 等）
- 状态枚举一致（draft/publish/scheduled 或后端自定义枚举）
- 响应解析一致（能稳定取到 article_id/article_url/status）
- 错误与冲突可预期（slug 冲突、鉴权失败、媒体上传失败等）

本计划只覆盖 **custom** provider 的 contract 与联调闭环；WordPress/Ghost/Strapi 继续保持“最小实现 + 可扩展”状态，不纳入本次 contract 固化范围。

---

## Current State Analysis (Repo Grounding)

### 已存在的实现基础

- Custom CMS 的请求体雏形已在 `CMSClient._build_custom_post_payload()` 中形成（`title/content_html/content_md/excerpt/slug/category/tags/featured_image/meta/status/publish_date/topic_id`）。
  - [cms_client.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/tools/cms_client.py#L102-L144)
- config 中已经引入了“custom contract”的配置块（endpoints、post_contract 的字段名、required_fields、response_id_field/response_url_field）。
  - [config.yaml](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/config.yaml#L23-L54)
- 当前系统已具备“默认 dry-run + 双闸门真实发布”的安全策略。
  - [cms_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py)

### 当前缺口（与“固定 contract + 真实联调”目标相比）

1) **contract 没有单一事实源**
- 代码与配置都在描述 contract，但解析逻辑目前只吃了少量配置（例如 version），并没有“按 contract 配置驱动组包/解析/校验”的完整闭环。

2) **响应结构不确定**
- 真实后端可能返回 `{id,url}`、`{id,link}`、或嵌套结构（例如 `data.id`），当前解析逻辑对嵌套结构容错不足。

3) **状态值不确定**
- `CMSAgent` 内部把 mode 映射为 `draft/scheduled/publish`，但后端可能用 `published`、数字状态或其他枚举，需要显式映射层。

4) **缺少“真实后端联调验证脚本/测试”**
- 目前单测主要覆盖 dry-run/slug/retry，不会对真实后端做字段级断言。

---

## Proposed Changes

### A. 固化 Custom CMS Contract（配置驱动 + 可校验）

**目标：** 将 custom contract 变成“可执行规范”，而不是散落在 prompt/注释里。

1) 扩展 `agents/cms_agent/config.yaml` 的 custom contract 描述（不含密钥）
- 在 `cms.custom.post_contract` 下补齐以下字段（作为 contract 的唯一事实源）：
  - `request`:
    - `create_post_path`（默认 `/posts`，配合 `version` 拼接）
    - `media_upload_path`（默认 `/media`）
    - `category_path` / `tags_path`（如后端支持 name→id/slug→id 则用于闭环）
  - `status_mapping`：内部状态 → 后端状态（draft/scheduled/publish）
  - `response_paths`：使用“路径表达式”取值，支持嵌套与多候选
    - `id`: 例如 `["id", "data.id", "post.id"]`
    - `url`: 例如 `["url", "link", "data.url", "post.url"]`
    - `status`: 例如 `["status", "data.status"]`
  - `error_paths`：用于提取后端错误消息（例如 `["error", "message", "detail"]`）

2) 在 `agents/cms_agent/tools/cms_client.py` 中实现“按 contract 配置驱动”
- 新增：
  - `CustomCMSContract`（简单 dataclass 或 Pydantic）从 config 读取并校验必填项
  - `extract_by_paths(data, paths)`：支持嵌套 key 访问与多候选
  - `map_status(internal_status)`：走 status_mapping
- 修改 `create_post()` 的 custom 分支：
  - 组包完全由 contract 定义（字段名、meta 字段名、content_html 字段名等）
  - 响应解析由 response_paths 完成，并输出统一结构 `{success, post_id, post_url, status, data}`
- 修改 `MediaUploader` 的 custom 分支：
  - 上传 endpoint 走 contract 配置（保持已有 `/api/v1/media` 拼接能力）
  - 响应解析同样支持 paths（例如 media id/url 字段）

### B. 联调模式与契约断言（真实后端）

**目标：** 有一套“只在你配置了后端地址/密钥时才跑”的联调测试，明确断言 contract 一致性。

1) 新增 `tests/integration/test_custom_cms_contract.py`（或 `scripts/contract_probe.py`）
- 由环境变量控制是否运行：
  - `CMS_CONTRACT_TEST=true` 才运行（默认不跑，避免 CI/本地误发）
  - 必须同时满足 `CMS_ENABLE_REAL_PUBLISH=true` 且 `publishing.dry_run=false` 才允许执行“创建/上传”动作
- 测试内容：
  - `GET posts?slug=...` 是否按 contract 可用（用于 slug_unique/overwrite_update）
  - `POST media` 上传一张小图片（可用本地 fixture），断言返回 id/url 可被解析
  - `POST posts` 创建一篇草稿（或 scheduled），断言：
    - 请求体字段名与 contract 一致（通过在客户端增加可选 debug hook：仅在联调测试时打印/返回 request_json，不写日志文件）
    - 响应 id/url/status 能稳定解析
  - 错误场景（至少验证一个）：
    - 401/403 鉴权失败时能提取 error message（error_paths）

2) 在 `agents/cms_agent/cms_agent.py` 增加“contract 对齐失败的可观测输出”
- 当 response 无法解析到 id/url 时，返回明确的 `errors: ["contract_response_parse_failed"]` 并附带 `details.data`（不包含密钥）
- 发布历史落盘时写入 `contract_version`、`response_extract_paths_used`（便于排查）

### C. 明确联调工作流（从“跑通”到“对齐”）

**目标：** 联调不是一次性“能发就行”，而是“字段/状态/响应”都一致。

- Step 1：向后端索要（或从网关/接口文档提取）OpenAPI/Swagger 或 2 组真实样例：
  - create post 请求体样例 + 响应样例
  - upload media 请求体样例 + 响应样例
- Step 2：把这些样例转写进 `cms.custom.post_contract` 的 response_paths/status_mapping
- Step 3：跑 integration tests，直到断言全部通过
- Step 4：锁死 contract（后续变更必须同步改 contract 配置与联调测试）

---

## Assumptions & Decisions

- 真实联调默认对接 **测试/预发 CMS**，不会直接打生产环境。
- 联调测试必须显式开启（`CMS_CONTRACT_TEST=true`），并且必须通过现有“双闸门”策略才允许真正发请求。
- contract 固化采用“配置驱动 + 解析工具函数”方案，避免把后端字段硬编码到业务逻辑中，便于后端升级时只改 contract 配置与少量解析器。

---

## Verification

1) 静态验证
- `venv\\Scripts\\python.exe -m compileall agents workflows tests`

2) 单元测试
- `venv\\Scripts\\python.exe -m unittest -v`

3) 真实联调（需你提供测试 CMS 的地址与密钥，且显式开启）
- 设置：
  - `.env`：`CMS_API_URL/CMS_API_KEY` 指向测试环境
  - `agents/cms_agent/config.yaml`：`publishing.dry_run=false`
  - 环境变量：`CMS_ENABLE_REAL_PUBLISH=true`、`CMS_CONTRACT_TEST=true`
- 运行 integration tests：确保请求字段/状态/响应解析全部匹配

