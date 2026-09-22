# 自动化测试结果

日期：2026-09-19

## 环境

- Linux 沙箱，Python 3.11、Node.js 22.22.3。
- 独立虚拟环境，mcp 1.30.0、websockets 15.0.1。
- 未安装 Cursor 桌面客户端或 Chromium，未使用真实网站登录会话。

## 最终结果

| 类别 | 数量 | 结果 |
|---|---:|---|
| Python Bridge/MCP 集成测试 | 14 | 全部通过 |
| JavaScript 扩展逻辑模拟测试 | 15 | 全部通过 |
| Python 编译检查 | 新模块全部 Python 文件 | 通过 |
| JavaScript 语法检查 | 独立扩展及复制的网站适配器 | 通过 |
| 扩展打包准备、Manifest 引用文件检查 | 全部引用 | 通过 |
| Git 补丁空白检查 | 本次修改 | 通过 |

### Python 测试范围

真实本机 WebSocket 连接：令牌生成/权限/持久性、错误令牌、角色校验、恶意网页 Origin 拒绝、扩展 Origin 接受、第二浏览器拒绝、会话收发、Unicode 回答、重复结果忽略、忙碌会话、无效输入、未知任务、伪造结果拒绝、浏览器断开、任务过期和迟到结果。

MCP 使用真实 SDK stdio 客户端启动独立 Python 子进程，执行 initialize、tools/list 和三个工具，打通 MCP → 真实 Bridge → 模拟浏览器 → Bridge → MCP 的请求结果闭环。浏览器 AI 回答是测试桩，不是真实网站回答。

### JavaScript 测试范围

在 Node VM 中执行实际扩展脚本，模拟 chrome API、网站适配器和时间：新回答捕获、后台页面拒绝、忙碌状态、未发送草稿保护、发送失败、截断、人工停止、超时、重复提交、失效绑定、运行中导航、超长回答、指定标签页结果验证、标签页关闭及刷新后的会话清理。

## 测试发现并修复

第一次 JS 测试：12 项通过、3 项失败。修复后 15 项全部通过。

1. **运行中切换网页会话后，旧会话 ID 仍然有效**：检测到非预期会话变化时，清空采集文本并更换绑定 ID，阻止后续请求沿用旧绑定。
2. **超过 250,000 字符的回答被静默裁剪且标记完成**：现在返回明确大小限制错误；裁剪部分不再被标记为完整答案。
3. **页面刷新后旧会话仍出现在列表中**：同一标签页注册新会话时删除旧会话，对原会话未完成任务返回失效错误。

另增加旧 WebSocket 消息防护，防止已替换连接的消息影响新连接状态。

## 无法由本次测试保证的部分

- Cursor 桌面客户端实际加载 MCP、原生 Diff、高亮、确认、文件列表及撤回。
- 三个网站当前 DOM、登录、验证码、限流、浏览器前后台节流行为。
- Windows/macOS 实机、浏览器扩展真实加载和长时间运行稳定性。
- 实际模型是否遵循项目规则、是否正确理解代码建议。

因此结论是 **29 项自动化测试通过，已修复已发现问题**，不是“真实 Cursor 和所有网站场景已保证无问题”。上线前仍需按 README 的手动验收流程在本地完成联调。

## 复现

```sh
.venv/bin/python -m unittest discover -s cursor-web/tests -v
node --test cursor-web/tests/extension.test.cjs
.venv/bin/python cursor-web/prepare_extension.py
.venv/bin/python -m compileall -q cursor-web
find cursor-web/extension -name '*.js' -print0 | xargs -0 -n1 node --check
git diff --check
```

## 0.2.0 后台传输修改复测（2026-09-21）

- Python 集成测试：14/14 通过。
- 扩展 VM 模拟测试：17/17 通过，共 31 项。
- 删除旧的“隐藏页面必须拒绝”测试；新增“发送前隐藏仍成功”“发送后隐藏仍成功”“后台无回答超时且不重发”3 项。
- 取消主动隐藏状态拒绝；用 MutationObserver + 定时兜底等待网页变化；增加连续空闲完成判断、可见性诊断与插件版本标记。
- 会话心跳过期窗口由 45 秒延长至 180 秒，减少后台定时器节流造成的误清理；这不能防止浏览器真正丢弃页面。
- 本次测试仍是网页适配器/浏览器 API 模拟，不是实际网站后台成功证明。尚未验证 Windows Chrome/Edge 的最小化、冻结或真实登录状态。请按照 README 的三组对照测试验证。

## 网页模型端点测试（2026-09-21）

- Python 测试共 29 项通过：原 Bridge/MCP 14 项 + 新端点 HTTP/协议 14 项 + 真实 HTTP/Bridge 工具闭环 1 项。
- 扩展 JavaScript 模拟测试 17 项通过，总计 **46 项自动化测试**。
- 新增覆盖：API Key 与 Origin 校验、模型列表、非流式及 SSE 回答、tool_calls 转换、工具结果回传、JSON/工具参数/schema/调用身份校验、格式失败不执行、并发忙碌、请求重试缓存、超长上下文和非文本输入拒绝、SSE 错误不冒充成功。
- 实际启动 Uvicorn HTTP 服务和 WebSocket Bridge，使用 HTTP 客户端发起读文件请求、获取模拟网页提出的工具调用，再将模拟文件内容作为工具结果送回，接收最终 SSE 回答。未实际读取或修改项目文件。
- 检查了 cursor-byok 的 OpenAI 接口及配置代码，但未安装或运行真实 cursor-byok/Cursor 客户端。网页结构化输出能力、原生工具执行、高亮、确认和撤回仍需按照 BYOK_SETUP.md 实机验证。
- 本地端点没有调用备用模型，测试未向真实网页发送代码。

## 0.3.0 输入预算修复（2026-09-21）

- Python 33 项、JavaScript 21 项，共 54 项自动化测试全部通过。
- 端点与实际 WebSocket Bridge 分别验证 90,000 字符的完整转发；扩展模拟验证三个网站预算边界、超限阻止、ChatGPT 行数限制和 UTF-16 计数。
- 验证超限返回 413，附角色/工具长度分项，错误不泄露正文。
- 新预算依据仓库现有适配器的安全阈值，不是本次对网站服务端最大上下文的实测。真实 Cursor 请求是否能容纳需按返回长度确认；未实现自动压缩或拆分上下文。

## 编辑输出格式兼容与诊断（2026-09-21）

- Python 38 项、JavaScript 21 项，共 59 项自动化测试通过。
- 新增编辑参数逐字符保真测试：多行代码、引号、反斜杠、中文及 emoji 在对象参数、JSON 字符串参数和标准 function 包装下保持一致。
- 验证单层完整 JSON 代码块可接受；说明文字混杂、多个 JSON、截断 JSON、重复键仍被拒绝。
- 验证请求编号、未知工具、参数 JSON、参数 schema、多调用分别返回明确错误 code；缺必填字段和 JSON 行列位置诊断不回显测试代码内容。
- 未取得用户失败时的原始网页响应，无法认定其具体根因已经修复；此次增加格式兼容和可定位的错误，待实机复测。

## 无效转义单次纠错（2026-09-21）

- Python 42 项、JavaScript 21 项，共 63 项自动化测试通过。
- 新增模拟网页测试：Windows 路径非法转义后一次纠错成功；HTTP 重试复用纠错结果；纠错仍失败最多发送两次；纠错后仍校验参数 schema；纠错提示超限不发送。
- 本地不重写代码字符串。纠错是另一次真实网页请求的协议流程，测试用模拟网页验证次数与输出；不保证真实模型重写的语义保真，也未证明用户当前编辑任务已成功。

## 0.4.0 原文采集修复（2026-09-21）

- Python 43 项、JavaScript 32 项，总计 75 项通过。
- 使用 markdown-it 实际渲染 JSON，再通过 linkedom 读取 DOM，复现普通段落反斜杠损坏：既包含 Invalid escape，也包含无报错但路径被错误解码的情况。
- 同样的 Windows 路径、正则、多行代码、中文和 emoji 放进 fenced json 代码块后，采集结果与原始 JSON 保持一致。
- 覆盖复制按钮/语言标签排除、多块拒绝、回答根节点隔离、CodeMirror 完整文档优先和原文缺失拒绝；扩展主流程仅在协议模式使用新采集路径，MCP 普通问答不变。
- 真实 HTTP/Bridge 测试验证 json_code_block 指令到浏览器连接的传播。
- 这些是 Markdown/DOM 与模拟浏览器回归测试，不是三家网站的在线实机验证。用户反复同列报错与此机制相符，但未获得其原始响应，不能宣称根因已完全确认。

## 0.4.18 适配其它网页 AI（GLM / Kimi K3 / Qwen / Gemini / Meta / ChatGPT）（2026-09-22）

- Python 46 项、JavaScript 60 项，总计 106 项通过（`unittest discover` + `npm test`）。
- 背景：用户要求适配其它网页 AI（GLM、Kimi K3 等）。8 个站点适配器本就存在（主 ZeroScript 扩展，GLM/Kimi DOM 于 2026-06 实机验证、Kimi K3 于 2026-07-30）；0.4.15–0.4.18 的三重发送确认、request_id 标记兜底、弹窗自动应答均与站点无关，缺的是接入。
- 改动：
  1. cursor-web manifest 注入范围 +5 站点（chat.z.ai / kimi.ai / gemini.google.com / meta.ai / chat.qwen.ai，qwen 含 MAIN-world 网络钩子）。
  2. 7 个旧适配器 `typeAndSend` 升级 0.4.15 接口：返回 `{sent, landedLen}`；sent = 发送后 ≤3s 内"输入框清空或开始生成"；各站多返回路径（图片轮询/禁用按钮/点击/Enter 回退）逐一补齐。
  3. GLM/Kimi `readAssistant` 增加 `thinkingSel`（推理区代码块不计入协议块搜索）。
  4. content.js 提取失败救援：作用域块搜索出错时先按 request_id 标记扫描再退回渲染文本（诊断 `extraction: marker_fallback`，detail 前缀 `scoped search failed`）。
  5. 预算表 +5 站点 = 100000 UTF-16 单位（input_limits.py 与 content.js 镜像），保守起点。
- 新增测试 3 项：glm/kimi/qwen/gemini/meta 预算边界（100000 通过 / 100001 拒绝）；作用域提取失败 → 标记救援成功（含诊断断言）。
- 所有 8 个 provider 文件 `node --check` 通过；manifest JSON 校验通过。
- 5 个新站点仍待用户桌面实机验证（登录会话 + 2KB 控制 + 真实任务）；预算与 DOM 选择器以实机结果为准。

## 0.4.17 任务成功后自动应答"此任务成功了吗?"弹窗（2026-09-22）

- Python 46 项、JavaScript 59 项，总计 105 项通过（`unittest discover` + `npm test`）。
- 实机依据：0.4.16 下 Cursor 成功收到网页回复（验收测试 A 在 Arena 跑通），但每次回复后页面弹出"此任务成功了吗?"（是/否/继续工作），不选则下一次对话无法进行。
- 改动：
  - content.js：回复完整读取并验证之后，最多等 10s 调用 `P.clearFollowupPrompt()`；命中即止。诊断 `followupCleared`。
  - arena.js 新增 `clearFollowupPrompt()`：文本恰为"是"的按钮 + 4 层祖先内含"成功了吗"上下文 + 按钮可见，才点击。其他 provider 无此方法（`typeof` 守卫），行为不变。
- 设计边界：只点"是"（任务确实成功）；点"继续工作"会让网页继续干活、点"否"会让网页返工，均错误。任务失败路径不点击。手动点击仍由 0.4.10 保护拦截。
- 新增测试 2 项：弹窗在回复后出现 → 成功返回且 `followupClicked=true`/`followupCleared=true`；无弹窗 → 成功返回且 `followupCleared=false`。
- 弹窗真实 DOM 下的点击效果（是否解锁下一次发送）待用户桌面复测确认。

## 0.4.16 回复读取 request_id 标记兜底（2026-09-22）

- Python 46 项、JavaScript 57 项，总计 103 项通过（`unittest discover` + `npm test`）。
- 实机依据：0.4.15 下"网页回复了但 Cursor 没有回复"——发送确认（三重证据）已通过，断点移到回复读取：`readAssistant()` 与用户消息计数共用 DOM 过滤器（`mx-auto` + `.prose` + 类名判定），新 Agent 聊天的回复轮不被识别 → 页面上 JSON 可见但扩展 240s 超时。
- 改动（content.js）：回复等待中若过滤器持续检测不到新回复，按本次发送的唯一 request_id 扫描全部代码块；命中"含该 id + 可 JSON.parse + 顶层键恰为 request_id/content/tool_calls"的块、稳定 4s → 判定完成（`finalReason: complete_json_fallback`）。
- 防误认设计：用户消息的 CURRENT_REQUEST 信封同样含该 request_id（若站点把用户文本渲染成代码块会被扫到）——信封顶层键为 request_id/messages/tools/tool_choice，与协议对象三键校验不符，被拒绝。
- 新增测试 2 项：staleReads（过滤器永远看不到新回复）下经标记兜底读到完整 JSON 并成功返回；信封块（含 id 但四键）被拒绝直至超时。
- 仍是模拟浏览器回归；新聊天回复阶段的真实 DOM 表现待用户桌面复测（预期 `marker_fallback` 路径接管）。

## 0.4.15 发送确认三重证据，不再依赖用户消息计数（2026-09-22）

- Python 46 项、JavaScript 55 项，总计 101 项通过（`unittest discover` + `npm test`）。
- 实机依据（决定性）：2KB 载荷、页面无卡死、`send confirmed (composer cleared)`（网站接受发送）、**网页返回正确协议 JSON** `{"request_id":"fbbe4d43…","content":"Hi! How can I help?","tool_calls":[]}`，但任务误报 `Message was not sent: no new message appeared in the chat`。根因：发送确认唯一判据是 `userCount()+1`，而新 Agent 聊天的用户轮 DOM 不匹配计数器的过滤链（`ol` 直接子级 + `mx-auto`/carousel + 含 `.prose` + `justify-end`），计数不涨。
- 改动 1（arena.js）：`typeAndSend` 返回 `{sent, landedLen}`——provider 自报"亲眼看到输入框清空 + 写入验证长度"。其他 provider 不变（返回 undefined，content.js 回退到计数判定）。
- 改动 2（content.js）：三重证据判定（新用户消息 / provider 确认 / 写入验证且输入框被清空，任一满足即确认）；诊断新增 `sendConfirmedBy`；快速失败保留（滞留 ~1s、写入验证缺失立即失败）。
- 改动 3（全部 provider）：清理 0.4.14 版本字符串误入的反斜杠（`"0.4\.14"`→`"0.4.15"`；JS 的 identity escape 使原值运行时无害，但属脏数据且会破坏版本升级正则）。
- 新增/改写测试：brokenUserCount 下由 provider 证据确认（sendConfirmedBy=provider，复现实测场景）；不报 provider 结果的慢用户消息（20s）仍被 30s 窗口覆盖（sendConfirmedBy=user_turn）；31s 窗口外失败措辞；sendDropped 快速失败保留。
- 仍是模拟浏览器回归；新聊天中**回复读取阶段**（readAssistant 依赖同样的 DOM 过滤）是否受影响尚未实测——若下一轮失败出现在回复阶段，诊断 `extraction` 字段可直接定位。

## 0.4.14 大载荷警告带构成明细 + 卡死提示（2026-09-22）

- Python 46 项、JavaScript 54 项，总计 100 项通过（`unittest discover` + `npm test`）。
- 实机依据（0.4.13 数据，Arena Agent 模式）：新对话下"你好"载荷 89,762 字符；12 块分块写入全落地；`send confirmed (composer cleared)`（网站接受）；30s 内聊天列表无新用户消息 → 任务失败；页面再次卡死。结论：该 Cursor 对话基线载荷约 9 万字符，超出 Arena 站点提交管线处理能力（站点侧容量问题）。
- 改动 1（model_endpoint.py）：>90k 警告输出各部分 UTF-16 字符数（按 role 汇总 + tools），区分"固定基线（system+tools）"与"历史累积（user/assistant）"——前者开新对话无效，后者开 Cursor 新会话可解决。
- 改动 2（content.js）：发送已被接受但 30s 无消息的报错补充"页面可能已卡死——刷新专用页并重新绑定会话"。
- 无新增断言（端点日志与措辞变化）；100 项保持通过。
- 待用户侧数据：构成明细 + 2KB curl 对照实验结果（网站能力下限）。据此决定：换 DeepSeek 专用页 / 裁剪上下文（需用户确认）/ 放弃 Arena。

## 0.4.13 发送确认窗口 8s→30s + 大载荷警告（2026-09-22）

- Python 46 项、JavaScript 54 项，总计 100 项通过（`unittest discover` + `npm test`）。
- 实机依据（0.4.12 面包屑截图，Arena Agent 模式）：`prompt 112912 chars`；15 块分块写入全部落地（`landed 112903/112912`）；`write done` → `clicking send button` → **`send confirmed (composer cleared)`**（网站已接受发送）；随后 8s 确认窗口内未出现新用户消息 → 误报 `Message was not sent: no new message appeared in the chat (the text did not land in the composer)`。用户另报告：点击回复后的"继续/否"选项会卡死标签页（网站带 11.3 万字符完整历史发起新一轮，站点自身管线被噎住）。
- 根因分析：11.3 万字符载荷来自对话历史膨胀（之前失败大任务 104,245 字符的完整 prompt 留在历史中，每次请求整体重发），已贴近 118,000 预算上限；网站对超大消息的渲染/后续处理超出其能力。
- 改动 1（content.js）：发送确认循环 40×200ms → 150×200ms（8s → 30s），覆盖超大用户消息的慢渲染。
- 改动 2（content.js）：确认失败时区分措辞——发送前输入框持有内容且现已清空（发送已被网站接受）→ `no new user turn appeared within 30s although the composer was cleared (the send WAS accepted; …)`；否则保持"文字未落进输入框"措辞。
- 改动 3（model_endpoint.py）：prompt > 90,000 字符时打印 `WARNING: large prompt (…) start a FRESH chat`。
- 新增测试 2 项：发送已被接受但用户消息 20s 才渲染（30s 窗口内确认成功，旧 8s 窗口会误判）；31s 才渲染（窗口外失败且报错措辞为 send WAS accepted）。
- 对话膨胀的根治靠操作规则（端点 WARNING 时开新对话），不做历史裁剪——裁剪会破坏跨轮工具结果连续性。
- 仍是模拟浏览器回归；11 万字符载荷下网站的真实渲染耗时/上限待用户桌面复测（新对话小载荷应为主要验证场景）。

## 0.4.12 卡死定位面包屑 + 回复读取限速（2026-09-22）

- Python 46 项、JavaScript 52 项，总计 98 项通过（`unittest discover` + `npm test`）。
- 实机依据（Arena Agent 模式，2026-09-22）：0.4.11 分块写入后网页仍卡死 → 卡死点未锁定。可能阶段：分块写入中 / 点发送与站点提交管线 / 流式生成期间 / 扩展自身的读取循环。
- 改动 1（content.js + arena.js）：全链路控制台面包屑 `[zs] task start (N chars) → arena: writing N chars (C chunks) → chunk i/C → write done → clicking send button → send confirmed → send confirmed, waiting for reply → reply complete (N chars, reason) / task failed`。标签页冻结时，DevTools 控制台的**最后一行 `[zs]` 即卡死阶段**，无需猜。
- 改动 2（content.js）：回复等待循环限速——流式生成时每 token 一次 DOM 变更，旧逻辑每次变更都全量重读对话（此时对话含 5 万+ 字符用户消息），可能占满标签页 CPU；现每 250ms 至多一次完整读取。
- 测试：无新增断言（面包屑不改行为；限速在模拟时钟 1000ms 节拍下不触发额外等待，既有计时用例不受影响）；98 项全部保持通过。
- 仍是模拟浏览器回归；卡死根因待用户桌面复测（打开 DevTools 控制台后重测，报告最后一行 `[zs]`）。

## 0.4.11 分块写入输入框：长载荷不再一次插完冻结网页（2026-09-22）

- Python 46 项、JavaScript 52 项，总计 98 项通过（`unittest discover` + `npm test`）。
- 实机依据（Arena Agent 模式，2026-09-22）：0.4.10 下任务发出后网页卡死、无反应；用户判断与发送文本过长有关。机制核对成立：旧 `setTextareaValue` 对 contenteditable 用**单次 `execCommand("insertText")` 写入全部载荷**（5 万–11.8 万字符），网站 onChange 一次性处理整段文本（React 状态/字符计数同步爆发；该处理器已知存在 `getComputedStyle` TypeError），标签页冻结。
- 改动 1（arena.js）：`insertContentEditable` 分块写入——每块 8,000 字符、块间 60ms 让网站 onChange 消化，每块前把选区重新锚定到末尾（抗网站重渲染挪光标）；对网站的每次 change 事件都是小事件。
- 改动 2（arena.js）：每块写入后核对 DOM 实际长度 < 已写入量 × 80% 即抛 `clamped the input mid-write`，网站输入上限小于载荷时绝不发出截断 JSON（原有写后 95% 校验保留为兜底）。
- 改动 3（model_endpoint.py）：任务启动日志带载荷尺寸 `prompt N chars, M lines`，"你好"实际发了多少字符可直接核对。
- 新增测试 3 项（首次为 provider DOM 逻辑建了假 DOM 测试台）：长载荷分块写入（每块 ≤8000、总量守恒、正常发送）；网站上限 12000 时在写入中途（第 2 块后）即失败且绝不点发送；短文本单次写入+遗留草稿场景。
- 假 DOM 台模拟 execCommand 追加语义，未模拟选区替换；仍是模拟回归，网页卡死是否消除待用户桌面复测。

## 0.4.10 JSON 回复完成即返回 + 任务期间手动操作快速失败（2026-09-22）

- Python 46 项、JavaScript 49 项，总计 95 项通过（`unittest discover` + `npm test`）。
- 实机依据（Arena Agent 模式）：0.4.9 下首次跑通发送与回复（"内容发送出去了，网页返回 json 了"），但 Cursor 长时间未收到结果——页面在回复后弹出"任务完成？是/否/继续"三选项，进入非空闲状态；完成判定要求页面空闲，持完整 JSON 干等到 240s 超时。用户手动点击三选项后站点自身卡死（已知的 getComputedStyle bug）。
- 改动 1（content.js）：新增 `isCompleteJson()`；回复围栏 JSON 块稳定 4s 且可 `JSON.parse` 时立即判定完成（`finalReason: "complete_json"`），与页面空闲状态解耦；纯文本回复不受此规则影响（仍走空闲判定，超时语义不变）。
- 改动 2（content.js）：等待回复期间用户消息数超过"发送前 +1"即快速失败 `Dedicated page was operated during the task (…)`，附修复步骤（刷新→重绑→重试），不再等满 240s。
- 改动 3（content.js）：`diagnostics.promptLen` 记录每次任务发出的载荷字符数（解释"你好"为何在网页中显示为长文：完整上下文+工具定义原样转发，架构使然）。
- 新增测试 4 项：可解析 JSON 在页面持续报告 generating 时照常完成；纯文本回复不触发 JSON 规则（仍超时）；额外用户消息导致快速失败；promptLen 入诊断。
- 仍是模拟浏览器回归，非在线实机验证；Arena 三选项的真实 DOM 未参与测试（JSON 判定规则不依赖该 UI 的结构）。

## 0.4.9 内容脚本与 provider 版本一致性闸门（2026-09-22）

- Python 46 项、JavaScript 45 项，总计 91 项通过（`unittest discover` + `npm test`）。
- 实机依据（Arena Agent 模式）：0.4.8 已发布并确认 `transportVersion: "0.4.8"` 后复测，仍报 `Message was not sent: 104245 characters are still in the composer. Composer: TEXTAREA (HIDDEN) placeholder="Ask anything…"`——该错误在 0.4.8 的 arena.js 中不可能产生（`getEditor()` 对隐藏框有 `offsetParent === null` 跳过），证明实际运行的 provider 仍是 0.4.7 旧拷贝。
- 根因：`cursor-web/extension/providers/` 在 .gitignore 中，`git pull` 只更新受跟踪的 `content.js`/`manifest.json`，provider 文件必须靠 `prepare_extension.py` 复制；`transportVersion` 由 content.js 自报，无法反映 provider 版本 → 出现内容脚本 0.4.8 + provider 0.4.7 的混搭，且旧代码静默写隐藏框。
- 改动 1（8 个 provider 文件）：每个 `ZSProvider` 导出 `version: "0.4.9"`。
- 改动 2（content.js）：会话通告新增 `providerVersion` 与 `inSync` 字段，`--sessions` 可一次性看到两个版本及一致性。
- 改动 3（content.js dispatch 闸门）：`P.version !== VERSION` 时直接拒绝，错误信息含两边版本与修复步骤（pull → prepare_extension.py → 重载扩展 → 刷新页面）；覆盖"版本不匹配"与"旧 provider 无 version 字段"两种情况。
- 新增测试 3 项：stale provider 拒绝（含修复指令断言）、无版本 provider 拒绝（`unversioned (stale)`）、会话通告含 `providerVersion`/`inSync`。
- 仍是模拟浏览器回归，非在线实机验证；Arena 页面真实行为待用户桌面复测（`inSync: true` 且 `providerVersion: "0.4.9"` 后）。

## 0.4.8 Arena 永不写入隐藏的遗留输入框（2026-09-22）

- Python 46 项、JavaScript 42 项，总计 88 项通过（`unittest discover` + `npm test`）。
- 实机依据（Arena Agent 模式，0.4.7）：任务失败 `Message was not sent: 105399 characters are still in the composer. Composer: TEXTAREA (HIDDEN) placeholder="Ask anything…"`。代码定位：0.4.7 的 `getEditor()` 回退会返回**隐藏的**遗留 `form textarea`（未检查可见性）；该隐藏框在页面刚刷新时先于 TipTap 输入框存在于 DOM，`getEditor()` 立即返回非 null，0.4.7 的"等待挂载"重试因此从未触发；整个 105,399 字符载荷写进隐藏框（textarea 写入总是"成功"，"文字未落位"检查也随之通过），而真正的发送按钮绑定 TipTap 状态（TipTap 为空）永不点亮，消息发不出去。
- 改动 1（arena.js `getEditor()`）：只返回**可见**编辑器（TipTap/ProseMirror contenteditable 优先，其次可见 `form textarea`），隐藏遗留框不再是候选；半加载页面真正返回 null，挂载等待才能覆盖挂载窗口。
- 改动 2（arena.js `typeAndSend()`）：挂载等待由约 10s 延长到约 15s（75×200ms）；仍找不到时报 `Arena input box not found after 15s (…)`。
- 改动 3（arena.js `typeAndSend()`）：写入后新增校验——输入框实际字符数 < 写入量 × 95% 时立即失败 `Arena composer clamped the input: wrote N characters, composer holds M`（网站输入上限小于载荷；5% 容差吸收 contenteditable 换行/空白归一化），防止截断的坏 JSON 载荷被发出。
- 版本核对改用 `--sessions` 输出中会话的 `transportVersion` 字段（页面内运行脚本自报，非复制文本）。
- 仍是模拟浏览器回归，非在线实机验证；"刷新页面→TipTap 挂载"的真实时序待用户桌面复测。

## 0.4.7 放宽 Arena 输入框选择 + 等待挂载（2026-09-22）

- Python 46 项、JavaScript 42 项，总计 88 项通过；arena.js 通过 `node --check`。
- 实机依据：0.4.6 上线后仍 `Arena input box not found`（1s 快速失败）。0.4.6 的 `getEditor()` 含"排除 `ol.flex-col-reverse`（聊天列表）内元素"过滤，Agent 模式下输入框可能就在该容器内而被误跳过；页面刚刷新时输入框也可能尚未挂载。
- 改动（仅 arena.js）：
  1. `getEditor()` 去掉聊天列表过滤，选择器放宽为任意 `[contenteditable]`（按 `isContentEditable` 判定），仅靠"可见 + 非 `#zs-root` + class 含 `tiptap`/`ProseMirror`"定位；回退 `form textarea` 保留。
  2. `typeAndSend()` 找不到输入框时轮询重试最多 ~10s（50×200ms）再报错，错误信息附带"页面可能未加载完/可能非聊天页，请刷新后重试"。
- 现有自动化测试用 mock provider，不直接驱动真实 arena.js，故此改动仍主要靠用户实机验证；等待重试会把"输入框缺失"场景的失败时间从 1s 延到最长 ~10s（仍属快速失败）。

## 0.4.6 Arena 目标改为可见的 TipTap 输入框（2026-09-22）

- Python 46 项、JavaScript 42 项，总计 88 项通过（`unittest discover` + `npm test`）；arena.js 通过 `node --check`。
- 实机定位依据（用户 F12 控制台调试）：页面只有一个 `form textarea` 且 `visible:false`（隐藏遗留表单），写入其 `.value` 成功且被保留；但真实输入框是用户点击后 `document.activeElement` 得到的**可见 `contenteditable` DIV**（class `tiptap ProseMirror prose …`、`inForm:false`）；可见可编辑元素表中该 DIV `isFocused:true`；发送键为可见按钮 `aria-label="Send message"`。
- 根因：适配器 `getEditor()` 选 `form textarea`（隐藏框），`setTextareaValue` 用 `.value` 写入——二者都作用在隐藏框上，真实 TipTap 框始终为空，故发送键永远 disabled、任务卡在 `waitFor(sendReady)`。
- 改动（仅 arena.js）：
  1. `getEditor()` 优先返回可见的 TipTap/ProseMirror contenteditable（排除 `#zs-root` 与 `S.list` 聊天列表内），回退 `form textarea`。
  2. `setTextareaValue()` 对 `isContentEditable` 元素用 `focus + selectNodeContents + execCommand("insertText")` 写入（失败回退 `textContent` + input 事件）；textarea 路径不变。
  3. `editorText()` 对 DIV 走 `textContent`（`e.value` 为 undefined）；发送键逻辑不变（可见 + `send message` aria）。
- 0.4.5 的"输入未落位即时失败"与"按新用户气泡判定发送"继续生效，现在作用在正确的 TipTap 框上。
- 限制：现有自动化测试用 mock provider，不直接驱动真实 arena.js，故此改动主要靠用户实机 F12 验证；回复读取仍依赖聊天列表 DOM，Agent 模式若结构不同需另行适配（先确认发送是否已通）。

## 0.4.5 按"是否出现新消息"判定发送 + 输入未落位即时失败（2026-09-22）

- Python 46 项、JavaScript 42 项，总计 88 项通过（`unittest discover` + `npm test`）。
- 实机依据（Arena）：任务 running 50 多秒，但输入框无文字、发送按钮灰、无生成中——文字根本没打进所看输入框。0.4.4 的"看输入框是否残留"判断对"输入框是空的（文字没落位）"会误判为已发送，仍会空等。
- 两个改动：
  1. **provider 层（arena/deepseek/chatgpt）**：`typeAndSend` 写入文字后立即校验 `editorText()` 非空；为空则**立刻抛错** `…composer did not accept the input…`，不再等待一个空输入框永远不会点亮的发送按钮（arena/deepseek 为 textarea 同步 `.value`，chatgpt 用同步 `execCommand`，均无时序风险）。三处均通过 `node --check`。
  2. **content.js 层（0.4.5）**：改用"发送后是否出现一条新的用户消息"（`usersAfter > usersBefore`）作为发送确认；发送前记录输入框状态（`editorFound/editorTag/editorVisible/editorPlaceholder`、`editorLenBefore`、`usersBefore`），发送后短暂轮询（≤8s）。未出现新消息即失败，区分 `no new message appeared…(did not land)` 与 `N characters still in the composer`，并附 `Composer: TAG (visible/HIDDEN) placeholder="…"`。诊断新增 `sendConfirmed/usersAfter/editorLenAfter/generatingAfter/hardGeneratingAfter`，失败 `phase=confirming_send`。
- 模拟 provider 更新为真实语义：发送被接受才清空输入框并 `userCount++`；新增 `sendDropped`（文字未落位）模式。新增 2 项测试：`unconfirmed send` 断言 `phase=confirming_send`；`send that never lands in the composer fails fast with composer state`（断言 usersBefore==usersAfter、leftoverLen==0、错误含 "did not land"）。
- 仍是模拟浏览器回归，非在线实机验证；"输入框非空=文字已落位"与"新用户气泡=消息已提交"依赖三家网站的既有行为。

## 0.4.4 发送未被接受时快速失败（2026-09-21）

- Python 46 项、JavaScript 41 项，总计 87 项通过（`unittest discover` + `npm test`）。
- 实机依据（Arena）：消息根本没有发出去，但 Cursor 长时间不返回。原因是三个网站适配器（arena/deepseek/chatgpt）的 `typeAndSend` 在"发不出去"时（发送按钮禁用、页面忙碌、仍在生成）都**静默返回而不抛错**，插件误判为已发送，随后空等一个不会来的回复最长 240 秒。
- 新增发送确认：`typeAndSend` 后插件轮询输入框是否被清空（网站接受消息即清空），最长约 5 秒；文字仍在则立即失败，错误信息带残留字符数与"页面是否有停止按钮（仍在生成）"，并给出可操作处理（停止生成、清空输入框、确认发送按钮可用）。
- 诊断新增 `sendConfirmed` 与 `leftoverLen`；失败时 `phase` 停留在 `sending`，便于区分"没发出去"与"发出去但没回复"。
- 模拟 provider 同步改为真实语义：发送成功清空输入框，`sendNotConfirmed` 时保留文字。新增 2 项测试：未确认发送快速失败（含停止按钮提示）、已确认发送继续等待回答。原"残留草稿"用例因模拟发送会清空输入框而继续通过。
- 仍是模拟浏览器回归，非在线实机验证；"输入框清空=已接受"依赖三家网站发送后清空输入框的既有行为。

## 0.4.3 残留输入草稿不再卡死专用页（2026-09-21）

- Python 46 项、JavaScript 39 项，总计 85 项通过（`unittest discover` + `npm test`）。
- 实机依据：任务在预检报 `Composer contains a draft; send or clear it manually first` 后失败，且专用页输入框残留会让后续每个任务都失败，直到手动清空。
- 原设计在"看到草稿就抛错"，对"上次发送失败残留"这种场景会永久卡住专用页。改为：专用页遇到残留草稿时记录 `composerDraftCleared`（前 200 字）与 `composerDraftLen` 到 diagnostics 并继续，因为 `typeAndSend` 用 `setTextareaValue` 覆盖输入框。
- 移除原"reject draft"用例，新增"leftover composer draft is recorded and replaced, not a hard failure"（断言草稿被记录、任务成功、仅发送一次）。
- 仍是模拟浏览器回归，非在线实机验证；专用页执行期间仍不应手动输入/发送。

## 0.4.2 网站报错快速失败与 409 可操作化（2026-09-21）

- Python 45 项、JavaScript 39 项，总计 84 项通过（`unittest discover` + `npm test`）。
- 实机依据：Arena Agent mode 下网站报 `model channel not available` 后，任务在端点内最长挂起约 4 分钟（扩展等待 240s / 端点 270s），期间所有不同内容的新请求 409；用户日志中 17:21:50→17:22:07 连续多次调用即此现象。
- 新增 provider 接口 `errorText()`（arena/deepseek/chatgpt）：返回可见站点错误提示文本（排除聊天内容区域），扩展等待循环中若无新回答且该文本持续约 3 秒则立即失败并带回网站原文；回答开始后错误提示被忽略（模拟测试覆盖两种路径）。
- 端点 409 现包含运行秒数与"重启 model_endpoint.py 清除"提示（Python 测试断言消息内容）；任务缓存改存 (task, 开始时间)。
- 端点终端新增任务生命周期日志（started / completed after Ns / failed after Ns: 原因 / reusing existing task），便于在出现 busy 时判断任务是否仍在等待网页（新增 1 项 Python 测试；总计 46 Python + 39 Node = 85 项通过）。
- 仍是模拟浏览器/HTTP 回归，非三家网站在线实机验证；网站错误提示的 DOM 形态变化可能仍需适配。

## 0.4.1 整轮回退与诊断（2026-09-21）

- Python 45 项、JavaScript 37 项，总计 82 项通过（`unittest discover` + `npm test`）。
- 实机反馈依据：DeepSeek 显示 `json` 代码块但旧采集报 `found 0`；且 DeepSeek 官方仓库 2026-08 换用了增量 mdast 渲染器，旧 DOM 嵌套假设不可靠。
- 新逻辑：回答根节点恰好一个块时直接使用（原有严格范围不变）；根节点零块时才回退整轮 `.ds-message` 搜索，排除思考区（DeepSeek `.ds-think-content`）与 `#zs-root`/`.zs-chip` 注入 UI。
- 新增协议回归测试：代码块作为 `.ds-markdown` 的兄弟节点（DeepSeek `.md-code-block` 结构）可被回退找到；思考区代码块被排除并计入 `thinking_blocks`；无 `replyRoots` 的旧版 provider 快照仍可经整轮读取；多个块安全失败；注入 UI 中的 `pre` 被忽略。
- `extraction_detail`（`roots=… scope=… turn_blocks=… thinking_blocks=…`）随结果诊断传到 Bridge/端点；解析错误信息附带它，端点对未知提取来源仍标记 `unverified_or_old_extension`（新增 2 项 Python 测试）。
- 仍是模拟 DOM 回归，不是三家网站在线实机验证。
