const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const MarkdownIt = require('markdown-it');
const {parseHTML} = require('linkedom');
const md = new MarkdownIt();
const source = fs.readFileSync(path.join(__dirname, '../extension/protocol.js'), 'utf8');
const payload = {request_id:'r', content:null, tool_calls:[{name:'edit_file', arguments:{
  path:String.raw`E:\project\test.js`, content:'const re = /\\d+/;\nconst path = "C:\\\\new";\n// 中文 😀'
}}]};
const raw = JSON.stringify(payload);
function reader(html) {
  const {document, CustomEvent} = parseHTML(`<html><body><main>${html}</main></body></html>`);
  const ctx = vm.createContext({document, CustomEvent});
  vm.runInContext(source,ctx);
  const api = vm.runInContext('ZSWebProtocol',ctx);
  const main = document.querySelector('main');
  return {document,
    read:(roots,item,extra={})=>api.read(Object.assign({replyRoots:roots || [main], item}, extra))};
}

test('reproduces Markdown collapsing JSON backslashes into Invalid escape',()=>{
  const r = reader(md.render(raw));
  const rendered = r.document.querySelector('main').textContent.trim();
  assert.notEqual(rendered,raw);
  assert.throws(()=>JSON.parse(rendered));
  assert.match(r.read().error,/exactly one JSON code block/);
});

test('fenced JSON preserves Windows paths, regex, code newlines and Unicode exactly',()=>{
  const r=reader(md.render('```json\n'+raw+'\n```'));
  const result=r.read();
  assert.equal(result.source,'code_text');
  assert.equal(result.text.trim(),raw);
  assert.deepEqual(JSON.parse(result.text),payload);
});

test('plain prose may corrupt VALID escapes silently; fenced block avoids it',()=>{
  const text=JSON.stringify({path:String.raw`C:\new\test`});
  const r=reader(md.render(text));
  const altered=JSON.parse(r.document.querySelector('main').textContent.trim());
  assert.notEqual(altered.path,JSON.parse(text).path);
  assert.ok(r.read().error);
});

test('multiple answer blocks are rejected, not searched for a valid tool fragment',()=>{
  const r=reader(md.render('```json\n'+raw+'\n```\n\n```json\n'+raw+'\n```'));
  assert.match(r.read().error,/found 2/);
});

test('extracts code only, excluding copy button and language label',()=>{
  const r=reader('<section><header>json<button>Copy</button></header><pre><code></code></pre></section>');
  r.document.querySelector('code').textContent=raw;
  assert.equal(r.read().text,raw);
});

test('non-reasoning roots exclude thought and other conversation blocks',()=>{
  const r=reader('<aside><pre><code>thinking draft</code></pre></aside><article><pre><code></code></pre></article>');
  r.document.querySelector('article code').textContent=raw;
  assert.equal(r.read([r.document.querySelector('article')]).text,raw);
});

test('CodeMirror full document wins over virtualized visible lines',()=>{
  const r=reader('<pre><div class="cm-content"><div class="cm-line">truncated</div></div></pre>');
  r.document.querySelector('.cm-content').setAttribute('data-zs-cm',raw);
  assert.equal(r.read().source,'codemirror_document');
  assert.equal(r.read().text,raw);
});

test('CodeMirror without full document fails rather than using incomplete DOM',()=>{
  const r=reader('<pre><div class="cm-content"><div class="cm-line">partial</div></div></pre>');
  assert.equal(r.read().source,'codemirror_unavailable');
  assert.ok(r.read().error);
});

test('pre without code preserves source and strips only UI buttons',()=>{
  const r=reader('<pre><span></span><button>Copy</button></pre>');
  r.document.querySelector('span').textContent=raw;
  assert.equal(r.read().text,raw);
  assert.equal(r.document.querySelector('button').textContent,'Copy');
});

test('turn fallback finds a code block that is a sibling of the answer roots',()=>{
  // DeepSeek layout: answer prose in .ds-markdown, .md-code-block as a
  // sibling inside the same .ds-message turn.
  const r=reader('<div class="ds-message">' +
    '<div class="ds-markdown">Here is the JSON.</div>' +
    '<div class="md-code-block"><div class="md-code-block-banner">json</div><pre><code></code></pre></div>' +
    '</div>');
  r.document.querySelector('.md-code-block code').textContent=raw;
  const res=r.read([r.document.querySelector('.ds-markdown')], r.document.querySelector('.ds-message'));
  assert.equal(res.source,'code_text');
  assert.equal(res.text,raw);
  assert.match(res.detail,/^roots=1 scope=answer_turn turn_blocks=1$/);
  assert.deepEqual(JSON.parse(res.text),payload);
});

test('reasoning code blocks are excluded from the turn fallback',()=>{
  const r=reader('<div class="ds-message">' +
    '<div class="ds-think-content"><div class="ds-markdown">thinking</div>' +
    '<div class="md-code-block"><pre><code>not the answer</code></pre></div></div>' +
    '<div class="ds-markdown">answer prose only</div></div>');
  const res=r.read([r.document.querySelectorAll('.ds-markdown')[1]],
    r.document.querySelector('.ds-message'), {thinkingSel:'.ds-think-content'});
  assert.equal(res.source,'code_block_unavailable');
  assert.match(res.error,/found 0/);
  assert.match(res.detail,/thinking_blocks=1/);
});

test('snapshot without replyRoots (older provider build) still reads the turn',()=>{
  const r=reader('<div class="ds-message"><div class="md-code-block"><pre><code></code></pre></div></div>');
  r.document.querySelector('code').textContent=raw;
  const res=r.read(undefined, r.document.querySelector('.ds-message'), {replyRoots:[]});
  assert.equal(res.source,'code_text');
  assert.equal(res.text,raw);
  assert.match(res.detail,/scope=answer_turn/);
});

test('multiple code blocks in the turn fail safely instead of guessing',()=>{
  const r=reader('<div class="ds-message">' +
    '<div class="md-code-block"><pre><code>a</code></pre></div>' +
    '<div class="md-code-block"><pre><code>b</code></pre></div></div>');
  const res=r.read(undefined, r.document.querySelector('.ds-message'), {replyRoots:[]});
  assert.equal(res.source,'code_block_unavailable');
  assert.match(res.error,/found 2/);
});

test('ZeroScript UI injections are never treated as answer code',()=>{
  const r=reader('<div class="ds-message">' +
    '<div class="ds-markdown">prose</div>' +
    '<div id="zs-root"><pre><code>injected</code></pre></div>' +
    '<div class="md-code-block"><pre><code></code></pre></div></div>');
  r.document.querySelector('.md-code-block code').textContent=raw;
  const res=r.read([r.document.querySelector('.ds-markdown')], r.document.querySelector('.ds-message'));
  assert.equal(res.text,raw);
});
