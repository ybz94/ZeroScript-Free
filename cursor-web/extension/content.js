/* Isolated from the old agent loop: webpage output is never executed. */
(() => {
  const P = ZSProvider;
  P.init({diag: () => {}});
  let id = crypto.randomUUID(), key = P.conversationKey(), busy = false;
  const seen = new Set();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const notify = data => chrome.runtime.sendMessage(data).catch(() => {});
  function announce() {
    const current = P.conversationKey();
    // A deliberate navigation invalidates binding. Initial chat creation during
    // our own submission keeps the same binding for subsequent turns.
    if (current !== key) {if (!busy) id = crypto.randomUUID(); key = current;}
    notify({type:'session', id, key, provider:P.id, title:document.title,
            url:location.href, visible:!document.hidden, busy});
  }
  async function run(msg) {
    busy = true;
    const sessionId = id;
    let text = '', failure;
    try {
      if (document.hidden) throw new Error('Keep the target tab active in its browser window; minimized/background pages are not supported yet');
      if (P.isBusyNow() || P.isGenerating()) throw new Error('Webpage is already generating');
      if (P.editorText().trim()) throw new Error('Composer contains a draft; send or clear it manually first');
      let taskKey = P.conversationKey();
      let mayCreateChat = P.isFreshChat();
      const before = P.lastAssistant();
      const beforeId = P.lastAssistantId?.();
      const count = P.assistantCount();
      const beforeText = P.readAssistant().reply;
      await P.typeAndSend(msg.prompt);
      const deadline = Date.now() + 240000;
      let last = '', changed = Date.now(), fresh = false, complete = false;
      while (Date.now() < deadline) {
        await sleep(600);
        if (document.hidden) throw new Error('Page became hidden; generation may continue. Check webpage before retrying');
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
        if (!fresh) continue;
        text = result.reply || '';
        if (text !== last) {last = text; changed = Date.now();}
        if (text && !P.isGenerating() && !P.isBusyNow() && Date.now() - changed > 4000) {
          if (P.findContinueBtn?.()) throw new Error('Reply is truncated; continue on webpage before requesting another task');
          if (P.turnHalted?.(result.item)) throw new Error('Webpage generation was stopped');
          complete = true; break;
        }
      }
      if (!complete) throw new Error('Timed out waiting for a complete new reply. Check login, rate limits or webpage status; do not automatically resend');
      if (text.length > 250000) throw new Error('Answer exceeds size limit; partial text returned. Request a shorter answer');
    } catch (e) {failure = String(e);}
    finally {
      // Capture a fresh-chat URL before dropping busy, preserving this binding.
      key = P.conversationKey();
      busy = false;
      notify({type:'result', job_id:msg.job_id, session_id:sessionId,
              text:text.slice(0, 250000), error:failure});
      announce();
    }
  }
  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (msg.type !== 'dispatch') return;
    if (msg.session_id !== id || msg.expectedKey !== P.conversationKey()) {
      reply({error:'Conversation changed; list and bind the session again'}); return;
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
