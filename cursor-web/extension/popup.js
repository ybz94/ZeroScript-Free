const status = document.getElementById('status');
chrome.storage.local.get(['port']).then(c => {document.getElementById('port').value = c.port || 17614;});
document.getElementById('save').onclick = async () => {
  const token = document.getElementById('token').value.trim();
  const port = Number(document.getElementById('port').value);
  if (!token || !Number.isInteger(port) || port < 1024 || port > 65535) {
    status.textContent = '请输入令牌及有效端口'; return;
  }
  await chrome.storage.local.set({token, port});
  await chrome.runtime.sendMessage({type:'reconnect'});
  document.getElementById('token').value = '';
  status.textContent = '已保存，正在连接。';
};
setInterval(async () => {
  const s = await chrome.storage.local.get('connectionStatus');
  status.textContent = s.connectionStatus || '未连接';
}, 1000);
