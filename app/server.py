"""Team conversion server. One uvicorn process owns the bounded local queue."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import sqlite3
import sys
import time
import zipfile
from typing import Any

from fastapi import FastAPI, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartParser, MultiPartException

from app.engines import catalog
from app.paths import ROOT, default_data

DATA = Path(os.environ.get('FORMAT_HUB_DATA', str(default_data()))).resolve()
MAX_MB = int(os.environ.get('FORMAT_HUB_MAX_FILE_MB', '200'))
MAX_FILES = int(os.environ.get('FORMAT_HUB_MAX_FILES', '30'))
WORKERS = max(1, min(int(os.environ.get('FORMAT_HUB_WORKERS', '2')), 8))
QUEUE_LIMIT = int(os.environ.get('FORMAT_HUB_QUEUE_LIMIT', '40'))
TIMEOUT = int(os.environ.get('FORMAT_HUB_TIMEOUT', '600'))
RETENTION = int(os.environ.get('FORMAT_HUB_RETENTION_HOURS', '24'))
TEAM_TOKEN = os.environ.get('FORMAT_HUB_TOKEN', '')
COOKIE = 'format_hub_session'
log = logging.getLogger('format_hub')
active: dict[str, asyncio.subprocess.Process] = {}
reserved_slots: set[str] = set()
STORAGE_MB = int(os.environ.get('FORMAT_HUB_STORAGE_MB', '10240'))
MIN_FREE_MB = int(os.environ.get('FORMAT_HUB_MIN_FREE_MB', '512'))
queue: asyncio.Queue[str]
slots: asyncio.Semaphore


def db():
    conn = sqlite3.connect(DATA / 'jobs.sqlite3', timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=15000')
    return conn


def initialize():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    (DATA / 'jobs').mkdir(exist_ok=True, mode=0o700)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expires REAL NOT NULL, authenticated INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, operation TEXT NOT NULL, tool_name TEXT NOT NULL,
          state TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL,
          files TEXT NOT NULL, options TEXT NOT NULL, outputs TEXT NOT NULL DEFAULT '[]',
          error TEXT, warnings TEXT NOT NULL DEFAULT '[]');
        CREATE INDEX IF NOT EXISTS job_owner_created ON jobs(owner, created_at DESC);
        ''')
        c.execute("UPDATE jobs SET state='failed', error='服务已重启，请重新提交转换', finished_at=? WHERE state IN ('running','queued')", (time.time(),))


def patch(job_id: str, **fields):
    with db() as c:
        c.execute('UPDATE jobs SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', [*fields.values(), job_id])


def view(row):
    result = dict(row)
    result.pop('owner', None)
    result.pop('options', None)
    for key in ('files', 'outputs', 'warnings'):
        result[key] = json.loads(result[key])
    for f in result['files']:
        f.pop('stored', None)
    for f in result['outputs']:
        f['url'] = f"/api/jobs/{result['id']}/files/{f['name']}"
    result['progress'] = 100 if result['state'] == 'succeeded' else (40 if result['state'] == 'running' else 0)
    return result


class Problem(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def session(request: Request, require=True):
    sid = getattr(request.state, 'session_id', None)
    with db() as c:
        row = c.execute('SELECT * FROM sessions WHERE id=? AND expires>?', (sid, time.time())).fetchone()
    if not row or (require and TEAM_TOKEN and not row['authenticated']):
        raise Problem(401, 'login_required', '请输入团队访问口令')
    return row


def owned(request, job_id):
    owner = session(request)['id']
    with db() as c:
        row = c.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (job_id, owner)).fetchone()
    if not row:
        raise Problem(404, 'job_not_found', '任务不存在或已到期')
    return row


def terminate_process(proc):
    if proc.returncode is not None:
        return
    try:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            import subprocess
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=15, check=False)
    except ProcessLookupError:
        log.info('worker already exited pid=%s', proc.pid)


def storage_used():
    total = 0
    for path in (DATA / 'jobs').rglob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            log.debug('file removed during quota scan')
    return total


def cleanup_ipc(directory):
    record = directory / 'ipc-directory.txt'
    if record.exists():
        target = Path(record.read_text(encoding='utf-8'))
        if target.parent == Path('/tmp') and re.fullmatch(r'fh-[0-9a-f]{12}', target.name) and target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)


upload_inflight = 0


@asynccontextmanager
async def parse_upload(request):
    global upload_inflight
    size = 0
    budget = STORAGE_MB * 1024 * 1024
    async def limited_stream():
        nonlocal size
        global upload_inflight
        async for chunk in request.stream():
            size += len(chunk)
            upload_inflight += len(chunk)
            if size > MAX_MB * MAX_FILES * 1024 * 1024 + 1024 * 1024:
                raise MultiPartException('上传总容量超过限制')
            current_used = storage_used()
            if current_used + upload_inflight > budget or shutil.disk_usage(DATA).free < MIN_FREE_MB * 1024 * 1024:
                raise MultiPartException('转换空间不足，请删除已结束任务后重试')
            yield chunk
    parser = LimitedMultipartParser(request.headers, limited_stream(), max_files=MAX_FILES + 20, max_fields=5, max_part_size=65536)
    form = None
    try:
        form = await parser.parse()
        yield form
    except MultiPartException as exc:
        raise Problem(413, 'upload_limit', str(exc)) from exc
    finally:
        upload_inflight -= size
        if form is not None:
            await form.close()


def release_slot(job_id):
    if job_id in reserved_slots:
        reserved_slots.remove(job_id)
        slots.release()


class LimitedMultipartParser(MultiPartParser):
    def on_part_begin(self):
        super().on_part_begin()
        self.file_bytes = 0

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > MAX_MB * 1024 * 1024:
                raise MultiPartException(f'单个文件超过 {MAX_MB} MB')
        super().on_part_data(data, start, end)


async def worker():
    while True:
        job_id = await queue.get()
        proc = None
        spawn_task = None
        try:
            with db() as c:
                row = c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['state'] != 'queued':
                continue
            patch(job_id, state='running', started_at=time.time())
            directory = DATA / 'jobs' / job_id
            logfile = directory / 'worker.log'
            command = [sys.executable, '--worker'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'app.worker']
            with logfile.open('wb') as stream:
                spawn_task = asyncio.create_task(asyncio.create_subprocess_exec(*command, str(directory), cwd=str(ROOT), stdout=stream, stderr=stream, start_new_session=os.name == 'posix', env={**os.environ, 'FORMAT_HUB_PARENT_PID': str(os.getpid())}))
                proc = await asyncio.shield(spawn_task)
                active[job_id] = proc
                with db() as c:
                    current = c.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
                if not current or current['state'] == 'cancelled':
                    terminate_process(proc)
                    await proc.wait()
                    continue
                deadline = time.monotonic() + TIMEOUT
                while proc.returncode is None:
                    try:
                        await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=0.5)
                    except asyncio.TimeoutError:
                        if time.monotonic() > deadline:
                            terminate_process(proc)
                            await proc.wait()
                            raise ValueError(f'转换超过 {TIMEOUT} 秒，已停止；请拆小文件重试')
                        if storage_used() > STORAGE_MB * 1024**2 or shutil.disk_usage(DATA).free < MIN_FREE_MB * 1024**2:
                            terminate_process(proc)
                            await proc.wait()
                            raise ValueError('转换磁盘预算不足，任务已停止；请删除旧任务或减小文件')
            with db() as c:
                current = c.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if current['state'] == 'cancelled':
                continue
            result_file = directory / 'result.json'
            if not result_file.exists():
                raise ValueError('转换引擎意外退出，请检查文件内容或更换转换方式')
            if storage_used() > STORAGE_MB * 1024**2 or shutil.disk_usage(DATA).free < MIN_FREE_MB * 1024**2:
                raise ValueError('转换结果超过磁盘预算，请删除此任务并减小文件')
            result = json.loads(result_file.read_text(encoding='utf-8'))
            if proc.returncode != 0 or not result['ok']:
                raise ValueError(result.get('error', '转换失败'))
            outputs = []
            for name in result['outputs']:
                file = directory / 'output' / name
                if Path(name).name != name or not file.is_file() or file.stat().st_size == 0:
                    raise ValueError('转换未产生完整文件')
                outputs.append({'name': name, 'size': file.stat().st_size})
            if not outputs:
                raise ValueError('转换没有生成结果')
            patch(job_id, state='succeeded', finished_at=time.time(), outputs=json.dumps(outputs, ensure_ascii=False), warnings=json.dumps(result.get('warnings', []), ensure_ascii=False))
        except asyncio.CancelledError:
            if proc is None and spawn_task is not None:
                proc = await asyncio.shield(spawn_task)
            if proc and proc.returncode is None:
                terminate_process(proc)
                await proc.wait()
            raise
        except Exception as exc:
            log.exception('conversion failed job=%s', job_id)
            with db() as c:
                current = c.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if current and current['state'] != 'cancelled':
                patch(job_id, state='failed', finished_at=time.time(), error=str(exc)[:500])
        finally:
            active.pop(job_id, None)
            cleanup_ipc(DATA / 'jobs' / job_id)
            release_slot(job_id)
            queue.task_done()


def cleanup():
    cutoff = time.time() - RETENTION * 3600
    with db() as c:
        rows = c.execute("SELECT id FROM jobs WHERE state NOT IN ('running','queued') AND created_at<?", (cutoff,)).fetchall()
        for row in rows:
            remove_job_files(row['id'])
            c.execute('DELETE FROM jobs WHERE id=?', (row['id'],))
        c.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))


def remove_job_files(job_id):
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('invalid job id')
    path = (DATA / 'jobs' / job_id).resolve()
    if path.parent != (DATA / 'jobs').resolve():
        raise ValueError('invalid job directory')
    if path.exists():
        cleanup_ipc(path)
        shutil.rmtree(path)


async def janitor():
    while True:
        await asyncio.sleep(300)
        await asyncio.to_thread(cleanup)


@asynccontextmanager
async def lifespan(app):
    global queue, slots
    initialize()
    cleanup()
    queue = asyncio.Queue()
    reserved_slots.clear()
    slots = asyncio.Semaphore(QUEUE_LIMIT + WORKERS)
    tasks = [asyncio.create_task(worker()) for _ in range(WORKERS)] + [asyncio.create_task(janitor())]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title='格式工坊 API', version='1.0.0', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')


@app.exception_handler(Problem)
async def problem_handler(request, exc):
    return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc):
    return JSONResponse({'error': {'code': 'validation_error', 'message': '参数格式错误，请检查输入'}}, status_code=422)


@app.exception_handler(HTTPException)
async def http_handler(request, exc):
    return JSONResponse({'error': {'code': 'http_error', 'message': str(exc.detail)}}, status_code=exc.status_code)


@app.middleware('http')
async def boundaries(request, call_next):
    # Keep POST/DELETE same-origin even when no team password is configured.
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
        from urllib.parse import urlsplit
        if urlsplit(origin).netloc != request.headers.get('host'):
            return JSONResponse({'error': {'code': 'origin_denied', 'message': '请从本站页面提交请求'}}, status_code=403)
    max_body = MAX_MB * 1024 * 1024 * MAX_FILES + 1024 * 1024
    try:
        if int(request.headers.get('content-length', '0')) > max_body:
            return JSONResponse({'error': {'code': 'too_large', 'message': '上传文件超过总容量限制'}}, status_code=413)
    except ValueError:
        return JSONResponse({'error': {'code': 'invalid_length', 'message': '请求长度错误'}}, status_code=400)
    sid = request.cookies.get(COOKIE, '')
    fresh = False
    if request.url.path.startswith('/api/'):
        with db() as c:
            row = c.execute('SELECT id FROM sessions WHERE id=? AND expires>?', (sid, time.time())).fetchone()
            if not row:
                sid, fresh = secrets.token_urlsafe(32), True
                c.execute('INSERT INTO sessions VALUES (?,?,?)', (sid, time.time() + RETENTION * 3600 + 86400, 0))
        request.state.session_id = sid
    response = await call_next(request)
    if fresh:
        response.set_cookie(COOKIE, sid, httponly=True, samesite='strict', secure=request.url.scheme == 'https', max_age=RETENTION * 3600 + 86400)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/')
def index():
    return FileResponse(ROOT / 'app/static/index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': '1.0.0', 'running': len(active), 'queued': queue.qsize(), 'workers': WORKERS}


@app.get('/api/session')
def get_session(request: Request):
    row = session(request, False)
    return {'authenticated': bool(row['authenticated']) or not TEAM_TOKEN, 'required': bool(TEAM_TOKEN)}


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(max_length=512)


login_attempts: dict[str, list[float]] = {}


@app.post('/api/login')
def login(request: Request, body: Login):
    ip = request.client.host
    attempts = [t for t in login_attempts.get(ip, []) if t > time.time() - 60]
    if len(attempts) >= 10:
        raise Problem(429, 'rate_limited', '尝试次数较多，请一分钟后重试')
    login_attempts[ip] = attempts + [time.time()]
    if TEAM_TOKEN and not hmac.compare_digest(hashlib.sha256(body.token.encode()).digest(), hashlib.sha256(TEAM_TOKEN.encode()).digest()):
        raise Problem(401, 'wrong_token', '团队访问口令错误')
    row = session(request, False)
    new_id = secrets.token_urlsafe(32)
    with db() as c:
        c.execute('UPDATE sessions SET id=?,authenticated=1 WHERE id=?', (new_id, row['id']))
        c.execute('UPDATE jobs SET owner=? WHERE owner=?', (new_id, row['id']))
    response = JSONResponse({'authenticated': True})
    response.set_cookie(COOKIE, new_id, httponly=True, samesite='strict', secure=request.url.scheme == 'https', max_age=RETENTION * 3600 + 86400)
    return response


@app.post('/api/logout')
def logout(request: Request):
    row = session(request, False)
    with db() as c:
        c.execute('UPDATE sessions SET authenticated=0 WHERE id=?', (row['id'],))
    return {'authenticated': False}


@app.get('/api/catalog')
def get_catalog(request: Request):
    session(request)
    return {'tools': catalog(), 'limits': {'max_file_mb': MAX_MB, 'max_files': MAX_FILES, 'workers': WORKERS, 'retention_hours': RETENTION}, 'version': '1.0.0'}


@app.get('/api/jobs')
def list_jobs(request: Request):
    owner = session(request)['id']
    with db() as c:
        rows = c.execute('SELECT * FROM jobs WHERE owner=? ORDER BY created_at DESC LIMIT 200', (owner,)).fetchall()
    return {'jobs': [view(row) for row in rows]}


@app.post('/api/inspect')
async def inspect_pdf(request: Request):
    session(request)
    if slots.locked():
        raise Problem(429, 'queue_full', '转换繁忙，请稍后预览')
    await slots.acquire()
    job_id = secrets.token_hex(16)
    reserved_slots.add(job_id)
    directory = DATA / 'jobs' / job_id
    proc = None
    spawn_task = None
    try:
        async with parse_upload(request) as form:
            uploads = form.getlist('files')
            password = form.get('password', '')
            if not isinstance(password, str) or len(password) > 256:
                raise Problem(422, 'invalid_password', 'PDF 密码格式错误')
            if not 1 <= len(uploads) <= MAX_FILES:
                raise Problem(422, 'invalid_files', f'请选择1至{MAX_FILES}份 PDF')
            (directory / 'input').mkdir(parents=True)
            (directory / 'output').mkdir()
            metadata = []
            for i, upload in enumerate(uploads):
                name = Path((getattr(upload, 'filename', '') or '').replace('\\', '/')).name[:180]
                if Path(name).suffix.lower() != '.pdf' or not hasattr(upload, 'read'):
                    raise Problem(422, 'invalid_pdf', '预览只支持 PDF 文件')
                stored = f'{i+1:03d}.pdf'
                with (directory / 'input' / stored).open('wb') as stream:
                    while chunk := await upload.read(1024 * 1024):
                        stream.write(chunk)
                metadata.append({'name': name, 'stored': stored})
            (directory / 'spec.json').write_text(json.dumps({'operation':'__inspect_pdf','files':[f['stored'] for f in metadata],'file_metadata':metadata,'options':{'password':password}},ensure_ascii=False),encoding='utf-8')
        command = [sys.executable, '--worker'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'app.worker']
        with (directory / 'worker.log').open('wb') as stream:
            spawn_task = asyncio.create_task(asyncio.create_subprocess_exec(*command, str(directory), cwd=str(ROOT), stdout=stream, stderr=stream, start_new_session=os.name == 'posix', env={**os.environ, 'FORMAT_HUB_PARENT_PID':str(os.getpid())}))
            proc = await asyncio.shield(spawn_task)
            active[job_id] = proc
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except asyncio.TimeoutError:
                terminate_process(proc)
                await proc.wait()
                raise Problem(422, 'preview_timeout', 'PDF 预览超时，请减小文件或稍后重试')
        if proc.returncode or not (directory / 'inspection.json').exists():
            raise Problem(422, 'preview_failed', 'PDF 预览失败，请检查文件是否完整')
        return json.loads((directory / 'inspection.json').read_text(encoding='utf-8'))
    except OSError as exc:
        log.exception('preview worker startup failed')
        raise Problem(503, 'preview_unavailable', '预览引擎启动失败，请稍后重试') from exc
    finally:
        try:
            if proc is None and spawn_task is not None:
                try:
                    proc = await asyncio.shield(spawn_task)
                except OSError:
                    log.error('preview spawn failed during cleanup')
            if proc and proc.returncode is None:
                terminate_process(proc)
                await proc.wait()
        finally:
            active.pop(job_id, None)
            release_slot(job_id)
            remove_job_files(job_id)


@app.post('/api/jobs', status_code=202, openapi_extra={'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': {'type': 'object', 'required': ['operation', 'files'], 'properties': {'operation': {'type': 'string', 'description': 'GET /api/catalog 返回的工具ID'}, 'options': {'type': 'string', 'description': 'JSON对象字符串，例如 {"target":"pdf"}'}, 'files': {'type': 'array', 'items': {'type': 'string', 'format': 'binary'}}}}}}}})
async def create_job(request: Request):
    owner = session(request)['id']
    if slots.locked():
        raise Problem(429, 'queue_full', '转换队列已满，请稍后提交')
    await slots.acquire()
    job_id = secrets.token_hex(16)
    directory = DATA / 'jobs' / job_id
    accepted = False
    reserved_slots.add(job_id)
    try:
        async with parse_upload(request) as form:
            operation = form.get('operation', '')
            if not isinstance(operation, str):
                raise Problem(422, 'invalid_operation', '请选择转换工具')
            tool = next((x for x in catalog() if x['id'] == operation), None)
            if not tool:
                raise Problem(422, 'unknown_operation', '转换工具不存在')
            if not tool['available']:
                raise Problem(422, 'engine_missing', tool['reason'])
            try:
                options = json.loads(form.get('options', '{}'))
            except (ValueError, TypeError):
                raise Problem(422, 'invalid_options', '转换参数不是有效 JSON')
            if not isinstance(options, dict) or len(json.dumps(options)) > 16384:
                raise Problem(422, 'invalid_options', '转换参数应为对象且长度小于16KB')
            if any(key.startswith('_') for key in options):
                raise Problem(422, 'private_option', '包含不允许的内部参数')
            font_uploads = form.getlist('fonts')
            if font_uploads and (operation != 'cad_convert' or len(font_uploads) > 20):
                raise Problem(422, 'invalid_fonts', 'CAD 字库最多选择20个字体文件')
            uploads = form.getlist('files')
            if not uploads or len(uploads) > MAX_FILES or any(not hasattr(u, 'read') for u in uploads):
                raise Problem(422, 'invalid_files', f'请选择1至{MAX_FILES}个文件')
            if not tool['multiple'] and len(uploads) != 1:
                raise Problem(422, 'single_file', '此工具每次处理一个文件')
            directory.mkdir(parents=True)
            (directory / 'input').mkdir()
            (directory / 'output').mkdir()
            files = []
            for i, upload in enumerate(uploads):
                name = Path((upload.filename or 'file').replace('\\', '/')).name
                name = re.sub(r'[\x00-\x1f<>:"|?*]', '_', name)[:180]
                suffix = Path(name).suffix.lower().lstrip('.')
                if suffix not in tool['inputs']:
                    raise Problem(422, 'unsupported_input', f'{name} 格式不适用于此工具')
                stored = f'{i + 1:03d}.{suffix}'
                dest = directory / 'input' / stored
                size = 0
                with dest.open('wb') as stream:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_MB * 1024 * 1024:
                            raise Problem(413, 'file_too_large', f'{name} 超过 {MAX_MB} MB')
                        stream.write(chunk)
                if not size:
                    raise Problem(422, 'empty_file', f'{name} 是空文件')
                files.append({'name': name, 'size': size, 'stored': stored})
            if font_uploads:
                fonts_dir = directory / 'fonts'
                fonts_dir.mkdir()
                total_fonts = 0
                seen_fonts = set()
                for upload in font_uploads:
                    from app.names import safe_stem
                    suffix = Path(upload.filename or '').suffix.lower()
                    if suffix not in {'.ttf', '.ttc', '.otf', '.shx'}:
                        raise Problem(422, 'invalid_font_type', '字库仅支持 TTF/TTC/OTF/SHX 字体')
                    name = safe_stem(upload.filename, 140) + suffix
                    if name.casefold() in seen_fonts:
                        raise Problem(422, 'duplicate_font', '字体文件名称重复，请保留一份')
                    seen_fonts.add(name.casefold())
                    size = 0
                    with (fonts_dir / name).open('wb') as stream:
                        while chunk := await upload.read(1024 * 1024):
                            size += len(chunk)
                            total_fonts += len(chunk)
                            if size > 20 * 1024**2 or total_fonts > 60 * 1024**2:
                                raise Problem(413, 'fonts_too_large', '每个字体最多20MB，单次字库最多60MB')
                            stream.write(chunk)
                    if size < 4:
                        raise Problem(422, 'empty_font', '字体文件为空或不完整')
                    with (fonts_dir / name).open('rb') as stream:
                        signature = stream.read(32)
                    valid = signature[:4] in {b'\x00\x01\x00\x00', b'OTTO', b'ttcf', b'true'} if suffix != '.shx' else signature.startswith(b'AutoCAD-86 ')
                    if not valid:
                        raise Problem(422, 'invalid_font', '字体内容与格式不符')
            spec = {'operation': operation, 'files': [f['stored'] for f in files], 'file_metadata': files, 'options': options}
            (directory / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
            with db() as c:
                c.execute('INSERT INTO jobs(id,owner,operation,tool_name,state,created_at,files,options) VALUES (?,?,?,?,?,?,?,?)', (job_id, owner, operation, tool['name'], 'queued', time.time(), json.dumps(files, ensure_ascii=False), json.dumps(options, ensure_ascii=False)))
            queue.put_nowait(job_id)
            accepted = True
            with db() as c:
                return view(c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
    finally:
        if not accepted:
            release_slot(job_id)
            remove_job_files(job_id)


@app.post('/api/jobs/{job_id}/cancel')
async def cancel_job(job_id: str, request: Request):
    row = owned(request, job_id)
    if row['state'] in ('queued', 'running'):
        patch(job_id, state='cancelled', finished_at=time.time(), error='任务已取消')
        if row['state'] == 'queued':
            # Remove a pending tombstone before returning its capacity.
            pending = []
            while not queue.empty():
                item = queue.get_nowait()
                queue.task_done()
                if item != job_id:
                    pending.append(item)
            for item in pending:
                queue.put_nowait(item)
            release_slot(job_id)
        proc = active.get(job_id)
        if proc and proc.returncode is None:
            terminate_process(proc)
            await proc.wait()
    with db() as c:
        return view(c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())


@app.delete('/api/jobs/{job_id}')
def delete_job(job_id: str, request: Request):
    row = owned(request, job_id)
    if row['state'] in ('queued', 'running') or job_id in active:
        raise Problem(409, 'job_active', '请先取消任务，等待引擎结束后删除')
    remove_job_files(job_id)
    with db() as c:
        c.execute('DELETE FROM jobs WHERE id=?', (job_id,))
    return {'deleted': True}


@app.get('/api/jobs/{job_id}/files/{name}')
def download(job_id: str, name: str, request: Request):
    row = owned(request, job_id)
    outputs = json.loads(row['outputs'])
    if row['state'] != 'succeeded' or name not in [f['name'] for f in outputs] or Path(name).name != name:
        raise Problem(404, 'file_not_found', '结果文件不存在或尚未生成')
    return FileResponse(DATA / 'jobs' / job_id / 'output' / name, filename=name, media_type='application/octet-stream')


@app.get('/api/jobs/{job_id}/bundle')
def bundle(job_id: str, request: Request):
    row = owned(request, job_id)
    if row['state'] != 'succeeded':
        raise Problem(409, 'not_ready', '转换完成后即可下载')
    # Unique temporary archives prevent two browser downloads racing on one file.
    from starlette.background import BackgroundTask
    total = sum(f['size'] for f in json.loads(row['outputs']))
    if storage_used() + total > STORAGE_MB * 1024**2 or shutil.disk_usage(DATA).free - total < MIN_FREE_MB * 1024**2:
        raise Problem(507, 'storage_full', '打包空间不足，请逐个下载文件')
    archive = DATA / 'jobs' / job_id / f'bundle-{secrets.token_hex(8)}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_STORED) as z:
        for f in json.loads(row['outputs']):
            z.write(DATA / 'jobs' / job_id / 'output' / f['name'], f['name'])
    return FileResponse(archive, filename='格式工坊-转换结果.zip', background=BackgroundTask(archive.unlink, missing_ok=True))
