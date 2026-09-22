/* Isolated from the old agent loop: webpage output is never executed. */
(() => {
  const P = ZSProvider;
  // Mirror input_limits.py. Never enter a provider's legacy truncation path.
  const inputCaps = {deepseek:160000, chatgpt:120000, arena:118000};
  const inputMaxChars = inputCaps[P.id] || 60000;
  const inputMaxLines = P.id === 'chatgpt' ? 600 : null;
  P.init({diag: () => {}});
  let id = crypto.randomUUID(), key = P.conversationKey(), busy = false;
  const VERSION = '0.4.11';
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
      await P.typeAndSend(msg.prompt);
      diagnostics.phase = 'confirming_send';
      // Confirm the send actually took. A successful send submits a new user
      // message, so a new user turn appears in the chat. If the text never
      // landed (wrong/hidden composer, React rejected the input, or the page
      // dropped it), no new turn appears and we would otherwise stall the 240s
      // reply wait. Poll briefly: the new turn renders a tick after the send.
      let leftover = '', usersAfter = diagnostics.usersBefore, gen = false, hard = false;
      for (let i = 0; i < 40; i++) {
        try { leftover = (P.editorText ? P.editorText() : '') || ''; } catch { break; }
        try { usersAfter = P.userCount ? P.userCount() : usersAfter; } catch {}
        try { gen = !!(P.isGenerating && P.isGenerating()); hard = !!(P.isHardGenerating && P.isHardGenerating()); } catch {}
        const newTurn = usersAfter != null && diagnostics.usersBefore != null && usersAfter > diagnostics.usersBefore;
        if (newTurn || leftover.trim() !== '') break;
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
      diagnostics.sendConfirmed = newTurnAfter;
      diagnostics.leftoverLen = leftover.length;
      if (!newTurnAfter) {
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
      diagnostics.phase = 'waiting_new_reply';
      const deadline = Date.now() + 240000;
      let last = '', changed = Date.now(), idleSince = null, fresh = false, complete = false;
      let errorSince = null;
      while (Date.now() < deadline) {
        await waitForChange();
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
          continue;
        }
        errorSince = null;
        diagnostics.phase = 'reading_reply';
        const extracted = msg.response_format === 'json_code_block'
          ? ZSWebProtocol.read(result) : {text:result.reply || '', source:'rendered_reply'};
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
      diagnostics.phase = 'completed';
    } catch (e) {failure = String(e);}
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
