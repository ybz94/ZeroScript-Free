let ws, authenticated = false;
const sessions = new Map();
const routes = new Map();
const send = data => { if (authenticated && ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data)); };
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
    routes.clear();
    chrome.storage.local.set({connectionStatus:'已断开；请检查 Bridge 和令牌'});
    setTimeout(connect, 3000);
  };
  socket.onmessage = async event => {
    if (ws !== socket) return;
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (!authenticated) {
      if (msg.ok) {authenticated = true; publish(); chrome.storage.local.set({connectionStatus:'已连接本地 Bridge'});}
      return;
    }
    if (msg.type !== 'dispatch') return;
    const s = sessions.get(msg.session_id);
    if (!s) {send({type:'result', job_id:msg.job_id, error:'Session unavailable; list sessions again'}); return;}
    routes.set(msg.job_id, {tabId:s.tabId, sessionId:s.id});
    try {
      const ack = await chrome.tabs.sendMessage(s.tabId, {...msg, expectedKey:s.key});
      if (!ack?.accepted) throw new Error(ack?.error || 'Page rejected task');
    } catch (e) {
      routes.delete(msg.job_id);
      send({type:'result', job_id:msg.job_id, error:String(e)});
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
    ws = null; routes.clear(); connect(); reply({ok:true});
  } else if (msg.type === 'session' && sender.tab && sender.frameId === 0) {
    // Only the extension's own matched content scripts register a session.
    for (const [oldId, session] of sessions) {
      if (session.tabId !== sender.tab.id || oldId === msg.id) continue;
      sessions.delete(oldId);
      for (const [jobId, route] of routes) {
        if (route.sessionId !== oldId) continue;
        send({type:'result', job_id:jobId, error:'Page refreshed or conversation changed; check webpage before retrying'});
        routes.delete(jobId);
      }
    }
    sessions.set(msg.id, {...msg, type:undefined, tabId:sender.tab.id, seen:Date.now()});
    publish(); reply({ok:true});
  } else if (msg.type === 'result' && sender.tab) {
    const route = routes.get(msg.job_id);
    if (route && route.tabId === sender.tab.id && route.sessionId === msg.session_id) {
      routes.delete(msg.job_id); send(msg);
    }
    reply({ok:true});
  }
});
chrome.tabs.onRemoved.addListener(tabId => {
  for (const [id,s] of sessions) if (s.tabId === tabId) sessions.delete(id);
  for (const [id,r] of routes) if (r.tabId === tabId) {
    send({type:'result', job_id:id, error:'Target tab closed'}); routes.delete(id);
  }
  publish();
});
chrome.alarms.create('connect', {periodInMinutes:0.5});
chrome.alarms.onAlarm.addListener(() => {connect(); publish();});
setInterval(publish, 15000);
connect();
