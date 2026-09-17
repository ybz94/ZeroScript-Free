// SPDX-License-Identifier: GPL-3.0-or-later
// core/config.js - provider-agnostic constants: app identity, system prompt,
// feedback strings, tool categorisation. NOTHING in this file may reference a
// specific AI site (DOM, selectors, site names) - that lives in providers/*.
// eslint-disable-next-line no-unused-vars
const ZS = (() => {
  "use strict";

  // Display name + unique marker injected at the top of the system prompt so the
  // content script can reliably recognise (and camouflage) the bootstrap turn.
  const APP_NAME = "ZeroScript";
  const SYS_MARKER = "⟦ZS-SYS⟧";
  // A re-statement of the system prompt mid-session (see withSysResend in
  // core/main.js). It carries SYS_MARKER TOO - that is what drives camouflage
  // and session detection, and neither should change - plus this second marker,
  // purely so the chip can say "Reminder" instead of inheriting the bootstrap's
  // "Starting Up". Same content, different label: a re-injection is not a start.
  const RESEND_MARKER = "⟦ZS-RE⟧";

  // ── Tool → visual category (icon + colour theme for the chips) ─────────
  // Generic name-based heuristics that work for ANY MCP server: read-ish
  // commands get the "read" theme, destructive-ish ones "edit", captures
  // "screen", generators "generate", everything else a plain "tool".
  // Returns one of: read | edit | screen | generate | tool
  function toolCategory(name) {
    const n = (name || "").includes("/") ? name.split("/").pop() : (name || "");
    if (/^(list|search|get|inspect|read|query)_/.test(n) || n === "list_commands" || n === "list_tools")
      return "read";
    if (n === "screen_capture" || /^screenshot/.test(n) || /_capture$/.test(n)) return "screen";
    if (/^generate_/.test(n) || /^create_/.test(n)) return "generate";
    if (/edit|write|create|insert|delete|remove|set_|update/.test(n)) return "edit";
    return "tool";
  }

  // Feedback strings sent back to the model so it can self-correct.
  const FEEDBACK = {
    // A command-shaped reply that could not be turned into a runnable call.
    // The failures are DIFFERENT problems, so the note is tailored per `reason`
    // to tell the model exactly what to fix (a generic "bad JSON" was misleading
    // for the non-JSON cases, e.g. a missing opener). Falls back to the generic
    // "malformed" text for any unrecognised reason.
    parseError: (reason, toolName) => {
      const notes = {
        malformed:
          "ERROR: a ZeroScript command was detected in your reply but its JSON could not be parsed. " +
          'Rewrite it as a single valid JSON object in plain text, exactly like {"command": "name", "params": {...}}. ' +
          "You may add a short note around it. Please retry.",
        unclosed:
          "ERROR: your ZeroScript command was cut off before it finished - the JSON object " +
          "never closed, so it could not run. Rewrite the WHOLE command in one " +
          'piece as valid JSON, exactly like {"command": "name", "params": {...}}. Please retry.',
        envelope:
          "ERROR: you wrote a command's parameters as a bare JSON object, but without the required " +
          "envelope, so it was not recognised as a command. Wrap them like " +
          '{"command": "name", "params": { ...your parameters... }} - the parameter keys go INSIDE ' +
          '"params". Please retry.',
        // The model named a REAL tool but under the wrong key - it wrote the call
        // the way a function-calling API would (e.g. {"toolName": "some_tool",
        // "arg1": "..."}) instead of ZeroScript's envelope. Naming the wrong
        // keys explicitly matters: a generic "bad JSON" note made the model
        // rewrite the SAME shape.
        toolKey:
          "ERROR: you used the wrong key to name the command, so it was not recognised and did not " +
          'run. The key must be exactly "command" - not "toolName", "tool", "name", "function" or ' +
          '"action" - and every argument goes INSIDE "params", like ' +
          '{"command": "name", "params": { ...your parameters... }}. Please retry.',
        // DeepSeek sometimes falls back to its own native agentic markup. The
        // note must NEVER quote the markers literally: the reply that follows
        // often echoes the wording, and a quoted marker would re-trigger the
        // detector and loop the error forever. Describe it, don't reproduce it.
        dsml:
          "ERROR: you wrote that call in your own internal tool-call markup (the DSML invoke/parameter " +
          "tags). ZeroScript cannot read that format, so the command did not run. Never use those tags " +
          "here. Write the call as a single plain-text JSON object instead, exactly like " +
          '{"command": "name", "params": { ...your parameters... }} - one command per reply. ' +
          "Please retry.",
      };
      return notes[reason] || notes.malformed;
    },
    multiTool: (names) =>
      "ERROR: You wrote multiple commands in one reply. Write ONE command at a " +
      "time and wait for its result before the next. You tried: " +
      names.join(", ") +
      ". Start over and write only the first command you need.",
    unknownTool: (name, valid) =>
      `ERROR: unknown command "${name}". It does not exist. Valid commands are: ` +
      valid.join(", ") +
      ". Use an exact name and parameter keys from the system prompt.",
    // The page outlived the extension build it was running (reload / auto-update
    // / disable+enable). Nothing here can recover it - only a page reload can -
    // so the model must NOT be told the bridge is down and must NOT retry, or it
    // burns the whole conversation re-issuing commands that can never run. See
    // isContextInvalidated in core/main.js.
    staleExtension:
      "ERROR: the ZeroScript extension was reloaded or updated while this page was open, so this " +
      "tab is running a version of it that no longer exists and NO command can reach the user's " +
      "machine from here. The bridge and its MCP servers are NOT the problem - do not tell the user " +
      "to check them, and do not retry the command, because every retry will fail the same way. " +
      "Tell the user in one short sentence to RELOAD THIS PAGE (F5), then stop and wait.",
    bridgeOffline:
      "ERROR: the local ZeroScript bridge is unreachable, so no command could run. " +
      "This is an environment problem on the user's machine (the bridge is not " +
      "running, or a configured MCP server cannot be reached), NOT your mistake. " +
      "Tell the user in one short sentence that the bridge or an MCP server is " +
      "offline, then stop sending commands until they confirm it is back.",
    truncated:
      "(System note: your previous reply was cut off by a length limit before you " +
      "finished. Continue from exactly where you stopped. Do NOT restart and do " +
      "NOT repeat what you already wrote.)",
  };

  function compactTools(tools) {
    return (tools || [])
      .map((t) => {
        const name = t.name || "?";
        const desc = (t.description || "").split("\n")[0].trim();
        const props = (t.inputSchema && t.inputSchema.properties) || {};
        const args = Object.keys(props).join(", ");
        return `  ${name}(${args}) - ${desc}`;
      })
      .join("\n");
  }

  // ── System prompt ─────────────────────────────────────────────────────────
  // ONE unified prompt sent to every AI on the first turn. To change the
  // wording, just edit the text below - it is a single template, no profiles
  // or branching on the AI SITE. `${siteName}` is filled in with the AI's
  // display name (e.g. "DeepSeek").
  //
  // `opts` may be a string (just the siteName) or an object { siteName,
  // customPrompt, providerNotes, servers, toolNames, userLang }. `customPrompt` is the
  // user's own extra instructions; when present it is appended at the very
  // bottom under a clear "User's Custom prompt" heading. It NEVER edits the
  // prompt above - it only adds a layer below it.
  //
  // `userLang` is the user's browser language (e.g. "zh-CN"); it selects the
  // reply-language rule (Chinese browser → explicit 简体中文 directive,
  // anything else → mirror the user's language).
  //
  // `servers` is the live per-server health list from the bridge
  // ([{id, alive, tools}]) and `toolNames` the advertised tool names - kept
  // in the signature for the future, the wording below is server-agnostic.
  //
  // `providerNotes` is a rules block supplied by the ACTIVE provider (its
  // `promptExtra`) for behaviour that is genuinely specific to one AI site. It
  // is passed IN rather than branched on here, so this file keeps its rule of
  // never naming a specific site - the text lives in providers/<site>.js and
  // every other provider is untouched by definition.
  function buildSystemPrompt(opts = {}) {
    if (typeof opts === "string") opts = { siteName: opts };
    const { siteName = "this AI site", customPrompt = "", providerNotes = "", userLang = "" } = opts;

    const intro = `CONTEXT: the user has installed a browser extension called ZeroScript in their own browser. Here is how it works, so you can use it on their behalf:
A browser extension (ZeroScript) is running inside this page. It watches your replies. When it detects a ZeroScript command in your text, it runs it against one or more connected MCP servers and sends the result back as the next message. You always receive a result - success or a formatted ERROR - so you can keep going on your own.

`;
    // What is connected + how to discover it. The model is NEVER told which
    // servers exist upfront - it must ask, so the prompt can't go stale.
    const discovery = `One or more local MCP servers are connected through a local bridge, each with its own command set - you are NOT told which ones upfront. So call \`list_mcp_servers\` FIRST to see what is connected, then \`list_commands\` with a "server" param (one of the ids list_mcp_servers shows) for the exact commands of the server(s) you need. The MOMENT the user names ANY app/tool/target you don't recognise as one of the connected servers (e.g. "Blender", "Sketchfab", or anything else), you MUST run \`list_mcp_servers\` FIRST, before replying - never answer from your own assumptions or prior knowledge about what is or isn't connected. Only after checking may you tell the user something is unsupported. You do not need any special capability yourself - you just write text. The extension does the rest.

`;
    const sandboxNote = `CRITICAL - technical note, not a restriction: this site's own tools (code interpreter, function calling, connectors, etc.) run in a separate sandbox that has no access to the user's machine or the MCP servers above - so calling them wouldn't reach the user's files or apps at all. ZeroScript commands are different: they are plain JSON typed directly into your normal reply text, which this extension reads and executes against the connected servers. So for anything involving the user's machine or projects, write the JSON below as ordinary text instead of calling a function - that's the only channel that actually reaches their machine. (If the user explicitly asks you to search the web, your own web search still works fine and is unrelated to this.) Internal reasoning (deep-think modes) is fine.

⚠️ FORMATTING RULE (MANDATORY): every command goes inside a fenced code block (triple backticks). Outside a code block this page renders your text as Markdown - it turns things like \`someFunction()\` into links and mangles special characters, silently CORRUPTING the command. Inside a code block it is kept verbatim.

━━━ STANDARD COMMAND FORMAT ━━━
Write this JSON object inside a fenced code block, replacing the placeholders with a REAL command name and its parameters (never type "command_name" literally - it is not a command):
\`\`\`json
{
  "command": "command_name",
  "params": {"key": "value"}
}
\`\`\`
For example, to list every available command you would write \`\`\`{"command": "list_commands"}\`\`\`.

`;
    // Reply-language rule. The prompt and the tool results are English, so
    // without an explicit instruction the model drifts into English even when
    // the user writes Chinese/Japanese/etc. Chinese browsers get an explicit
    // 简体中文 directive; every other language gets "mirror the user".
    // Commands themselves must NEVER be translated either way.
    const langRule = (String(userLang || "").toLowerCase().startsWith("zh")
      ? "- LANGUAGE: the user's browser is in Chinese - reply in 简体中文 (Simplified Chinese): every explanation, note around a command, and final answer must be in Chinese. If the user writes in another language, follow THEIR language instead. "
      : "- LANGUAGE: reply to the user in the SAME language they write to you in (they write Chinese → answer in Chinese, Japanese → Japanese, English → English). "
    ) + "NEVER translate the commands themselves: ZeroScript commands, JSON, tool names, parameter keys, code and file paths stay exactly as the command list specifies - only your own prose follows the user's language.";
    const rules = [
      langRule,
      "- ONE command block per reply, inside a fenced code block. If you need several, do them one at a time and wait for each result. (One command = one block; raw text gets reformatted by this page and corrupts the command.)",
      "- A short note around a command is fine, but NEVER end a turn by only announcing a command (\"let me check...\", \"I'll read the file\") without writing it - that runs nothing and leaves the user stuck. Either write the command now, or give your final answer.",
      "- Final answers: plain text only, no Markdown or code fences. Do ONLY what was asked - fewest commands, no unrequested double-checks. When the task is done or the user is satisfied (\"thanks\", \"perfect\"...), reply ONE short sentence and STOP.",
      `- Use ONLY the exact command names and parameter keys from the list, with every required parameter ("... is required" means you omitted one). Do NOT use ${siteName}'s own features (web search, connectors...) unless the user explicitly asks.`,
      "- NEVER DELETE/OVERWRITE BROADLY: before any command that deletes or overwrites existing files, data or resources, make sure the target is EXACTLY what the user asked for - never a whole directory, database, or \"to be safe\" side-effect of a bigger change. If the change could affect more than the specific thing named by the user (e.g. a broad path or name match), STOP and ask them to confirm scope first, or run a read/list command on the target to check what it actually contains before changing it. Never delete something as a troubleshooting step (\"let me just remove it and rebuild\") without asking first.",
      "- On ERROR: read it and adapt - fix the command, try another, or tell the user plainly if it is an environment problem (the server's app is closed, bridge offline).",
      "- NEVER CLAIM THE BRIDGE OR A SERVER IS OFFLINE WITHOUT TESTING IT ON THIS TURN. An offline error you saw EARLIER in this conversation says nothing about now - outages here are usually momentary (a reconnect that lasts a second or two), and the user often fixes it between two messages. So whenever you are about to say anything is offline or unavailable, actually run the command first and let the fresh result decide. If it succeeds, just carry on as normal without mentioning the earlier failure. Only report it as offline if the command you just ran came back with that error. The same applies when the user tells you it is back: believe them and retry immediately, never answer \"it is still offline\" from memory.",
      "- On a property/attribute/value error (e.g. \"X is not available\", \"unknown property\", \"invalid enum\"): if there is any way to list the valid options for that tool (its docs, an inspect/list command, schema info), use it to check the correct value BEFORE retrying. Never guess blindly a second time.",
    ];
    const rulesBlock = "RULES:\n" + rules.join("\n") + "\n\n";
    const actBlock = `━━━ YOU CAN ACT DIRECTLY ON THE USER'S MACHINE ━━━
This extension gives you real, live access to the user's local MCP servers through the commands above - so when a task calls for running something or changing something, you're able to just do it yourself instead of writing instructions for the user to follow (they have no way to run these commands for you - only you can). When the user asks for something a connected server can do, just run it and report the result. Show code only if the user explicitly asks to see it.

`;
    const firstAction = `IMPORTANT: Your very first action is to write \`list_mcp_servers\` with no params to see which local MCP servers are connected, then \`list_commands\` with a "server" param (one of the ids shown) for the server(s) you need - never guess a command name or parameter that wasn't in those results. After receiving them, reply with exactly one short sentence confirming you are ready, then wait for the user's first request. If a listing comes back offline or empty, tell the user in one short sentence which server is offline, list what IS connected (if anything), then ask what they want to do and wait - do not act until they answer.`;

    const prompt = intro + discovery + sandboxNote + rulesBlock + actBlock + firstAction;

    // Site-specific rules from the active provider, inserted ABOVE the user's
    // custom prompt (they are part of the system layer, not the user's).
    const siteRules = providerNotes.trim()
      ? `\n\n━━━ ADDITIONAL RULES FOR THIS SITE ━━━\n${providerNotes.trim()}`
      : "";

    // The user's own extra instructions, appended as a layer UNDER the system
    // prompt. Optional - empty by default. It cannot change the rules above.
    const extra = customPrompt.trim()
      ? `\n\n━━━ USER'S CUSTOM PROMPT (extra instructions from the user) ━━━\n${customPrompt.trim()}`
      : "";

    // The marker leads the prompt; it tags the bootstrap turn for camouflage.
    return `${SYS_MARKER}\n${prompt}${siteRules}${extra}`;
  }

  // ── Curated, TESTED usage notes per command ─────────────────────────────────
  // The MCP's own schema descriptions are often thin, and models make the same
  // mistakes repeatedly. Key notes that were validated by actually running the
  // command here, keyed by BARE command name; they are appended to that command
  // in the list_commands output. Keep each note tight and concrete - it costs
  // context on every reminder. (Empty by default - add notes for YOUR servers'
  // error-prone tools.)
  const TOOL_NOTES = {
  };

  // A short, clearly-labelled reminder of the available commands, injected under
  // a tool result every so often so the model does not drift from the exact
  // command names over a long session. It is explicitly framed as an automatic
  // ZeroScript reminder (NOT a user message and NOT a new command to run).
  function toolsReminder(tools) {
    return (
      "\n\n────────────────────────────────\n" +
      "(System note from ZeroScript - this is an automatic REMINDER, not a request and not a new result. " +
      "Do NOT reply to it or run any command because of it; just keep it in mind for your next command.)\n" +
      "Reminder of the connected MCP commands (use exact names and parameter keys; " +
      "for other connected servers call list_mcp_servers):\n" +
      compactTools(tools)
    );
  }

  return {
    APP_NAME,
    SYS_MARKER,
    RESEND_MARKER,
    FEEDBACK,
    toolCategory,
    buildSystemPrompt,
    compactTools,
    toolsReminder,
    TOOL_NOTES,
  };
})();
