/* Isolated from the old agent loop: webpage output is never executed. */
(() => {
  const P = ZSProvider;
  // Mirror input_limits.py. Never enter a provider's legacy truncation path.
  const inputCaps = {deepseek:160000, chatgpt:120000, arena:118000};
  const inputMaxChars = inputCaps[P.id] || 60000;
  const inputMaxLines = P.id === 'chatgpt' ? 600 : null;
  P.init({diag: () => {}});
  let id = crypto.randomUUID(), key = P.conversationKey(), busy = false;
  const VERSION = '0.4.4';
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
  const notify = data => chrome.runtime.sendMessage(data).catch(() => {});
  function announce() {
    const current = P.conversationKey();
    // A deliberate navigation invalidates binding. Initial chat creation during
    // our own submission keeps the same binding for subsequent turns.
    if (current !== key) {if (!busy) id = crypto.randomUUID(); key = current;}
    notify({type:'session', id, key, provider:P.id, title:document.title,
            url:location.href, visible:!document.hidden, busy, transportVersion:VERSION, inputMaxChars, inputMaxLines});
  }
  async function run(msg) {
    busy = true;
    const sessionId = id;
    let text = '', failure;
    const diagnostics = {version:VERSION, startedHidden:document.hidden, sawHidden:document.hidden, phase:'preflight'};
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
      await P.typeAndSend(msg.prompt);
      // Fail fast if the send did not actually take. All three providers'
      // typeAndSend return normally (without throwing) when the composer
      // refuses the message - send button disabled, page busy, or still
      // generating - which would otherwise stall the 240s reply wait with no
      // visible activity on the page. A successful send clears the composer
      // within ~1-2s, so if our text is still there after a short grace
      // period, the send failed.
      let leftover = '';
      for (let i = 0; i < 25; i++) {
        try { leftover = (P.editorText ? P.editorText() : '') || ''; } catch { break; }
        if (leftover.trim() === '') break;
        await new Promise(r => setTimeout(r, 200));
      }
      diagnostics.sendConfirmed = leftover.trim() === '';
      diagnostics.leftoverLen = leftover.length;
      if (leftover.trim() !== '') {
        let busy2 = false;
        try { busy2 = !!(P.isHardGenerating && P.isHardGenerating()); } catch {}
        throw new Error('Message was not sent: ' + leftover.length +
          ' characters are still in the composer' +
          (busy2 ? ' and the page shows a Stop button (it is still generating)' : '') +
          '. The send button is probably disabled or the page is busy. Check the dedicated webpage - stop any in-progress generation, clear the composer, confirm the send button is enabled - then retry.');
      }
      diagnostics.phase = 'waiting_new_reply';
      const deadline = Date.now() + 240000;
      let last = '', changed = Date.now(), idleSince = null, fresh = false, complete = false;
      let errorSince = null;
      while (Date.now() < deadline) {
        await waitForChange();
        diagnostics.sawHidden ||= document.hidden;
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
        if (text && idle && Date.now() - idleSince > 4000 && Date.now() - changed > 4000) {
          if (extracted.error) throw new Error(extracted.error);
          if (P.findContinueBtn?.()) throw new Error('Reply is truncated; continue on webpage before requesting another task');
          if (P.turnHalted?.(result.item)) throw new Error('Webpage generation was stopped');
          complete = true; break;
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
