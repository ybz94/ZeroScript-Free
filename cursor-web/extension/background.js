let ws, authenticated = false;
const sessions = new Map();
const routes = new Map();
// MV3 service-worker lifecycle: Chrome can KILL this script (idle ~30s,
// memory pressure). In-memory state would be lost with it - and a lost
// in-flight job's RESULT wedges the bridge session for up to 9 minutes
// (every resend fails with "网页忙", cancel finds no route either). Routes
// are the only per-job state, so persist them in chrome.storage.session:
// it survives service-worker restarts and clears when the browser closes.
function saveRoutes() { try { chrome.storage.session.set({zsRoutes: Object.fromEntries(routes)}); } catch {} }
chrome.storage.session.get('zsRoutes').then(st => {
  for (const [k, v] of Object.entries(st.zsRoutes || {})) routes.set(k, v);
  keepalive();
}).catch(() => {});
// While a job is in flight, keep the service worker alive with an extra
// timer. A hidden dedicated tab (the user works in the foreground app) has
// its CONTENT-script timers throttled, so the 30s alarm alone can let
// Chrome kill this worker mid-task; the result message then races the
// worker's wake-up and can be dropped (live report 2026-09-30).
let keepaliveTimer = null;
function keepalive() {
  if (!keepaliveTimer && routes.size) {
    keepaliveTimer = setInterval(() => {
      if (!routes.size) { clearInterval(keepaliveTimer); keepaliveTimer = null; }
    }, 20000);
  }
}
// Outbox: a result sent while the WS is down (bridge/exe restarting) would
// otherwise be SILENTLY dropped, leaving the bridge job 'running' until its
// deadline and making every resend fail with "Session busy". Results are the
// only messages that must survive a reconnect - queue them and flush on the
// next authenticated open. (Stale entries for jobs the new bridge doesn't
// know about are ignored by the bridge, so flushing is always safe.)
const outbox = [];
const send = data => {
  if (authenticated && ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(data));
  } else if (data.type === 'result' && outbox.length < 50) {
    outbox.push(data);
  }
};
function publish() {
  const now = Date.now();
  for (const [id, s] of sessions) if (now - s.seen > 180000) sessions.delete(id);
  send({type:'sessions', sessions:[...sessions.values()].map(({tabId, seen, ...s}) => s)});
}
async function connect() {
  if (ws && ws.readyState < 2) return;
  const {token, port = 17614} = await chrome.storage.local.get(['token', 'port']);
  if (!token) return;
  const socket = new WebSocket(`ws://127.0.0.1:${port}`);
  ws = socket;
  socket.onopen = () => socket.send(JSON.stringify({role:'extension', token}));
    socket.onclose = () => {
      if (ws !== socket) return;
      authenticated = false;
      routes.clear(); saveRoutes();
      chrome.storage.local.set({connectionStatus:'已断开；请检查 Bridge 和令牌'});
      setTimeout(connect, 3000);
    };
  socket.onmessage = async event => {
    if (ws !== socket) return;
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (!authenticated) {
      if (msg.ok) {
        authenticated = true;
        for (const queued of outbox.splice(0)) ws.send(JSON.stringify(queued));  // lost-window results
        publish(); chrome.storage.local.set({connectionStatus:'已连接本地 Bridge'});
      }
      return;
    }
    if (msg.type === 'cancel') {
      // Control-center "取消" button: abort the in-flight task in its tab.
      // The content script ends the job through its normal result path,
      // which frees the bridge session. If the cancel CANNOT reach a tab,
      // the job must be freed NOW (an unreachable job would otherwise wedge
      // the session for up to 9 minutes); a page still generating will
      // refuse the next task before typing anything.
      const sess = msg.session_id ? sessions.get(msg.session_id) : null;
      const route = routes.get(msg.job_id) || (sess ? {tabId: sess.tabId, sessionId: sess.id} : null);
      const free = err => send({type:'result', job_id: msg.job_id, error: err});
      if (!route) { free('任务已终止（网页端不可达；若网页仍在回答，请打开专用页查看）'); return; }
      chrome.tabs.sendMessage(route.tabId, {type:'cancel', job_id: msg.job_id}).catch(() =>
        free('任务已终止（网页端未确认取消；若网页仍在回答，请打开专用页查看）'));
      return;
    }
    if (msg.type !== 'dispatch') return;
    const s = sessions.get(msg.session_id);
    if (!s) {send({type:'result', job_id:msg.job_id, error:'Session unavailable; list sessions again'}); return;}
    routes.set(msg.job_id, {tabId:s.tabId, sessionId:s.id}); saveRoutes(); keepalive();
    try {
      const ack = await chrome.tabs.sendMessage(s.tabId, {...msg, expectedKey:s.key});
      if (!ack?.accepted) throw new Error(ack?.error || 'Page rejected task');
      // Report the content script's build generation so the endpoint can
      // detect a stale dedicated page (old extension) BEFORE the task runs.
      if (ack.build) send({type:'ack', job_id:msg.job_id, build:ack.build});
    } catch (e) {
      routes.delete(msg.job_id); saveRoutes();
      const m = String((e && e.message) || e);
      // "Receiving end does not exist" = no extension content script in that
      // tab: the URL is not a supported site (e.g. chatglm.cn, which is NOT
      // chat.z.ai), or the page is mid-load. Say what to do instead of
      // surfacing the Chrome internals.
      const err = /Receiving end does not exist/i.test(m)
        ? '目标标签页里没有扩展内容脚本（该网址不是受支持的站点，或页面还没加载完）。注意：GLM 适配器支持的是国际版 chat.z.ai，不是国内 chatglm.cn。请在专用标签页打开受支持的聊天网站并刷新，然后重新绑定会话'
        : m;
      send({type:'result', job_id:msg.job_id, error:err});
    }
  };
}
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === 'snapshot' && !sender.tab) {
    // Popup UI: current connection state + all known dedicated-page sessions.
    reply({connected: authenticated && ws && ws.readyState === WebSocket.OPEN,
           version: chrome.runtime.getManifest().version,
           sessions: [...sessions.values()].map(({tabId, seen, ...s}) => s)});
  } else if (msg.type === 'reconnect' && !sender.tab) {
    authenticated = false;
    if (ws) { ws.onclose = null; ws.close(); }
    ws = null; routes.clear(); saveRoutes(); connect(); reply({ok:true});
  } else if (msg.type === 'session' && sender.tab && sender.frameId === 0) {
    // Only the extension's own matched content scripts register a session.
    for (const [oldId, session] of sessions) {
      if (session.tabId !== sender.tab.id || oldId === msg.id) continue;
      sessions.delete(oldId);
      for (const [jobId, route] of routes) {
        if (route.sessionId !== oldId) continue;
        send({type:'result', job_id:jobId, error:'Page refreshed or conversation changed; check webpage before retrying'});
        routes.delete(jobId); saveRoutes();
      }
    }
    sessions.set(msg.id, {...msg, type:undefined, tabId:sender.tab.id, seen:Date.now()});
    publish(); reply({ok:true});
  } else if (msg.type === 'result' && sender.tab) {
    const route = routes.get(msg.job_id);
    // A lost route (service worker restarted before the persisted routes
    // were restored, or any route anomaly) must NOT drop the result - a
    // dropped result wedges the bridge job for up to 9 minutes. The bridge
    // re-validates job ownership on receipt, so forwarding the matching
    // session's result is always safe.
    if (route && route.tabId === sender.tab.id && route.sessionId === msg.session_id) {
      routes.delete(msg.job_id); saveRoutes(); send(msg);
    } else if (!route && msg.session_id) {
      const s = sessions.get(msg.session_id);
      if (s && s.tabId === sender.tab.id) send(msg);
    }
    reply({ok:true});
  }
});
chrome.tabs.onRemoved.addListener(tabId => {
  for (const [id,s] of sessions) if (s.tabId === tabId) sessions.delete(id);
  for (const [id,r] of routes) if (r.tabId === tabId) {
    send({type:'result', job_id:id, error:'Target tab closed'}); routes.delete(id); saveRoutes();
  }
  publish();
});
chrome.alarms.create('connect', {periodInMinutes:0.5});
chrome.alarms.onAlarm.addListener(() => {connect(); publish();});
setInterval(publish, 15000);
connect();
