"""Local-only authenticated webpage task broker. Never reads/writes project files."""
import asyncio
import json
import os
import secrets
import time
import uuid
from pathlib import Path

from websockets.asyncio.server import serve
from input_limits import size_error

TOKEN_FILE = Path(__file__).with_name('.bridge-token')
PORT = int(os.getenv('CURSOR_WEB_PORT', '17614'))
clients = {}
jobs = {}


def token():
    if not TOKEN_FILE.exists():
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32))
    return TOKEN_FILE.read_text().strip()


async def handle(ws):
    role = None
    try:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if not secrets.compare_digest(str(hello.get('token', '')), token()):
            await ws.close(1008, 'Authentication failed')
            return
        role = hello.get('role')
        if role not in ('extension', 'cursor'):
            await ws.close(1008, 'Invalid role')
            return
        if role == 'extension':
            if clients:
                await ws.close(1008, 'Only one browser connection is allowed')
                return
            clients[ws] = []
        await ws.send(json.dumps({'ok': True}))
        async for raw in ws:
            msg = json.loads(raw)
            kind = msg.get('type')
            for job in jobs.values():
                if job['status'] == 'running' and time.time() - job['created'] > 300:
                    job.update(status='error', error='Task deadline exceeded; webpage may still be running. Check it before retrying.')
            result = {'error': 'Unsupported request'}
            if role == 'extension':
                if kind == 'sessions':
                    clients[ws] = msg.get('sessions', [])
                elif kind == 'result':
                    job = jobs.get(msg.get('job_id'))
                    if job and job['owner'] == ws and job['status'] == 'running':
                        job.update(status='error' if msg.get('error') else 'completed',
                                   result=msg.get('text', ''), error=msg.get('error'),
                                   diagnostics=msg.get('diagnostics', {}))
                continue
            if kind == 'list':
                result = {'sessions': [s for sessions in clients.values() for s in sessions]}
            elif kind == 'send':
                sid, prompt = msg.get('session_id'), msg.get('prompt')
                if not isinstance(prompt, str) or not prompt.strip() :
                    result = {'error': 'Prompt must be a nonempty string'}
                elif any(j['session_id'] == sid and j['status'] == 'running' for j in jobs.values()):
                    result = {'error': 'Session busy; query the existing task instead of resending'}
                else:
                    owner = next((w for w, sessions in clients.items()
                                  if any(s.get('id') == sid for s in sessions)), None)
                    session = next((s for s in clients.get(owner, []) if s.get('id') == sid), {})
                    oversize = size_error(prompt, session)
                    if owner is None:
                        result = {'error': 'Session unavailable; list sessions again'}
                    elif oversize:
                        result = {'error': oversize}
                    elif len(jobs) >= 500:
                        result = {'error': 'Task capacity reached; restart bridge after collecting results'}
                    else:
                        jid = str(uuid.uuid4())
                        jobs[jid] = {'job_id': jid, 'session_id': sid, 'status': 'running',
                                     'created': time.time(), 'owner': owner}
                        try:
                            await owner.send(json.dumps({'type': 'dispatch', 'job_id': jid,
                                                        'session_id': sid, 'prompt': prompt}))
                            result = {'job_id': jid, 'status': 'running'}
                        except Exception:
                            jobs[jid].update(status='error', error='Browser disconnected; delivery uncertain. Do not automatically resend.')
                            result = {'job_id': jid, 'error': jobs[jid]['error']}
            elif kind == 'get':
                job = jobs.get(msg.get('job_id'))
                result = {k: v for k, v in job.items() if k != 'owner'} if job else {'error': 'Unknown task (bridge may have restarted)'}
            await ws.send(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    finally:
        clients.pop(ws, None)
        if role == 'extension':
            for job in jobs.values():
                if job['owner'] == ws and job['status'] == 'running':
                    job.update(status='error', error='Browser disconnected; webpage may still be running. Check it before retrying.')


async def main():
    token()
    print(f'Cursor Web Bridge: ws://127.0.0.1:{PORT}')
    print(f'Pairing token file: {TOKEN_FILE} (keep private)')
    # Browser extensions have their own origin; ordinary websites are rejected.
    import re
    async with serve(handle, '127.0.0.1', PORT, max_size=2_000_000,
                     origins=[None, re.compile(r'chrome-extension://[a-p]{32}')]):
        await asyncio.Future()


if __name__ == '__main__':
    asyncio.run(main())
