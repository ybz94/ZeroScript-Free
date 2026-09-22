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
    id:options.provider || 'mock', init(){}, conversationKey:()=>key, isFreshChat:()=>false,
    isBusyNow:()=>!!options.busy, isGenerating:()=>!!options.generating,
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
  assert.equal(r.diagnostics.version,'0.4.6');
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
