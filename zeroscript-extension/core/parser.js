// SPDX-License-Identifier: GPL-3.0-or-later
// core/parser.js - ZeroScript command parsing. PURE string logic, zero DOM:
// the provider extracts a turn's text from the site's DOM; everything here
// operates on that text. The command format ({"command":…} JSON, optionally
// wrapped in ###MCP_TOOL### markers) is defined by OUR system prompt, so it is
// the same on every AI site - only the way a site's markdown may mangle it
// differs.
// eslint-disable-next-line no-unused-vars
const ZSParse = (() => {
  "use strict";

  const START_M = "###MCP_TOOL###";
  const END_M = "###END_MCP_TOOL###";

  // ── Marker vocabulary ────────────────────────────────────────────────────
  // The ONE place the wrapper's spelling is defined. Every consumer (this
  // parser, core/main.js's classify ladder, a provider's camouflage sweep) asks
  // these predicates instead of re-deriving the strings or reaching for a regex
  // that may no longer exist: the 2.0.0 Roblox strip deleted LUA_START_RE from
  // the exports while deepseek.js still called `.test()` on it, so every
  // DeepSeek tool call died with "Cannot read properties of undefined (reading
  // 'test')" BEFORE runTool() and the model never got a result.
  // Tolerant on purpose: markdown spaces the hashes (`### MCP_TOOL ###`) and the
  // closer is written with an underscore OR a dash. Detection and extraction
  // must agree on what a marker is, or a block gets registered as a command and
  // then not found by the extractor (a dead turn).
  const START_RE = /###\s*mcp[_\- ]?tool\s*###/i;
  const END_RE = /###\s*end[_\- ]?mcp[_\- ]?tool\s*###/i;
  const hasStartMarker = (t) => !!t && START_RE.test(t);
  const hasEndMarker = (t) => !!t && END_RE.test(t);

  // A command is `{"command":"name", ...}` (or "tool"). The params/arguments
  // object is OPTIONAL: paramless commands like list_commands are written as
  // `{"command":"list_commands"}`, so requiring "params" too would MISS them
  // (they'd be shown raw and never executed). We key on the `"command":"…"` /
  // `"tool":"…"` shape instead - a string-valued key, which prose almost never
  // contains - so paramless calls are detected without false-positiving on text.
  const CMD_KEY_RE = /"(?:command|tool)"\s*:\s*"/;

  // DeepSeek's OWN agentic tool-call markup ("DSML"), which it sometimes emits
  // instead of a ZeroScript command - seen live in user reports, DeepSeek only.
  // It carries no "command"/"tool" key and no ###…### markers, so every guard in
  // the classify ladder missed it and the turn finalized as a plain-text answer:
  // the tool never ran and the loop ended with the user watching a dead agent.
  // Matches the opener AND the closing "</|DSML|>…" form. Verified live
  // 2026-08-22: DeepSeek emits PLAIN ASCII bars, `<|DSML|>tool_calls>`. The
  // doubled "< |  | DSML |  |" seen in user screenshots is only how the site
  // RENDERS it, but the class is deliberately permissive - any run of bars and
  // spaces on either side - so a build that really does emit doubled or
  // full-width (U+FF5C) bars is covered too, without needing another sample.
  // Deliberately NOT gated on a known tool name: the degenerate case seen in
  // the wild is a bare `<|DSML|>tool_calls>` with no invoke and no name at
  // all, and the marker itself never occurs in ordinary prose.
  const DSML_RE = /<[\s\/]*[|｜][\s|｜]*DSML[\s|｜]*[|｜]/i;

  function hasToolSignature(r) {
    // A CLOSER with no opener counts too: that is the model having written a
    // command and mis-written the opening marker. Handing it to the parse path
    // makes the classify ladder nudge a rewrite instead of silently treating the
    // turn as a final answer (a dead turn with nothing on screen).
    return (
      hasStartMarker(r) ||
      hasEndMarker(r) ||
      CMD_KEY_RE.test(r)
    );
  }

  // True if the reply contains a tool block that has STARTED but not yet CLOSED
  // (a ###MCP_TOOL### opener with no matching end marker). Used by the
  // response watcher to avoid finalizing a command that is still being streamed.
  function hasOpenToolBlock(r) {
    if (!r) return false;
    if (hasStartMarker(r) && !hasEndMarker(r)) return true;
    // An inline JSON command ({"command"/"tool": …}) whose object has NOT closed yet
    // is still being streamed (a big command with many parameters can take many
    // seconds). Treat it as open so the watcher keeps waiting instead of
    // finalizing - and failing to parse - half a command, which would drop the
    // tool and end the turn as plain text.
    for (const key of ['"command"', '"tool"']) {
      const k = r.indexOf(key);
      if (k === -1) continue;
      const open = r.lastIndexOf("{", k);
      if (open !== -1 && matchBrace(r, open) === -1) return true;
    }
    return false;
  }

  // Normalise a parsed JSON object into { tool, arguments }, accepting both the
  // new ZeroScript schema ("command"/"params") and the legacy/function-calling
  // schema ("tool"/"arguments"/"name"/"args"). Returns null if not a valid call.
  function normalizeCall(o) {
    if (!o || typeof o !== "object") return null;
    const name = o.command != null ? o.command : (o.tool != null ? o.tool : o.name);
    let args = o.params != null ? o.params : (o.arguments != null ? o.arguments : o.args);
    if (typeof name !== "string" || !name) return null;
    if (!args || typeof args !== "object") args = {};
    return { tool: name, arguments: args };
  }

  // String-aware matching-brace finder: index of the "}" that closes the "{" at
  // `start`, SKIPPING braces inside JSON string literals (escaped quotes handled).
  // A naive depth counter miscounts the braces embedded in code passed as a string
  // value (e.g. a snippet inside a "code" parameter), grabs the wrong end, and
  // makes JSON.parse fail - which silently dropped the command, so the tool never
  // ran and the turn was treated as a plain-text answer. Returns -1 if unbalanced.
  function matchBrace(text, start) {
    let depth = 0, inStr = false, esc = false;
    for (let i = start; i < text.length; i++) {
      const c = text[i];
      if (inStr) {
        if (esc) esc = false;
        else if (c === "\\") esc = true;
        else if (c === '"') inStr = false;
      } else if (c === '"') inStr = true;
      else if (c === "{") depth++;
      else if (c === "}") { if (--depth === 0) return i; }
    }
    return -1;
  }

  // JSON.parse with a fallback for RAW control characters inside string
  // literals (tab/newline/CR). Models sometimes emit a literal TAB instead of
  // \t inside a command's string value (seen live on Gemini in a big command);
  // strict JSON rejects it, the parse failed silently and the command was never
  // executed. The fallback walks the text string-aware and escapes those
  // characters ONLY inside string literals, then re-parses.
  function parseLoose(raw) {
    try {
      return JSON.parse(raw);
    } catch (e0) {
      let out = "", inStr = false, esc = false;
      for (const c of raw) {
        if (inStr) {
          if (esc) { esc = false; out += c; continue; }
          if (c === "\\") { esc = true; out += c; continue; }
          if (c === '"') { inStr = false; out += c; continue; }
          if (c === "\t") { out += "\\t"; continue; }
          if (c === "\n") { out += "\\n"; continue; }
          if (c === "\r") { out += "\\r"; continue; }
          out += c;
          continue;
        }
        if (c === '"') inStr = true;
        out += c;
      }
      return JSON.parse(out); // may still throw - callers catch
    }
  }

  function extractJson(raw) {
    raw = raw.trim().replace(/^(?:json|JSON)\s*/, "");
    raw = raw.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "").trim();
    const s = raw.indexOf("{");
    if (s === -1) return null;
    const e = matchBrace(raw, s);            // string-aware: not the last "}" in code
    if (e === -1) return null;
    try {
      return parseLoose(raw.slice(s, e + 1));
    } catch {
      return null;
    }
  }

  function extractToolAnywhere(text) {
    for (const key of ['"command"', '"tool"']) {
      let pos = 0;
      while (true) {
        const s = text.indexOf(key, pos);
        if (s === -1) break;
        const start = text.lastIndexOf("{", s);
        if (start === -1) { pos = s + 1; continue; }
        const end = matchBrace(text, start); // string-aware brace matching
        if (end === -1) { pos = s + 1; continue; }
        try {
          const call = normalizeCall(parseLoose(text.slice(start, end + 1)));
          if (call) return call;
        } catch {}
        pos = s + 1;
      }
    }
    return null;
  }

  // Locate a marker's POSITION at or after `from`. The extractor needs offsets,
  // not just a yes/no, so these mirror the predicates above off the same two
  // regexes - a marker the predicates recognise is always one these can slice.
  function findStartMarker(text, from = 0) {
    const m = START_RE.exec(text.slice(from));
    return m ? { pos: from + m.index, len: m[0].length } : { pos: -1, len: 0 };
  }

  function findEndMarker(text, from = 0) {
    const m = END_RE.exec(text.slice(from));
    return m ? { pos: from + m.index, len: m[0].length } : { pos: -1, len: 0 };
  }

  function parseToolCalls(r) {
    const out = [];
    let from = 0;
    while (true) {
      const sm = findStartMarker(r, from);
      if (sm.pos === -1) break;
      const em = findEndMarker(r, sm.pos + sm.len);
      if (em.pos === -1) break;
      const body = r.slice(sm.pos + sm.len, em.pos);
      for (const sub of body.split(START_M)) {
        const cleaned = sub.trim().replace(/^(?:json|JSON|Copy|copy)\s*/i, "").trim();
        if (!cleaned) continue;
        const p = normalizeCall(extractJson(cleaned));
        if (p) out.push(p);
      }
      from = em.pos + em.len;
    }
    // Prefer a JSON command envelope when one is present anywhere in the turn.
    if (out.length === 0) {
      const f = extractToolAnywhere(r);
      if (f) out.push(f);
    }
    return out;
  }

  // Last-resort salvage of a CUT-OFF JSON command: the model hit its output
  // limit with the whole payload complete but the trailing closers missing
  // (seen live on Qwen: a big command missing exactly ONE final "}").
  // Strictly conservative - we only auto-close when it is provably just the
  // closing sequence that was lost, never when actual content was amputated:
  //  - the scan must NOT end inside a string literal (a value cut mid-string
  //    means real content is missing → keep the parse_error);
  //  - the last non-whitespace char must terminate a complete JSON value
  //    (`"`, `}`, `]`, digit, or the tail of true/false/null);
  //  - at most MAX_SALVAGE_CLOSERS closers may be appended. 2 covers the
  //    root-brace and params-brace cases; a deeper deficit usually means the
  //    cut fell between items, where running a partial command would be
  //    dangerous - the retry feedback stays the right call.
  // Callers must only invoke this once generation has ENDED (the watcher's
  // parse_error branch), never on a still-streaming reply.
  const MAX_SALVAGE_CLOSERS = 2;
  function salvageCutOff(text) {
    for (const key of ['"command"', '"tool"']) {
      const k = text.indexOf(key);
      if (k === -1) continue;
      const start = text.lastIndexOf("{", k);
      if (start === -1) continue;
      if (matchBrace(text, start) !== -1) continue; // closed → not our case
      // String-aware bracket stack from the opener to the end of the text.
      const stack = [];
      let inStr = false, esc = false;
      for (let i = start; i < text.length; i++) {
        const c = text[i];
        if (inStr) {
          if (esc) esc = false;
          else if (c === "\\") esc = true;
          else if (c === '"') inStr = false;
        } else if (c === '"') inStr = true;
        else if (c === "{") stack.push("}");
        else if (c === "[") stack.push("]");
        else if (c === "}" || c === "]") {
          if (stack.pop() !== c) return null; // mismatched nesting → hopeless
        }
      }
      if (inStr) return null;                       // cut mid-string value
      if (!stack.length || stack.length > MAX_SALVAGE_CLOSERS) return null;
      const body = text.slice(start).trimEnd();
      if (!/["}\]0-9]$|(?:true|false|null)$/.test(body)) return null; // value incomplete
      try {
        const call = normalizeCall(parseLoose(body + stack.reverse().join("")));
        if (call) return call;
      } catch {}
      return null;
    }
    return null;
  }

  function toolNameFromText(txt) {
    // Match the name even BEFORE its closing quote (`[^"]*`), so the chip shows
    // the real command name AS IT IS TYPED instead of a generic "command"
    // placeholder until the JSON closes. A still-empty value falls through.
    // Trim: while the value is still streaming it can be whitespace-only (e.g.
    // some sites render `"tool": "    "` for a beat), which would otherwise show
    // a blank chip label until the loop repaints it. A whitespace value falls
    // through to the placeholder below instead.
    const m = txt.match(/"(?:command|tool)"\s*:\s*"([^"]*)/);
    if (m && m[1].trim()) return m[1].trim();
    return "command";
  }

  // A turn the EXTENSION injected (always sent as a user turn): a tool result, an
  // ERROR, or a "(System note: …)" control message. Matched ONLY by the fixed
  // shapes we emit - never by command-like keywords, since a parse-error note
  // quotes a {"command": …} example that must NOT be read as a real command.
  function isInjectedFeedback(txt) {
    return /^\s*Output of '/.test(txt) ||
           /^\s*ERROR\b/.test(txt) ||
           /^\s*\(System note:/.test(txt);
  }

  // The assistant emitted a ZeroScript command (JSON), or a command-turn ATTEMPT
  // the camouflage sweep should still mask. DSML counts: the turn IS the model
  // calling a tool, just in the wrong dialect, so it gets a chip like any other
  // command turn instead of dumping raw markup at the user (reported live
  // 2026-08-22 - the error fired correctly but the tags stayed on screen).
  // Note this is deliberately NOT mirrored in hasToolSignature: that one gates
  // the parse/execute path, and DSML must keep falling through to the classify
  // ladder so it fires the "dsml" parse_error rather than being handed to
  // parseToolCalls, which cannot read it.
  function hasCommandShape(txt) {
    return hasStartMarker(txt) ||
           DSML_RE.test(txt) ||
           CMD_KEY_RE.test(txt); // command/tool with OR without params (e.g. list_commands)
  }

  return {
    START_M, END_M, START_RE, END_RE, CMD_KEY_RE, DSML_RE,
    hasStartMarker, hasEndMarker, findStartMarker, findEndMarker,
    matchBrace, extractJson, normalizeCall,
    hasToolSignature, hasOpenToolBlock, parseToolCalls, salvageCutOff, toolNameFromText,
    isInjectedFeedback, hasCommandShape,
  };
})();
