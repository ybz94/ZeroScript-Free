/* Isolated from the old agent loop: webpage output is never executed. */
(() => {
  const P = ZSProvider;
  // Mirror input_limits.py. Never enter a provider's legacy truncation path.
  const inputCaps = {deepseek:160000, chatgpt:120000, arena:118000,
                     glm:100000, kimi:100000, qwen:100000, gemini:100000, meta:100000};
  const inputMaxChars = inputCaps[P.id] || 60000;
  const inputMaxLines = P.id === 'chatgpt' ? 600 : null;
  P.init({diag: () => {}});
  let id = crypto.randomUUID(), key = P.conversationKey(), busy = false;
  const VERSION = '0.4.19';
  const seen = new Set();
  // DOM events can wake the watcher even when background timers are throttled.
  // Keep a timer fallback for generation-state changes without DOM mutations.
  function waitForChange() {
    return new Promise(resolve => {
      let observer;
      const done = () => { clearTimeout(timer); observer?.disconnect(); resolve(); };
      const timer = setTimeout(done, 1000);
      observer = new MutationObserver(done);
      observer.observe(document.documentElement, {subtree:true, childList:true,
        characterData:true, attributes:true});
    });
  }
  // The protocol reply is exactly one fenced JSON block, so a parseable
  // extraction IS a complete answer - regardless of the site's post-reply UI
  // (e.g. Arena Agent mode's "task complete? yes/no/continue" prompt leaves
  // the page non-idle and would otherwise stall the wait to the 240s timeout).
  function isCompleteJson(t) {
    if (typeof t !== 'string') return false;
    const s = t.trim();
    if (!s.startsWith('{') && !s.startsWith('[')) return false;
    try { JSON.parse(s); return true; } catch { return false; }
  }
  // Marker fallback (0.4.16): in some DOMs (fresh Agent-mode chats) the reply
  // turn does not match the provider's turn filter, so readAssistant() never
  // reports it as fresh and the task would time out at 240s even though the
  // complete protocol JSON sits visible in the chat. The protocol reply is a
  // fenced block carrying this send's UNIQUE request id, so it can be located
  // by scanning code blocks directly - no turn structure assumptions.
  function markerBlockText(b) {
    const code = b.querySelector ? b.querySelector('code') : null;
    if (code && code.textContent != null) return code.textContent;
    const copy = b.cloneNode ? b.cloneNode(true) : null;
    if (copy) { try { for (const ui of copy.querySelectorAll('button, [role="button"], .md-code-block-banner')) ui.remove(); } catch {} }
    return (copy || b).textContent || '';
  }
  function findMarkerBlock(rid) {
    if (!rid) return null;
    try {
      const blocks = [...document.querySelectorAll('pre, code, .cm-content, .md-code-block')];
      const outer = blocks.filter(b => !blocks.some(o => o !== b && o.contains && o.contains(b)));
      for (const b of outer) {
        if ((b.textContent || '').includes(rid)) return b;
      }
    } catch {}
    return null;
  }
  // The block must be the protocol object itself, not the request envelope:
  // the user turn also carries this request id (inside CURRENT_REQUEST), and a
  // site that fences user text would put that envelope in a code block too.
  function isProtocolObject(rid, raw) {
    try {
      const v = JSON.parse(raw);
      return !!v && typeof v === 'object' && v.request_id === rid &&
        Object.keys(v).sort().join(',') === 'content,request_id,tool_calls';
    } catch { return false; }
  }
  const notify = data => chrome.runtime.sendMessage(data).catch(() => {});
  function announce() {
    const current = P.conversationKey();
    // A deliberate navigation invalidates binding. Initial chat creation during
    // our own submission keeps the same binding for subsequent turns.
    if (current !== key) {if (!busy) id = crypto.randomUUID(); key = current;}
    notify({type:'session', id, key, provider:P.id, title:document.title,
            url:location.href, visible:!document.hidden, busy, transportVersion:VERSION,
            providerVersion:P.version || null, inSync:P.version === VERSION,
            inputMaxChars, inputMaxLines});
  }
  async function run(msg) {
    busy = true;
    const sessionId = id;
    let text = '', failure;
    const diagnostics = {version:VERSION, startedHidden:document.hidden, sawHidden:document.hidden, phase:'preflight', promptLen:msg.prompt.length};
    try {
      // Console breadcrumbs: when the tab freezes, the LAST [zs] line in
      // DevTools is the stage the freeze happened in.
      console.log('[zs] task start: prompt ' + msg.prompt.length + ' chars');
      if (P.isBusyNow() || P.isGenerating()) throw new Error('Webpage is already generating');
      // Dedicated page: a leftover draft (from manual typing or a prior send
      // that never registered) must not wedge the workflow. typeAndSend below
      // replaces the composer content, so record the draft and proceed.
      const draft = P.editorText();
      if (draft.trim()) {
        diagnostics.composerDraftCleared = draft.slice(0, 200);
        diagnostics.composerDraftLen = draft.length;
      }
      let taskKey = P.conversationKey();
      let mayCreateChat = P.isFreshChat();
      const before = P.lastAssistant();
      const beforeId = P.lastAssistantId?.();
      const count = P.assistantCount();
      const beforeText = P.readAssistant().reply;
      diagnostics.phase = 'sending';
      // Capture the composer state so a silent send failure is diagnosable:
      // which textarea we used, whether it is visible, and whether a new user
      // turn appears after the send.
      let ed = null;
      try { ed = P.getEditor ? P.getEditor() : null; } catch {}
      diagnostics.editorFound = !!ed;
      if (ed) {
        diagnostics.editorTag = ed.tagName;
        diagnostics.editorVisible = ed.offsetParent !== null;
        diagnostics.editorPlaceholder = String(ed.placeholder || '').slice(0, 60);
      }
      diagnostics.editorLenBefore = (P.editorText ? P.editorText() : '').length;
      diagnostics.usersBefore = P.userCount ? P.userCount() : null;
      // The provider reports its own send outcome (Arena does: verified landed
      // length + composer-cleared-after-click). Older/other providers return
      // nothing - those fall back to user-turn counting as before.
      const sendInfo = (await P.typeAndSend(msg.prompt)) || {};
      const writeLanded = typeof sendInfo.landedLen === 'number' && sendInfo.landedLen > 0;
      const landedKnown = typeof sendInfo.landedLen === 'number';
      const providerSent = sendInfo.sent === true;
      diagnostics.phase = 'confirming_send';
      // Confirm the send actually took. Three independent evidences:
      //   1. a new user turn appears in the chat (turn count grows) - the classic
      //      signal, but the Agent-mode fresh-chat DOM does NOT grow it (live:
      //      composer cleared, correct JSON reply received, yet the task failed
      //      with "no new message appeared in the chat" for a 1688-char prompt);
      //   2. the provider confirms the send (it watched the composer clear);
      //   3. the composer is empty although the provider verified our write -
      //      only the site consuming the input can have cleared it.
      // Fail fast (in ~1s) when the text is stranded in the composer, and also
      // immediately when the provider verified the write and it is NOT there.
      let leftover = '', usersAfter = diagnostics.usersBefore, gen = false, hard = false;
      for (let i = 0; i < 150; i++) {
        try { leftover = (P.editorText ? P.editorText() : '') || ''; } catch { break; }
        try { usersAfter = P.userCount ? P.userCount() : usersAfter; } catch {}
        try { gen = !!(P.isGenerating && P.isGenerating()); hard = !!(P.isHardGenerating && P.isHardGenerating()); } catch {}
        const newTurn = usersAfter != null && diagnostics.usersBefore != null && usersAfter > diagnostics.usersBefore;
        const cleared = leftover.trim() === '';
        if (newTurn || providerSent || (cleared && writeLanded)) break;      // accepted
        if (landedKnown && !writeLanded && cleared) break;                   // write verified absent -> fail fast
        if (!cleared && i >= 5) break;                                       // stranded for ~1s -> fail fast
        await new Promise(r => setTimeout(r, 200));
      }
      diagnostics.editorLenAfter = leftover.length;
      diagnostics.usersAfter = usersAfter;
      // Our send added exactly one user turn; any further growth means the
      // dedicated page was operated manually (or its follow-up prompt was
      // clicked) mid-task.
      const expectedUsers = (diagnostics.usersBefore ?? 0) + 1;
      diagnostics.generatingAfter = gen;
      diagnostics.hardGeneratingAfter = hard;
      const newTurnAfter = usersAfter != null && diagnostics.usersBefore != null && usersAfter > diagnostics.usersBefore;
      diagnostics.sendConfirmed = newTurnAfter || providerSent || ((leftover.trim() === '') && writeLanded);
      diagnostics.sendConfirmedBy = newTurnAfter ? 'user_turn' : (providerSent ? 'provider' : ((leftover.trim() === '') && writeLanded ? 'composer_cleared' : 'none'));
      diagnostics.leftoverLen = leftover.length;
      if (!diagnostics.sendConfirmed) {
        const why = leftover.trim() !== ''
          ? leftover.length + ' characters are still in the composer'
          : 'no new message appeared in the chat (the text did not land in the composer)';
        throw new Error('Message was not sent: ' + why +
          (hard ? ' and the page shows a Stop button (it is still generating)' : '') +
          '. Composer: ' + (diagnostics.editorFound
              ? diagnostics.editorTag + (diagnostics.editorVisible ? ' (visible)' : ' (HIDDEN)') + (diagnostics.editorPlaceholder ? ' placeholder="' + diagnostics.editorPlaceholder + '"' : '')
              : 'not found') +
          '. Make sure the bound session is the tab you expect, the composer is cleared, and the page can accept a new message; then retry.');
      }
      console.log('[zs] send confirmed, waiting for reply');
      diagnostics.phase = 'waiting_new_reply';
      // Unique id of THIS send: embedded in the payload and copied verbatim
      // into the protocol reply. Identifies the reply block regardless of the
      // chat's turn DOM structure.
      const ridMatch = msg.prompt.match(/"request_id":"([0-9a-fA-F]{6,})"/);
      const rid = ridMatch ? ridMatch[1] : null;
      const deadline = Date.now() + 240000;
      let last = '', changed = Date.now(), idleSince = null, fresh = false, complete = false;
      let errorSince = null, lastReadAt = 0, fbLogged = false;
      while (Date.now() < deadline) {
        await waitForChange();
        // Throttle full reads: while a reply streams, DOM mutations arrive per
        // token, and each wake would re-read a conversation that now includes
        // our 50k+ char user turn. Cap it at one full read per 250ms so the
        // watcher cannot saturate the tab's CPU during generation.
        const sinceRead = Date.now() - lastReadAt;
        if (sinceRead < 250) await new Promise(r => setTimeout(r, 250 - sinceRead));
        lastReadAt = Date.now();
        diagnostics.sawHidden ||= document.hidden;
        // Manual operation of the dedicated page (typing, or clicking the
        // site's post-reply follow-up prompt) injects extra user turns.
        // Fail in seconds with an actionable message instead of waiting out
        // the 240s deadline reading a conversation we no longer own.
        const usersNow = P.userCount ? P.userCount() : null;
        if (usersNow != null && usersNow > expectedUsers) {
          throw new Error('Dedicated page was operated during the task (an extra user message appeared - manual input or the site\'s follow-up prompt was clicked). Refresh the page, rebind the session, and retry; never use the dedicated page while a task runs');
        }
        const currentKey = P.conversationKey();
        if (currentKey !== taskKey) {
          if (!mayCreateChat) {
            id = crypto.randomUUID();
            text = '';
            throw new Error('Conversation changed during task; result discarded');
          }
          taskKey = currentKey; mayCreateChat = false;
        }
        const result = P.readAssistant();
        fresh ||= P.assistantCount() > count || (beforeId != null && P.lastAssistantId?.() !== beforeId) ||
          (result.item !== before && result.reply !== beforeText);
        if (!fresh) {
          // A visible site error (toast/alert) with no reply means the page
          // rejected the request; fail in seconds instead of blocking the
          // dedicated page for the full 240s wait. A reply that starts after
          // the error cancels it (the error was about something else).
          const errText = P.errorText ? P.errorText() : null;
          if (errText) {
            if (errorSince === null) errorSince = Date.now();
            else if (Date.now() - errorSince > 3000) {
              throw new Error('Webpage showed an error and produced no reply: ' + errText.slice(0, 300));
            }
          } else {
            errorSince = null;
          }
          // Marker fallback: the reply turn is invisible to the provider's
          // turn filter (fresh Agent-mode chat), but the complete protocol
          // JSON is sitting in a fenced block. Require a parseable protocol
          // object with THIS send's request id, stable for 4s.
          let fbText = null;
          if (msg.response_format === 'json_code_block' && rid) {
            const b = findMarkerBlock(rid);
            if (b) {
              const t = markerBlockText(b).trim();
              if (t.startsWith('{') && isProtocolObject(rid, t)) fbText = t;
            }
          }
          if (fbText === null) continue;
          if (!fbLogged) { fbLogged = true; console.log('[zs] reply turn invisible to provider filter - reading via request-id marker fallback'); }
          diagnostics.fallbackRead = true;
          diagnostics.phase = 'reading_reply';
          diagnostics.extraction = 'marker_fallback';
          text = fbText;
          if (text !== last) {last = text; changed = Date.now();}
          if (Date.now() - changed > 4000) {
            complete = true;
            diagnostics.finalReason = 'complete_json_fallback';
            break;
          }
          continue;
        }
        errorSince = null;
        diagnostics.phase = 'reading_reply';
        let extracted = msg.response_format === 'json_code_block'
          ? ZSWebProtocol.read(result) : {text:result.reply || '', source:'rendered_reply'};
        // Rescue (0.4.18): the scoped block search failed (wrong block count or
        // an unexpected reply DOM) - before degrading to rendered prose, locate
        // the protocol block by this send's unique request id. Provider-agnostic;
        // keeps every site adapter working as long as the JSON is fenced.
        if (extracted.error && msg.response_format === 'json_code_block' && rid) {
          const b = findMarkerBlock(rid);
          if (b) {
            const t = markerBlockText(b).trim();
            if (t.startsWith('{') && isProtocolObject(rid, t)) {
              diagnostics.fallbackRead = true;
              extracted = {text: t, source: 'marker_fallback',
                           detail: 'scoped search failed (' + (extracted.detail || '') + ') - request-id marker'};
            }
          }
        }
        diagnostics.extraction = extracted.source;
        diagnostics.extraction_detail = extracted.detail || '';
        // Use rendered text only to determine settling when extraction failed;
        // never return that text as a successful protocol response.
        text = extracted.error ? (result.reply || '') : extracted.text;
        if (text !== last) {last = text; changed = Date.now();}
        const idle = !P.isGenerating() && !P.isBusyNow();
        if (!idle) idleSince = null;
        else if (idleSince === null) idleSince = Date.now();
        // Settle on idle+stable as before, OR on a stable, parseable protocol
        // JSON block: that block IS the complete answer by construction, so a
        // non-idle post-reply UI must not hold a finished reply hostage.
        const settled = Date.now() - changed > 4000;
        const idleStable = idleSince !== null && Date.now() - idleSince > 4000;
        const jsonComplete = msg.response_format === 'json_code_block' && !extracted.error && isCompleteJson(text);
        if (text && settled && (idleStable || jsonComplete)) {
          if (extracted.error) throw new Error(extracted.error);
          if (P.findContinueBtn?.()) throw new Error('Reply is truncated; continue on webpage before requesting another task');
          if (P.turnHalted?.(result.item)) throw new Error('Webpage generation was stopped');
          complete = true;
          diagnostics.finalReason = (!idleStable && jsonComplete) ? 'complete_json' : 'idle';
          break;
        }
      }
      if (!complete) throw new Error('Timed out waiting for a complete new reply. Background throttling, login or website state may block progress. Activate the webpage and inspect the original request before retrying; do not automatically resend');
      if (text.length > 250000) throw new Error('Answer exceeds size limit; partial text returned. Request a shorter answer');
      console.log('[zs] reply complete: ' + text.length + ' chars, reason=' + (diagnostics.finalReason || 'idle'));
      diagnostics.phase = 'completed';
      // The site's post-reply follow-up prompt ("此任务成功了吗?" with
      // 是/否/继续工作) blocks the next send on the page until answered. The
      // reply is fully read and validated at this point, so answering 是 (the
      // task succeeded) now is safe and keeps the page ready for the next task
      // without the user touching anything. Wait up to 10s: the prompt may
      // appear a moment after the reply finishes.
      if (typeof P.clearFollowupPrompt === 'function') {
        const promptDeadline = Date.now() + 10000;
        while (Date.now() < promptDeadline) {
          if (P.clearFollowupPrompt()) {
            diagnostics.followupCleared = true;
            console.log('[zs] post-reply follow-up prompt answered (是)');
            break;
          }
          await new Promise(r => setTimeout(r, 1000));
        }
        diagnostics.followupCleared = diagnostics.followupCleared === true;
      }
    } catch (e) {failure = String(e); console.log('[zs] task failed: ' + String(e).slice(0, 300));}
    finally {
      // Capture a fresh-chat URL before dropping busy, preserving this binding.
      key = P.conversationKey();
      busy = false;
      notify({type:'result', job_id:msg.job_id, session_id:sessionId,
              text:text.slice(0, 250000), error:failure, diagnostics:{...diagnostics, endedHidden:document.hidden}});
      announce();
    }
  }
  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (msg.type !== 'dispatch') return;
    // providers/ is git-ignored: a bare "git pull" updates content.js but NOT
    // the provider files. A mixed load (new content script + stale provider)
    // previously failed with a misleading composer error. Refuse dispatch and
    // say exactly what to fix instead.
    if (P.version !== VERSION) {
      reply({error:`Extension files out of sync: content script ${VERSION}, provider ${P.version || 'unversioned (stale)'}. Fix: git pull --ff-only, then run python cursor-web/prepare_extension.py, reload the extension in chrome://extensions, and refresh this page`}); return;
    }
    if (msg.session_id !== id || msg.expectedKey !== P.conversationKey()) {
      reply({error:'Conversation changed; list and bind the session again'}); return;
    }
    if (typeof msg.prompt !== 'string' || msg.prompt.length > inputMaxChars ||
        (inputMaxLines !== null && msg.prompt.split('\n').length > inputMaxLines)) {
      reply({error:`Input exceeds ${P.id} adapter safety budget (${inputMaxChars} UTF-16 units, ${inputMaxLines ?? 'unlimited'} lines); nothing sent or truncated`}); return;
    }
    if (busy || seen.has(msg.job_id)) {reply({error:'Busy or duplicate task'}); return;}
    seen.add(msg.job_id);
    reply({accepted:true});
    run(msg);
  });
  announce();
  setInterval(announce, 10000);
  document.addEventListener('visibilitychange', announce);
})();
