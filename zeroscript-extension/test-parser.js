// Quick Node smoke test for core/parser.js (run: node test-parser.js). Not shipped.
const fs = require("fs");
const ZSParse = new Function(fs.readFileSync(__dirname + "/core/parser.js", "utf8") + "; return ZSParse;")();

const ok = (name, cond) => { console.log((cond ? "PASS" : "FAIL") + "  " + name); if (!cond) process.exitCode = 1; };

const paramless = ZSParse.parseToolCalls('{"command":"list_commands"}');
ok("paramless command", paramless.length === 1 && paramless[0].tool === "list_commands");

const braces = ZSParse.parseToolCalls('{"command":"write_file","params":{"code":"if (x) { y(); }"}}');
ok("braces inside string value", braces.length === 1 && braces[0].arguments.code === "if (x) { y(); }");

const legacy = ZSParse.parseToolCalls('{"tool":"read_file","arguments":{"path":"notes.md"}}');
ok("legacy tool/arguments schema", legacy.length === 1 && legacy[0].tool === "read_file");

const mcp = ZSParse.parseToolCalls('###MCP_TOOL###\n{"command":"list_files"}\n###END_MCP_TOOL###');
ok("mcp_tool wrapper", mcp.length === 1 && mcp[0].tool === "list_files");

const mcpJsonFence = ZSParse.parseToolCalls('###MCP_TOOL###\n```json\n{"command":"list_files"}\n```\n###END_MCP_TOOL###');
ok("mcp_tool wrapper with json fence", mcpJsonFence.length === 1 && mcpJsonFence[0].tool === "list_files");

// Kimi bleeds its code-block "Copy" button caption into the block text right
// after the fence: the cleaned body must not keep the "Copy" chrome.
const mcpCopy = ZSParse.parseToolCalls('###MCP_TOOL###\nCopy {"command":"list_files"}\n###END_MCP_TOOL###');
ok("strips Copy chrome from wrapper body", mcpCopy.length === 1 && mcpCopy[0].tool === "list_files");

ok("open json command detected", ZSParse.hasOpenToolBlock('{"command":"write_file","params":{"a":1') === true);
ok("open mcp_tool wrapper detected", ZSParse.hasOpenToolBlock('###MCP_TOOL###\n{"command":"list_files"}') === true);
ok("closed mcp_tool wrapper not open", ZSParse.hasOpenToolBlock('###MCP_TOOL###\n{"command":"list_files"}\n###END_MCP_TOOL###') === false);

ok("prose has no signature", ZSParse.hasToolSignature("Here is how you could use a command in theory.") === false);
ok("command shape detected", ZSParse.hasCommandShape('{"command":"x"}') === true);
ok("injected feedback detected", ZSParse.isInjectedFeedback("Output of 'list_files':\n2") === true);
ok("parse-error note is feedback not command", ZSParse.isInjectedFeedback('ERROR: bad JSON, write {"command": "name"}') === true);
ok("tool name mid-stream", ZSParse.toolNameFromText('{"command":"write_fi') === "write_fi");

// ── salvageCutOff: auto-close a command whose trailing closers were cut ──
// The live Qwen case: a big command missing exactly ONE final "}".
const cut1 = ZSParse.salvageCutOff('{"command": "write_file", "params": {"path": "notes.md", "content": "a"}');
ok("salvage: one missing root brace", cut1 && cut1.tool === "write_file" && cut1.arguments.content === "a");
// Two missing closers (params + root) still salvages.
const cut2 = ZSParse.salvageCutOff('{"command": "list_files", "params": {"verbose": true');
ok("salvage: two missing closers", cut2 && cut2.tool === "list_files" && cut2.arguments.verbose === true);
// Cut MID-STRING = real content amputated -> refuse.
ok("salvage refuses mid-string cut", ZSParse.salvageCutOff('{"command": "write_file", "params": {"content": "elseif command ==') === null);
// Deep deficit (cut between items: ] } } missing = 3 closers) -> refuse.
ok("salvage refuses deep deficit", ZSParse.salvageCutOff('{"command": "write_file", "params": {"tags": ["a", "b"') === null);
// A CLOSED command is not salvage's business.
ok("salvage ignores closed command", ZSParse.salvageCutOff('{"command": "list_commands"}') === null);
// Dangling comma after the last complete value = incomplete next value -> refuse.
ok("salvage refuses trailing comma", ZSParse.salvageCutOff('{"command": "write_file", "params": {"tags": ["a"],') === null);
// Escaped quotes inside values must not confuse the string tracking.
const cutEsc = ZSParse.salvageCutOff('{"command": "write_file", "params": {"path": "q.txt", "content": "say(\\"hi\\")"}');
ok("salvage handles escaped quotes", cutEsc && cutEsc.tool === "write_file" && cutEsc.arguments.content === 'say("hi")');

// ── DeepSeek's native DSML tool-call markup ────────────────────────────────
// DeepSeek sometimes answers in its own agentic markup instead of a ZeroScript
// command. It has no "command"/"tool" key and no ###...### markers, so the
// classify ladder used to miss it entirely and the turn died as plain text.
// DSML_RE is what fires the "dsml" parse_error that asks for a rewrite.
const dsmlFull = [
  '<|DSML|>tool_calls>',
  '<|DSML|>invoke name="read_file">',
  '<|DSML|>parameter name="path" string="true">notes/project.md</|DSML|>parameter>',
  '</|DSML|>invoke>',
  '</|DSML|>tool_calls>',
].join("\n");
ok("dsml full invoke block", ZSParse.DSML_RE.test(dsmlFull));
// The degenerate form seen in the wild: a bare opener and NO tool name at all -
// which is why the guard must not be gated on a known command name.
ok("dsml bare opener + prose",
   ZSParse.DSML_RE.test('<|DSML|>tool_calls>\n\n<section>Let me explore the remaining files.</section>'));
// DeepSeek writes its special tokens with the FULL-WIDTH bar (U+FF5C).
ok("dsml full-width bar", ZSParse.DSML_RE.test('<｜DSML｜>invoke name="read_file">'));
// The form as it appeared in user screenshots - doubled bars with spaces. The
// live capture (2026-08-22) showed DeepSeek actually emits plain ASCII bars and
// that this spacing is only the site's rendering, but the detector stays
// permissive so a build that really emits it is covered.
ok("dsml doubled bars with spaces", ZSParse.DSML_RE.test('< |  | DSML |  | tool_calls>'));
ok("dsml doubled-bar closer", ZSParse.DSML_RE.test('</ |  | DSML |  | parameter>'));
ok("dsml closing tag alone", ZSParse.DSML_RE.test('</|DSML|>parameter>'));
// DSML is NOT a ZeroScript command shape: it must reach the fallthrough guards.
ok("dsml is not a tool signature", !ZSParse.hasToolSignature(dsmlFull));
// DSML must NOT be a tool signature (it has to fall through to the classify
// ladder so the "dsml" parse_error fires) but it MUST be a command shape, so the
// camouflage sweep masks the raw markup behind a chip instead of showing it.
ok("dsml IS a command shape (so it gets masked)", ZSParse.hasCommandShape(dsmlFull));
ok("dsml bare opener is a command shape too", ZSParse.hasCommandShape('<|DSML|>tool_calls>'));
// No false positives: ordinary prose, a real command, and - critically - OUR OWN
// error note, which names DSML in words. If the note matched, the model echoing
// it would re-fire the error forever.
ok("no dsml false positive on prose",
   !ZSParse.DSML_RE.test("I considered the DSML invoke and parameter tags, but used JSON instead."));
ok("no dsml false positive on a real command",
   !ZSParse.DSML_RE.test('{"command": "read_file", "params": {"path": "x"}}'));
