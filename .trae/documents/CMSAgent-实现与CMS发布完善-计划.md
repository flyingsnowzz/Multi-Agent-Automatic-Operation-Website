## Summary

本计划用于补齐 `agents/cms_agent` 的缺失实现，使系统在保持默认安全（dry-run）的前提下，具备“真实调用 CMS API 发布文章”的完整链路，并在 CrewAI / LangGraph / Hybrid 三种工作流中落地。

目标覆盖用户指出的 4 个问题：

1. 补齐可导入的 `CMSAgent` 类（`from agents.cms_agent import CMSAgent` 可用）。
2. LangGraph / Hybrid 的 CMS 节点不再只是占位打印，能够在开启开关时真实发布。
3. CrewAI 的 `self.cms_agent = Agent(...)` 挂载 `cms_client` 与 `media_uploader` 工具，使其具备真实工具调用能力。
4. 完成 CMS API 的“Custom/WordPress/Ghost/Strapi”适配框架，并对当前实际使用的 `custom` 适配做可运行实现；其他 provider 给出字段映射与认证逻辑的最小可用实现。

约束与偏好（来自用户确认）：

- 当前对接 CMS：`custom`
- 默认策略：`dry-run`
- 内容格式：HTML 优先
- 安全闸门：真实发布必须同时满足
  - `publishing.dry_run == false`（配置显式关闭 dry-run）
  - 环境变量 `CMS_ENABLE_REAL_PUBLISH == true`

---

## Current State Analysis

### 1) 缺少可导入的 CMSAgent

- `agents/cms_agent/SKILL.md` 给出了示例：`from agents.cms_agent import CMSAgent`，但目录下没有 `CMSAgent` 实现文件与 `agents/cms_agent/__init__.py`（当前目录是“仅文档+工具”的形态）。
- 现状会导致示例代码不可运行，且工作流无法复用统一的“发布封装”。

相关位置：

- [SKILL.md](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/SKILL.md#L39-L47)

### 2) LangGraph / Hybrid 工作流 CMS 节点是占位实现

- LangGraph `_cms_node` 仅打印“发布完成（简化实现）”，没有调用 `cms_client.py` / `media_uploader.py`。
- Hybrid `_cms_node` 仅生成 payload，并注明“不实际请求 CMS”。

相关位置：

- [langgraph_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L357-L373)
- [hybrid_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L402-L433)

### 3) CrewAI CMSAgent 未挂工具

- `workflows/crewai_workflow.py` 创建了 `self.cms_agent = Agent(...)`，但未传入 `tools=[...]`，导致无法调用实际 CMS API。
- 仓库中 `cms_client.py` 与 `media_uploader.py` 已存在 CrewAI Tool 包装函数 `get_cms_client_tool()` / `get_media_uploader_tool()`，但未被工作流使用。

相关位置：

- [crewai_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crewai_workflow.py#L170-L182)
- [crewai_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crewai_workflow.py#L325-L345)
- [cms_client.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/tools/cms_client.py#L340-L416)
- [media_uploader.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/tools/media_uploader.py#L334-L389)

### 4) CMS API 适配不完整

- `agents/cms_agent/config.yaml` 声明支持 `custom / wordpress / ghost / strapi`，但 `cms_client.py` 当前是单一“/posts + /auth/login + X-API-Key/Bearer”混合实现，字段也混用了 WP 风格（如 `featured_media`）。
- 需要把“provider 选择、鉴权、endpoint、字段映射”做成结构化的适配层；其中 `custom` 要做到真实可用，其他 provider 提供最小可用实现与清晰边界。

相关位置：

- [config.yaml](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/config.yaml#L24-L45)
- [cms_client.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/tools/cms_client.py#L81-L175)

---

## Proposed Changes

### A. 增加可导入的 CMSAgent 封装

**新增文件**

1) `agents/cms_agent/__init__.py`

- 导出 `CMSAgent`，使 `from agents.cms_agent import CMSAgent` 成立。

2) `agents/cms_agent/cms_agent.py`

- 提供 `CMSAgent` 类，职责：
  - 读取并解析 `agents/cms_agent/config.yaml`（支持 `${ENV}` 变量替换）
  - 做发布前校验（content_not_empty、category_assigned、featured_image_set 等，按配置可开关）
  - 根据 `publishing.dry_run` 与 `CMS_ENABLE_REAL_PUBLISH` 决定是否真实发请求
  - HTML 优先：优先使用 `article["content_html"]`；缺失时回退 `content` / `content_md`
  - 组装 payload 并通过 provider adapter 发起发布
  - 可选上传图片：对 `featured_image_url`（或 image_result 提供的 url）调用 `MediaUploader` 后把返回的 media 信息写入创建文章的请求

**对外接口**

- `await CMSAgent().execute(article: dict, page_info: dict, images: dict | None = None) -> dict`
- 返回值对齐 `prompt.md` 的结构化输出规范：`article_id/article_url/status/published_at/checks/errors`

### B. CMS provider 适配层（重点：custom 可运行）

**修改文件**

1) `agents/cms_agent/tools/cms_client.py`

- 将当前“混合实现”改为按 provider 分流（但保持对外 `CMSClient.create_post/update_post/...` 的调用习惯）。
- 重点实现 `custom`：
  - base_url 统一读取 `CMS_API_URL`（兼容旧的 `CMS_BASE_URL`）
  - 支持 `X-API-Key` 认证（`CMS_API_KEY`）
  - 保留 `/auth/login` 的 token 认证作为可选路径（当 username/password 提供且未提供 api_key 时）
  - endpoints 与版本：支持把 config 中的 `cms.api.version` 拼到路径上，形成 `/api/v1/posts`（与 `prompt.md` 文档一致）
  - 字段：使用 `content_mapping.fields` 将内部字段映射到 custom CMS 字段（例如 title/content/excerpt/slug/date/author）

- 最小实现 `wordpress/ghost/strapi`：
  - WordPress：Basic Auth（username/password），posts `/wp-json/wp/v2/posts`；媒体 `/wp-json/wp/v2/media`
  - Ghost：Admin API Key 生成 JWT；posts `/ghost/api/admin/posts/`
  - Strapi：Bearer token；posts `/api/posts`，body 包一层 `{data:{...}}`
  - 这些 provider 的字段映射优先从 `content_mapping` 读取，其次使用合理默认。

2) `agents/cms_agent/tools/media_uploader.py`

- 为 `custom` 确保上传 endpoint 与 `CMS_API_URL` / version 一致（形成 `/api/v1/media`）。
- 保持现有表单上传模式（`files={file:(...)}`），并允许从 URL 下载后上传。
- 为 WordPress/Strapi/Ghost 增加最小上传实现（各自 endpoint 与认证头不同），并把返回值统一成 `{success, media_id, url, ...}` 便于上层使用。

### C. 工作流：把“占位 CMS 节点”替换为真实可执行节点（仍默认 dry-run）

**修改文件**

1) `workflows/langgraph_workflow.py`

- 在 `_cms_node` 内引入 `CMSAgent`：
  - 从 `state` 聚合出 `article/page_info/images`
  - 调用 `CMSAgent.execute(...)`
  - 将结果写回 `state["cms_result"]`
  - 仍按用户策略默认 dry-run：当未满足双开关时只返回 payload/模拟结果，并显式标记 `status: "dry_run"`

2) `workflows/hybrid_workflow.py`

- 保留“LLM 生成 payload”的步骤（方便对齐 schema），并新增：
  - 若满足双开关，则调用 `CMSAgent` 使用该 payload 真实发布
  - 否则保留现有行为（生成 payload，不发请求）

### D. CrewAI：CMSAgent 挂工具 + 让任务明确调用工具

**修改文件**

1) `workflows/crewai_workflow.py`

- 创建 `self.cms_agent = Agent(...)` 时传入工具：
  - `tools=[get_cms_client_tool(), get_media_uploader_tool()]`
- 更新 cms_task 的描述，让模型明确：
  - 必须先用 `media_uploader` 上传封面图（如果有 URL/本地路径）
  - 再用 `cms_client` 执行 `create`
  - 当处于 dry-run 时只生成 payload，不调用工具（与全局安全闸门一致）

---

## Assumptions & Decisions

- 当前主打落地 `custom` provider；其他 provider 实现为“最小可用 + 可扩展框架”，不会覆盖所有 SEO 插件字段与复杂 taxonomy 同步。
- 默认不进行 Markdown→HTML 的完整渲染转换（仓库当前无 markdown 渲染依赖）；HTML 优先策略下若缺少 HTML，将把 Markdown 当作正文直发（由 CMS 或前端处理）。
- 真实发布开关采用“双闸门”：
  - 配置：`agents/cms_agent/config.yaml` 增加 `publishing.dry_run: true`（默认 true）
  - 环境：`.env` 增加 `CMS_ENABLE_REAL_PUBLISH=false`（默认 false）

---

## Verification

1) 静态校验

- 确认 `from agents.cms_agent import CMSAgent` 可导入，且存在 `async def execute(...)`。

2) dry-run 冒烟

- 在不设置 `CMS_ENABLE_REAL_PUBLISH` 或保持其为 false 的情况下：
  - `python main.py --engine langgraph` 走到 CMS 节点，`cms_result.status == "dry_run"`，且包含 payload/检查结果。
  - `python main.py --engine hybrid` 的 `cms_result` 仍输出 payload，不会发出真实请求。

3) 真实发布（需要用户本地准备 custom CMS 测试环境）

- 设置：
  - `publishing.dry_run=false`
  - `CMS_ENABLE_REAL_PUBLISH=true`
  - `CMS_API_URL/CMS_API_KEY` 指向测试 CMS
- 运行 `python main.py --engine langgraph`，验证：
  - 创建文章成功，返回 `article_id/article_url/status`
  - 若配置了图片上传且提供 featured_image_url，则先上传媒体再创建文章

