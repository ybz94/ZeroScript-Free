# Cursor 网页助手（第一版）

**新：无需第二个模型的网页模型端点 → cursor-byok 接入见 [BYOK_SETUP.md](BYOK_SETUP.md)。** 以下正文是仍然保留的 MCP 咨询模式；两种模式不要嵌套调用。

保留 Cursor 原生 Agent 对话及代码编辑流程。网页 AI 通过 MCP 提供答案和修改建议，**由 Cursor 的原生编辑工具应用修改**，不通过 Bridge 写入项目文件。原生差异、确认、撤回和文件列表的具体行为取决于 Cursor 版本及工作模式。

这是独立入口，不使用仓库根目录的旧 Bridge 或旧启动脚本。第一版复用现有 DeepSeek、ChatGPT、Arena 网站适配器，但不加载旧核心、工具执行循环或旧界面。Cursor 自身模型仍参与工具调度及编辑，不能用此方案完全替代 Cursor 模型。

## 1. 安装（在本地电脑执行）

需要 Python 3.10+、Cursor 和 Chromium 系浏览器。以下命令在仓库根目录运行：

```sh
python -m pip install -r cursor-web/requirements.txt
python cursor-web/prepare_extension.py
python cursor-web/bridge.py
```

Bridge 默认只监听 `127.0.0.1:17614`，首次启动生成 `cursor-web/.bridge-token`。该文件已忽略，请勿提交、截图分享或发送给 AI。Windows 上还应保护所在用户目录访问权限。

## 2. 配对浏览器

1. 禁用旧扩展，避免两个扩展同时操作页面。
2. 在浏览器扩展管理页启用开发者模式，选择“加载已解压的扩展程序”，选择 `cursor-web/extension`。
3. 点击扩展图标，在弹窗输入 `.bridge-token` 文件内容，点击“保存并连接”。
4. 打开并登录支持的网站，刷新目标页面。为避免会话误配，建议先在网页建立一个普通对话，再绑定。
5. 0.2.0 支持实验性后台收发，不再因页面隐藏直接报错。先验证前台，再分别验证切换标签页和最小化窗口；网站或浏览器冻结页面时仍可能失败。

生成的 `extension/providers/` 不提交；其他电脑使用前需要重新运行准备脚本。服务目前仅允许一个已配对浏览器连接。扩展令牌存储于浏览器本地扩展存储，不同步到云端。

## 3. 配置 Cursor MCP

在 Cursor 的 MCP 配置中合并以下条目，不要覆盖已有服务。`command` 最好用安装了依赖的 Python 可执行文件绝对路径；`args` 用脚本绝对路径：

```json
{
  "mcpServers": {
    "web-assistant": {
      "command": "python",
      "args": ["/absolute/path/to/ZeroScript-Free/cursor-web/cursor_mcp.py"]
    }
  }
}
```

Windows 示例路径可以写成 `C:/projects/ZeroScript-Free/cursor-web/cursor_mcp.py`。

如果自定义端口，给 Bridge 和 MCP 进程同时设置 `CURSOR_WEB_PORT`，并修改扩展弹窗中的端口。MCP 脚本自动读取同目录的令牌文件，无需把令牌写入 Cursor 配置。

仓库新增 `.cursor/rules/web-assistant.mdc`，引导 Cursor 使用网页助手且保留原生编辑。用于其他项目时，将规则复制到那个项目或作为用户规则配置。

## 4. 在 Cursor 原生 Agent 对话中使用

可以输入：

> 使用网页助手，先列出会话让我选择。把我授权的相关代码发给该会话获取建议。收到建议后由你使用 Cursor 原生编辑工具修改文件，不要通过外部程序写文件，不要运行测试。

工具：

- `web_chat_list_sessions()`：列出会话、网站、URL、可见状态和忙碌状态。
- `web_chat_send(session_id, prompt)`：提交一次任务，返回 `job_id`。
- `web_chat_get_result(job_id)`：返回 `running`、`completed` 或 `error`，以及回答/错误。

保存 `job_id`，使用查询工具收取结果；不要通过重新发送来“查询”。网页普通答案作为工具结果保留在 Cursor 对话中，不会导入为 Cursor 原生模型消息。Cursor 需主动查询结果，本版不是向原生聊天框异步推送。

网页返回文本仅作为不可信建议。不要按其中指令泄露密钥、执行任意命令或上传其他文件。发送范围由 Cursor 和用户控制，不会自动读取整个工作区。

## 范围与限制

- 仅提供网页文本问答/代码建议闭环。没有实现通用浏览器操作、附件上传、增量流式回传、逐块补丁解析或任务取消工具。
- 不执行网页输出中的代码或工具标记，不在磁盘落地网页建议。网页自己的原生功能仍由网站决定。
- 一个会话同时只接收一个任务，不自动重发；网页发送动作的不确定失败需要人工检查。
- 会话绑定在当前页面生命周期内有效。刷新、导航或浏览器重启后通常需要重新列出并绑定；不是永久会话数据库。
- 任务及回答仅保存在 Bridge 进程内存中，最多 500 个；重启丢失。Cursor 对话历史由 Cursor 自己管理。
- 扩展等待新回答且连续稳定至少 4 秒，以页面生成状态作为完成判据。这是 DOM 启发式判断，不是网站 API 的完成保证；网站更新、迟到流片段和反自动化机制可能使其失效。
- 人工停止、截断、超时或连接中断会报告错误，可能附带部分文本；不能把错误状态中的部分文本当成完整答案。后台卡住时先激活网页检查原请求，不自动重发、不抢焦点。
- 登录、验证码、网站授权和使用条款仍需用户自行处理，不绕过访问控制。
- 配对只保护本机接入，不构成对恶意本机程序的隔离；不应暴露端口到公网。
- 已通过 Bridge/MCP 自动化集成测试和扩展逻辑模拟测试，详见 `TEST_RESULTS.md`。尚未进行真实 Cursor/浏览器网站联调，不能视为已验证生产版本。

## 手动验收建议（本次未执行）

确认会话列表 → 发送简单问答 → 查询返回 → 连续追问 → 请求一处代码建议 → 由 Cursor 原生工具编辑 → 查看原生差异并拒绝或接受 → 尝试原生撤回。

再检查页面切换、后台页面、断线、登录失效和超时是否明确报错，且不会自动重复发送任务。

## 运行自动化测试

测试使用 Python 3.11+ 和 Node.js 22；建议安装依赖到虚拟环境，避免系统 Python 的包管理限制。

```sh
python -m venv .venv
# Linux/macOS；Windows 对应 .venv/Scripts/python.exe
.venv/bin/python -m pip install -r cursor-web/requirements.txt
.venv/bin/python -m unittest discover -s cursor-web/tests -v
node --test cursor-web/tests/extension.test.cjs
```

MCP 还支持通过 `CURSOR_WEB_TOKEN_FILE` 指向本地令牌文件，默认仍读取脚本同目录的 `.bridge-token`。自动化测试使用临时令牌和随机端口，不改动真实配对信息。

## 0.2.0 后台收发本地验收

更新代码后重启 `cursor-web/bridge.py`，在扩展管理页重新加载插件，再刷新 AI 网页，重新列出并绑定会话。扩展版本应为 0.2.0，会话的 `transportVersion` 也应为 0.2.0。

分别提交三个独立任务（每个只发送一次）：

1. 目标网页前台：请求回复“前台测试成功”。
2. 切换到同窗口的另一个标签页：请求回复“后台标签测试成功”。
3. 最小化浏览器窗口：请求回复“最小化测试成功”。

使用原 job_id 查询每个任务。记录 status、error 和 diagnostics。后两项成功且 `diagnostics.sawHidden` 为 true，才说明该网站本机环境的后台链路实际通过。失败时恢复窗口、检查网页有没有收到原问题；不要立即重复发送。

结果诊断包括 `version`、`startedHidden`、`sawHidden`、`endedHidden`、`phase`。这些描述扩展观察到的状态，不是网站确认回执。浏览器彻底冻结页面或断线时可能无法获得诊断，需结合 Bridge 错误查看。
