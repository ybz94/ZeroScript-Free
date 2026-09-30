/* Popup: connection state, dedicated-page sessions, supported-site links. */
const SITES = [
  ['deepseek', 'DeepSeek', 'https://chat.deepseek.com', '#4d6bfe'],
  ['glm', 'GLM · Z.ai', 'https://chat.z.ai', '#ff7a1a'],
  ['kimi', 'Kimi · K3', 'https://www.kimi.ai', '#2f6bff'],
  ['qwen', 'Qwen 通义', 'https://chat.qwen.ai', '#7c3aed'],
  ['gemini', 'Gemini', 'https://gemini.google.com', '#4285f4'],
  ['meta', 'Meta AI', 'https://www.meta.ai', '#0081fb'],
  ['chatgpt', 'ChatGPT', 'https://chatgpt.com', '#10a37f'],
  ['arena', 'Arena', 'https://arena.ai', '#f97316'],
];

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const host = u => String(u || '').replace(/^https?:\/\//, '').split('/')[0] || '?';

const sitesEl = document.getElementById('sites');
for (const [pid, name, url, color] of SITES) {
  const a = document.createElement('a');
  a.className = 'site';
  a.href = url;
  a.target = '_blank';
  a.rel = 'noreferrer';
  a.innerHTML = `<span class="glyph" style="background:${color}">${esc(name[0])}</span>` +
                `<span>${esc(name)}</span><span class="pid">${esc(pid)}</span>`;
  sitesEl.appendChild(a);
}

const status = document.getElementById('status');
document.getElementById('save').onclick = async () => {
  const token = document.getElementById('token').value.trim();
  const port = Number(document.getElementById('port').value);
  if (!token || !Number.isInteger(port) || port < 1024 || port > 65535) {
    status.textContent = '请输入令牌及有效端口';
    return;
  }
  await chrome.storage.local.set({token, port});
  await chrome.runtime.sendMessage({type: 'reconnect'});
  document.getElementById('token').value = '';
  status.textContent = '已保存，正在连接。';
};

async function refresh() {
  let snap = null;
  try { snap = await chrome.runtime.sendMessage({type: 'snapshot'}); } catch {}
  const dot = document.getElementById('dot');
  const conn = document.getElementById('conn');
  const on = !!(snap && snap.connected);
  dot.classList.toggle('on', on);
  conn.textContent = on
    ? `已连接本地 Bridge · v${snap.version || '?'}`
    : '未连接（令牌/端口见下方"设置"）';

  const el = document.getElementById('sessions');
  const list = (snap && snap.sessions) || [];
  if (!list.length) {
    el.innerHTML = '<div class="empty">暂无会话 — 在专用标签页打开聊天网站并刷新</div>';
    return;
  }
  el.innerHTML = list.map(s => {
    const ok = !!s.inSync;
    const badge = ok
      ? `<span class="badge ok">同步 · ${esc(s.transportVersion || '')}</span>`
      : `<span class="badge bad">版本不同步</span>`;
    const meta = [host(s.url), s.busy ? '任务进行中' : '空闲', s.visible === false ? '后台' : null]
      .filter(Boolean).map(esc).join(' · ');
    return `<div class="session"><div class="s-title"><span>${esc(s.provider || '?')}</span>${badge}</div>` +
           `<div class="s-meta">${meta}</div></div>`;
  }).join('');
}

refresh();
setInterval(refresh, 2000);
