"""Read-only file MCP server for the "file externalization" flow.

Purpose: when the dedicated webpage (e.g. Arena) can connect to an MCP
server by URL, the webpage model can FETCH file contents itself instead of
the endpoint stuffing whole files through the webpage input box (the 118k
budget wall). This server exposes the user's project root, READ-ONLY.

Security model (by design):
  * read-only: list_dir / read_file only - no write, no exec, no delete
  * bearer token: ZW_FILE_MCP_TOKEN or <project root>/.file-mcp-token
    (auto-generated, 0600). Compare with secrets.compare_digest.
  * path confinement: every path is resolved and must stay inside the root
    (no .., no absolute escapes, no symlink escapes out of the root)
  * bind 127.0.0.1 by default; expose via `cloudflared tunnel --url
    http://127.0.0.1:17618` (no open ports, HTTPS, no public IP of your own)
  * per-read size cap (ZW_FILE_MCP_MAX_CHARS, default 200000)

Usage:
  python file_mcp.py                 # streamable-http on 127.0.0.1:17618
  python file_mcp.py --transport sse # if the site wants an /sse-style URL
  ZW_FILE_MCP_ROOT=E:\\project       # which tree is exposed
"""
import argparse
import os
import secrets
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PORT = int(os.getenv('ZW_FILE_MCP_PORT', '17618'))
MAX_CHARS = int(os.getenv('ZW_FILE_MCP_MAX_CHARS', '200000'))
MAX_ENTRIES = 500


def _root() -> Path:
    r = Path(os.getenv('ZW_FILE_MCP_ROOT', str(Path.cwd()))).resolve()
    return r


def _token() -> str:
    env = os.getenv('ZW_FILE_MCP_TOKEN')
    if env:
        return env
    p = _root() / '.file-mcp-token'
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32))
    return p.read_text().strip()


def _confine(rel: str) -> Path:
    """Resolve `rel` inside the root or raise. Defeats .., absolute paths
    and symlinks pointing outside the root."""
    root = _root()
    cand = (root / (rel or '.')).resolve()
    if cand != root and root not in cand.parents:
        raise ValueError('Path is outside the exposed project root')
    return cand


mcp = FastMCP('cursor-web-files')


@mcp.tool()
def list_dir(path: str = '.') -> dict:
    """List a directory in the exposed project. Returns files and folders."""
    try:
        d = _confine(path)
    except ValueError as exc:
        return {'error': str(exc)}
    if not d.is_dir():
        return {'error': f'Not a directory: {path}'}
    out = []
    for i, e in enumerate(sorted(d.iterdir(), key=lambda x: x.name)):
        if i >= MAX_ENTRIES:
            out.append(f'… (truncated at {MAX_ENTRIES} entries)')
            break
        out.append(e.name + ('/' if e.is_dir() else ''))
    return {'path': path, 'entries': out}


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int = 0) -> dict:
    """Read a text file from the exposed project (1-based lines; end_line=0
    means to the end). Read-only. Returns line-numbered content."""
    try:
        p = _confine(path)
    except ValueError as exc:
        return {'error': str(exc)}
    if not p.is_file():
        return {'error': f'Not a file: {path}'}
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
    except Exception as exc:
        return {'error': f'Could not read {path}: {type(exc).__name__}'}
    lines = text.splitlines()
    total = len(lines)
    start = max(1, start_line)
    end = total if end_line <= 0 else min(total, end_line)
    if start > total:
        return {'error': f'start_line {start} beyond file end ({total} lines)'}
    chunk = lines[start - 1:end]
    body = '\n'.join(f'{start + i}\t{ln}' for i, ln in enumerate(chunk))
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS] + f'\n… (truncated at {MAX_CHARS} chars; narrow the line range)'
    return {'path': path, 'total_lines': total,
            'showing': f'{start}-{min(end, total)}', 'content': body}


def _auth_app(app):
    """Pure-ASGI bearer-token gate in front of the MCP ASGI app."""
    token = _token()

    async def deny(scope, receive, send):
        from starlette.responses import JSONResponse
        resp = JSONResponse({'error': 'unauthorized: Bearer token required'}, status_code=401)
        await resp(scope, receive, send)

    async def wrapper(scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            await app(scope, receive, send)
            return
        headers = {k.decode(): v.decode() for k, v in scope.get('headers', [])}
        auth = headers.get('authorization', '')
        if not auth.startswith('Bearer ') or not secrets.compare_digest(auth[7:].strip(), token):
            await deny(scope, receive, send)
            return
        await app(scope, receive, send)

    return wrapper


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--transport', choices=['streamable-http', 'sse'], default='streamable-http')
    args = ap.parse_args()
    root = _root()
    print(f'cursor-web file MCP (READ-ONLY) root: {root}', flush=True)
    print(f'Bearer token: {_token()}', flush=True)
    print(f'Local URL: http://{args.host}:{args.port} (expose via: '
          f'cloudflared tunnel --url http://{args.host}:{args.port})', flush=True)
    app = mcp.streamable_http_app() if args.transport == 'streamable-http' else mcp.sse_app()
    import uvicorn
    uvicorn.run(_auth_app(app), host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
