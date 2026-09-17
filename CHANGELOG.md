# Changelog

All notable changes to ZeroScript Free are documented here.

## [2.0.0] - 2026-09-17

### Added
- **External MCP clients can now connect to the Bridge over HTTP (Streamable HTTP).** The Bridge exposes the exact same tool set on a second, token-protected endpoint: `http://127.0.0.1:17614/mcp/<token>` (port configurable via `ZS_MCP_HTTP_PORT`, `0` disables). The per-launch random token is printed to the console and written to `mcp_http_url.txt` next to the bridge; wrong tokens get a bare 404 so the endpoint is undiscoverable. With a tunnel (e.g. `cloudflared tunnel --url http://127.0.0.1:17614`) the public URL works in any HTTP MCP client - Arena.ai Agent Mode, Claude Desktop, Cursor - driving the same local servers the extension drives. Protocol: 2025-06-18 Streamable HTTP - `initialize` with `Mcp-Session-Id` sessions, `tools/list`, `tools/call` (failures as `isError` results), `notifications/*` → 202, `DELETE` for session teardown. The URL is a credential (full read/write access, including code execution on your servers), so it is token-gated, localhost-only, random per launch, and changes on every restart.
- **Bundled test server.** `test_mcp.py` is a zero-dependency filesystem MCP server (list_files / read_file / write_file, rooted at the directory you pass, path escapes rejected) to verify the whole flow end-to-end without installing any external MCP server.

### Changed
- **Fully generic: Roblox Studio is no longer part of the project.** The bridge, the system prompt, and the status UI manage only the MCP servers you configure. The default `config.json` is now empty, the Roblox launcher (`launch_studio_mcp.py`) and all Studio connectivity/probe/recovery machinery are removed, and the prompt discovers servers via `list_mcp_servers`. This release is the clean base for building your own agent on top of the ZeroScript bridge.

### Removed
- The `roblox` default server and `launch_studio_mcp.py` (Studio MCP launcher).
- The Studio connectivity probe, place-loaded status, StudioMCP port forensics, and the studio recovery watcher in `bridge.py`.
- The Roblox sections of the system prompt (execute_luau / ###LUA### format, project memory, place discovery), the `###LUA###` command format in the parser, and the Roblox-only UI states (degraded start, place/app sub-states, Robux tips).
- The `studio`, `studio_app`, `studio_proc` fields and the `studio_status` message from the extension ↔ bridge protocol.

## [1.5.5] - 2026-09-10

Upstream release; see the original project history for details. From this fork
on, 2.0.0 removed the Roblox Studio integration described above.
