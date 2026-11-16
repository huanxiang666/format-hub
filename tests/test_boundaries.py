"""Regression tests from independent review, using real OS processes."""
import asyncio
import io
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest
from app import server as s


def seed(tmp_path):
    s.DATA = tmp_path
    s.initialize()
    s.queue = asyncio.Queue()
    s.slots = asyncio.Semaphore(0)
    s.reserved_slots.clear()
    jid = 'a' * 32
    directory = s.DATA / 'jobs' / jid
    directory.mkdir()
    (directory / 'output').mkdir()
    with s.db() as c:
        c.execute('INSERT INTO sessions VALUES (?,?,?)', ('owner', time.time() + 3600, 1))
        c.execute('INSERT INTO jobs(id,owner,operation,tool_name,state,created_at,files,options) VALUES (?,?,?,?,?,?,?,?)', (jid, 'owner', 'image_convert', 'x', 'queued', time.time(), '[]', '{}'))
    s.reserved_slots.add(jid)
    return jid


@pytest.mark.parametrize('cancel_before_registered', [True, False])
def test_cancel_spawn_and_shutdown_reap(tmp_path, monkeypatch, cancel_before_registered):
    async def run():
        jid = seed(tmp_path)
        original = asyncio.create_subprocess_exec
        spawned, release = asyncio.Event(), asyncio.Event()
        processes = []
        async def delayed(*args, **kwargs):
            proc = await original(sys.executable, '-c', 'import time; time.sleep(60)', start_new_session=True)
            processes.append(proc)
            spawned.set()
            await release.wait()
            return proc
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed)
        task = asyncio.create_task(s.worker())
        await s.queue.put(jid)
        await spawned.wait()
        if cancel_before_registered:
            await s.cancel_job(jid, SimpleNamespace(state=SimpleNamespace(session_id='owner')))
        release.set()
        if cancel_before_registered:
            await asyncio.wait_for(processes[0].wait(), timeout=3)
            assert processes[0].returncode is not None
        else:
            while jid not in s.active:
                await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None
        assert jid not in s.active
    asyncio.run(run())


def test_queued_cancel_releases_capacity_once(tmp_path):
    async def run():
        jid = seed(tmp_path)
        await s.queue.put(jid)
        assert s.slots.locked()
        await s.cancel_job(jid, SimpleNamespace(state=SimpleNamespace(session_id='owner')))
        assert not s.slots.locked() and s.queue.empty()
        await s.slots.acquire()
        assert s.slots.locked()
        s.release_slot(jid)
        assert s.slots.locked()
    asyncio.run(run())


def test_stream_upload_rejected_before_full_body(tmp_path, monkeypatch):
    async def run():
        seed(tmp_path)
        s.slots = asyncio.Semaphore(1)
        monkeypatch.setattr(s, 'MAX_MB', 1)
        monkeypatch.setattr(s, 'TEAM_TOKEN', '')
        body = (b'--test\r\nContent-Disposition: form-data; name="operation"\r\n\r\nimage_convert\r\n' b'--test\r\nContent-Disposition: form-data; name="files"; filename="large.png"\r\nContent-Type: image/png\r\n\r\n' + b'x' * (3*1024*1024) + b'\r\n--test--\r\n')
        sent = 0
        async def chunks():
            nonlocal sent
            for i in range(0, len(body), 65536):
                chunk = body[i:i+65536]
                sent += len(chunk)
                yield chunk
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=s.app), base_url='http://review') as client:
            response = await client.post('/api/jobs', content=chunks(), headers={'Content-Type': 'multipart/form-data; boundary=test'})
        assert response.status_code == 413, response.text
        assert sent < 2 * 1024 * 1024
        assert s.upload_inflight == 0
    asyncio.run(run())


def test_storage_budget_counts_interleaved_upload(tmp_path, monkeypatch):
    async def run():
        seed(tmp_path)
        s.slots = asyncio.Semaphore(4)
        s.reserved_slots.clear()
        monkeypatch.setattr(s, 'STORAGE_MB', 1)
        monkeypatch.setattr(s, 'TEAM_TOKEN', '')
        started, release = asyncio.Event(), asyncio.Event()
        def body(name):
            return (b'--b\r\nContent-Disposition: form-data; name="operation"\r\n\r\nimage_convert\r\n--b\r\nContent-Disposition: form-data; name="files"; filename="'+name.encode()+b'"\r\n\r\n'+b'x'*600000+b'\r\n--b--\r\n')
        async def delayed():
            value = body('b.png')
            yield value[:100]
            started.set()
            await release.wait()
            for i in range(100,len(value),65536):
                yield value[i:i+65536]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=s.app),base_url='http://review') as c:
            await c.get('/api/session')
            task = asyncio.create_task(c.post('/api/jobs',content=delayed(),headers={'Content-Type':'multipart/form-data; boundary=b'}))
            await started.wait()
            first=await c.post('/api/jobs',content=body('a.png'),headers={'Content-Type':'multipart/form-data; boundary=b'})
            assert first.status_code==202,first.text
            release.set()
            second=await task
            assert second.status_code==413,second.text
            assert s.storage_used() < 1024**2
    asyncio.run(run())


def test_worker_low_nofile_and_sandbox(tmp_path):
    directory = tmp_path / 'job'
    (directory / 'input').mkdir(parents=True)
    (directory / 'output').mkdir()
    Image.new('RGB', (10, 10), 'blue').save(directory / 'input/001.png')
    (directory / 'spec.json').write_text(json.dumps({'operation': 'image_convert', 'files': ['001.png'], 'options': {'target': 'jpg'}}))
    def limit():
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    proc = subprocess.run([sys.executable, '-m', 'app.worker', str(directory)], cwd=s.ROOT, preexec_fn=limit, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'landlock' in proc.stdout
    result = json.loads((directory / 'result.json').read_text())
    assert result['ok'] and Image.open(directory / 'output' / result['outputs'][0]).size == (10, 10)


def test_sandbox_denies_private_files_and_ip_sockets(tmp_path):
    directory = tmp_path / 'allowed'
    directory.mkdir()
    secret = tmp_path / 'private.txt'
    secret.write_text('private')
    source = """
import json,socket,sys
from pathlib import Path
from app.sandbox import confine
confine(Path(sys.argv[1]))
result={}
try: Path(sys.argv[2]).read_text(); result['file']='allowed'
except PermissionError: result['file']='denied'
try: socket.socket(socket.AF_INET,socket.SOCK_STREAM); result['network']='allowed'
except PermissionError: result['network']='denied'
Path(sys.argv[1],'result.json').write_text(json.dumps(result))
"""
    proc = subprocess.run([sys.executable, '-c', source, str(directory), str(secret)], cwd=s.ROOT, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((directory / 'result.json').read_text()) == {'file': 'denied', 'network': 'denied'}


def test_worker_rejects_fast_output_over_budget(tmp_path):
    directory = tmp_path / 'budget'
    (directory / 'input').mkdir(parents=True)
    (directory / 'output').mkdir()
    Image.new('RGB', (1000, 1000), 'red').save(directory / 'input/001.png')
    (directory / 'spec.json').write_text(json.dumps({'operation':'image_convert','files':['001.png'],'options':{'target':'bmp'}}))
    proc = subprocess.run([sys.executable,'-m','app.worker',str(directory)],cwd=s.ROOT,env={**os.environ,'FORMAT_HUB_JOB_MB':'1'},capture_output=True,text=True,timeout=15)
    assert proc.returncode == 1
    result = json.loads((directory/'result.json').read_text())
    assert not result['ok'] and '容量预算' in result['error']


def test_worker_checks_expected_parent_before_conversion(tmp_path):
    directory = tmp_path / 'parent'
    directory.mkdir()
    proc = subprocess.run([sys.executable,'-m','app.worker',str(directory)],cwd=s.ROOT,env={**os.environ,'FORMAT_HUB_PARENT_PID':'99999999'},start_new_session=True,capture_output=True,text=True,timeout=15)
    assert proc.returncode == -9
    assert not (directory/'result.json').exists()


def test_office_worker_runs_inside_sandbox(tmp_path):
    from docx import Document
    import fitz
    directory = tmp_path / 'office'
    (directory / 'input').mkdir(parents=True)
    (directory / 'output').mkdir()
    doc = Document()
    doc.add_paragraph('Isolated Office success 12345')
    doc.save(directory / 'input/001.docx')
    (directory / 'spec.json').write_text(json.dumps({'operation':'office_convert','files':['001.docx'],'options':{'target':'pdf'}}))
    proc = subprocess.run([sys.executable,'-m','app.worker',str(directory)],cwd=s.ROOT,capture_output=True,text=True,timeout=30)
    assert proc.returncode == 0, proc.stdout+proc.stderr+(directory/'result.json').read_text()
    result=json.loads((directory/'result.json').read_text())
    with fitz.open(directory/'output'/result['outputs'][0]) as pdf:
        assert 'Isolated Office success 12345' in pdf[0].get_text()
