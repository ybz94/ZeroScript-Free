# ZeroScript - Free AI Agent for your Local MCP Servers

![GitHub stars](https://img.shields.io/github/stars/sebattfg/ZeroScript-Free?style=social)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey)
![License](https://img.shields.io/badge/license-GPL--3.0-blue)

**ZeroScript** is a free browser extension that turns ChatGPT, DeepSeek, Gemini, Kimi, GLM, Qwen, Arena or Meta AI into a local MCP agent.
Control any MCP server on your PC (filesystem, git, GitHub, Blender, ...) directly from your browser - just describe what you want, and the AI runs the commands itself. No API key, no terminal, no coding needed.

> 🌐 **Website: [zerodev.tools/zeroscript](https://zerodev.tools/zeroscript)** the free, open-source way to build with AI on your own machine.

Eight AI providers are supported: **DeepSeek** (chat.deepseek.com, recommended), **ChatGPT** (chatgpt.com), **Google Gemini** (gemini.google.com), **Kimi** (kimi.ai, Moonshot AI), **GLM** (chat.z.ai, Z.ai), **Qwen** (chat.qwen.ai), **Arena** (arena.ai, a multi-model playground) and **Meta AI** (meta.ai). On ChatGPT, screenshots and image input are turned off on purpose: the free tier limits files and images on a separate quota from messages, so vision would only work part of the day. Gemini and Kimi can be unstable: Gemini tends to stop using the ZeroScript commands in long sessions, and Kimi sometimes uses its own native tools instead. On Arena, use **Direct** mode (ZeroScript only supports Direct; it blocks Start in Battle / Side-by-Side / Agent modes). DeepSeek is the recommended provider.

> 💬 **Stuck? Join the [Discord community](https://discord.gg/9aNyZsMWcb)** get help, share feedback, and follow updates.

## ⚠️ ZeroScript is Free Beware of Paid Copycats

ZeroScript is 100% free and open-source. It always has been, and it always will be. There is no official paid version, no subscription, and no sign-in required to use the extension.

If you come across a site or extension using the ZeroScript name that asks for payment or account creation, it is **not** this project. The only official links are the ones listed at the top of this README.

## How it works

```
AI chat (ChatGPT / DeepSeek / Gemini / Kimi / GLM / Qwen / Arena / Meta AI, in your browser) -> ZeroScript Extension -> Bridge (your PC) -> MCP server(s) (yours)
```

The extension runs inside the chat page (ChatGPT, DeepSeek, Gemini, Kimi, GLM, Qwen, Arena or Meta AI). When you type a request, it sends commands to the Bridge running on your PC, which drives your MCP servers over the standard [Model Context Protocol](https://modelcontextprotocol.io).

### Any MCP server works

The Bridge speaks MCP over stdio, so any local MCP server can be added - the same servers you would configure in an MCP-capable editor (filesystem, git, GitHub, Puppeteer/Playwright, PostgreSQL, Blender, ...).

- **Add one:** ⋯ menu in the ZeroScript bar (on any supported AI page) → **MCP servers** → type a name and a start command (e.g. `npx -y @modelcontextprotocol/server-filesystem /path/to/dir`) → **Add server**. The bridge restarts briefly to load it.
- **Remove one:** the ✕ on its row. The agent then runs on the remaining server(s) only.
- **Or edit `config.json`** next to `bridge.py` and restart the bridge:
  ```json
  {
    "mcpServers": {
      "files": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/me/projects"] },
      "git":   { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-git", "/home/me/projects"] }
    }
  }
  ```
- **Using it:** in the AI chat, the agent discovers servers with `list_mcp_servers` and lists a server's exact commands with `list_commands` (passing `"server": "<id>"`). Everything else - the command format, the chips, the stop button - is identical.
- **Testing without any external server:** `test_mcp.py` next to `bridge.py` is a tiny filesystem MCP server (list_files / read_file / write_file, rooted at the directory you pass) - add `{"testfs": {"command": "test_mcp.py", "args": ["/path/to/dir"]}}` to `config.json` to verify the whole flow end-to-end.

The status dot goes green when any connected server has tools, and the bar always tells you which server is up.

### Use your servers from an external MCP client (e.g. Arena Agent Mode)

The Bridge also exposes the exact same tools over **MCP over HTTP** (Streamable HTTP transport), so any external MCP client can drive the same local servers without the browser extension. This is how to connect **Arena.ai Agent Mode** (or Claude Desktop, Cursor, or any other HTTP MCP client):

1. **Run the Bridge** (step 3 below). The console window prints a line like:
   ```
   HTTP MCP endpoint (external clients): http://127.0.0.1:17614/mcp/9f2ab3c1...
   ```
   The long tail is a random access token - **the URL itself is the credential**. Keep it secret, treat it like a password. (The port is fixed to `17614` by default and binds to `127.0.0.1` only; set `ZS_MCP_HTTP_PORT=0` to turn the endpoint off.)
2. **Open a public tunnel to it** (the client must be able to reach your PC). In a terminal:
   ```
   cloudflared tunnel --url http://127.0.0.1:17614
   ```
   Cloudflared prints a public `https://....trycloudflare.com` URL. (Any tunnel that forwards to that local port works.)
3. **Add the endpoint in your client.** For Arena Agent Mode: paste
   ```
   https://<your-tunnel-host>.trycloudflare.com/mcp/9f2ab3c1...
   ```
   (the tunnel host + the `/mcp/<token>` path printed in step 1) into the MCP server settings and connect.
4. The client now sees the same tool set the extension uses, and can drive your servers directly.

Notes:

- The endpoint is **read/write**: an external client can do everything the extension can. Only connect clients you trust.
- The token changes every time the Bridge restarts, so a stale URL stops working (404) after a restart - that is intentional.
- Kill the tunnel when you are done; nobody else can reach your machine while the tunnel is up.

## Setup

> 📺 **Lost? Watch the [setup tutorial on YouTube](https://youtu.be/kPKiZLZ9_Ps) it covers every step below.**

### 1. Download the zip and install the extension

Download the latest zip from the **Releases** page and extract it. The zip contains both the **Bridge** and the **extension folder**.

To load the extension:

- Go to `edge://extensions` (Edge) or `chrome://extensions` (Chrome)
- Enable **Developer mode** (top right toggle)
- Click **Load unpacked**
- Select the `zeroscript-extension` folder from the extracted zip

### 2. Add an MCP server

In the ⋯ menu of the ZeroScript bar (open on any supported AI page after the bridge is running) → **MCP servers** → type a name and a start command → **Add server**. Or edit `config.json` next to `bridge.py` directly (see the example above). The `test_mcp.py` bundled with the bridge works out of the box if you just want to try the flow.

### 3. Run the Bridge

- **Windows:** double-click `start.bat` inside the extracted folder.
- **macOS:** double-click `MacOS_Start.command` inside the extracted folder. The first time, macOS will show a security warning ("could not verify... free of malware") - this is normal for any script downloaded outside the App Store, click **Done**, then go to **System Settings > Privacy & Security**, scroll to the bottom, and click **Open Anyway**. You only need to do this once.

A small window opens, that means the Bridge is running.

### 4. Start a session

Go to https://chat.deepseek.com (recommended), https://chatgpt.com, https://gemini.google.com, https://www.kimi.ai, https://chat.z.ai, https://chat.qwen.ai, https://arena.ai or https://www.meta.ai and open a new chat. The ZeroScript bar appears above the input box. Click **Start session**. Type what you want to do.

> Only works on chat.deepseek.com, chatgpt.com, gemini.google.com, kimi.ai, chat.z.ai, chat.qwen.ai, arena.ai and meta.ai - it will not work on any other site.
> On Arena, keep the mode dropdown on **Direct** - ZeroScript blocks Start in Battle / Side-by-Side / Agent modes (it only drives a single Direct reply).
> Gemini and Kimi can be unstable (model behavior, not the extension): Gemini may stop using the ZeroScript commands after a while, and Kimi may use its own native tools instead. If the AI starts answering in plain text instead of acting, remind it to use the commands or start a new session.

### 5. Watch the setup tutorial

[Watch the setup tutorial on YouTube](https://youtu.be/kPKiZLZ9_Ps)

## What the AI can do

- Whatever your MCP servers expose: read/edit files, run git commands, drive Blender, query a database, ...
- **Discover what's connected** on its own with `list_mcp_servers` / `list_commands`
- **Reply in your language** - the agent mirrors the language you write in
- **Run from external clients** too (Arena Agent Mode, Claude Desktop, ...) over the HTTP endpoint
- **Never hang the chat** - every command always produces a result, success or a formatted error

## New in 2.0.0

- **Fully generic.** All Roblox Studio machinery is gone: the default server set is empty, the bridge only manages whatever you configure, and the prompt/UI speak only in terms of MCP servers. Adding your first server takes 30 seconds (⋯ menu → MCP servers, or `config.json`).
- **External MCP clients over HTTP.** Streamable HTTP endpoint on `127.0.0.1:17614` with a per-launch token - connect Arena Agent Mode, Claude Desktop, Cursor, or any HTTP MCP client through a tunnel (see "Use your servers from an external MCP client" above).
- **Bundled test server.** `test_mcp.py` gives you a zero-dependency filesystem MCP server to verify the whole flow before installing anything.

## Panel status

| Dot | Meaning |
|-----|---------|
| Green | Bridge + at least one MCP server ready (has tools) |
| Yellow | Bridge OK, but no MCP server is usable yet - check the ⋯ menu → MCP servers (a server that had tools is restarting shows the same dot for a moment) |
| Grey | Bridge offline - run start.bat (Windows) or MacOS_Start.command (macOS) |

## Requirements

- Windows or macOS
- Microsoft Edge or Chrome
- Python 3.9+ (installed automatically on Windows, or install it yourself on macOS - see [python.org/downloads](https://www.python.org/downloads/))
- The MCP server(s) you want to use (any stdio MCP server)

## Support

ZeroScript is free. If it saves you time: [Ko-fi](https://ko-fi.com/sebattfg) - or star the repo

---

Credit: the idea for connecting other MCP servers (Blender, Sketchfab, etc.) came from [javnpa](https://github.com/javnpa).

Credit: macOS/Linux support contributed by [archivealf](https://github.com/archivealf).
