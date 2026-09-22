const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const root = path.join(__dirname, '..', 'extension');

function content(options = {}) {
  const messages = [], intervals = [];
  let listener, now = 0, sent = 0, old = {}, item = old, text = 'old answer', count = 1, key = '/c/1';
  let editorContent = options.draft || '', userCountVar = 1;
  const document = {hidden:!!options.hidden, title:'Chat', addEventListener(){}};
  const provider = {
    id:options.provider || 'mock',
    version:options.providerVersion === undefined ? '0.4.12' : options.providerVersion, // null => pre-0.4.9 (no version field)
    init(){}, conversationKey:()=>key, isFreshChat:()=>false,
    isBusyNow:()=>!!options.busy, isGenerating:()=>!!options.generating && sent>0, // generating only AFTER our send (post-reply prompt state)
    isHardGenerating:()=>!!options.hardGenerating,
    getEditor:()=>({tagName:'TEXTAREA', offsetParent:{}, placeholder:'Message', value:editorContent}),
    editorText:()=>editorContent, lastAssistant:()=>item, lastAssistantId:()=>item===old?'old':'new',
    assistantCount:()=>count, userCount:()=>userCountVar, readAssistant:()=>({reply:text,item}),
    errorText:()=>options.siteError || null,
    findContinueBtn:()=>!!options.truncated, turnHalted:()=>!!options.halted,
    async typeAndSend(t){sent++; if(options.sendError) throw Error('send failed');
      if(options.navigate) key='/c/2';
      if(options.hideAfterSend) document.hidden=true;
      const accepted = !options.sendNotConfirmed && !options.sendDropped;
      if(!options.sendDropped) editorContent = t;              // text typed (unless it never landed)
      if(accepted){ editorContent=''; userCountVar++; }        // accepted -> cleared + new user turn
      userCountVar += options.extraUserTurns || 0;             // simulate manual use of the page
      if(!options.noReply && accepted){ item={}; text=options.answer || 'new answer'; count++; }
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(root,'content.js'),'utf8'), {
    ZSProvider:provider, ZSWebProtocol:{read:()=>options.protocol || {text:'',source:'code_block_unavailable',error:'Protocol code block missing'}}, crypto:{randomUUID}, document, location:{href:'https://site/c/1'},
    chrome:{runtime:{sendMessage:async msg=>{messages.push(msg);},onMessage:{addListener:fn=>listener=fn}}},
    MutationObserver:class {observe(){} disconnect(){}}, clearTimeout(){},
    Date:{now:()=>now}, setInterval:fn=>intervals.push(fn),
    setTimeout:(fn, ms)=>{now+=ms;setImmediate(fn);}
  });
  const first = messages[0];
  const dispatch = (extra={}) => {let ack;listener({type:'dispatch', job_id:'j1',session_id:first.id,expectedKey:'/c/1',prompt:'hi',...extra},{},v=>ack=v);return ack;};
  async function result(){for(let i=0;i<1000;i++){const r=messages.find(m=>m.type==='result');if(r)return r;await new Promise(setImmediate);}throw Error('No result');}
  return {messages, dispatch, result, get sent(){return sent;}, intervals};
}
test('content returns completed new response, not previous answer',async()=>{
  const c=content();assert.equal(c.dispatch().accepted,true);assert.equal((await c.result()).text,'new answer');assert.equal(c.sent,1);
});
for(const [name,options,pattern] of [
  ['busy page',{busy:true},/already generating/],
  ['send failure',{sendError:true},/send failed/],
  ['truncated answer',{truncated:true},/truncated/],['stopped answer',{halted:true},/stopped/],
  ['no new reply',{noReply:true},/Timed out/],
]) test(`content rejects ${name}`,async()=>{const c=content(options);c.dispatch();assert.match((await c.result()).error,pattern);});
test('leftover composer draft is recorded and replaced, not a hard failure',async()=>{
  const c=content({draft:'unsent manual work'});c.dispatch();
  const r=await c.result();
  assert.equal(r.error,undefined);
  assert.equal(r.text,'new answer');
  assert.equal(r.diagnostics.composerDraftCleared,'unsent manual work');
  assert.equal(r.diagnostics.composerDraftLen,'unsent manual work'.length);
  assert.equal(c.sent,1);
});
test('unconfirmed send (text stays in composer) fails fast, not after 240s',async()=>{
  const c=content({sendNotConfirmed:true, hardGenerating:true});c.dispatch();
  const r=await c.result();
  assert.match(r.error,/Message was not sent/);
  assert.match(r.error,/Stop button/);
  assert.equal(r.diagnostics.phase,'confirming_send');
  assert.equal(r.diagnostics.sendConfirmed,false);
  assert.ok(r.diagnostics.leftoverLen>0);
  assert.equal(c.sent,1);
});
test('send that never lands in the composer fails fast with composer state',async()=>{
  const c=content({sendDropped:true});c.dispatch();
  const r=await c.result();
  assert.match(r.error,/Message was not sent/);
  assert.match(r.error,/did not land in the composer/);
  assert.equal(r.diagnostics.phase,'confirming_send');
  assert.equal(r.diagnostics.sendConfirmed,false);
  assert.equal(r.diagnostics.leftoverLen,0);
  assert.equal(r.diagnostics.usersBefore,1);
  assert.equal(r.diagnostics.usersAfter,1);
  assert.equal(c.sent,1);
});
test('confirmed send (composer cleared) proceeds to wait for the reply',async()=>{
  const c=content();c.dispatch();
  const r=await c.result();
  assert.equal(r.error,undefined);
  assert.equal(r.diagnostics.sendConfirmed,true);
  assert.equal(r.diagnostics.leftoverLen,0);
});
test('content rejects duplicate submission',async()=>{
  const c=content();c.dispatch();assert.match(c.dispatch().error,/Busy|duplicate/);await c.result();assert.match(c.dispatch().error,/duplicate/);assert.equal(c.sent,1);
});
test('content rejects stale binding before sending',()=>{
  const c=content();assert.match(c.dispatch({expectedKey:'/other'}).error,/changed/);assert.equal(c.sent,0);
});
test('stale provider (git pull without prepare) is refused with an actionable fix',()=>{
  const c=content({providerVersion:'0.4.8'});
  const ack=c.dispatch();
  assert.match(ack.error,/out of sync/);
  assert.match(ack.error,/0\.4\.8/);
  assert.match(ack.error,/prepare_extension\.py/);
  assert.equal(c.sent,0);
});
test('unversioned provider (pre-0.4.9 build) is also refused, not mixed',()=>{
  const c=content({providerVersion:null});
  assert.match(c.dispatch().error,/out of sync/);
  assert.match(c.dispatch().error,/unversioned \(stale\)/);
  assert.equal(c.sent,0);
});
test('session announcement reports provider version and sync state',()=>{
  const c=content();
  const s=c.messages.find(m=>m.type==='session');
  assert.equal(s.transportVersion,'0.4.12');
  assert.equal(s.providerVersion,'0.4.12');
  assert.equal(s.inSync,true);
});
test('stable parseable JSON reply finalizes even while page reports generating (Arena follow-up prompt)',async()=>{
  const json='{"content":"ok","tool_calls":[]}';
  const c=content({generating:true, protocol:{text:json,source:'code_text',detail:'roots=1'}});
  c.dispatch({response_format:'json_code_block'});
  const r=await c.result();
  assert.equal(r.error,undefined);
  assert.equal(r.text,json);
  assert.equal(r.diagnostics.finalReason,'complete_json');
  assert.equal(r.diagnostics.phase,'completed');
  assert.equal(c.sent,1);
});
test('plain prose reply does NOT finalize via the JSON rule while page reports generating',async()=>{
  const c=content({generating:true, answer:'plain prose'});
  c.dispatch({response_format:'json_code_block'});
  const r=await c.result();
  assert.match(r.error,/Timed out/);
  assert.equal(r.diagnostics.phase,'reading_reply');
  assert.equal(r.diagnostics.finalReason,undefined);
});
test('manual use of the dedicated page mid-task fails fast, not after 240s',async()=>{
  const c=content({extraUserTurns:1});
  c.dispatch();
  const r=await c.result();
  assert.match(r.error,/operated during the task/);
  assert.match(r.error,/Refresh the page, rebind/);
  assert.equal(r.diagnostics.phase,'waiting_new_reply');
  assert.equal(c.sent,1);
});
test('prompt length is reported in diagnostics',async()=>{
  const c=content();c.dispatch({prompt:'hello'});const r=await c.result();
  assert.equal(r.diagnostics.promptLen,'hello'.length);
});
test('navigation while running invalidates original session binding',async()=>{
  const c=content({navigate:true});c.dispatch();assert.match((await c.result()).error,/changed/);
  const sessions=c.messages.filter(m=>m.type==='session');assert.notEqual(sessions.at(-1).id,sessions[0].id);
});
test('oversized answer is reported rather than silently truncated',async()=>{
  const c=content({answer:'x'.repeat(250001)});c.dispatch();assert.match((await c.result()).error,/large|limit/);
});

async function background(){
  let runtimeListener, removedListener;
  const sockets=[], stored={}, dispatched=[];
  class WS {
    static OPEN=1;
    constructor(){this.readyState=0;this.out=[];sockets.push(this);}
    send(raw){this.out.push(JSON.parse(raw));}
    close(){this.readyState=3;this.onclose?.();}
    async receive(msg){await this.onmessage({data:JSON.stringify(msg)});}
  }
  vm.runInNewContext(fs.readFileSync(path.join(root,'background.js'),'utf8'), {
    WebSocket:WS, Date, setInterval(){},setTimeout(){},
    chrome:{storage:{local:{get:async()=>({token:'test',port:17614}),set:async v=>Object.assign(stored,v)}},
      runtime:{onMessage:{addListener:fn=>runtimeListener=fn}},
      tabs:{sendMessage:async(tab,msg)=>{dispatched.push({tab,msg});return {accepted:true};},onRemoved:{addListener:fn=>removedListener=fn}},
      alarms:{create(){},onAlarm:{addListener(){}}}}
  });
  await new Promise(setImmediate);
  const socket=sockets[0];socket.readyState=1;socket.onopen();await socket.receive({ok:true});
  const message=(msg,tab=7)=>runtimeListener(msg,{tab:{id:tab},frameId:0},()=>{});
  return {socket,message,dispatched,remove:tab=>removedListener(tab)};
}
test('background routes request and accepts result only from bound tab/session',async()=>{
  const b=await background();b.message({type:'session',id:'s',key:'/c/1'});
  await b.socket.receive({type:'dispatch',job_id:'j',session_id:'s',prompt:'hi'});
  assert.equal(b.dispatched[0].tab,7);assert.equal(b.dispatched[0].msg.expectedKey,'/c/1');
  b.message({type:'result',job_id:'j',session_id:'s',text:'fake'},8);
  assert.equal(b.socket.out.filter(m=>m.type==='result').length,0);
  b.message({type:'result',job_id:'j',session_id:'s',text:'real'});
  assert.equal(b.socket.out.at(-1).text,'real');
});
test('background closing a tab fails pending task',async()=>{
  const b=await background();b.message({type:'session',id:'s',key:'/c/1'});
  await b.socket.receive({type:'dispatch',job_id:'j',session_id:'s'});b.remove(7);
  assert.match(b.socket.out.find(m=>m.type==='result').error,/closed/);
});
test('background removes old session on page refresh',async()=>{
  const b=await background();b.message({type:'session',id:'old',key:'/c/1'});b.message({type:'session',id:'new',key:'/c/1'});
  assert.equal(b.socket.out.at(-1).sessions.length,1);assert.equal(b.socket.out.at(-1).sessions[0].id,'new');
});

for (const options of [{hidden:true}, {hideAfterSend:true}]) {
  test(`background reply completes without activation: ${JSON.stringify(options)}`, async()=>{
    const c=content(options);c.dispatch();const r=await c.result();
    assert.equal(r.error,undefined);assert.equal(r.text,'new answer');
    assert.equal(r.diagnostics.sawHidden,true);assert.equal(r.diagnostics.phase,'completed');
    assert.equal(c.sent,1);
  });
}
test('background no reply returns actionable timeout without resend',async()=>{
  const c=content({hidden:true,noReply:true});c.dispatch();const r=await c.result();
  assert.match(r.error,/Activate the webpage/);assert.equal(c.sent,1);
  assert.equal(r.diagnostics.phase,'waiting_new_reply');
});

for (const [provider, limit] of [['deepseek',160000],['chatgpt',120000],['arena',118000]]) {
  test(`${provider} accepts budget boundary and blocks legacy truncation`, async()=>{
    const c=content({provider});
    assert.match(c.dispatch({prompt:'x'.repeat(limit+1)}).error,/safety budget/);
    assert.equal(c.sent,0);
    assert.equal(c.dispatch({prompt:'x'.repeat(limit)}).accepted,true);
    assert.equal((await c.result()).error,undefined);
    assert.equal(c.sent,1);
  });
}
test('ChatGPT line guard and UTF-16 guard prevent silent truncation',()=>{
  const c=content({provider:'chatgpt'});
  assert.match(c.dispatch({prompt:'\n'.repeat(600)}).error,/safety budget/);
  assert.match(c.dispatch({prompt:'😀'.repeat(60001)}).error,/safety budget/);
  assert.equal(c.sent,0);
});

test('model protocol returns code block extraction rather than rendered reply',async()=>{
  const raw=JSON.stringify({path:String.raw`E:\project\file.js`});
  const c=content({answer:'json Copy corrupted prose',protocol:{text:raw,source:'code_text',detail:'roots=1 scope=answer_roots turn_blocks=1'}});
  c.dispatch({response_format:'json_code_block'});
  const r=await c.result();assert.equal(r.error,undefined);assert.equal(r.text,raw);
  assert.equal(r.diagnostics.extraction,'code_text');
  assert.equal(r.diagnostics.extraction_detail,'roots=1 scope=answer_roots turn_blocks=1');
  assert.equal(r.diagnostics.version,'0.4.12');
});
test('site error with no reply fails the task in seconds, not 240s',async()=>{
  const c=content({noReply:true,siteError:'model channel not available'});
  c.dispatch();
  const r=await c.result();
  assert.match(r.error, /Webpage showed an error and produced no reply: model channel not available$/);
  assert.equal(r.diagnostics.phase,'waiting_new_reply');
  assert.equal(c.sent,1);
});
test('site error is ignored once a real reply has started',async()=>{
  const c=content({siteError:'model channel not available',answer:'new answer'});
  c.dispatch();
  const r=await c.result();
  assert.equal(r.error,undefined);
  assert.equal(r.text,'new answer');
});
test('model protocol never returns missing code block as successful prose',async()=>{
  const c=content({answer:'plain reply'});c.dispatch({response_format:'json_code_block'});
  const r=await c.result();assert.match(r.error,/code block missing/);
  assert.equal(r.diagnostics.extraction,'code_block_unavailable');
  assert.equal(r.diagnostics.extraction_detail,'');
});

// ── arena.js composer writer (fake DOM: the 0.4.11 chunked-insert fix) ──────
function arenaSend(options = {}) {
  const calls = [];
  const cap = options.cap === undefined ? Infinity : options.cap;
  const editor = {
    isContentEditable: true, offsetParent: {}, className: 'tiptap ProseMirror',
    textContent: '', value: undefined, focus(){}, dispatchEvent(){},
    closest(){ return null; },
  };
  let clicked = 0;
  const sendBtn = {
    offsetParent: {}, disabled: false,
    getAttribute: n => (n === 'aria-label' ? 'Send message' : null),
    click(){ clicked++; editor.textContent = ''; }, // site accepts: composer clears
  };
  const document = {
    querySelectorAll(sel){
      if (sel === '[contenteditable]') return [editor];
      if (sel === 'button') return [sendBtn];
      if (sel === 'form textarea') return [];
      return [];
    },
    createRange(){ return { selectNodeContents(){}, collapse(){} }; },
    execCommand(cmd){
      if (cmd !== 'insertText') return false;
      const val = arguments[2];
      calls.push(val.length);
      const t = editor.textContent + val;
      editor.textContent = t.length > cap ? t.slice(0, cap) : t; // simulate site cap
      return true;
    },
  };
  const sandbox = {
    document,
    window: { getSelection(){ return { removeAllRanges(){}, addRange(){} }; } },
    location: { pathname: '/c/1', href: 'https://arena.ai/c/1' },
    setTimeout, clearTimeout, setInterval, clearInterval, Date, console,
    KeyboardEvent: class KeyboardEvent {}, Event: class Event {},
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(root, 'providers', 'arena.js'), 'utf8'), ctx);
  const P = vm.runInContext('ZSProvider', ctx);
  return { P, calls, editor, get clicked(){ return clicked; } };
}
test('arena writes a long payload in chunks, never one giant insertText', async () => {
  const a = arenaSend();
  const payload = 'x'.repeat(21000);
  await a.P.typeAndSend(payload);
  assert.equal(a.clicked, 1);
  assert.equal(a.editor.textContent, ''); // accepted by the site
  assert.ok(a.calls.length >= 3, 'expected chunked inserts, got ' + JSON.stringify(a.calls));
  assert.ok(a.calls.every(c => c <= 8000), 'no chunk may exceed 8000 chars: ' + JSON.stringify(a.calls));
  assert.equal(a.calls.reduce((s, c) => s + c, 0), payload.length);
});
test('arena detects a site-side input cap MID-write and never sends truncated JSON', async () => {
  const a = arenaSend({ cap: 12000 });
  let err;
  try { await a.P.typeAndSend('x'.repeat(21000)); } catch (e) { err = e; }
  assert.ok(err, 'expected the mid-write clamp to fail the send');
  assert.match(err.message, /clamped the input mid-write/);
  assert.equal(a.clicked, 0, 'must never click send with a clamped payload');
});
test('arena still replaces a leftover draft and sends in one chunk when short', async () => {
  const a = arenaSend();
  a.editor.textContent = 'leftover draft';
  await a.P.typeAndSend('hello');
  assert.equal(a.clicked, 1);
  assert.equal(a.calls.length, 1);
  assert.equal(a.calls[0], 'hello'.length);
});
