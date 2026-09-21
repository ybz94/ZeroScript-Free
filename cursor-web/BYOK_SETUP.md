# 网页模型端点 → cursor-byok（实验接入）

## 架构与验证边界

```
Cursor 原生 Agent / 工具执行链
        ↕
cursor-byok 的 OpenAI 兼容模型配置
        ↕ HTTP Chat Completions
model_endpoint.py（普通程序，无本地模型）
        ↕ WebSocket
bridge.py ↔ 浏览器扩展 ↔ 网页 AI
```

端点没有备用模型、不读写项目文件、不执行 shell 命令。它将模型请求的完整 messages 和 tools 转交网页，把网页输出校验后转换成普通答案或工具调用。实际工具执行仍交给客户端现有执行链。

已检查 ybz94/cursor-byok 的配置结构和 OpenAI 流式适配代码，具有 BaseURL / ModelID / OpenAIEndpoint 及 tool_calls 接入点。但**尚未在真实 cursor-byok + Cursor 桌面客户端上验证**。原生修改高亮、确认、撤回及工具执行兼容性必须实测；HTTP 协议测试通过不等于桌面流程已通过。

## 前置条件

- 已完成浏览器插件 0.3.0 的发送/取回答案实测。
- cursor-byok 安装在本地，可配置自定义 OpenAI 兼容服务；不是另一个容器/远程机器中的 localhost。
- 测试时**禁用旧 web-assistant MCP 服务**，不要把网页再作为咨询工具嵌套调用。端点就是模型入口，不需要 MCP 中转。项目规则已增加模式区分，但禁用旧服务更可靠。
- 一个专用网页会话，只绑定一个 Cursor 对话。不要让其他工具或人工同时在该网页输入。
- Cursor 发送的 messages 可能包含文件、系统提示、工具输出；这些都会发给目标网站。只在授权的代码范围内使用，排除密钥与其他敏感内容。

## Windows 配置

### 1. 更新并安装依赖

在仓库根目录：

```powershell
git pull --ff-only origin arena/01a0b7a2-zeroscript-free
.\.venv\Scripts\python.exe -m pip install -r cursor-web\requirements.txt
```

如果已有本地修改导致拉取失败，不要强制覆盖。

### 2. 保持 Bridge 与网页扩展在线

终端 A：

```powershell
.\.venv\Scripts\python.exe cursor-web\bridge.py
```

如已有 Bridge 运行，不要重复启动。打开已登录的目标 AI 网页，建议新建专用会话，手动问候一次并等它回答，清空未发送草稿。

### 3. 查询网页会话 ID

终端 B：

```powershell
.\.venv\Scripts\python.exe cursor-web\model_endpoint.py --sessions
```

根据 provider/title/url 选择正确会话，复制它的 `id`。不要复制 Bridge 的 job_id。

### 4. 启动模型端点

将下面的占位内容替换为上一步的真实会话 ID：

```powershell
.\.venv\Scripts\python.exe cursor-web\model_endpoint.py --session "网页会话ID"
```

保持终端 B 运行。默认地址是 `http://127.0.0.1:17615/v1`，模型名 `web-ai`。

首次启动生成 `cursor-web/.endpoint-token`。这是 API Key，和插件配对的 `.bridge-token` **不是同一个令牌**。用本地文本编辑器打开并复制，不要发送给 AI 或分享截图。两种令牌都已 Git 忽略。

### 5. 在 cursor-byok 添加模型

按当前版本对应的模型配置页面填写（界面标签可能不同）：

| 配置 | 值 |
|---|---|
| Provider / 类型 | OpenAI 兼容 |
| Base URL | `http://127.0.0.1:17615/v1` |
| API Key | `.endpoint-token` 的内容 |
| Model ID | `web-ai` |
| 显示名称 | 网页 AI |
| OpenAI endpoint | Chat Completions，不选 Responses |
| 并行工具调用 | 关闭 / `parallel_tool_calls=false` |
| 额外响应格式 | 不设置 JSON schema / JSON mode |

端点已经提供 SSE 响应，不需要关闭流式请求。网页完整回答通过校验后才作为 SSE 数据返回，不是逐 token 转播网页文字。等待期间只有 SSE 注释心跳；它不保证所有客户端都会延长自己的超时，可在助手配置中适当提高 provider 等待上限，并实测。

在 Cursor 中选择这条模型配置。移除“先调用 web_chat_send”的旧指令；现在直接提需求。

## 三步验收

### A. 普通问答

在新的 Cursor 对话输入：

> 请只回复“网页模型端点测试成功”，不要调用工具。

检查网页收到的请求与 Cursor 回答是否对应。网页侧看到协议 JSON 是正常的；Cursor 应显示其中的 content，而不是把整个 JSON 原样展示出来。

### B. 读取文件

准备不含敏感信息的测试文件，输入：

> 请使用可用的原生工具读取 cursor-web-demo.js，概括其作用。不要修改文件，不要执行命令。

检查 Cursor 是否出现真实读文件工具调用，以及网页下一轮是否收到实际工具结果。不能把网页声称“已读取”当成通过。

### C. 原生编辑与撤回

使用可丢弃的示例文件：

```javascript
function greet(name) {
  return "Hello, " + name;
}
```

输入：

> 只修改这个示例文件，让 greet 没传入名字时返回 Hello, world。使用可用的编辑工具，不执行命令、不运行测试。

检查实际文件 diff、原生文件列表、确认/拒绝/撤回。不要开启命令自动批准；网页 AI 的工具请求仍需服从本地权限策略。确认真实原生流程后再用于正式项目。

## 第一版的明确限制

- 只实现 `GET /v1/models` 和 `POST /v1/chat/completions`。不支持 Responses、Embeddings、图像/音频、旧 functions API、并行工具、强制 JSON response_format。
- 每轮最多一个工具调用。工具定义来自客户端，参数按其 JSON Schema 校验；拒绝外部 schema 引用。不自行发明工具、不猜测修复参数、不自动执行网页指令。
- 网页必须返回带本轮 request_id 的 JSON；不合规会失败，不自动转交其他模型。网站对结构化输出的遵循能力需要实测。
- 单次完整请求（包括工具定义和包装指令）按适配器安全预算检查：DeepSeek 160,000、ChatGPT 120,000、Arena 118,000 UTF-16 单位；未知网站保持 60,000。ChatGPT 另有 600 行限制。它们不是模型 token 上下文窗口。超限返回 413，附消息角色/工具的长度统计，**不会静默删减上下文**。真实 Cursor 系统提示/工具列表可能很长；若超限，先禁用无关工具和 MCP、缩小上下文，必要时再设计显式上下文管理，不宣称当前已支持大型项目。
- temperature、max_tokens 等生成调节不映射为网站模型原生采样控制；本版不能保证这些参数生效。不返回虚构 token usage。
- 同进程内相同语义请求共享结果/失败和 tool_call ID，客户端断连不会自动重发网页消息；不同请求在忙碌时返回 409。最多缓存 128 个请求。缓存仅内存，不保证跨重启去重，不保证客户端修改重试请求后仍被识别。
- 相同完整请求在缓存期内返回相同结果，不提供独立“重新生成”语义。需要不同回答时明确追加用户消息；若前一次仍在网页执行，先处理它。
- 不支持多 Cursor 对话隔离。换对话请换专用网页会话并重启端点。每轮发送完整客户端历史，但旧网页历史依然存在；指令要求只参考当前历史不等于模型层面的强隔离。
- 刷新网页使 session ID 变化时，端点明确报错，不自动挑选另一个标签页。重新执行 --sessions 并用新 ID 启动。
- 页面登录失效、冻结、截断、Bridge 断线等照常可能导致失败。不实现验证码绕过。
- 只绑定本机，不接受带 Origin 的浏览器 API 请求。不要公开端口。Windows 上需同时保护令牌所在目录的访问权限。

## 错误定位

| 错误 | 处理 |
|---|---|
| 401 | 使用 `.endpoint-token`，不是 `.bridge-token` |
| 404 / Responses not supported | 在助手中选 Chat Completions，确认最终路径 `/v1/chat/completions` |
| 400 | 查看错误信息，关闭不支持的并行工具、结构化输出或非文本请求 |
| 409 | 当前会话忙碌或绑定失效；不要盲目重新发送 |
| 413 | 完整上下文超限，减少不相关工具/消息；原请求未发往网页 |
| 502 / output failed validation | 查看网页是否按协议返回 JSON，工具名、参数与 request_id 是否正确；任何失败都不会返回可执行工具调用 |
| 504 / 网页超时 | 检查原网页任务是否仍在运行，恢复页面后再决定；不要直接重复提交 |

流式响应开始后的失败会通过 SSE `data: {"error": ...}` 返回（HTTP 状态可能仍是 200），不能只凭 HTTP 200 判定成功。

## 0.3.0：修复统一 60,000 上限导致的 413

端点和 Bridge 改用同一个按网站选择的预算；插件发送前再次校验，避免触发复用适配器内部的头尾截断。长度按 JavaScript UTF-16 计量，非 BMP 字符占两单位。

更新后必须：停止并重启 Bridge 和 model_endpoint、重新加载浏览器插件、刷新目标网页、重新查询 session ID 并绑定。会话应显示 transportVersion=0.3.0 和 inputMaxChars。

如果仍然 413，错误中的 utf16_units/limit 是完整提示的实际长度和安全预算；Breakdown 是 system/developer/user/assistant/tool/tools 的 JSON 长度分项，不包含正文。请只分享这些统计和网站名称，不分享密钥或完整代码。此修改不提供无限上下文，也不自动丢弃历史、工具或系统指令。

## 编辑工具输出的兼容与诊断

端点现在兼容以下等价格式，工具名、参数与代码内容不会被猜测修改：

- 完整响应 JSON 外的一层 `json` Markdown 代码块（不从混杂说明文字中提取片段）。
- `arguments` 为对象，或内容为合法 JSON 对象的字符串。
- 紧凑 `{name, arguments}` 调用，或标准 `{type:"function", function:{name, arguments}, id:...}` 包装。网站提供的 call id 不作为客户端调用 ID 使用。

请求编号、工具选择、工具名、参数 schema 和单调用限制继续严格检查。语法不完整的 JSON、原始未转义换行、多段回复、重复键不自动修复，不执行部分修改。

| 新错误 code | 含义 |
|---|---|
| web_output_json | 完整网页输出不是合法 JSON；含行列位置，不含正文 |
| web_envelope | 缺少或多出了顶层协议字段 |
| web_request_identity | 回答未使用当前 request_id |
| web_call_count | 一轮输出了多个工具调用，或 tool_calls 不是数组 |
| web_unknown_tool | 返回了当前客户端没有提供的工具名 |
| web_arguments_json | 字符串形式的 arguments 不是合法 JSON |
| web_arguments_type | arguments 不是对象 |
| web_arguments_schema | 参数不满足工具定义；指出校验规则/字段路径及缺失必填字段，不打印参数值 |
| web_tool_choice | 不符合客户端的工具选择要求 |

如果读文件成功、修改时报错，不能仅凭旧的笼统报错认定是某一具体原因。升级后重启 model_endpoint.py，再用小型可丢弃示例测试，提供新的 code 和错误说明即可。不需要上传真实文件内容。原生编辑/撤回能力仍须实机确认。

## Invalid \\escape 的单次网页纠错

遇到 JSON 解码器明确报告的 `Invalid \\escape` 时，端点现在最多向**同一个网页会话**追加一次格式纠错请求。该请求包含原请求、校验错误和原始错误回答（作为引用数据），要求保留原意、工具选择及代码内容，只修正 JSON 序列化。

- 本地不直接替换反斜杠、不使用第二个模型，不将无效调用交给 Cursor。
- 纠错后的 request_id、工具名称、选择约束和参数 schema 仍完整校验。
- 截断、重复键、未知工具、schema 失败、连接中断不会触发该机制。
- 纠错仍失败就返回明确错误，不再进行第三次发送。同一 HTTP 请求的重试仍复用缓存。
- 纠错输入超出网站预算时返回 `web_repair_budget`，不截断、不发送纠错。
- 这会增加最多一次网页请求和等待时间，每次网页任务仍有独立等待上限。
- 网页模型可能在重写格式时改变意图或代码；schema 校验不能证明语义等价。因此仍需审阅 Cursor 实际 diff，不可把“格式合法”当作“修改正确”。不要开启高风险工具自动批准。

更新后仅需重启模型端点；旧的失败缓存随进程退出清除。先确认目标文件的当前状态，再用可丢弃示例测试。

## 0.4.0：协议 JSON 必须从代码块原文采集

此前要求网页输出“没有代码块的 JSON”，而插件读取渲染后的回答文字。标准 Markdown 会把普通段落中的部分反斜杠转义消耗掉，导致合法 JSON 变成 Invalid escape；还有可能使 Windows 路径被误解为合法的换行/制表符转义而不报错。这是已用 Markdown 渲染器复现的传输缺陷，不代表已证实每个用户错误都由此引起。

新版模型请求要求恰好一个 fenced json 代码块。Bridge 传递 `response_format=json_code_block`，插件只在当前非思考回答区域提取代码块原文；CodeMirror 使用完整文档属性。找不到块、有多个块或 CodeMirror 原文不可用时拒绝回退到普通段落文字。旧 MCP 普通问答仍按原方式取文本，不强制用户问答都使用 JSON。

**此次必须更新并重新加载插件，不是只重启模型端点：**

1. 停止 Bridge 和模型端点，拉取更新。
2. 运行 `python cursor-web/prepare_extension.py`，同步网站适配器。
3. 浏览器扩展管理页重新加载，确认 0.4.0，然后刷新 AI 网页。
4. 重启 Bridge，重新查询 session ID，再启动模型端点。
5. cursor-byok 配置/API Key 不变。用可丢弃文件验证路径、代码和原生 diff。

结果诊断 `extraction` 应为 `code_text`、`pre_text` 或 `codemirror_document`。若代码块原文本身仍含非法 JSON，单次格式纠错/严格校验仍生效；报错会附提取来源。网站 DOM 变化可能需要继续适配，不能将 Markdown 测试视作真实网站已验证。

### 新增 DOM/Markdown 回归测试

仅开发者运行这些测试需要 Node 开发依赖，正常使用模型端点不需要安装 npm 包：

```sh
npm ci --prefix cursor-web/tests
npm test --prefix cursor-web/tests
```
