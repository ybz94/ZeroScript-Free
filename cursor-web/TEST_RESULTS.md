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

## 0.4.20 一键启动 + 弹窗美化（站点跳转）+ 回复跟随用户语言（2026-09-22）

- Python 46 项、JavaScript 63 项，总计 109 项通过（`unittest discover` + `npm test`）。
- **一键启动**：新增 `cursor-web/launcher.py` + `cursor-web/start.bat`（Windows 双击）。流程：找 Python（py 启动器/PATH/标准目录，跳过商店桩）→ 缺依赖自动 pip 安装 → prepare_extension.py → 启动 Bridge（已运行则复用）→ 轮询等待会话（90s）→ 唯一会话自动绑定/多个选序号 → 启动端点 → Ctrl+C 全停。
  - 沙箱端到端验证：模拟扩展客户端（websockets 连接报 kimi 会话）→ 启动器 4 步全部走通，端点绑定 `sess-test-123` 并在 17615 就绪；SIGTERM 后子进程正常退出，端口释放。
- **弹窗**：popup.html 重做（状态点/版本、会话卡片含同步徽章与 busy 状态、8 个站点点击跳转、设置折叠、深色模式）；popup.js 渲染 + 2s 刷新；background.js 新增 `snapshot` 消息（connected/version/sessions）。HTML/JS 语法校验通过。
- **语言跟随**：content.js `withLangRule()` 按 `navigator.languages[0]` 追加指令（zh-* → 简体中文；其它 → 镜像用户语言；request_id/JSON 键/工具名/代码/路径永不翻译；顶格 payload 跳过指令不超预算）。新增测试 3 项：zh-CN 含 `简体中文` 指令；en-US 含镜像规则且无简体指令；顶格 payload（59995 字符）原样发出（长度断言 59995）。
- **主扩展语言移植**：core/config.js `buildSystemPrompt` 增加 `userLang`（zh → 显式简体中文指令，否则镜像用户语言，命令本身永不翻译）；core/main.js 传 `navigator.languages[0]`。来自 01a0a947 分支 83e0f69（该分支其它提交属旧 agent 架构，未合并）。
- 待用户桌面实机：一键启动器在 Windows 上的完整流程；弹窗站点跳转；中文浏览器下网页回复是否切为中文。

## 0.4.21 启动器依赖检查修复（2026-09-22）

- Python 46 项、JavaScript 63 项，总计 109 项通过（`unittest discover` + `npm test`）。
- 实机依据：用户 Windows 上 start.bat 第 4 步 `model_endpoint.py` 报 `ModuleNotFoundError: No module named 'jsonschema'`——启动器硬编码只查 3 个依赖，漏网。
- 修复：`check_python_deps` 改读 requirements.txt 全量核对（缺即安装 + 安装后复验）；requirements 显式加 `referencing`；启动显示 Python 版本与路径（排查"双 Python"）；端点退出报错改为按 traceback 指向（缺模块/端口占用）。
- 沙箱验证：① 构造缺 jsonschema/mcp/referencing 的环境 → 启动器正确列出缺失并自动 pip 安装；② 依赖齐全环境 + 模拟扩展会话 → 4 步全流程至端点就绪；③ 端口最终干净释放。

## 0.4.22 chatglm.cn 报错明确化（2026-09-22）

- Python 46 项、JavaScript 63 项，总计 109 项通过。
- 实机依据：用户以国内版 chatglm.cn（智谱清言）为专用页，Cursor 报 `Could not establish connection. Receiving end does not exist`——该域名不在 manifest 注入范围（GLM 适配器面向国际版 chat.z.ai），dispatch 发到无内容脚本的标签页。
- 改动：background.js dispatch catch 分支识别 "Receiving end does not exist"，替换为可操作中文指引（打开受支持站点/刷新/重新绑定，并点名 chatglm.cn ≠ chat.z.ai）；popup.html 站点列表下加"国内版暂不支持"提示。
- 后续：chatglm.cn 适配器待用户提供 DOM 快照（控制台探针脚本已随消息给出）后按真实结构实现。

## 0.4.23 桌面软件：一个进程 = Bridge + 端点 + 控制中心（2026-09-22）

- Python 47 项、JavaScript 63 项，总计 110 项通过。
- 新增 `cursor-web/app.py`：同进程运行 bridge（`bridge.main()` 作为任务）+ 端点（`uvicorn.Server` 任务，`create_app(key, SessionRef)`）+ 控制界面（starlette 静态 UI + /api/status|rebind|stop，端口 17616，WebView2 原生窗口；`--no-window` 无头模式）。
- `model_endpoint.create_app` 会话绑定改为可变引用（接受字符串或 callable）→ **窗口一键重绑，端点不重启**；CLI `--session` 行为不变（回归测试覆盖）。
- `bridge.py` 新增 `type:"jobs"` 查询（任务日志用）。
- 令牌文件冻结（frozen）适配：exe 模式下所有令牌固定在 exe 同目录（_MEIPASS 是临时目录，不能放令牌）；bridge 模块 TOKEN_FILE 与端点 RPC 统一指向同一文件。
- `web/index.html` 控制界面：状态条、5 步向导（localStorage 记忆手动步骤，出现会话自动点亮①②）、8 站点跳转、会话卡片+一键重绑、任务日志、Cursor 连接三项复制、停止按钮。
- **集成测试 test_app.py（子进程隔离，端口 177xx）**：假 content script 应答 dispatch（回显 request_id）→ 单进程起全部服务 → UI HTML/status → 会话注册 → POST 重绑 → **真实 HTTP chat/completions 200 且内容 = 假网页回复** → 任务日志出现 completed。
- 打包：`build_exe.bat`（pip requirements-desktop.txt → prepare → PyInstaller onefile windowed，--add-data web+extension）→ `dist\CursorWebAssistant.exe`；exe 行为（PyInstaller 冻结环境）待用户 Windows 上首次构建验证，沙箱为 Linux 不能跨平台编译 Windows exe。
- **修复（用户首次运行 exe 时报错后）**：`ImportError: The 'appdirs' package is required` —— pywebview 运行时导入 pkg_resources，新版 setuptools 的 pkg_resources.extern 需要真实 appdirs 模块而 onefile 未打包 → `requirements-desktop.txt` 加 `appdirs>=1.4`，PyInstaller 参数加 `--hidden-import appdirs`（另加 `--hidden-import uvicorn.loops.auto` 防同类动态导入缺件）。扩展代码无变化，版本号仍 0.4.23，浏览器里的扩展不用重新加载；只需重新双击 build_exe.bat。
- **修复 2（用户第二次运行 exe 时）**：`ValueError: Unable to configure formatter 'default'` —— `--windowed` 无控制台 exe 里 `sys.stdout` 为 None，uvicorn 配置日志时调 `sys.stdout.isatty()` 崩溃 → `app.py` 启动时 `_ensure_streams()`：stdout/stderr 为 None 就把两者指向 exe 同目录 `cursor_web.log`（行缓冲追加，可用 `CURSOR_WEB_LOG_FILE` 改位置），兼作调试日志；UI "Cursor 连接"卡片显示日志路径（可复制）。回归测试 `test_windowed_exe_no_console` 在 stdout/stderr=None 下真实执行 `uvicorn.Config(...).configure_logging()` 验证。
- **顺带修复 UI 隐性 bug**：向导里动态渲染的"复制路径/复制令牌"按钮从未绑定点击事件（绑定循环在首次渲染前执行）→ 改为 `bindCopy()` 每次渲染后重新绑定。
- **修复 3（用户第三次运行：窗口打开但纯白）**：根因是架构级——`webview.start()` 阻塞主线程进入 WinForms 消息循环，而 bridge/端点/UI 三个服务都在主线程的同一个 asyncio 事件循环里，窗口一开循环即冻结 → UI 服务器无法响应任何请求 → 页面空白。修复：`run_with_window()` 把事件循环整体挪到独立 daemon 线程（`threading.Event` 跨线程停止信号，`/api/stop` 同时置位），主线程只跑窗口；主线程另做端口就绪探测，服务起不来时日志写明原因。回归测试 `test_window_mode_serves_while_main_thread_blocked`：假 webview 的 start() 模拟"主线程冻结"期间，页面与 /api/status 必须仍能被真实 HTTP 取回（旧架构下会挂起）。
- **日志判读（用户发来 exe 日志）**：日志显示两段启动；第一段"就绪"后的一大段 ERROR（lifespan CancelledError×2、proactor AssertionError、WinError 10054、websockets handshake failed×2、Task destroyed）经逐条判读**全部是关窗退出时的正常噪音**（退出时 poller 的 list+jobs 两条在途 WS 连接被掐断 = 两条 handshake failed；Windows proactor 关闭竞态）——正常运行期间无任何错误，服务健康。
- **可诊断性增强**：① 日志文件每行加 `HH:MM:SS` 时间戳（`_TimestampedStream`）；② 窗口打开前做**页面自检**——真实 HTTP GET 控制页并记录 `HTTP 200 / 字节数 / 标题✓`，日志从此能一锤定音区分"服务没供页面"vs"WebView2 渲染问题"；③ 退出前把 uvicorn/websockets 日志器调静 + 自定义 loop 异常处理器吞掉 Windows 良性竞态（AssertionError@proactor、连接重置、取消），关窗不再刷一大段吓人但无害的 ERROR；④ 关窗/停止时打明确标记行（"窗口已关闭，正在停止服务…"/"已停止：…"）。
- **修复 4（用户第四次运行：线程修复后窗口仍白）**：用户日志无时间戳/自检行 → 判为旧构建；进一步定位真正根因——pywebview 的 WebView2 互操作 DLL（`Microsoft.Web.WebView2.Core/WinForms.dll`、`runtimes/win-x64/native/WebView2Loader.dll`）与 `js/*.js` 是**数据包里的数据文件**，`interop_dll_path()` 在冻结模式下从 `_MEIPASS/webview/lib/...` 查找，而原打包命令只有 `--hidden-import`（只收集代码不收集数据）→ DLL 缺失 → WebView2 控件窗口能建、浏览器引擎初始化不了 → 白屏无崩溃。修复：`build_exe.bat` 改用 **`--collect-all webview`**（数据+二进制+子模块全收，DLL 落位与 `interop_dll_path` 搜索路径逐一核对过）。另在窗口创建前打印 pywebview 版本/引擎行，日志可确认引擎。
- **专用浏览器（免手动装扩展，回应"能不能嵌入装好插件的浏览器"）**：WebView2 控件本身不支持装扩展（浏览器安全模型），但 exe 可以**启动系统 Edge/Chrome 的专用实例**：`--user-data-dir=cursor-web-profile`（独立配置，隔离主浏览器）+ `--load-extension=extension\` + `--disable-extensions-except` 预装扩展 + 直达所选站点。界面 8 个站点卡片点击即弹专用浏览器（`POST /api/launch-browser`），首次登录后配置/令牌保留在独立 profile 里，之后一键复开；重复点击安全（Chromium 同 profile 复用实例）。同时修复 UI 隐性 bug：站点网格原来只在页面加载时渲染一次（此时 state 尚未取回）→ 永远空白，改为每次 render 渲染。测试：`_dedicated_browser_command` 纯函数单测（argv 四要素）+ 端点集成测试（mock launcher，deepseek→正确 URL，未知站点 400）。BUILD_ID→b6。
- **修复 5（双通道显示，保证用户一定有窗）**：考虑到 WebView2 原生窗口在 Windows 上仍有不可在沙箱验证的残余风险（系统运行时缺失等），`run_with_window` 增加看门狗：pywebview 的 `window.events.loaded` 只在"页面渲染成功且 JS 桥运行"后触发（已核对源码：WebView2 初始化失败时永不触发）→ 15 秒未触发即判原生窗口失效 → **自动用系统 Edge/Chrome `--app` 应用窗口打开同一界面**（无地址栏/无标签页）并 `win.destroy()` 关闭坏窗口。新增 `_find_system_browsers()`（PATH + 4 个标准安装路径）、`_open_system_app_window()`（找不到浏览器才退回普通标签页）、`_run_browser_display()`（`--display browser` 专用路径：界面"停止程序"按钮 → 关闭浏览器窗口并退出）。回归测试 ×2：loaded 永不触发 → 兜底打开+坏窗关闭；loaded 触发 → 无兜底。51 Python 全绿。
- **cursor-byok 自动接入（回应"把 cursor 助手集成进去，自动连接、不用输地址和 key"，构建 b7）**：cursor-byok（github.com/WHUT666/cursor-byok，MIT）的用户配置是磁盘上的普通 YAML（`~/.cursor-local-assistant-v2/config.yaml`，核对其源码 `internal/appdata/paths.go` 与 `internal/backend/server/config/types.go`）⇒ 可直接代填。新增 `cursor-web/byok_setup.py`：`merge_webai_adapter()` 幂等把 web-ai 适配器（baseURL=本程序端点、apiKey=端点密钥、modelID=web-ai、Chat Completions）合并进 config.yaml——文件不存在按官方默认骨架创建；已有则更新地址/密钥并保留其它模型/字段；解析失败**绝不改动**；改动前自动 `.yaml.bak` 备份。程序启动时自动执行并在日志打印 `[cursor-byok] 模型配置: …`。`/api/byok`（write|launch|status）+ UI "Cursor 连接"卡片：五项状态（配置文件/模型/程序/运行中/代理，10 秒缓存）+ 两个按钮（写入连接配置 / 启动 cursor-byok，后者按 环境变量→exe 同目录→常见目录→PATH 定位 `cursor-byok*.exe`）。**明确边界（不重实现其 MITM 核心）**：TLS 拦截、CA 证书安装、系统代理仍是 cursor-byok 程序自身的职责，首次启用需管理员权限、必须在其自己的窗口里点一次"启用/开始"。`requirements.txt`/`requirements-desktop.txt` 加 `pyyaml>=6,<7`。新增测试 ×6（Python 59 项 + JS 63 项 = 122 全绿）：merge 纯函数 5 项（新建/幂等/旧密钥更新且保留用户字段与顶层设置/保留其它适配器/拒绝解析失败）+ `/api/byok` 端点集成 1 项（write 代填端点密钥与地址、二次 write 幂等、status 反映 config_exists/adapter_ok、launch 启动定位到的 exe）。
- **修复 6（用户 b7 实机反馈两项，构建 b8；Python 59 + JS 64 = 123 全绿）**：
  1. **专用浏览器"版本不同步"**：0.4.23 发布时 `content.js` 的 VERSION 与 `manifest.json` 升到 0.4.23，但 8 个站点 provider（`zeroscript-extension/providers/`，prepare 时复制进扩展）的 `version` 字段漏改、停在 0.4.22 → 0.4.9 的一致性闸门判定 `inSync=false`：会话徽章显示"版本不同步"，且任何任务在发送前被硬闸门拒绝（`Extension files out of sync: content script 0.4.23, provider 0.4.22`）。此前从未暴露是因为桌面版发布后第一次真跑完整链路就是用户这次。修复：8 个 provider 版本字段 → 0.4.23；新增回归测试 `tests/versionsync.test.cjs`——真实读取 content.js / manifest.json / 全部 provider 文件断言版本一致（再犯直接红）。
  2. **黑窗口反复闪**：`byok_setup.is_byok_running()` 每 10 秒（byok 状态缓存 TTL）用 `subprocess.run(['tasklist', …])` 探测一次——`--windowed` 无控制台 exe 里子进程会被 Windows 新分配一个控制台 → 黑窗口每 10 秒闪一下、永不消失。修复：tasklist 调用加 `creationflags=CREATE_NO_WINDOW`（0x08000000），探测完全静默。
- **上下文压缩（回应 Arena 长对话 413，构建 b9；Python 65 + JS 64 = 129 全绿）**：长多轮工具对话的载荷会不断增长——每一轮都把**完整历史（含旧工具结果）整体重发**，旧结果永久驻留直到顶破站点输入预算（用户实机：119,845/118,000，其中 `tool` 26,558 为旧工具结果累积）。新增 `fold_old_tool_results()`：发送前把**旧的、超过阈值的大工具结果**折叠为一行占位符（`[工具结果已折叠（原 N UTF-16 单位）。如仍需该内容，请重新调用相应工具获取。]`）——数据可重取（重新调工具即可）。**绝不折叠**：user/assistant/system 内容、最近 2 条工具结果、低于阈值的条目、多段（list）内容。协议提示词加了一句让网页 AI 识别占位符的说明。环境变量 `ZW_FOLD_MAX_UNITS`（默认 4000，0 = 关闭）。折叠发生时端点日志打印 `folded N stale tool result(s), saved M UTF-16 units`；413 Breakdown 改为反映折叠后的真实待发内容，若折叠后仍超限会明确注明。新增测试 ×6：纯函数 4 项（只折旧大结果/最近2条与小额不动/0 关闭/非字符串内容）+ 集成 2 项（**同一请求：压缩关 → 413，压缩开 → 200 且恰好只剩最近 2 条完整**；巨大 user 消息不折叠仍被护栏 413）。
- **压缩第二遍兜底（用户 b9 实机仍 413：tool=27,408 全是"中块"，单块不到 4,000 阈值 → 第一遍一条没折，构建 b10；Python 67 + JS 64 = 131 全绿）**：新增第二遍——第一遍后仍超预算时，**从最旧一条开始逐条折叠（下限 `ZW_FOLD_FLOOR_UNITS` 默认 600），装下即停**（只折恰好需要的数量，损失最小；最近 2 条与用户内容仍永不触碰）。按用户实机载荷（120,940/118,000，tool 27,408）推算：折最旧 2~3 块即通过。新增测试 ×2：32 条 3,456 单位中块历史（未折叠超预算）→ 第一遍无效、第二遍折到装下，断言最旧条已折/最近 2 条完整/HTTP 200；`ZW_FOLD_FLOOR_UNITS=0` 时第二遍禁用、护栏仍 413。
- **文件外置（回应"读取文件造成 413"，构建 b11；Python 72 + JS 64 = 136 全绿）**：实机 413 拆解——`tool` 27,019 是 Agent **刚读的两个文件**（"最近 2 条"，按设计永不折叠）+ 基线 60,471 + user 30,425，压缩无物可折。用户实测过"给网页一个 MCP 地址它可直接连接使用"⇒ 让网页模型自取文件内容：新增 `file_mcp.py`（只读文件 MCP：list_dir/read_file 带行号、bearer token、路径防穿越、200,000 字符上限、默认 127.0.0.1、streamable-http/sse 双传输）+ 端点 `externalize_file_results()`（开关：窗口"文件外置"勾选框持久化 config.json / `ZW_FILE_EXTERN=1`；文件类大结果 >`ZW_FILE_EXTERN_MIN_UNITS` 默认 4,000 单位 → 引用占位符 + 协议行"先调文件 MCP 获取、未获取不下结论、拿不到明说不猜"，**文件内容不再进输入框**）。新增测试 ×5：externalize 纯函数 3 项（只外置文件类大结果/字符串参数可解析/未知 tool_call_id 不动/输入不变）+ 端点集成 1 项（**复刻用户实机载荷：关=413 且折叠无物可取，开=200，prompt 含引用且不含文件内容**）+ file_mcp 真实 HTTP 1 项（真实 MCP 客户端：错 token 拒绝、list_dir、read_file、路径逃逸被拦）。
- **文件 MCP 与隧道融入 exe（回应"MCP 和 cloudflared 能不能都融合进 exe"，构建 b12；Python 77 + JS 64 = 141 全绿）**：零命令行化——文件 MCP 改为**进程内运行**：`app.py` 把 `file_mcp` 的 ASGI 应用挂到 uvicorn 任务（随程序启停；项目根/开关/端口存 `config.json`，UI「文件外置」区填项目目录+勾选即启动）；`file_mcp.py` 保留独立运行方式（环境变量那套不变），认证网关新增 `?token=` 查询参数（Bearer 头照旧），方便把"连接地址（含 token）"整串粘进网页 MCP 设置。cloudflared 本身无法嵌入（它就是本机到 Cloudflare 边缘的连接进程），但其**下载**（官方 GitHub release，放 exe 同目录，约 55MB）/**启动**（隐藏窗口 `CREATE_NO_WINDOW`，`--no-autoupdate`）/**抓地址**（stdout 正则 `https://*.trycloudflare.com`）/**停止**全部由 exe 代劳：`/api/tunnel`（start|stop|download|status）+ UI 三个按钮 + 状态行 + 一键复制的隧道连接地址。`build_exe.bat` 加 `--collect-submodules mcp.server --collect-submodules mcp.shared`（**不能用 `--collect-all mcp`**：`mcp.cli` 子模块在导入时 `sys.exit(1)`（typer 缺失/无命令行），PyInstaller 收集全部子模块时会整个打包挂掉——用户 b12 首包实机复现；已用 PyInstaller 同款收集函数在沙箱复现同一处 `mcp/cli/cli.py:18 SystemExit`，并验证 `collect_submodules('mcp.server'/'mcp.shared')`（59+24 模块）干净通过。另用 AST 导入图遍历（含父包解析，等价 PyInstaller modulegraph 语义）验证：进程内 MCP 运行时实际导入的 83 个 mcp 模块全部在打包覆盖内（静态导入链 + 两个 collect-submodules），`mcp.cli` 不在其中）。隧道进程用 `asyncio.create_subprocess_exec` 管理（可安全取消，不会留下阻塞的读管线程；Linux/Windows 同一路径）。新增测试 ×4：进程内 MCP + 隧道端点集成 1 项（真实 MCP 客户端走 UI 端点启用→Bearer 与 ?token= 双路读文件→config.json 持久化→假 cloudflared 二进制启动→stdout 抓到 trycloudflare 地址→connect_url 拼接→停止→禁用即停→含 `/api/pick-dir` 非 Windows 报错路径）+ file_mcp ?token= 查询参数 1 项（并入真实 HTTP 测试）+ `_tunnel_url_from_line` 纯函数 1 项（多行格式/非隧道 URL 忽略）+ `download_file` 1 项（本地 HTTP 服务器：字节级进度首末值/content-length 上报/落盘字节一致/404 不落残留文件）。**MCP 连接自动携带（回应"不用用户手动发连接地址"）**：隧道 + 进程内 MCP 同时就绪时，`app.py` 把 `隧道地址/mcp?token=…` 写入 `ZW_FILE_MCP_URL`（隧道退出/MCP 停止即清除；四个状态变化点同步），`model_endpoint._build_prompt()` 读到该变量就在协议前置说明里自动附上连接指令（"A read-only local file MCP is available at … If this session has not connected to it yet, connect to it now…"）——**用户全程零粘贴**，AI 首条消息收到即自行连接，同对话后续保持；隧道重启地址变化后新地址自动生效（旧会话里 AI 已连的旧地址会失效，新消息带的是新地址）。新增测试 ×2：make_prompt 级（设 `ZW_FILE_MCP_URL` → prompt 含完整 URL/工具名/连接指令；不设 → 无噪音）+ 进程内集成（假隧道地址捕获后 env 变量发布 == 连接地址、隧道停止后清除）。
- **413 明细加"逐条消息大小"诊断（用户实机：只打了一句话，user 角色却 30,485 单位——Cursor Agent 把 js/css 文件内容作为上下文自动塞进了 user 消息；按角色汇总看不出是哪条肿的）**：413 Breakdown 现在追加 `Largest messages (units, sizes only): user#1:30485 tool#3:15000 …`（≥2,000 单位的消息逐条列名，最多 8 条，**只记大小不记内容**，日志不泄代码）。用户据此定位到"自己那条消息"被 Cursor 附加了文件上下文 → 正确姿势：消息只写任务+路径、发送前摘掉自动附加的文件，让网页 AI 走文件 MCP 自取。新增测试 ×1：413 明细点名 `user#1`/`tool#3` 且断言内容（长串 ctx/x）绝不进入错误文本。
- **b12 用户实机反馈两项 UI 修复**：① 下载 cloudflared 无进度感知（国内直连 GitHub 常卡死）→ 改流式下载（`download_file` 256KB 分块 + 进度回调），状态行显示 `已下 X.XMB / 总 Y.YMB（Z.ZZMB/s）`，30 秒无字节判定失败并显示原因 + 常显"手动下载"兜底行（官方页链接，下好放 exe 同目录即自动识别）；② 项目目录输入框在浅色主题下黑字不可见（内联样式写死深色底）→ 全部改用主题 CSS 变量（`--card2/--text/--line`），并新增**「浏览…」**按钮：`/api/pick-dir` → PowerShell + `System.Windows.Forms.FolderBrowserDialog`（`CREATE_NO_WINDOW` 隐藏控制台、以当前输入值为起始目录、180 秒超时、非 Windows 明确报错）。
- **静态上下文外置——"绕行"大段固定内容（回应"把输入的网页文本框中的内容减少…我要的不是压缩 是想办法绕行…完全可以使用上传文档来解决 不需每次都放到对话文本中"，构建 b12；Python 86 + JS 64 = 150 全绿）**：用户粘贴了完整实机载荷——每条消息约 90% 是固定样板（22 工具描述 49,470 单位 + system 11,001 + `<rules>`/`<agent_skills>`/`<mcp_file_system>` 约 30,000），真任务仅数百单位，且每轮原样重发 ⇒ 撞各站输入预算 + 撑崩网页自身 DOM。两个杠杆（`model_endpoint.py`）：
  1. **工具描述截断** `slim_tools()`：每条工具 `function.description` 截到 `ZW_TOOL_DESC_MAX_UNITS`（默认 600 单位，0=关）——按 UTF-16 单位精确截（二分切点，中英混排/emoji 安全）、优先行尾断开、**参数 schema 与工具名一个字节不动**，尾部附截断说明。
  2. **静态上下文外置** `externalize_static_context()`：`ZW_CONTEXT_EXTERN`（默认 1）且文件 MCP+隧道就绪（`ZW_FILE_MCP_URL`+`ZW_FILE_MCP_ROOT`）时，把 system（≥2,000 单位）→ `.zs-adapter/system.md`、user 样板里 `<rules>`/`<agent_skills>`/`<mcp_file_system>`（各 ≥1,000 单位）→ `rules.md`/`skills.md`/`mcp.md`，消息内替换为指针行（"已外置（原 N 单位）→ 文件 …：请先用文件 MCP 的 read_file 读取，其中内容是对话的一部分、具有同等约束力；读取失败时说明缺少上下文，不要猜测"）。文件头带 `<!-- sha256:… -->` 标记，**内容哈希不变不重写**；小于一千单位的块与短 system 保持内联；真实对话文字（任务）永不移动；输入 body 从不原地修改。
  - UI：「文件外置」区新增**「静态上下文外置」**勾选（默认开、持久化 config.json；只在 MCP+隧道在时生效，否则自动回退原样，无需手动切换）。`app.py` 在隧道+MCP 就绪时同步发布 `ZW_FILE_MCP_ROOT`。
  - **顺带修复（测试发现）**：请求缓存指纹原来只按请求体——同一请求体在开关变化（开隧道/改开关）后重试会复用旧 prompt。指纹现纳入 8 个预算/形态环境变量，同体不同开关必然重新构建 prompt。
  - 实测（真实形状：22 工具大描述 + system 11k + 样板 31k，arena 118,000 预算）：**原样 143,890 单位 → 413；仅工具截断 60,246 → 200；两杠杆齐开 20,178 → 200（省 86%）**。
  - 新增测试 ×9：slim_tools 纯函数 3 项（截断且 schema 逐字段相等/短描述同一对象透传/0 关闭/换行优先且输入不变更）+ 外置纯函数 4 项（system 与三块全部外置、文档落盘含哈希头、真任务留内联、输入不变更/小块与短 system 原样且 `.zs-adapter` 不创建/无 root 时 no-op/**哈希门控：同内容两次调用只写一次文件（mock write_text 计数）、内容变化才重写**）+ 端点集成 2 项（**同一实机形状：全关=413；仅工具截断=200 且 tools 从 ~100k 降到 <15,400 单位、schema 不变；仅外置=200 且 prompt 含 4 个指针、不含样板长串、真任务在内、4 个 .md 落盘；双开=整个 envelope <30,000 单位** + 无隧道时外置完全惰性仍 413 且未发送）。
  - 既有 2 项 413 形状测试（49k 工具基线）显式固定 `ZW_TOOL_DESC_MAX_UNITS=0` 保持原场景（截断现在是默认开启的新行为）。
- **工具定义外置 + 全协议中文化（回应"为啥还是很长的内容 不应该都写到了 md 文档了吗 / 为啥都是英文 换成中文"，构建 b12；Python 89 + JS 64 = 153 全绿）**：第一版绕行后用户实机载荷仍约 20,551 单位——剩余大头是 **21 个工具的完整定义**（描述+参数 schema，约 15,000 单位/条消息）。新增 `externalize_tools()`：隧道+MCP 就绪时，**完整原始工具定义写入 `.zs-adapter/tools.md`**（sha256 哈希门控，内容不变不重写），消息内 tools 数组替换为 `[{name, 一行提示}]`（约 70 单位/个，保证网页 AI 不连 MCP 也知道有哪些工具）；端点校验目录（catalog）仍用**原始**请求体构建，工具调用的 schema 校验不受影响。隧道未启动则回退为"描述截断 600 单位"的旧路径。协议提示词、MCP 连接说明、413 报错、格式修复提示、语言规则（content.js）**全部中文化**（`provider=/utf16_units=/limit=/Breakdown` 等机器字段保留）。实测（用户实机形状：21 工具 + system 10,247 + 样板 31k）：**约 150,000（413）→ 20,551（第一版绕行）→ 约 6,161（当前版，较原样省约 96%）**；剩余约 6k = 中文协议契约 ~1,100 + MCP 连接行 ~400 + 指针行 ~1,000 + 21 工具名+提示 ~2,000 + 用户自己的对话内容（每轮在变，不可外置）。新增测试 ×2（`externalize_tools` 纯函数：完整定义落盘+哈希门控 mock 计数/紧凑名+提示 ≤60 单位+输入不变；无 root/空/不可写目录均回退）；集成测试 r2/r3 段改为断言 envelope tools 为 name+hint 且 `tools.md` 内容与原始 tools 全等；语言规则 JS 断言与 413/修复提示的英文断言同步改为中文。
  - **用户实机反馈"输入框内容被裁剪/路径反斜杠丢失"核查**：程序**从不修改**用户消息正文（代码路径只替换 system + 4 个样板块 + 工具描述，真实任务文字逐字节保留，测试断言）。输入框里是**原始 JSON 文本**，Windows 路径的反斜杠按 JSON 转义显示为**双反斜杠**（`G:\\下载\\…`）——这是正常转义、内容完整；用户三次粘贴中第一次完整、后两次丢反斜杠，差异发生在**复制/渲染环节**而非发送端。
- **输入框提示词堆积（三条完整提示词串联）修复（实机事故 2026-09-29：发一条 → 网页还在回复 → 程序又往框里打了第二条 → 再等 → 又追加第三条；ce20b701 已发送，7096b2ae/5b6ccb22/f43567b5 堆在框里未发送。Python 91 + JS 67 = 158 全绿，提交 557145c）**：根因三处——
  1. **追加而非替换（主因）**：`arena.js` 旧注释"第一个分块会替换已有草稿"实际不成立——分块循环**第一次** `selectEnd(true)` 就把选区折叠到末尾，随后的 `insertText` 是**追加**；框里留有上次未发送的草稿时，每次重试都追加一整条提示词 ⇒ 三条完整提示词串联。
  2. **页面"思考期"漏判**：网页生成中但模型在思考（Stop 按钮存在、无 token 流出）时，旧的忙碌预检（流增长启发式）判为空闲 ⇒ 生成期间写入，发送按钮被禁用，文字滞留。
  3. **端点无熔断**：Cursor 每次重发都原样再打进同一个卡住的框。
  修复：① `content.js` 预检加 `isHardGenerating()`（Stop 按钮存在即拒绝，**打字前**失败，中文提示）；② `arena.js` `typeAndSend` 入口加 Stop 按钮硬门槛；`insertContentEditable` 改**验证式清空**（全选+删除+复检，最多 6 次，清不掉就拒绝写入、一个字都不打）；写入后**追加检测**（框内容 >115% 写入量 = 有残留，拒绝发送+尽力清空）；新错误信息中文化。③ `model_endpoint.py` **熔断器**：同一专用页连续 3 次任务失败（6 分钟窗口）后，下一次请求在**打字前**直接 409（中文处置指引：刷新专用页/Cursor 新开对话）；90 秒冷却后自动放行**一次试探**（扩展已保证不会堆积），成功即清零。
  新增测试 ×5：熔断拦截（3 连败→409、零写入）+ 试探成功清零、预检硬生成拒绝（零打字、phase=preflight）、追加守卫（模拟站点把旧草稿重新渲染回来 → 拒发+清空、不点发送）；mock 的 `isHardGenerating` 改为时序感知。
- **输入提示词"字面标记"——原始内容用围栏/行内代码标识（回应"整体都是乱的…不能把一些文字内容使用 text 标识起来吗"，Python 92 + JS 70 = 162 全绿，提交见本分支）**：网页的 Markdown 渲染会把提示词里的裸 URL 变超链接（连括号里的"（streamable"都吞进链接）、把 `.zs-adapter/tools.md` 变成 `http://tools.md` 假链接、渲染复制时丢反斜杠——观感混乱且可能误导模型。现在 `model_endpoint._build_prompt`：① **整个 CURRENT_REQUEST 的 JSON 包进 `~~~text` 围栏**（用波浪号而不用反引号：编码对话的 JSON 里常有 ``` 序列，反引号围栏会被提前闭合；波浪号在真实代码内容里几乎不出现），提示词里注明"围栏内是 JSON 原文、按字面解析、围栏不属于请求内容"；② **MCP 端点 URL 用行内代码**（`` `https://…` ``，防自动链接）；③ **tools.md 指针路径用行内代码**（防变假链接）。协议契约（输出 json 围栏、request_id 等）不变；新增回归测试（围栏恰好一对、以围栏闭合、URL 行内代码、围栏内 JSON 仍可解析回原请求）；测试的 envelope 提取改为先剥围栏（`_envelope` 助手，test_model_endpoint ×7 / test_bridge ×1）。
- **提示词内容结构化（回应"我说的是内容乱"——乱的是提示词的内容结构而非渲染，提交 4401fa7，162 全绿）**：协议原文是一堵密集规则墙，现由 `_build_prompt` 生成带标签章节的有序文档（角色 / 输入 / 工具 / 回答格式 / 请求），一条规则一行；字面围栏与 MCP 行内代码回归保留。
- **删除角色锚点行（用户审阅："没用删掉"——该行无必要，提交 071d4df，162 全绿）**。
- **按用户审阅裁剪协议：删输入2（指令优先级）、输入4（折叠占位符说明）与末尾"语言规则"行（提交 b01a692，160 全绿）**：对应 JS 断言同步移除；语言规则此前已由 content.js 注入，协议里不再重复。
- **专属页旧版扩展检测 + 任务后反馈弹窗处理（回应实机四连问：文本仍带英文 LANGUAGE 行 / 不理解上一条 / "此任务成功了吗"弹窗未处理 / 409 熔断，根因＝专用浏览器窗口仍运行上一构建的旧扩展，Python 95 + JS 69 = 164 全绿）**：exe 重新打包只更新了 Python 端，旧浏览器窗口里的 content.js 还是旧代码（英文 LANGUAGE 行、无清空/防堆积/弹窗修复）——且版本号一直是 0.4.23，无从识别。新增 **BUILD_ID 构建号**：content.js 在 dispatch ack 上报构建号（background 转 'ack' 消息，bridge 记到 job 上），`model_endpoint.exchange` 轮询时比对：构建号不符 → 409"专属网页运行的是旧版扩展…请完全关闭旧的专用浏览器窗口并从程序重开"；10 秒内完全没上报（比 ack 更老的扩展）→ 同样 409（`ack_timeout_s` 可配）。新增通用**弹窗关闭**：`arena.js typeAndSend` 预检阶段 `dismissBlockingDialog()`——对可见的 role=dialog 优先点关闭类按钮（aria-label/文字含 close/关闭/cancel/×），否则向对话框发 Escape；**绝不代点"成功/失败"选项**（那是用户反馈，等确认弹窗的确切按钮后再加针对性点击）。测试 +4：构建号不符 409、静默旧扩展 409、content.js 与端点 BUILD_ID 锁步、dispatch ack 携带构建号。另修复 bridge 测试暴露的回归：'ack' 分支误吞了扩展消息的"永不回复"continue（sessions 消息会被回 Unsupported，测试全部 KeyError）。
- **等待回复期间"旧内容回到输入框"清除（回应"发送完第一条消息后 等待网页回复的时候 在文本框中会输入上次的内容"，Python 95 + JS 71 = 166 全绿）**：程序等待循环本身不碰输入框——是网页在回复流式输出期间把**草稿自动保存的内容重新渲染回输入框**（上一条提示词重新出现）。修复：① content.js 等待回复循环（250ms 读频率）加**草稿扫清**——发送已确认之后输入框本应为空，一旦重新出现任何文字立即 `P.clearComposer()` 清掉（诊断计数 `draftSweeps`、控制台 `[zs] stale draft reappeared … wiping`），任务不受影响；② arena.js 新增并导出 `clearComposer()`（复用验证式清空的选择区+delete/insertText 交替策略，永不抛错）；其它站点 provider 未导出时该调用自动跳过。测试 +2（等待期间草稿重现→恰好清一次、任务成功、draftSweeps=1；干净输入框全程不被触碰）。

## 0.4.24 版本号恢复递增 + 文件 MCP 隧道 421 修复随新版发布（构建 b13，2026-09-29）

- **版本号提升（回应"加个版本"；Python 96 + JS 71 = 167 全绿）**：0.4.23 → **0.4.24**，一处不漏地同步——content.js `VERSION`、manifest.json、8 个站点 provider 的 `version` 字段（源 `zeroscript-extension/providers/`，生成副本经 `prepare_extension.py` 同步）、`app.py` VERSION 与 User-Agent、测试 fixture；BUILD_ID 20260929.2 → 20260929.3（content.js ↔ model_endpoint.py 锁步由测试断言），构建标记 b12 → b13（启动横幅 + build_exe.bat 收尾核对行）。`versionsync` 回归测试（读真实文件断言内容脚本/manifest/8 provider 三方一致）与 BUILD_ID 同步测试保证版本漂移再犯必红。
- **网页连接文件 MCP 报 `421 Invalid Host header` 修复（回应实机单测"让网页连接 MCP"失败；网页 AI 判断"被本地服务拦截"其实**正确**，Python 96 + JS 71 = 167 全绿）**：排查先排除隧道链路——逐条核对 cloudflared 当前源码（无任何 421/Invalid Host 逻辑）、Cloudflare 官方 421 文档（SNI/Host 不符与连接复用，均要求客户端发错域名，与本例不符）；421 响应体与 **mcp Python SDK 完全一致**：`mcp/server/transport_security.py` 返回 `Response("Invalid Host header", 421)`。根因：mcp SDK ≥1.12（exe 打包 1.30.0）内置 **DNS rebinding 防护默认开启**——`FastMCP` 绑定回环地址时自动启用 Host/Origin 校验，默认白名单仅 `127.0.0.1:*` / `localhost:*` / `[::1]:*`；而隧道转发的请求 Host 是**每次运行随机的 trycloudflare.com 域名**（Cloudflare 边缘原样转发自己的公网主机名）⇒ 每个请求都被本地 MCP 服务确定性 421 拒绝（沙箱内 `curl -H "Host: …trycloudflare.com"` 直打本地服务复现：白名单内 Host=200、隧道形 Host=421）。修复：`file_mcp.py` 显式 `transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)` 关闭 SDK 该校验——真实认证边界是 bearer token 网关（随机 token + `secrets.compare_digest`，服务仅绑 127.0.0.1、只读工具），关闭不新增暴露面；docstring 安全模型注明原因。测试 +1：`test_tunnel_host_header_accepted`——**原始 socket 精确发送边缘会转发的隧道形 Host**（既有 MCP 客户端库的 Host 恒由本地 URL 推导，永远到不了这个分支，故此前测试全绿却从未暴露）：正确 token → 200 且 initialize 成功返回 serverInfo；错误 token → 仍 401（token 网关对外域 Host 同样生效）；回环 Host 不受影响。

## 0.4.25 专用浏览器残留实例自动清理 + 熔断不再吞掉"旧版扩展"提示（构建 b14，2026-09-29）

- **专用浏览器残留进程自动清理（回应实机 409 风暴："网页AI还没回答就直接报错了"——用户看到的是熔断 409，真因被遮住；Python 98 + JS 71 = 169 全绿）**：根因链：**关程序 ≠ 关专用浏览器**——Popen 拉起的 Chromium 子进程在程序退出后仍存活，继续持有 profile（`cursor-web-profile`）与它当时加载的扩展；重建后"从程序重新打开"命中同一 `--user-data-dir` 的存活实例 ⇒ Chromium 复用旧实例、**静默忽略新的 `--load-extension`** ⇒ 旧扩展继续运行 ⇒ BUILD_ID 409 ×3 ⇒ 第 4 次请求轮到熔断器 409（泛化的"已连续 3 次…输入框异常"），把具体的"旧版扩展"提示顶掉。修复：① `_launch_dedicated_browser` 启动前先 `_kill_stale_dedicated(profile_dir)`——PowerShell（`-NoProfile -NonInteractive`、隐藏窗口 `CREATE_NO_WINDOW`、30 秒超时）枚举**命令行含专用 profile 目录**的进程（只有专用实例才有该命令行，用户主浏览器绝不会被误杀；`Where-Object` 排除 powershell 自身防自杀）并 `taskkill /PID /T /F` 整棵进程树，返回清理数量，日志 `[dedicated] 已清理 N 个残留的旧专用浏览器进程（它们仍加载着上一构建的扩展）`；best-effort（异常/超时/非 Windows 一律返回 0，永不阻塞启动）。从此"从程序重新打开"是**真重开**（登录态在 profile 里保留，代价只是几秒冷启动）。② 新增 `StaleExtensionError`（`AdapterError` 子类）：`exchange()` 的两类旧版扩展 409（构建号不符 / 静默不报构建号）改抛它，`_task_done` **不计入熔断器连续失败数**——它在打字前就失败、不构成"输入框异常"，且其 409 本身可操作（关旧窗重开）；日志明确标注 `(stale extension - not counted by the breaker)`。测试 +2：3 次旧版扩展 409 后第 4 次请求仍收到具体的"旧版扩展"409（`assertNotIn '已连续'`），且每次都到达 exchange（sent=4）；`_kill_stale_dedicated` 单测（非 Windows no-op 且不碰 subprocess / Windows 命令行含 profile 目录 + taskkill + CREATE_NO_WINDOW / 异常返回 0）。
- **版本号**：程序 0.4.24 → **0.4.25**（构建 b14：横幅 + User-Agent + build_exe.bat 核对行）；**本轮扩展未改动**——content.js/manifest/providers 保持 0.4.24、BUILD_ID 保持 20260929.3（扩展代次未变则构建戳不升，否则用户当前 b13 页面会被全部误判为"旧版"）。
- **环境备注（非代码）**：沙箱 venv / tests/node_modules 被清除后按 requirements 重建（mcp 1.30.x），新版 uvicorn/h11 严格拒绝**重复 Host 头** ⇒ `test_tunnel_host_header_accepted` 的原始 socket 改 `skip_host=True`（只发一个显式 Host），生产代码无变化。

## 0.4.26 长回答（MCP 任务）不再被中途掐断 + "网页忙"报错可操作化（构建 b15，2026-09-29）

- **长回答超时修复（回应实机"还是网页没回答完呢 报错了"——`Session busy; query the existing task instead of resending`；Python 99 + JS 71 = 170 全绿）**：旧超时阶梯（扩展 240s < 端点 270s < bridge 300s）对"网页 AI 走文件 MCP 读文件再回答"的分钟级任务全太短——健康的进行中回答在 240s 被扩展掐掉，端点 270s 放弃后，bridge 里**结果丢失的任务**（WS 瞬断窗口静默丢弃 / 程序重启残留）继续占住会话最多 300s，期间每次重发都失败，且用户看到的是 bridge 的英文机器语原文（对人不可操作）。修复：
  1. **超时阶梯整体拉长**：扩展 240s→**480s**（content.js 等待循环 + 相关注释）、端点 270s→**510s**、bridge 任务上限 300s→**540s**（逐层递增，结果能按序传导；正常长回答 ≤8 分钟可答完）。
  2. **全部超时/忙碌错误改可操作中文**：扩展超时"等待网页回答超时（480 秒）。网页可能仍在回答——打开专用页可直接查看进度…"；端点 504"网页回答超时（510 秒，任务 xxxx）…不要立即重发（任务可能仍在网页上运行，重发会报"网页忙"）"；端点在跑拒绝"网页仍在回答上一个请求（已运行 N 秒；一个任务最长约 8.5 分钟）…无需重启程序"（移除旧的 `restart model_endpoint.py` CLI 提示）；bridge 忙碌"网页仍在回答上一个任务（job xxxx，已运行 N 秒；最长约 9 分钟）。请等网页回答完成后再发送…"；bridge 超时"任务超时（540 秒）：网页可能仍在回答…"。
  3. **background.js 结果防丢（outbox）**：原 `send()` 在 WS 非 OPEN 时**静默丢消息**——任务 result 恰逢程序重启窗口被丢 ⇒ bridge 任务永远 "running" ⇒ 之后每次重发都报 busy（用户报错的僵尸任务机制）。现在 result 发不出去时进 outbox（上限 50 条），下次 WS 握手成功后冲刷（bridge 对未知任务条目忽略，冲刷恒安全）。
  4. bridge.py send 分支重构：busy 检查从 `elif any(...)` 改为取 job 引用（带 job_id 与已运行秒数）；重构时一次缩进回归（raise 被移出 if，30 项测试当场变红）被测试拦下并修复。
  5. **版本号**：扩展 0.4.24→**0.4.25**（content.js + background.js 有改动；BUILD_ID 20260929.3→20260929.4），程序 0.4.25→**0.4.26**（构建 b15）。测试 +1（bridge busy 错误经端点原样带出且含"仍在回答上一个任务/已运行 12 秒"、零重发）；5 处断言同步新中文消息 / 540s 上限 / 3 个测试标题 240s→480s。

## 0.4.19 qwen / gemini / meta / chatgpt 逐项适配完成（2026-09-22）

- Python 46 项、JavaScript 60 项，总计 106 项通过（`unittest discover` + `npm test`）。
- 背景：用户要求 qwen/gemini/meta/chatgpt 全部直接适配。0.4.18 已完成通用接入；0.4.19 对四个站点逐项审计 content.js 依赖的完整接口并补齐缺口。
- 改动：
  1. `errorText` ×5（glm/kimi/qwen/gemini/meta）：可见 toast/alert（8–600 字符，排除模型内容）→ 网站报错时几秒内失败并给原文（chatgpt 同款实现，各站用自己的 errorSurfaces/anyItem）。
  2. 协议块搜索作用域：gemini `readAssistant` 加 `replyRoots:[message-content]` + `thinkingSel`（model-thoughts 为兄弟元素）；qwen 加 `replyRoots:[.response-message-content]`；meta 加 `thinkingSel:[data-testid="thinking-status"],[data-testid="subagent-cot-list"]`（Réflexion 推理代码块出局）。
  3. 审计矩阵 8/8 齐备：errorText/landedLen/(replyRoots 或 thinkingSel)/userCount/isGenerating/isHardGenerating/scanError/attachImages/installSendHooks/findToolBlockSpot/turnHalted。gemini/kimi 无 lastAssistantId（非虚拟化列表，item 身份 + 文本判新轮，content.js 可选处理）。
- 新增站点使用须知（BYOK_SETUP）：qwen=国际版 chat.qwen.ai（国内版未覆盖）、gemini/meta/chatgpt 需可访问对应网络；chatgpt 保持 120000/600 行预算；meta 强制 Réflexion 模式。
- 四站 + glm/kimi 均待用户桌面实机验证（登录 + 2KB 控制 + 真实任务）；errorText 与协议块作用域为结构性保障，具体 DOM 命中以实机为准。

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

## 0.4.27 正在执行的任务可取消（任务日志"取消"按钮）（构建 b16，2026-09-30）

- **背景**：实机反馈——上一个任务（MCP 长任务，1~5 分钟无可见输出）还在跑时再发新任务，报"网页仍在回答上一个任务"。按设计一页同时只跑一个任务，但此前没有任何途径终止正在跑的任务（只能干等到 540 秒超时）。
- **取消链路**（新增，端到端）：
  1. 程序 UI 任务日志：`running` 的行显示红色**取消**按钮（确认框说明"网页上正在执行的任务将立即终止"）→ `POST /api/cancel {job_id}`（缺 job_id 返回 400；bridge 拒绝返回 409 原文；bridge 不可达返回 502）。
  2. 程序 → bridge（cursor 角色 `cancel`）→ 校验任务存在/在跑 → 转发给持有该任务的扩展。
  3. background.js → 专用页 content.js：等待循环下一次轮询即 `throw 任务被取消`，走**正常 result 通道**结束任务（会话立即可用，可马上发新任务）。
  4. 未知任务返回"未知任务（bridge 可能已重启）"；已结束返回"任务已结束（done），无需取消"；持有任务的那台浏览器已断开时任务标为 error 并释放。
- **报错文案升级**：bridge 的忙碌错误补上"前 1~5 分钟没有可见输出属于正常"与"不想等可点'取消'"；端点忙碌错误补"不想等可点任务日志里的'取消'（或刷新专用页）"。
- **测试**：+2 扩展（取消在跑任务→立刻结束且只发送过一次；错误 job_id 的取消被忽略）；+2 bridge（取消转发+结果路径释放会话后新任务立即受理；未知/已结束任务的取消报错）；+1 程序集成（`/api/cancel` 端到端：假网页"思考"中 → 取消 → 任务状态 error'任务被取消' → 再发新任务受理，缺 job_id 400）。
- **版本号**：扩展 0.4.25→**0.4.26**（content.js + background.js 有改动；BUILD_ID 20260929.4→20260929.5），程序 0.4.26→**0.4.27**（构建 b16）。全量 **175** 项：102 Python + 73 JavaScript，0 失败。

## 0.4.28 修复四类"任务卡死会话"断点 + 取消不再落空（构建 b17，2026-09-30）

- **背景**：实机反馈——发送消息后 Cursor 报"网页仍在回答上一个任务（已运行 20 秒）"。排查确认：该报错本身是"一页一任务"的串行设计，但审计发现四类会让任务**假死占住会话**（最长 9 分钟、期间重发全部报忙、取消也不生效）的真实断点，全部修复：
  1. **MV3 服务工作者被杀后丢结果（主嫌疑）**：Chrome 会回收扩展后台脚本（空闲约 30 秒/内存压力），其内存态（任务路由表）随之丢失——正在执行任务的**结果消息被静默丢弃**，bridge 任务保持"进行中"直到 540 秒清扫；取消也因找不到路由而落空。修复：路由表持久化到 `chrome.storage.session`（跨 SW 重启、浏览器关闭即清）；结果消息路由缺失时按"会话+标签页"匹配兜底转发（bridge 端仍校验归属，安全）。
  2. **取消落空**：旧代码在取消无法送达标签页时静默吞掉，而 bridge 仍回复"已发送取消"。修复：bridge 的取消消息附带 `session_id`（路由丢失也能按会话找到标签页）；彻底不可达时**立即以明确报错结束任务**释放会话（网页若仍在生成，下一个任务会在写入前被拒绝并给出说明）。
  3. **端点单次 bridge 抖动即放弃**：轮询任务时一次连接失败就结束等待，但网页上的任务还在跑——之后 9 分钟内重发全部报忙。修复：容忍约 10 秒的连续抖动；仍失败时报错指向"任务日志"与"取消"。
  4. **失败任务被缓存重放**：去重缓存让同一消息的重发永远重放同一次失败（取消后无法重发同一指令）。修复：失败按"网页是否已消费 prompt"分类——已消费（完整回答校验失败/截断/停止/480s、540s 超时/510s 端点超时/任务中途被手动操作）保留重放，防止编辑类任务执行两遍；未消费（取消、忙、传输故障、发送前被拒、站点报错、旧版扩展等）移出缓存，重发即为全新任务（页面自身 busy/生成中防护仍会先拦截双发）。
- **其他**：取消在"发送确认"阶段（写入后最多约 30 秒）即生效（原先只在等待回答阶段）；页面仍答上一任务时的扩展报错改为中文可操作指引。
- **测试**：+10——background SW 重启/取消兜底 5（新增 background.test.cjs：路由跨 SW 重启存活、结果兜底转发、取消经会话表找标签页、不可达/被拒取消立即结束任务）；扩展 1（发送确认阶段取消）；bridge 2（取消转发含 session_id；浏览器断开后取消明确答复且任务释放）；端点集成 1（失败任务重发不再重放、全新任务成功；旧"坏回答不重发"防护测试保持通过）。
- **版本号**：扩展 0.4.26→**0.4.27**（content.js + background.js 有改动；BUILD_ID 20260929.5→20260929.6），程序 0.4.27→**0.4.28**（构建 b17）。全量 **183** 项：104 Python + 79 JavaScript，0 失败。

## 0.4.29 长任务等待期可见进度 + 相同内容重发自动接管（不再报"网页忙"）（构建 b18，2026-09-30）

- **背景**：实机反馈——发送后 26 秒（MCP 长任务无输出的正常窗口）Cursor 报"网页仍在回答上一个任务"，用户打开专用页确认"网页 AI 才刚开始"。根因：MCP 长任务前 1~5 分钟 Cursor 端**零反馈**，用户（或客户端）在静默期重发即撞串行限制报错，且报错文案的"上一个任务"让用户误以为是别的东西在跑。
- **改动**（仅程序端，扩展未改动）：
  1. **流式等待可见进度线**：任务运行超过约 15 秒仍未出回答时，向 Cursor 的流里写入一条可见状态（`⏳ 任务正在专用网页上执行（MCP 长任务的前几分钟没有可见输出——属正常现象）…`），之后回答照常追加——用户立刻知道消息在网页上执行，不再误判为"没反应"。
  2. **相同内容重发自动接管（adopt/harvest）**：原任务因传输中断/端点超时等在途死亡、但网页上的任务还在跑（或已答完）时，**相同内容的重发不再报错**：端点查询原任务——还在跑则接管等待、已答完则直接取回其回答（按原请求的 request_id 校验），绝不把 prompt 在网页上执行第二遍。
  3. **文案**：bridge 忙碌错误澄清"上一个任务 = 你最近发送的那条消息正在网页上处理"，并说明"相同内容重发无需任何操作，回答会自动到达"；端点 409（不同内容撞进行中任务）同样澄清。
- **测试**：+3（等待期可见进度线；bridge 抖动杀死原任务后相同重发接管"运行中"任务、prompt 只发送一次；原任务在盲窗期已答完时重发直接取回回答）。旧防护测试保持通过（坏回答仍不重发、不同内容仍 409）。
- **版本号**：本轮**扩展未改动**——扩展保持 0.4.27、BUILD_ID 保持 20260929.6（扩展代次未变则不升，否则当前页面会被误判"旧版"）；程序 0.4.28→**0.4.29**（构建 b18）。全量 **186** 项：107 Python + 79 JavaScript，0 失败。

## 0.4.30 新消息自动排队（AI 还在思考时新消息不再报错）（构建 b19，2026-09-30）

- **背景**：实机反馈——AI 还在思考（网页上正在生成）时发送新消息，请求被直接以"网页仍在回答上一个任务"结束。用户质疑：为什么不检查网页状态、等 AI 思考完再发送？
- **改动**（仅程序端，扩展未改动）：
  1. **新消息排队**：任务运行中收到**不同的新内容**时不再立即报错——该消息**排队等待**，前一个任务到达终态（完成或失败）后自动发送并返回回答；排队中的请求在 Cursor 流里**立即**显示可见状态（`⏳ 网页还在回答上一个任务——本条消息已排队，等它完成后自动发送…`）。
  2. **队列深度 1**：同一页同时只能跑一个任务、只保留一个排队位置；第二条等待中的消息报 409（说明原因），防止无限堆积。
  3. **排队后的发送保护**：前任务端点侧结束但网页任务可能仍占会话约 30 秒（510s→540s 阶梯），排队任务发送遇忙时短暂退避重试（≤90 秒）；扩展端写入输入框前仍执行页面状态检查（Stop 按钮/生成中判定），网页仍在生成则写入前拒绝——不会把消息堆进输入框。
  4. 相同内容的重发仍走 b18 的"接管"路径（不排队、不重放、不二次执行）。
- **测试**：旧"不同内容立即 409"测试改写为"排队后自动发送、prompt 恰好发送一次"；+2（排队请求的流立即显示"排队"状态行；第二个等待中的消息被拒绝且说明原因）。
- **版本号**：本轮**扩展未改动**——扩展保持 0.4.27、BUILD_ID 保持 20260929.6；程序 0.4.29→**0.4.30**（构建 b19）。全量 **188** 项：109 Python + 79 JavaScript，0 失败。
