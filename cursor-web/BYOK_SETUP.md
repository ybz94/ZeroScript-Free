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

## 0.4.1：整轮回答回退搜索与可诊断失败（2026-09-21）

实机反馈：DeepSeek 已把回答显示成 `json` 代码块，但插件仍报 `found 0`。原因是代码块可能不在插件原先只搜索的非思考 `.ds-markdown` 回答根节点内（网站 2026-08 重构了 Markdown 渲染器，旧 DOM 假设不再可靠）。

0.4.1 的行为：

1. 仍优先在回答根节点内找“恰好一个”代码块（保持原有严格范围）。
2. 只有根节点内**一个块都没有**时，才回退到整个回答轮次（`.ds-message`）搜索；排除网站思考区（DeepSeek `.ds-think-content`）和 ZeroScript 自身注入的 UI。
3. 代码块容器（如 DeepSeek `.md-code-block`）与其内部 `pre`/`code` 只计一次。
4. 仍拒绝：找不到块、多个块、CodeMirror 原文不可用。绝不回退到渲染段落。
5. 失败信息现在自带诊断，例如 `found 0 in answer_turn (roots=1 scope=answer_turn turn_blocks=0 thinking_blocks=0)`：可区分“整个回答里没有任何代码块”“块只在思考区”还是“有多个块”。端点解析错误也会附带该诊断。

**升级步骤（与 0.4.0 相同，只是版本变为 0.4.1）：**

1. 停止 Bridge 和模型端点，拉取更新。
2. 运行 `python cursor-web/prepare_extension.py`，同步网站适配器。
3. 浏览器扩展管理页重新加载，确认 **0.4.1**，然后刷新 AI 网页。
4. 重启 Bridge，重新查询 session ID，再启动模型端点。
5. cursor-byok 配置/API Key 不变。用可丢弃文件验证路径、代码和原生 diff。

验证新插件已加载：在 AI 网页按 `F12` 控制台输入
`document.documentElement.dataset.zsDsVer`，DeepSeek 页应显示 `2026-09-21_protocol-fallback`。

## 0.4.2：网站报错快速失败与可操作的 409（2026-09-21）

实机反馈（Arena Agent mode）：网站显示 `model channel not available` 这类错误时，没有生成正常回答，插件会一直等到 240 秒超时；期间端点里该任务未结束，再发任何新请求都得到 409 `Dedicated webpage is busy with another request`，页面看起来"卡死"。

0.4.2 的行为：

1. **网站报错快速失败**：等待回答期间，若页面出现可见错误提示（toast/alert，不含聊天内容本身）且持续约 3 秒、始终没有新回答，任务立即失败，错误信息带上网站原文，例如：
   `Webpage showed an error and produced no reply: model channel not available`
   不再空等 240 秒。回答一旦开始生成，之前的错误提示即被忽略。
2. **409 可操作**：忙碌报错现在说明已有任务运行了多久、单任务最长约 4.5 分钟，并明确"等待其结束，或重启 model_endpoint.py 清除后再试"。

**两条使用规则（重要）：**

- **任务执行期间不要手动操作专用网页**（不要在里面输入/发送/删除消息）。专用页在任务期间归 Cursor 使用，手动产生的回答会被当作任务结果，手动输入也会与新请求冲突。
- 遇到"busy"或任务卡住时，**重启 `model_endpoint.py` 即可清空进程内缓存**，然后重新 `--sessions` 绑定、重测。

升级步骤同 0.4.1（停止 Bridge/端点 → 拉取 → `prepare_extension.py` → 重新加载插件确认 **0.4.2** → 刷新网页 → 重启 Bridge → 重新绑定会话）。DeepSeek 页验证值变为 `2026-09-21_site-error-fastfail`。

**任务生命周期日志（端点终端）**：`model_endpoint.py` 现在会在终端打印每个专用页任务：

```text
[a1b2c3d4] task started on dedicated webpage
[a1b2c3d4] task completed after 47s        ← 或 task failed after 5s: Webpage showed an error and produced no reply: ...
[a1b2c3d4] reusing existing task for identical request
```

出现 409 "busy" 时看端点终端：如果上一条还在 `started` 之后没有 completed/failed，说明任务仍在等待网页（最长约 4.5 分钟）；等待期间**不要重发**，或 `Ctrl+C` 重启端点清除后再发。

## 0.4.3：残留输入草稿不再卡死专用页（2026-09-21）

此前若专用页输入框里有没发送的文字（手动测试残留，或上次"打了字但发送没成功"的残留），任务会在预检直接失败 `Composer contains a draft`，且之后每个任务都失败，直到手动清空——一个会永久卡住专用页的陷阱。

0.4.3：专用页上遇到残留草稿时**记录（diagnostics `composerDraftCleared`/`composerDraftLen`）并继续**，因为 `typeAndSend` 本来就会覆盖输入框内容。这样失败发送的残留不再阻塞后续任务。

**注意**：专用页在执行任务期间不要手动输入/发送——残留文字会被当作可清理项覆盖，手动消息也会被当作任务结果。升级步骤同上（确认 **0.4.3**）。

## 0.4.4：发送未被接受时快速失败，不再空等 240 秒（2026-09-21）

实机反馈（Arena）：消息**根本没发出去**，但 Cursor 一直不返回——因为三个网站的 `typeAndSend` 在"发不出去"时（发送按钮禁用、页面忙碌、仍在生成）是**静默返回而不报错**，插件误以为已发送，然后干等一个不会来的回复最长 240 秒。

0.4.4：发送后插件会确认**输入框是否被清空**（网站接受消息即清空输入框）。若约 5 秒后文字仍在，立即失败并给出可操作原因，例如：
`Message was not sent: 123 characters are still in the composer and the page shows a Stop button (it is still generating). The send button is probably disabled or the page is busy. ...`
不再空等 4 分钟。诊断新增 `sendConfirmed`（发送是否被接受）与 `leftoverLen`（输入框残留字符数）。

**遇到该报错时的处理**：到专用网页上 ① 点掉/等待"停止"结束任何进行中的生成；② 清空输入框；③ 确认发送按钮是亮的（可发新消息）；再重发任务。若页面状态混乱，刷新网页并重新 `--sessions` 绑定。

升级步骤同前（确认 **0.4.4**）。

## 0.4.5：按"是否出现新消息"判定发送，并记录输入框状态（2026-09-22）

实机反馈（Arena）：任务显示 running 50 多秒，但**输入框里没有文字、发送按钮是灰的、没有生成中**——说明文字根本没打进你看到的那个输入框（可能是 dispatch 发到了另一个标签页，或 `getEditor()` 选到了隐藏的 textarea，或写入被 React 丢弃）。0.4.4 的"看输入框是否残留文字"判断抓不住这种情况（输入框是空的，会被误判为"已发送"）。

0.4.5 改为用**"发送后是否出现一条新的用户消息"**作为判定（网站接受消息就会在对话里新增一条用户气泡）：

1. 发送前记录输入框状态：`editorFound`（是否找到 textarea）、`editorTag`、`editorVisible`（是否可见）、`editorPlaceholder`、`editorLenBefore`、`usersBefore`。
2. `typeAndSend` 后短暂轮询（最长约 8 秒）：出现新用户消息即确认成功；否则立即失败。
3. 失败信息区分两种：
   - `no new message appeared in the chat (the text did not land in the composer)` —— 文字没落进输入框（你遇到的情况）；
   - `N characters are still in the composer` —— 文字打进了但没发出去。
   并附带 `Composer: TEXTAREA (visible/HIDDEN) placeholder="..."` 帮助定位是否选错了元素。
4. 诊断新增 `sendConfirmed`、`usersBefore/usersAfter`、`editorLenAfter`、`generatingAfter/hardGeneratingAfter`、`phase=confirming_send`。

**遇到 `did not land in the composer` 时**：几乎总是"你看的标签页 ≠ 绑定的标签页"或页面处于拒绝输入的状态。请 ① 用 `--sessions` 核对绑定会话的 URL 与你正在看的标签页是否一致；② 刷新该页、重新绑定；③ 确认页面能正常手动发消息后再重测。升级步骤同前（确认 **0.4.5**）。

## 0.4.6：Arena 目标改为可见的 TipTap 输入框（2026-09-22）

实机定位（F12 调试）：当前 Arena 页面的**真实输入框是一个可见的 TipTap/ProseMirror `contenteditable` DIV**（class 含 `tiptap ProseMirror`，不在 `<form>` 里），而适配器原先 `getEditor()` 选的 `form textarea` 是一个**隐藏的遗留表单**——往它写字"成功"且被保留，但你看不到、也永远不会发送；真正的发送键（可见、`aria-label="Send message"`）在 TipTap 框旁边。

0.4.6 的 Arena 适配器改动：

1. **`getEditor()`**：优先返回**可见的 TipTap/ProseMirror contenteditable**（排除 `#zs-root` 与聊天列表内部），找不到才回退到 `form textarea`（旧 DOM）。
2. **`setTextareaValue()`**：对 contenteditable 用 `focus + 全选 + execCommand("insertText")` 写入（`.value` 对 DIV 无效）；textarea 仍用原生 setter。
3. 发送键沿用现有逻辑（可见 + `aria-label` 含 `send message`），无需改动。

**这要求重新加载插件**（改的是网站适配器）：停止 Bridge/端点 → 拉取 → `prepare_extension.py` → 重新加载扩展确认 **0.4.6** → 刷新网页 → 重启 Bridge → 重新绑定会话 → 重测。

**注意**：此改动针对"当前 Arena 用 TipTap 输入框"的 DOM。若网站回退到旧的 `form textarea` 输入框，适配器会自动回退，两条路径都保留。回复读取（`readAssistant` 等）依赖聊天列表 DOM；若发送成功但取不到回复，说明 Agent 模式的聊天结构也不同，需另行适配——先确认发送这一步是否已通。

## 0.4.7：放宽 Arena 输入框选择 + 等待挂载（2026-09-22）

0.4.6 上线后实机仍报 `Arena input box not found`（1s 快速失败）。原因是 0.4.6 的 `getEditor()` 多了一个"排除聊天列表（`ol.flex-col-reverse`）内元素"的过滤——Agent 模式下输入框可能就在该容器里，被误跳过；另外页面刚刷新时输入框可能还没挂载完。

0.4.7：
1. `getEditor()` 去掉聊天列表过滤、放宽为任意 `contenteditable`（按 `isContentEditable` 判定），仅靠"可见 + 非 `#zs-root` + class 含 `tiptap`/`ProseMirror`"定位输入框（该类名足够特异，不会误中聊天代码块）；仍回退 `form textarea`。
2. `typeAndSend()` 找不到输入框时**等待重试最多约 10 秒**（覆盖"输入框还没挂载完"），仍找不到才报错，且报错附带"页面可能没加载完 / 可能不是聊天页"的提示。

升级步骤同 0.4.6（确认 **0.4.7**）。若仍 `input box not found`，请在报错的页面按 BYOK_SETUP/聊天里给的 F12 诊断脚本确认页面上到底有没有可见的 TipTap 输入框。

## 0.4.8：Arena 永不写入隐藏的遗留输入框（2026-09-22）

实机反馈（0.4.7，Arena Agent 模式）：任务失败 `Message was not sent: 105399 characters are still in the composer. Composer: TEXTAREA (HIDDEN) placeholder="Ask anything…"`——即 **105,399 字符的完整载荷被写进了那个隐藏的遗留 `form textarea`**，一个字都没进你看到的 TipTap 输入框。

根因：0.4.7 的 `getEditor()` 回退"找不到可见 TipTap 就返回 `form textarea`"时**不检查可见性**。页面刚刷新时，这个隐藏框在 TipTap 挂载之前就已存在于 DOM 中，`getEditor()` 立刻返回它（非 null），于是 `typeAndSend` 里"等待挂载最多 10 秒"的重试**从未触发**；载荷写进隐藏框后，真正的发送按钮（绑定 TipTap 的状态，TipTap 为空）永不点亮，消息发不出去。

0.4.8 改动（arena.js）：

1. **`getEditor()` 只返回可见编辑器**：可见 TipTap/ProseMirror contenteditable 优先，其次**可见**的 `form textarea`（旧 DOM）；隐藏遗留框不再被返回。半加载页面会真正返回 null，挂载等待才有机会生效。
2. **挂载等待从约 10 秒延长到约 15 秒**（75×200ms），仍找不到才报 `Arena input box not found after 15s (…)`。
3. **新增截断校验**：写入后核对输入框实际字符数，少于写入量的 95% 立即失败 `Arena composer clamped the input: wrote N characters, composer holds M`——说明网站有比我们载荷更小的输入上限，需要缩小请求（防止把截断的、坏掉的 JSON 发出去）。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → `prepare_extension.py` → 重新加载扩展（确认 **0.4.8**；更可靠的核对方式：`--sessions` 输出里该会话的 `transportVersion` 应为 `0.4.8`，它是页面里正在运行的脚本自报的）→ 刷新专用页并**等输入框在页面上可见后再多等几秒（页面完全加载）** → 重启 Bridge → `--sessions` → `--session "ID"` 重测。

## 0.4.9：内容脚本与 provider 版本必须一致，否则拒绝发送（2026-09-22）

**为什么要这一版**：0.4.8 修好了"永不写隐藏输入框"，但你复测仍报 `Composer: TEXTAREA (HIDDEN) placeholder="Ask anything…"`。原因是**加载目录里实际跑的 `providers/arena.js` 还是 0.4.7 旧版**——`cursor-web/extension/providers/` 被 git 忽略，`git pull` **不会**更新它，只有 `prepare_extension.py` 会。而 `transportVersion` 是 `content.js`（受 git 跟踪、pull 会更新）自报的，所以会出现"内容脚本 0.4.8 + provider 0.4.7"的**混搭**：`transportVersion` 显示 0.4.8，但真正碰输入框的 arena.js 还是旧逻辑，把整段写进了隐藏框。

0.4.9 改动：

1. **每个 provider 自带 `version` 字段**（arena/deepseek/chatgpt 等 8 个），内容脚本把它一起上报。
2. **`--sessions` 现在同时显示 `transportVersion`（内容脚本）和 `providerVersion`（实际碰页面的 provider），并给出 `inSync` 布尔值**。两者不一致时一目了然。
3. **硬闸门**：dispatch 前先比对两者，不一致直接拒绝并给出可执行的修复指令（`git pull --ff-only` → `prepare_extension.py` → 重载扩展 → 刷新页面），不再让混搭的旧 provider 把字写进错误位置、事后报一句误导性的 composer 错误。

**核对方式升级**：`--sessions` 里该会话应同时满足 `transportVersion: "0.4.9"` 且 `providerVersion: "0.4.9"` 且 `inSync: true`。只要 `inSync` 是 `false` 或 `providerVersion` 不是 `0.4.9`，先别发任务——按上面三步把 provider 更新到位再测。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → **`prepare_extension.py`（关键，pull 不会更新 providers 目录）** → 重新加载扩展 → 刷新专用页并等输入框可见后再多等几秒 → 重启 Bridge → `--sessions`（确认 `inSync: true` 且两版本都是 0.4.9）→ `--session "ID"` 重测。

## 0.4.10：JSON 回复完成即返回，不再被页面"是否完成"提示卡死（2026-09-22）

**为什么要这一版**：0.4.9 之后发送与回复链路已通（网页确实返回了 JSON），但 Cursor 长时间收不到结果。原因：Arena Agent 模式**每答完一次都会弹出"任务完成？是 / 否 / 继续"的三选项**，页面随即进入"等你选择"的非空闲状态；扩展的完成判定要求"页面空闲 + 文本稳定 4 秒"，于是拿着已经完整的 JSON 回复干等到 240 秒超时。另外手动点了这三个选项还会触发站点自身的 bug 把页面卡死。

0.4.10 改动：

1. **可解析的协议 JSON = 完成**：回复的围栏 JSON 块一旦稳定（4 秒不再变化）且 `JSON.parse` 成功，立即判定回复完成并返回，不再依赖页面空闲状态（Arena 的三选项、或其他任何回复后 UI 都挡不住返回）。诊断字段 `finalReason: "complete_json"` / `"idle"` 标明走了哪条判定。
2. **任务期间动过专用页 → 快速失败**：等待回复期间若出现额外的用户消息（手动输入，或点了"是/否/继续"），数秒内报 `Dedicated page was operated during the task (…)` 并给出修复步骤（刷新页面、重新绑定会话、重试），不再空等 240 秒。
3. **`promptLen` 进诊断**：每次任务把发出的载荷字符数写进 diagnostics，方便核对"你好"为什么在网页里很长（见下）。

**关于"我就发了你好，网页里却特别长"**：这是架构设计使然——网页 AI 直接作为 Cursor 的模型源，**完整上下文**（协议说明 + 全部工具定义 + 对话历史）原样转发，没有第二个模型做转述。工具定义（文件读写、终端等）本身就占几千到几万字符，属正常现象，不是 bug。

**重要规则**：任务运行期间**绝不要手动操作专用页**——包括点"是/否/继续"、手动发送、编辑输入框。这些操作要么触发站点自身的 `getComputedStyle` 卡死，要么让任务快速失败（0.4.10 会明确报错）。等 Cursor 拿到结果后再做任何手动操作。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → `prepare_extension.py` → 重新加载扩展 → 刷新专用页并等输入框可见后再多等几秒 → 重启 Bridge → `--sessions`（确认 `transportVersion: "0.4.10"`、`providerVersion: "0.4.10"`、`inSync: true`）→ `--session "ID"` 重测。

## 0.4.11：分块写入输入框，长载荷不再一次插完导致网页卡死（2026-09-22）

**为什么要这一版**：0.4.10 复测时网页直接卡死、毫无反应，怀疑是发送文本太长所致——机制上成立：旧代码把**整个载荷（5 万–11.8 万字符）用一次 `execCommand("insertText")` 插进 TipTap 输入框**，网站自己的 onChange 处理器（之前就已发现它有 `getComputedStyle` bug）被一次性塞进 10 万字符，React 状态更新/字符计数在同一个同步事件里跑完，标签页直接冻结。

0.4.11 改动：

1. **分块写入**：输入框写入改为每块 8,000 字符、块间停 60ms（对网站而言就像快速打字），每次写入前把光标重新锚定到末尾（防止网站重渲染挪走光标）。每块的 onChange 都是小事件，不再产生一次性的巨型同步负载。
2. **写入中途截断检测**：每块写完后立即核对输入框实际字符数，少于已写入量的 80% 立即失败 `Arena composer clamped the input mid-write: …`——网站若有比我们预算更小的输入上限，**绝不会再发出被截断的坏 JSON**。
3. **端点日志带载荷尺寸**：任务开始时打印 `task started on dedicated webpage (prompt N chars, M lines)`——"你好"到底发了多少字符，日志里一眼可见（长是正常的：完整上下文+工具定义原样转发，见 0.4.10 说明）。

**如果页面已经卡死**：卡死无法自愈，关掉该标签页重新打开（或强制刷新）再继续；刷新后重新 `--sessions` → `--session "ID"` 绑定。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → `prepare_extension.py` → 重新加载扩展 → 刷新专用页并等输入框可见后再多等几秒 → 重启 Bridge → `--sessions`（确认 `transportVersion: "0.4.11"`、`providerVersion: "0.4.11"`、`inSync: true`）→ `--session "ID"` 重测。

## 0.4.12：卡死定位面包屑 + 回复读取限速（2026-09-22）

**为什么要这一版**：0.4.11 的分块写入后网页仍卡死，说明卡死点还没锁定（可能在写入阶段，可能在点发送/提交管线，也可能在生成回复阶段，甚至可能是我们自己的读取循环）。0.4.12 不猜——直接加阶段标记把卡死点逼出来。

0.4.12 改动：

1. **控制台面包屑**：任务全链路每到一个阶段就在页面 DevTools 控制台打印一行 `[zs] …`：
   `task start（载荷字符数）→ arena: writing N chars（分几块）→ chunk i/N → write done → clicking send button → send confirmed → send confirmed, waiting for reply → reply complete（或 task failed）`。
   **页面卡死时，控制台里最后一行 `[zs]` 就是卡死发生的阶段**——把这行（和旁边的红色报错，如有）发给我即可定位。
2. **回复读取限速**：等待回复期间，页面每次 DOM 变化（流式生成时每个 token 都算一次）都会触发一次"读整个对话"——而对话里现在含我们 5 万+ 字符的用户消息，高频全量读取可能占满标签页 CPU。现限制为**每 250ms 最多一次完整读取**，排除我们自己的代码作为卡死嫌疑。

**复测方法（重要）**：升级 0.4.12 后，**先在专用页打开 DevTools 控制台（F12 → Console）**，再从 Cursor 发任务。卡死后别关页面，把控制台里**最后一行 `[zs]`** 和任何红色报错截图/复制给我。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → `prepare_extension.py` → 重新加载扩展 → 刷新专用页并等输入框可见后再多等几秒 → 重启 Bridge → `--sessions`（确认 `transportVersion: "0.4.12"`、`providerVersion: "0.4.12"`、`inSync: true`）→ `--session "ID"` 重测。

## 0.4.13：发送确认窗口 8s→30s（大消息不再误判失败）+ 大载荷警告（2026-09-22）

**为什么要这一版**：0.4.12 面包屑实机确认：分块写入与发送**全部成功**（`send confirmed (composer cleared)`——网站接受了发送），但 112,912 字符的用户消息渲染进聊天列表超过 8 秒，发送确认窗口（8s）到期后误报 `Message was not sent`。同时暴露出对话历史膨胀问题：一个"你好"的载荷高达 11.3 万字符（历史里堆着之前失败大任务的完整 prompt），已贴近 118,000 预算上限；网站处理这个体量的消息/后续点击（点"继续/否"发起新一轮）会卡死标签页——**这是站点侧对超大对话的处理能力问题**。

0.4.13 改动：

1. **发送确认窗口 8s → 30s**（150×200ms）：输入框清空已是网站"接受发送"的信号，超大消息渲染慢不再误判失败。
2. **报错措辞区分两种情况**：输入框曾持有内容且已清空（发送已被接受）→ 报 `send WAS accepted; the page may process very large messages slowly`；文字从未落进输入框 → 保持原措辞。
3. **端点大载荷警告**：prompt 超过 90,000 字符时端点控制台打印 `WARNING: large prompt (…) start a FRESH chat`。

**重要操作规则（针对对话膨胀）**：
- **看到端点打印 `WARNING: large prompt` 时，开新对话**：Cursor 里新建会话 + 专用页新开聊天（重新绑定会话），让历史归零。历史里的每个大 prompt/大工具结果都会随每次请求整体重发，越堆越大，最终把网站自己卡死。
- **永远不要点"是/否/继续"三选项**（任务期间或期间外都尽量别点）：点"继续/否"会让网站带着完整历史发起新一轮，11 万字符量级会直接卡死标签页。
- 正常的小任务（新对话里的普通问答/单文件操作）载荷通常在 2–5 万字符，网站可以正常处理。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → `prepare_extension.py` → 重新加载扩展 → **开新对话**（Cursor 新会话 + 专用页新聊天）→ 刷新并等输入框可见 → 重启 Bridge → `--sessions`（确认 `transportVersion: "0.4.13"`、`providerVersion: "0.4.13"`、`inSync: true`）→ `--session "ID"` 重测。

## 0.4.14：大载荷警告带构成明细 + 卡死提示（2026-09-22）

**背景（0.4.13 实机数据）**：新对话下"你好"载荷仍有 89,762 字符；网站接受发送（输入框清空）但 30 秒内不渲染用户消息、标签页卡死。说明该 Cursor 对话的**基线载荷**（系统提示+工具定义+历史）已达 9 万字符量级，超出 Arena 网站提交管线的处理能力——这是站点侧容量问题，扩展侧已无更多可修。

0.4.14 改动：

1. **端点大载荷警告带构成明细**：prompt > 90,000 字符时打印各部分 UTF-16 字符数 `{"system": N, "user": N, "assistant": N, "tools": N}`——一眼看出是"系统+工具的固定基线"（开新对话也缩不掉）还是"历史累积"（开新对话可解决）。
2. **报错措辞补充**：发送已被接受但 30s 无消息时，提示"页面可能已卡死——刷新专用页并重新绑定会话后再试"。

**下一步判断（按端点明细决定）**：
- `system`+`tools` 占大头（>6 万）→ 这是 Cursor Agent 模式的固定成本，**Arena 无法服务这个 Cursor 项目**；可选：换 DeepSeek 专用页（预算 16 万、实测链路已通）、或在 Cursor 用更轻的模式、或接受网页 AI 只拿到裁剪后的上下文（需另行确认，会降低决策质量）。
- `user`/`assistant` 历史占大头 → 确认真的开了**Cursor 侧新会话**（载荷来自 Cursor 的对话，只刷新网页页面对载荷无影响）。
- 无论哪种，先跑**小载荷对照实验**确认网站能力下限：端点+Bridge 运行、页面绑定后执行
  `curl -s http://127.0.0.1:17615/v1/chat/completions -H "Authorization: Bearer $(cat cursor-web/.endpoint-token)" -H "Content-Type: application/json" -d '{"model":"web-ai","messages":[{"role":"user","content":"hi"}]}'`
  成功 = 网站本身可用、只是扛不住大消息；卡死 = 该站点/会话另有问题，需重开聊天或放弃该网页。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → `prepare_extension.py` → 重新加载扩展 → 刷新专用页 → 重启 Bridge → `--sessions`（确认 `transportVersion: "0.4.14"`、`providerVersion: "0.4.14"`、`inSync: true`）→ 先跑上面的 curl 对照实验 → 再按明细决定下一步。

## 0.4.15：发送确认改用三重证据，不再依赖用户消息计数（2026-09-22）

**为什么要这一版（实机决定性证据）**：2KB 载荷、无卡死、网站接受发送（输入框清空）、**网页已返回正确的协议 JSON**（`{"request_id":"fbbe4d43…","content":"Hi! How can I help?","tool_calls":[]}`）——但任务仍误报 `Message was not sent: no new message appeared in the chat`。原因：发送确认只认"用户消息计数 +1"，而**新 Agent 聊天的用户轮 DOM 结构不匹配计数器的过滤条件**（`mx-auto` + `.prose` + `justify-end`），计数永远不涨 → 30 秒后误判。

0.4.15 改动：

1. **provider 自报发送结果**：arena.js `typeAndSend` 现在返回 `{sent, landedLen}`（它亲眼看着输入框清空、并验证过写入字符数）。其他 provider 暂不返回——自动回退到旧的计数判定。
2. **三重独立证据判定发送成功**（满足其一即确认）：① 用户消息计数 +1（老信号）；② provider 确认（`sent: true`）；③ provider 验证过写入且输入框已被清空（只有网站消费了输入才会清空）。诊断新增 `sendConfirmedBy: user_turn / provider / composer_cleared`。
3. **保留快速失败**：文字滞留输入框 ~1 秒内报错；provider 验证"写入不在"立即报错（不傻等）。
4. 顺带清理 0.4.14 版本字符串里误入的反斜杠（JS 运行时无影响，但属脏数据）。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → `prepare_extension.py` → 重新加载扩展 → 刷新专用页 → 重启 Bridge → `--sessions`（确认 `transportVersion: "0.4.15"`、`providerVersion: "0.4.15"`、`inSync: true`）→ 重测（curl 或 Cursor）。

## 0.4.16：回复读取加 request_id 标记兜底（2026-09-22）

**为什么要这一版**：0.4.15 下"网页回复了但 Cursor 没有回复"——发送确认已通过（三重证据），断点移到**回复读取**：`readAssistant()` 用的 DOM 过滤器和用户消息计数是同一套（`mx-auto` + `.prose` + 类名判定），新 Agent 聊天的回复轮同样"看不见"→ 扩展拿着页面上明明可见的 JSON 干等 240 秒超时。

0.4.16 改动（content.js）：

1. **request_id 标记兜底**：协议 JSON 块携带本次发送的**唯一 request_id**。若过滤器 4 秒以上检测不到新回复，直接扫描页面所有代码块（`pre/code/.cm-content/.md-code-block`），找到"包含该 id、可 `JSON.parse`、顶层键恰好是 `request_id/content/tool_calls`"的块，稳定 4 秒即判定完成返回。不依赖任何轮次 DOM 结构。
2. **防误认**：用户消息里也含同一个 request_id（在 CURRENT_REQUEST 信封里）——信封顶层键是 `request_id/messages/tools/tool_choice`，与协议对象三键不符，被键校验拒绝。
3. 诊断：`extraction: "marker_fallback"`、`finalReason: "complete_json_fallback"`、`fallbackRead: true`，控制台打印 `[zs] reply turn invisible to provider filter - reading via request-id marker fallback`。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → `prepare_extension.py` → 重新加载扩展 → 刷新专用页 → 重启 Bridge → `--sessions`（确认 `transportVersion: "0.4.16"`、`providerVersion: "0.4.16"`、`inSync: true`）→ 重测。

## 0.4.17：任务成功后自动应答"此任务成功了吗?"弹窗（2026-09-22）

**为什么要这一版**：0.4.16 已实现 Cursor 收到网页回复（验收测试 A 跑通），但 Arena 每次返回结果后会弹出"此任务成功了吗?"（是 / 否 / 继续工作），不点就无法进行下一次对话。

0.4.17 改动：

1. **自动点"是"**：回复完整读取并验证之后（此时结果已在手，应答不会污染本次结果），扩展在最多 10 秒内等待弹窗出现并点击"是"（任务成功）——页面随即恢复干净，下一次任务无需任何手动操作。弹窗稍晚出现也能等到。
2. **定位方式**（arena.js `clearFollowupPrompt`）：找文本恰为"是"的按钮，向上 4 层祖先内必须含"成功了吗"字样（防误点页面其他"是"按钮），隐藏（不可见）的按钮不点。
3. 诊断：`diagnostics.followupCleared`（true = 已自动应答），控制台 `[zs] post-reply follow-up prompt answered (是)`。
4. 任务失败时**不**点击弹窗（状态未知，留给你人工判断）。

**注意**：任务进行中弹窗若已出现，扩展只读不碰；点击只发生在"回复已确认完整"之后。手动点继续/否仍会触发 0.4.10 的手动操作保护（任务立即失败并提示）。

升级步骤：停止端点（Ctrl+C）和 Bridge → `git pull --ff-only` → `prepare_extension.py` → 重新加载扩展 → 刷新专用页 → 重启 Bridge → `--sessions`（确认 `transportVersion: "0.4.17"`、`providerVersion: "0.4.17"`、`inSync: true`）→ 重测。预期：Cursor 收到回复 → 几秒后控制台出现 `post-reply follow-up prompt answered (是)` → 直接发下一条对话，无需点弹窗。

## 0.4.18：适配其它网页 AI（GLM / Kimi K3 / Qwen / Gemini / Meta / ChatGPT）

**结论：可以。** 扩展原本就带 8 个站点适配器（来自主 ZeroScript 扩展），0.4.15–0.4.18 的三重发送确认 + request_id 标记兜底 + 弹窗自动应答都是**与站点无关**的——任何适配器只要"能写进输入框、能点发送、回复带围栏 JSON 块"就能工作。0.4.18 把其余 5 个站点接入了本流水线：

| provider | 站点 | 状态 | 输入预算 (UTF-16) |
|---|---|---|---|
| deepseek | chat.deepseek.com | 本流水线实机验证（端到端） | 160000 |
| arena | arena.ai | 本流水线实机验证（端到端 + 弹窗自动应答） | 118000 |
| glm | chat.z.ai | 主扩展 DOM 验证 2026-06；本流水线待实机 | 100000（保守起点） |
| kimi | www.kimi.ai（含 K3，DOM 验证 2026-07-30） | 主扩展 DOM 验证；本流水线待实机 | 100000（保守起点） |
| qwen | chat.qwen.ai | 主扩展适配器；本流水线待实机 | 100000（保守起点） |
| gemini | gemini.google.com | 主扩展适配器；本流水线待实机 | 100000（保守起点） |
| meta | www.meta.ai | 主扩展适配器；本流水线待实机 | 100000（保守起点） |
| chatgpt | chatgpt.com | 主扩展适配器（本流水线专用页未常用） | 120000 / 600 行 |

0.4.18 改动：

1. **manifest**：cursor-web 扩展注入范围加入 chat.z.ai、kimi.ai、gemini.google.com、meta.ai、chat.qwen.ai（qwen 另注入 MAIN-world 的 `qwen-net.js` 网络钩子，与主扩展一致）。
2. **全部 7 个旧适配器的 `typeAndSend` 升级到 0.4.15 接口**：返回 `{sent, landedLen}`（发送后最多等 3 秒确认"输入框清空或开始生成"，作为发送被接受的直接证据）——即使某站点的用户消息 DOM 计数不可靠，三重证据里的 provider 证据 + 写长证据仍然有效。
3. **GLM/Kimi `readAssistant` 加 `thinkingSel`**：推理过程（Thought Process / K3 Thinking）里起草的代码块不会被误当成协议 JSON 块。
4. **提取失败救援**（content.js）：作用域块搜索失败（如"找到 2 个代码块"）时，退回按 request_id 标记扫描，再退回渲染文本——协议 JSON 只要被围栏包裹，任何站点的意外 DOM 都有路可读。
5. **预算表**（input_limits.py + content.js 镜像）：5 个新站点各 100000 UTF-16 单位的保守起点；实机确认大 payload 完整落地后再上调。

**换站点使用步骤**（以 GLM 为例）：
1. 专用标签页打开 chat.z.ai，登录，**开一个全新会话**
2. `git pull` 后 `python cursor-web\prepare_extension.py`（首次接入新站点必做）→ 重载扩展 → 刷新专用页
3. 重启 Bridge → `--sessions`：确认新会话的 provider 名（`glm`）、`0.4.18`/`0.4.18`、`inSync: true`
4. 用新 session ID 重启 model_endpoint.py
5. 先跑 2KB 控制测试（curl 或 Cursor 发"你好"），再跑真实 Cursor 任务

**注意**：
- 一次只有一个专用页/一个会话在工作；切换网站 = 换专用标签页 + 重新绑定会话
- 新站点的输入预算是保守起点：若真实任务被 413 拒绝，先试"新会话"（去掉历史），仍不行再按需上调该站预算
- 某站点若把输入截断，0.4.11 的中途截断报错会直接指出（wrote N, holds M），按提示调预算
- 站点的"任务成功了吗?"类后续弹窗目前只有 Arena 有自动应答；其它站点若出现同类弹窗且挡住下一次对话，告诉我站点名，按同样方式加

## 0.4.19：qwen / gemini / meta / chatgpt 逐项适配完成（2026-09-22）

在 0.4.18 通用接入之上，对四个站点做了逐项接口审计与补齐（8 个适配器现在全部实现 content.js 依赖的完整接口）：

1. **`errorText` 站点报错快速失败 ×5**（glm/kimi/qwen/gemini/meta 补齐）：读取可见的 toast/alert 报错区（8–600 字符、排除模型内容）——网页已拒绝请求时几秒内失败并给出原文，而不是空等 240 秒。
2. **协议块搜索作用域补齐**：
   - gemini `readAssistant`：`replyRoots` = `message-content`（回答体），`model-thoughts` 是它的兄弟元素，思考中的代码块天然出局
   - qwen `readAssistant`：`replyRoots` = `.response-message-content`
   - meta `readAssistant`：`thinkingSel` = `[data-testid="thinking-status"],[data-testid="subagent-cot-list"]`（Réflexion 模式的推理代码块不计入协议块搜索）
3. **审计确认**：`userCount`/`lastAssistantId`/`isGenerating`/`isHardGenerating`/`scanError`/`attachImages`/`installSendHooks`/`findToolBlockSpot`/`turnHalted` 等接口 8/8 齐备；gemini/kimi 无 `lastAssistantId`（非虚拟化列表，item 身份 + 文本变化足以判新轮，content.js 已做可选处理）。

**四站使用须知**（账号/网络要求不同）：

| 站点 | 要求 | 备注 |
|---|---|---|
| chat.qwen.ai | Qwen 国际版账号 | 适配的是**国际版** chat.qwen.ai；国内版 tongyi.aliyun.com DOM 不同，未覆盖 |
| gemini.google.com | Google 账号（需可访问） | 思考（model-thoughts）已排除 |
| www.meta.ai | Meta 账号（需可访问） | 适配器强制 Réflexion（think_hard）模式，回复质量更好、更慢 |
| chatgpt.com | ChatGPT 账号（需可访问） | 输入预算 120000 UTF-16 / **600 行**（ProseMirror 性能限制，实机验证过） |

升级步骤同 0.4.18：`git pull` → `prepare_extension.py` → 重载扩展 → 刷新专用页 → 重启 Bridge → `--sessions`（`0.4.19`/`0.4.19`/`inSync: true`）→ 换站点开新会话 → 2KB 控制 → 真实任务。

## 0.4.20：一键启动 + 弹窗美化（站点跳转）+ 回复跟随用户语言（2026-09-22）

### 1. 一键启动（解决"本地启动繁琐"）

原来 3 步手动：`bridge.py` → `--sessions` 抄 ID → `--session <ID>`。现在**双击 `cursor-web\start.bat`**（或 `python cursor-web\launcher.py`）：

1. 自动找 Python（py 启动器 → PATH → 标准安装目录；跳过商店桩）
2. 缺依赖自动 `pip install -r requirements.txt`
3. 自动跑 `prepare_extension.py`（刷新扩展文件）
4. 启动 Bridge（已运行则直接复用，不重复启动）
5. 轮询等待专用页会话（90 秒窗口，浏览器里打开聊天页并刷新即可）
6. 唯一会话自动绑定；多个会话列出来让你选序号
7. 启动端点 → 显示 `▶ 就绪：Cursor → http://127.0.0.1:17615/v1`
8. **Ctrl+C 一键停掉 Bridge + 端点**

版本不同步 / 会话 busy 会直接打 ⚠ 警告。手动三步法仍然可用（`bridge.py` + `model_endpoint.py`），两者不冲突。

### 2. 弹窗美化 + 支持的 AI 网站点击跳转

重做了扩展弹窗（popup.html/js + background `snapshot` API）：

- 顶部：连接状态点（绿=已连 Bridge）+ 版本号
- **专用页会话**卡片：provider 名、站点、`同步/版本不同步` 徽章、busy/后台状态（2 秒自动刷新）
- **支持的 AI 网站**列表（点击新开标签页）：DeepSeek / GLM·Z.ai / Kimi·K3 / Qwen通义 / Gemini / Meta AI / ChatGPT / Arena，各带 provider ID
- 设置（令牌/端口）收进折叠区
- 深色模式自适应

### 3. 回复跟随用户语言（合并自 01a0a947 分支 83e0f69）

协议提示词是全英文的，会把网页模型带偏成英文回复。现在：

- **cursor-web 管道**（content.js `withLangRule`）：发送时读浏览器语言——`zh-*` 浏览器追加"content 字段用简体中文"指令，其它语言追加"用用户所用语言回复"；**request_id/JSON 键/工具名/代码/路径永不翻译**；payload 已顶格时自动跳过该指令（绝不超预算）
- **主 ZeroScript 扩展**（core/config.js + core/main.js）：移植 01a0a947 分支 83e0f69 的 `userLang` 系统提示规则（zh 浏览器 → 显式简体中文指令）

说明：01a0a947 分支的其它提交（去 Roblox / 通用 MCP 模式 / Streamable HTTP 桥）属于旧 agent 循环架构，本分支的 cursor-web 直连管道不使用 core/ 那套，未合并。

## 0.4.21：启动器依赖检查修复（2026-09-22）

**实机问题**：start.bat 走到第 4 步时 `model_endpoint.py` 报 `No module named 'jsonschema'`。原因：启动器原来只硬编码检查 3 个模块（websockets/httpx/uvicorn），全在就跳过安装——但 `jsonschema`（工具 schema 校验）等其它依赖可能缺。

修复：

1. **依赖检查改为读 `requirements.txt` 全量核对**（mcp/websockets/starlette/uvicorn/jsonschema/referencing），缺任何一个就 `pip install -r requirements.txt`，装完再核对一遍
2. `requirements.txt` 显式补 `referencing`（model_endpoint.py 直接 import，此前只靠 jsonschema 间接带入）
3. 启动第一行显示 **Python 版本 + 完整路径**——"手动跑 model_endpoint.py 正常、start.bat 不正常"基本都是机器上有第二个 Python（`py -3` 解析到了另一个安装）；对照这行就能定位
4. 端点立即退出的报错改为指向真实原因（上方 traceback：缺模块 → pip install；端口占用 → 任务管理器）

**如果你刚才也遇到同样报错**：`git pull` 后重新双击 start.bat 即可（会自动装缺的依赖）。若仍报缺模块，在 cmd 里手动跑一次 `py -3 -m pip install -r cursor-web\requirements.txt` 再试。

## 0.4.22：chatglm.cn 报错明确化（2026-09-22）

**实机问题**：用国内版 **chatglm.cn（智谱清言）** 当专用页 → Cursor 报 `Could not establish connection. Receiving end does not exist`。原因：GLM 适配器针对的是**国际版 chat.z.ai**（2026-06 实机验证的 Svelte DOM），chatglm.cn 是另一套站点，扩展代码不会注入 → 消息发到一个没有监听者的标签页。

0.4.22 改动：

1. **错误明确化**（background.js）：该 Chrome 内部错误现在翻译为可操作指引——"目标标签页里没有扩展内容脚本（该网址不是受支持的站点…）注意：GLM 适配器支持的是国际版 chat.z.ai，不是国内 chatglm.cn…"
2. **弹窗提示**：站点列表下注明"国内版 chatglm.cn（智谱清言）暂不支持"。

**两条路**：
- **立即可用**：专用标签页改用 **https://chat.z.ai**（Z.ai 国际版，需国际版账号、需可访问）→ 刷新 → 重新绑定 → 2KB 控制
- **要国内版 chatglm.cn**：发我一个 DOM 快照，我按真实结构写适配器（方法见本消息 / 测试记录）

## 0.4.23：桌面软件 — 一个程序 = 整个项目（2026-09-22）

**新入口：`cursor-web/app.py`（桌面控制中心）** —— Bridge、模型端点、控制界面**全部在同一个进程**里，一个窗口管理所有东西：

- **窗口里有什么**：
  - 状态条：Bridge / 端点 / 专用页会话 / 绑定 四个状态点 + 运行时长
  - **设置向导 5 步**：① 加载扩展（带复制文件夹路径按钮 + "我已加载"打勾）② 配对令牌（令牌直接展示 + 一键复制）③ 打开专用页（8 个站点卡片点击新开）④ 绑定会话 ⑤ 开始用 Cursor（端点地址/模型名/密钥一键复制）
  - **专用页会话**卡片：版本同步徽章、busy 状态、**"绑定到此会话"一键重绑**（页面刷新导致 ID 变化时不用再开新程序）
  - **任务日志**：每个任务的 完成/失败/进行中 + 报错摘录
  - **停止按钮**；关窗口 = 全部干净停止，无残留进程
- **界面（双通道，保证有窗）**：默认先开**原生窗口**（Windows 系统自带 WebView2，无需额外安装）；若 15 秒内没渲染出来（WebView2 引擎缺失/损坏时才会发生），程序**自动**改用系统 Edge/Chrome 以**应用窗口**打开同一个界面（无地址栏、无标签页，效果等同桌面窗口），并关闭坏窗口——日志里会写明走了哪条路。深色/浅色自适应。命令行可选：`--display auto`（默认）/ `--display native`（只试原生窗口）/ `--display browser`（直接用最稳妥的系统应用窗口）
- **源码运行**：双击 `start.bat`（或 `python app.py`）——不再需要分别开 bridge / --sessions / --session
- **打包 exe（一次）**：双击 **`build_exe.bat`** → 自动装打包依赖 → 生成 **`dist\CursorWebAssistant.exe`**（单文件，PyInstaller onefile）。以后**只需双击这个 exe**，不需要 Python 环境。首次运行会在 exe 同目录生成 `extension\`（扩展文件）和两个令牌文件。

**重新绑定不再需要重启**：端点的会话绑定改成了可变引用（`model_endpoint.create_app` 接受 callable），窗口里点"绑定"立即生效。

**物理上装不进 exe 的两样**（平台安全，向导里带路）：
1. **Chrome 扩展**：浏览器规定必须由用户手动"加载已解压的扩展"（向导第 1 步，仅一次；exe 运行时自动把扩展文件释放到 exe 同目录 `extension\`）
2. **cursor-byok**：你的 Go 适配器（窗口"Cursor 连接"卡片给出要填的三项：端点地址、模型名 web-ai、端点密钥，各带复制按钮）

**注意**：exe 与 git 仓库分离——`git pull` 拿到新代码后重新双击一次 `build_exe.bat` 即可。旧的手动三步法（bridge.py + --sessions + --session）与 launcher.py 仍然可用。
