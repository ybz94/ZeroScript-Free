"""cursor-byok 自动接入（配置写入 / 程序定位 / 状态检测）。

cursor-byok (github.com/WHUT666/cursor-byok) 是一个本地 MITM 代理，让 Cursor
能使用任意模型后端。它的用户配置是磁盘上的一个普通 YAML 文件：
    ~/.cursor-local-assistant-v2/config.yaml
（见其源码 internal/appdata/paths.go 与 internal/backend/server/config/types.go）

所以我们可以在它的 UI 之外直接写入模型配置——用户在 cursor-byok 里不再需要
手动填 Base URL / API Key：

  * merge_webai_adapter(): 幂等地把 web-ai 适配器（指向本程序端点 + 端点密钥）
    合并进 config.yaml（保留其它模型与设置，写前自动备份）
  * find_byok_exe():        定位 cursor-byok.exe
  * byok_status():          配置/程序/运行/系统代理 四项状态

**明确边界**：本模块不重实现 cursor-byok 的 MITM 核心（TLS 拦截 + CA 证书
安装 + 系统代理设置）——那部分仍是 cursor-byok 程序本身的职责，且首次启用
需要管理员权限，必须在其自己的窗口里按提示完成一次。
"""
import os
import platform
import subprocess
from pathlib import Path

BYOK_DIRNAME = '.cursor-local-assistant-v2'
WEBAI_MODEL_ID = 'web-ai'
WEBAI_DISPLAY = '网页 AI (Cursor Web Assistant)'
WEBAI_TOOLTIP = '通过本地 Bridge 转发到网页 AI；由 Cursor Web Assistant 自动配置'
DEFAULT_PROXY_ADDR = '127.0.0.1:18080'
DEFAULT_BACKEND_ADDR = '127.0.0.1:18090'


def byok_config_path():
    """config.yaml 路径（可用环境变量 CURSOR_BYOK_CONFIG 覆盖，测试用）。"""
    p = os.getenv('CURSOR_BYOK_CONFIG')
    if p:
        return Path(p)
    return Path(os.path.expanduser('~')) / BYOK_DIRNAME / 'config.yaml'


def _load_config(path):
    """Read config.yaml -> dict, or None (absent / not a mapping)."""
    import yaml
    if not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def merge_webai_adapter(endpoint_url, api_key, path=None):
    """幂等地确保 config.yaml 里存在 web-ai 适配器。返回 (changed, detail)。

    - 文件不存在        -> 按 cursor-byok 的默认配置创建并加入适配器
    - 已有 web-ai 适配器 -> 更新 baseURL/apiKey 等（保留该条其它字段）
    - 适配器已是最新     -> 不写盘
    - 文件存在但解析失败 -> 不动它（宁可让用户处理，也不破坏现有配置）
    """
    import yaml
    path = Path(path) if path else byok_config_path()
    existed = path.exists()
    cfg = _load_config(path)
    if existed and cfg is None:
        return False, f'配置文件无法解析，未改动：{path}（请手动检查）'
    if cfg is None:
        cfg = {
            'log': False,
            'providerStreamIdleTimeout': 240,
            'backendListenAddr': DEFAULT_BACKEND_ADDR,
            'proxyListenAddr': DEFAULT_PROXY_ADDR,
            'modelAdapters': [],
            'routing': {'mode': 'local'},
        }
    adapters = cfg.get('modelAdapters')
    if not isinstance(adapters, list):
        adapters = []
        cfg['modelAdapters'] = adapters
    adapter = {
        'displayName': WEBAI_DISPLAY,
        'type': 'openai',
        'baseURL': endpoint_url,
        'apiKey': api_key,
        'tooltipData': WEBAI_TOOLTIP,
        'modelID': WEBAI_MODEL_ID,
        'reasoningEffort': 'low',
        'openAIEndpoint': '/v1/chat/completions',
        'contextWindowTokens': 200000,
        'maxCompletionTokens': 32000,
    }
    changed = False
    for i, a in enumerate(adapters):
        if isinstance(a, dict) and a.get('modelID') == WEBAI_MODEL_ID:
            if a != adapter:
                adapters[i] = {**a, **adapter}
                changed = True
            break
    else:
        adapters.append(adapter)
        changed = True
    if not changed:
        return False, f'已是最新（{path}）'
    path.parent.mkdir(parents=True, exist_ok=True)
    if existed:
        path.with_suffix('.yaml.bak').write_bytes(path.read_bytes())
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return True, f'已写入 {path}'


def find_byok_exe():
    """定位 cursor-byok 可执行文件。顺序：环境变量 → 本程序同目录 →
    常见目录（Downloads / LOCALAPPDATA / Program Files）→ PATH。"""
    import shutil
    env = os.getenv('CURSOR_BYOK_EXE')
    if env and Path(env).exists():
        return Path(env)
    import sys
    bases = []
    try:
        if getattr(sys, 'frozen', False):
            bases.append(Path(sys.executable).resolve().parent)
        else:
            bases.append(Path(__file__).resolve().parent)
    except Exception:
        pass
    home = Path(os.path.expanduser('~'))
    local = os.getenv('LOCALAPPDATA')
    bases += [
        home / 'Downloads',
        Path(local) if local else None,
        home,
        Path('C:/Program Files'),
        Path('C:/Program Files (x86)'),
    ]
    for base in bases:
        if not base or not base.exists():
            continue
        try:
            for p in sorted(base.glob('cursor-byok*.exe')):
                return p
            for sub in ('cursor-byok', 'CursorBYOK', 'cursor-byok-windows'):
                d = base / sub
                if d.is_dir():
                    for p in sorted(d.glob('cursor-byok*.exe')):
                        return p
        except OSError:
            continue
    p = shutil.which('cursor-byok')
    if p:
        return Path(p)
    return None


def is_byok_running():
    """tasklist 检查 cursor-byok.exe 是否在运行（仅 Windows 有意义）。"""
    if platform.system() != 'Windows':
        return False
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq cursor-byok.exe'],
                             capture_output=True, text=True, timeout=5).stdout
        return 'cursor-byok.exe' in out
    except Exception:
        return False


def system_proxy_pointing(addr):
    """Windows 系统代理当前是否指向 addr（cursor-byok 启用拦截时会设置）。"""
    if platform.system() != 'Windows':
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as k:
            enabled, _ = winreg.QueryValueEx(k, 'ProxyEnable')
            server, _ = winreg.QueryValueEx(k, 'ProxyServer')
        return bool(enabled) and str(server).split(';')[0].strip() == addr
    except Exception:
        return False


def byok_status(endpoint_url, api_key):
    """四项状态：模型配置 / 适配器是否最新 / 程序是否找到 / 是否运行 + 代理。"""
    path = byok_config_path()
    st = {'config': str(path), 'config_exists': path.exists(), 'adapter_ok': False,
          'byok_exe': None, 'byok_running': False,
          'proxy_addr': DEFAULT_PROXY_ADDR, 'proxy_on': False}
    cfg = _load_config(path)
    if isinstance(cfg, dict):
        st['proxy_addr'] = str(cfg.get('proxyListenAddr') or DEFAULT_PROXY_ADDR)
        for a in cfg.get('modelAdapters') or []:
            if isinstance(a, dict) and a.get('modelID') == WEBAI_MODEL_ID:
                st['adapter_ok'] = (a.get('apiKey') == api_key
                                    and a.get('baseURL') == endpoint_url)
    exe = find_byok_exe()
    if exe:
        st['byok_exe'] = str(exe)
    st['byok_running'] = is_byok_running()
    st['proxy_on'] = system_proxy_pointing(st['proxy_addr'])
    return st
