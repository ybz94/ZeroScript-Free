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
  return {document,read:roots=>api.read({replyRoots:roots || [document.querySelector('main')]})};
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
