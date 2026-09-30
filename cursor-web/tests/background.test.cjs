// background.js service-worker lifecycle: routes/results/cancel must survive
// an MV3 service-worker restart (Chrome kills the worker; in-memory state is
// lost) - otherwise an in-flight job's result is dropped and the bridge
// session wedges for up to 9 minutes ("网页忙" on every resend).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const BG = path.join(__dirname, '..', 'extension', 'background.js');

const SESSION = {type:'session', id:'s1', key:'/c/1', provider:'mock', title:'T',
                 url:'https://x/c/1', visible:true, busy:false, transportVersion:'0.4.27',
                 providerVersion:'0.4.27', inSync:true, inputMaxChars:160000};

// `shared` persists across boots exactly like chrome.storage.session does
// across service-worker restarts; `frames` collects everything the
// background sends to the (fake) bridge.
function makeEnv(shared) {
  shared.frames = shared.frames || [];
  shared.sessionStore = shared.sessionStore || {};
  const tabsSent = [];
  let runtimeListener = null;
  class FakeWS {
    static OPEN = 1;  // background.js checks WebSocket.OPEN, like the browser
    constructor() { this.readyState = 1; shared.ws = this; setImmediate(() => this.onopen && this.onopen()); }
    send(d) { shared.frames.push(JSON.parse(d)); }
    close() {}
  }
  const chrome = {
    storage: {
      local: { get: async () => ({token: 'tok', port: 1}), set: async () => {} },
      session: {
        get: async () => shared.sessionStore,
        set: async (o) => Object.assign(shared.sessionStore, o),
      },
    },
    runtime: {
      sendMessage: async () => {},
      getManifest: () => ({version: '0.4.27'}),
      onMessage: { addListener: fn => { runtimeListener = fn; } },
    },
    tabs: {
      sendMessage: (tabId, msg) => {
        tabsSent.push({tabId, msg});
        if (shared.tabReject) return Promise.reject(new Error('Receiving end does not exist'));
        if (msg.type === 'dispatch') return Promise.resolve({accepted: true, build: '20260929.6'});
        return Promise.resolve({ok: true});
      },
      onRemoved: { addListener: () => {} },
    },
    alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
  };
  return {
    tabsSent,
    boot() {
      vm.runInNewContext(fs.readFileSync(BG, 'utf8'),
        {WebSocket: FakeWS, chrome, console, setTimeout: () => 0, setInterval: () => 0, setImmediate, Date});
    },
    bridge: obj => shared.ws.onmessage({data: JSON.stringify(obj)}),
    trigger: (obj, sender = {tab: {id: 7}, frameId: 0}) => {
      let out; runtimeListener(obj, sender, v => { out = v; }); return out;
    },
    settle: async (n = 6) => { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); },
  };
}

async function bootAuthed(shared) {
  const env = makeEnv(shared);
  env.boot();
  await env.settle();
  const hello = shared.frames[shared.frames.length - 1];
  assert.equal(hello.role, 'extension');  // re-auth after a restart
  env.bridge({ok: true});
  await env.settle();
  return env;
}

test('in-flight job result survives a service-worker restart (no 9-minute session wedge)', async () => {
  const shared = {};
  const env1 = await bootAuthed(shared);
  env1.trigger(SESSION); await env1.settle();
  env1.bridge({type: 'dispatch', job_id: 'j1', session_id: 's1', prompt: 'hi', response_format: 'json_code_block'});
  await env1.settle();
  assert.equal(env1.tabsSent.find(t => t.msg.type === 'dispatch').tabId, 7);
  const rt = shared.sessionStore.zsRoutes;  // persisted (plain field checks: vm-realm objects)
  assert.equal(rt.j1.tabId, 7);
  assert.equal(rt.j1.sessionId, 's1');
  const before = shared.frames.length;
  // Chrome KILLS the worker mid-job: all in-memory state (routes, ws) is gone.
  const env2 = await bootAuthed(shared);
  // The content script (still alive on the page) reports the finished job.
  env2.trigger({type: 'result', job_id: 'j1', session_id: 's1'});
  await env2.settle();
  const res = shared.frames.slice(before).find(f => f.type === 'result' && f.job_id === 'j1');
  assert.ok(res, 'result must reach the bridge after the SW restart');
  assert.equal(Object.keys(shared.sessionStore.zsRoutes || {}).length, 0); // route consumed
});

test('result with a lost route is forwarded via the live session map (not dropped)', async () => {
  const shared = {};
  const env = await bootAuthed(shared);
  env.trigger(SESSION); await env.settle();
  env.trigger({type: 'result', job_id: 'jX', session_id: 's1'}); // no route for jX ever existed
  await env.settle();
  assert.ok(shared.frames.find(f => f.type === 'result' && f.job_id === 'jX'),
            'a result must never be silently dropped while its session is live');
});

test('cancel finds the tab via the session map when the job route is lost', async () => {
  const shared = {};
  const env1 = await bootAuthed(shared);
  env1.trigger(SESSION); await env1.settle();
  const env2 = await bootAuthed(shared);  // restart -> routes (none here) lost, sessions re-announce
  env2.trigger(SESSION); await env2.settle();
  env2.bridge({type: 'cancel', job_id: 'j9', session_id: 's1'});
  await env2.settle();
  const relay = env2.tabsSent.find(t => t.msg.type === 'cancel');
  assert.ok(relay, 'cancel must be relayed even without a persisted route');
  assert.equal(relay.tabId, 7);
  assert.equal(relay.msg.job_id, 'j9');
});

test('cancel with no reachable tab frees the bridge job immediately', async () => {
  const shared = {};
  const env = await bootAuthed(shared);  // no sessions registered at all
  env.bridge({type: 'cancel', job_id: 'j9', session_id: 's-nope'});
  await env.settle();
  const res = shared.frames.find(f => f.type === 'result' && f.job_id === 'j9');
  assert.ok(res, 'an unreachable cancel must end the job, not hang it');
  assert.match(res.error, /任务已终止/);
});

test('cancel whose tab has no content script frees the job instead of hanging', async () => {
  const shared = {tabReject: true};
  const env = await bootAuthed(shared);
  env.trigger(SESSION); await env.settle();
  env.bridge({type: 'cancel', job_id: 'j9', session_id: 's1'});
  await env.settle();
  const res = shared.frames.find(f => f.type === 'result' && f.job_id === 'j9');
  assert.ok(res, 'a rejected cancel must end the job, not hang it');
  assert.match(res.error, /未确认取消/);
});
