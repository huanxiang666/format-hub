"""Settings, management isolation and admission tests against isolated real SQLite/DATA."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import stat
import sys
import time

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from pydantic import ValidationError

from app import server as s
from app.settings import Settings, SettingsStore, SettingsUpdate, RevisionConflict, environment_defaults


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(s, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(s, 'TEAM_TOKEN', '')
    monkeypatch.setenv('FORMAT_HUB_ADMIN_TOKEN', 'independent-admin-secret')
    with TestClient(s.app, client=('198.51.100.42', 4000)) as c:
        c.get('/api/session')
        yield c


def admin(c):
    assert c.post('/api/admin/login', json={'token': 'independent-admin-secret'}).status_code == 200


def update(c, **values):
    config = c.get('/api/settings').json()
    result = c.put('/api/settings', json={'values': values, 'revision': config['revision']})
    assert result.status_code == 200, result.text
    return result.json()


def image():
    stream = io.BytesIO()
    Image.new('RGB', (20, 20)).save(stream, 'PNG')
    return stream.getvalue()


def submit(c, names=('a.png',), operation='image_convert', options=None):
    return c.post('/api/jobs', data={'operation': operation, 'options': json.dumps(options or {'target': 'jpg'})},
                  files=[('files', (name, image())) for name in names])


def test_remote_auth_is_independent_and_header_spoof_does_not_work(client, monkeypatch):
    monkeypatch.setattr(s, 'TEAM_TOKEN', 'team-secret')
    assert client.get('/api/settings').status_code == 401
    assert client.post('/api/login', json={'token': 'team-secret'}).status_code == 200
    public = client.get('/api/settings', headers={'X-Forwarded-For': '127.0.0.1'}).json()
    assert not public['can_edit'] and public['needs_admin_login']
    text = json.dumps(public)
    assert 'secret' not in text and str(s.DATA) not in text
    assert client.put('/api/settings', json={'values': {'workers': 4}, 'revision': 0}).status_code == 403
    assert client.post('/api/admin/login', json={'token': 'team-secret'}).status_code == 403
    assert client.get('/api/catalog').status_code == 200
    admin(client)
    assert client.get('/api/settings').json()['can_edit']
    assert client.post('/api/logout').status_code == 200
    assert client.post('/api/login', json={'token': 'team-secret'}).status_code == 200
    assert not client.get('/api/settings').json()['can_edit']


def test_loopback_no_extra_password_and_generated_token_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(s, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(s, 'TEAM_TOKEN', '')
    monkeypatch.delenv('FORMAT_HUB_ADMIN_TOKEN', raising=False)
    with TestClient(s.app, client=('127.0.0.1', 5555), base_url='http://127.0.0.1:8766') as c:
        result = c.get('/api/settings').json()
        assert result['can_edit'] and not result['needs_admin_login']
        assert len((s.DATA / 'admin-token').read_text().strip()) >= 32
        assert stat.S_IMODE((s.DATA / 'admin-token').stat().st_mode) == 0o600
        assert update(c, workers=32)['values']['workers'] == 32


@pytest.mark.parametrize('host,headers,editable', [
    ('127.0.0.1:8766', {}, True), ('localhost:8766', {}, True), ('[::1]:8766', {}, True),
    ('public.example', {}, False), ('127.0.0.1:8766', {'X-Forwarded-For': '198.51.100.20'}, False),
    ('localhost:8766', {'Forwarded': 'for=198.51.100.20'}, False),
    ('localhost:8766', {'X-Forwarded-Proto': 'https'}, False),
    ('localhost:8766', {'X-Real-IP': '198.51.100.20'}, False),
])
def test_local_proxy_is_not_implicitly_admin(tmp_path, monkeypatch, host, headers, editable):
    monkeypatch.setattr(s, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(s, 'TEAM_TOKEN', 'team-secret')
    monkeypatch.setenv('FORMAT_HUB_ADMIN_TOKEN', 'independent-admin-secret')
    with TestClient(s.app, client=('127.0.0.1', 5555), base_url='http://' + host) as c:
        assert c.post('/api/login', json={'token': 'team-secret'}).status_code == 200
        response = c.get('/api/settings', headers=headers)
        assert response.status_code == 200
        assert response.json()['can_edit'] is editable
        update_response = c.put('/api/settings', headers=headers, json={'values': {'workers': 3}, 'revision': 0})
        assert update_response.status_code == (200 if editable else 403)
        if not editable:
            assert c.get('/api/catalog').status_code == 200
            assert c.post('/api/admin/login', headers=headers, json={'token': 'independent-admin-secret'}).status_code == 200
            assert c.get('/api/settings', headers=headers).json()['can_edit']


def test_admin_rate_limit_preserves_team(client):
    for _ in range(10):
        assert client.post('/api/admin/login', json={'token': 'bad'}).status_code == 403
    assert client.post('/api/admin/login', json={'token': 'bad'}).status_code == 429
    assert client.get('/api/catalog').status_code == 200


@pytest.mark.parametrize('values', [{'workers': 0}, {'workers': 33}, {'workers': True}, {'workers': '4'},
                                     {'timeout_seconds': 29}, {'cad_timeout_seconds': 86401}, {'memory_mb': 511},
                                     {'media_threads': 17}, {'secret': 1}, {'max_files': 101}])
def test_settings_reject_without_partial_write(client, values):
    admin(client)
    before = client.get('/api/settings').json()
    response = client.put('/api/settings', json={'values': values, 'revision': before['revision']})
    assert response.status_code == 422
    after = client.get('/api/settings').json()
    assert after['values'] == before['values'] and after['revision'] == before['revision']
    assert not (s.DATA / 'settings.json').exists()


def test_revision_race_and_atomic_failure(client, monkeypatch):
    admin(client)
    revision = client.get('/api/settings').json()['revision']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda workers: client.put('/api/settings', json={'values': {'workers': workers}, 'revision': revision}), [3, 4]))
    assert sorted(r.status_code for r in results) == [200, 409]
    path = s.DATA / 'settings.json'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()
    previous = client.get('/api/settings').json()
    def fail_replace(*args):
        raise OSError('injected disk failure')
    monkeypatch.setattr(os, 'replace', fail_replace)
    result = client.put('/api/settings', json={'values': {'workers': 6}, 'revision': previous['revision']})
    assert result.status_code == 503
    assert path.read_bytes() == before
    assert client.get('/api/settings').json()['values'] == previous['values']
    assert not list(s.DATA.glob('.settings-*.tmp'))


def test_saved_precedence_and_strict_disk(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMAT_HUB_WORKERS', '3')
    store = SettingsStore(tmp_path, environment_defaults())
    store.update(SettingsUpdate(values={'workers': 7}, revision=0))
    monkeypatch.setenv('FORMAT_HUB_WORKERS', '9')
    loaded = SettingsStore(tmp_path, environment_defaults())
    assert loaded.values.workers == 7 and loaded.revision == 1
    with pytest.raises(RevisionConflict):
        loaded.update(SettingsUpdate(values={'workers': 8}, revision=0))
    loaded.path.write_text('{"values":{"workers":"7"},"revision":1}')
    with pytest.raises(ValidationError):
        SettingsStore(tmp_path, Settings())


def test_cad_batch_snapshot_fonts_and_bad_middle_rollback(client, monkeypatch):
    admin(client)
    update(client, cad_timeout_seconds=1800, media_threads=4)
    monkeypatch.setattr(s, 'catalog', lambda: [{'id': 'cad_convert', 'available': True, 'multiple': True, 'inputs': ['dxf'], 'name': 'CAD'}])
    response = client.post('/api/jobs', data={'operation': 'cad_convert', 'options': '{"target":"pdf"}'}, files=[
        ('files', ('one.dxf', b'dxf-data')), ('files', ('two.dxf', b'dxf-data')),
        ('fonts', ('example.ttf', b'\x00\x01\x00\x00font-data'))])
    assert response.status_code == 202, response.text
    body = response.json()
    assert body['id'] == body['jobs'][0]['id'] and len(body['jobs']) == 2
    for job in body['jobs']:
        spec = json.loads((s.DATA / 'jobs' / job['id'] / 'spec.json').read_text())
        assert spec['batch_id'] == body['batch_id'] == job['batch_id']
        assert len(spec['files']) == 1 and 'batch_mode' not in spec['options']
        assert spec['runtime']['timeout_seconds'] == job['timeout_seconds'] == 1800
        assert spec['runtime']['media_threads'] == 4
        assert len(list((s.DATA / 'jobs' / job['id'] / 'fonts').iterdir())) == 1
    before = {p.name for p in (s.DATA / 'jobs').iterdir()}
    result = client.post('/api/jobs', data={'operation': 'cad_convert', 'options': '{}'}, files=[
        ('files', ('first.dxf', b'x')), ('files', ('middle.exe', b'x')), ('files', ('last.dxf', b'x'))])
    assert result.status_code == 422
    assert {p.name for p in (s.DATA / 'jobs').iterdir()} == before
    assert len(client.get('/api/jobs').json()['jobs']) == 2


def test_batch_capacity_rejects_whole_batch(client):
    admin(client)
    update(client, workers=1, queue_limit=1)
    before = client.get('/api/jobs').json()['jobs']
    response = submit(client, ('a.png', 'b.png', 'c.png'), options={'target': 'jpg', 'batch_mode': 'separate'})
    assert response.status_code == 429
    assert client.get('/api/jobs').json()['jobs'] == before
    assert not list((s.DATA / 'jobs').iterdir()) and not s.reserved_slots
    assert s.slots.used == 0


def wait_until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    pytest.fail('condition did not become true')


def test_dynamic_concurrency_real_processes_no_loss_or_duplicate(client, monkeypatch, tmp_path):
    admin(client)
    update(client, workers=2, timeout_seconds=90)
    original = asyncio.create_subprocess_exec
    records = tmp_path / 'execution'
    records.mkdir()
    # Only the engine boundary is substituted; scheduling, SQLite, admission,
    # cancellation, settings updates and OS processes remain real.
    source = '''import json,os,sys,time
from pathlib import Path
d=Path(sys.argv[1]); records=Path(sys.argv[2]); jid=d.name
with (records/(jid+'.count')).open('a') as f: f.write('run\\n')
(records/(jid+'.started')).write_text(json.dumps({k:v for k,v in os.environ.items() if k.startswith('FORMAT_HUB_') and k in ['FORMAT_HUB_TIMEOUT','FORMAT_HUB_MEMORY_MB','FORMAT_HUB_MEDIA_THREADS','FORMAT_HUB_JOB_MB']}))
while not (records/(jid+'.release')).exists(): time.sleep(.01)
(d/'output'/'done.txt').write_text('done')
(d/'result.json').write_text(json.dumps({'ok':True,'outputs':['done.txt']}))
'''
    async def controlled(*args, **kwargs):
        directory = args[-1]
        return await original(sys.executable, '-c', source, directory, str(records), **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', controlled)
    response = submit(client, tuple(f'{i}.png' for i in range(8)), options={'target': 'jpg', 'batch_mode': 'separate'})
    assert response.status_code == 202, response.text
    ids = [job['id'] for job in response.json()['jobs']]
    started = lambda: {p.stem for p in records.glob('*.started')}
    wait_until(lambda: len(started()) == 2)
    time.sleep(.1)
    assert len(started()) == 2
    update(client, workers=4)
    wait_until(lambda: len(started()) == 4)
    first = started()
    update(client, workers=1)
    # All four active tasks survive lowering. Completing three opens no new task.
    for jid in list(first)[:3]:
        (records / (jid + '.release')).touch()
    wait_until(lambda: len(s.active) == 1)
    assert started() == first
    (records / (list(first)[3] + '.release')).touch()
    wait_until(lambda: len(started()) == 5)
    assert len(s.active) <= 1
    # Cancel one queued child and ensure capacity is released exactly once.
    cancelled = next(jid for jid in ids if jid not in started())
    assert client.post(f'/api/jobs/{cancelled}/cancel').json()['state'] == 'cancelled'
    deadline = time.monotonic() + 10
    while True:
        assert time.monotonic() < deadline, 'queued tasks stalled after resizing'
        jobs = client.get('/api/jobs').json()['jobs']
        pending = [job for job in jobs if job['state'] in ('running', 'queued')]
        if not pending:
            break
        assert len(s.active) <= 1
        for jid in started():
            (records / (jid + '.release')).touch()
        time.sleep(.03)
    assert len(jobs) == 8
    assert sum(job['state'] == 'succeeded' for job in jobs) == 7
    assert sum(job['state'] == 'cancelled' for job in jobs) == 1
    assert all(p.read_text() == 'run\n' for p in records.glob('*.count'))
    assert len(started()) == 7 and cancelled not in started()
    assert not s.reserved_slots and s.slots.used == 0
    for path in records.glob('*.started'):
        env = json.loads(path.read_text())
        assert env['FORMAT_HUB_TIMEOUT'] == '90'
        assert env['FORMAT_HUB_MEMORY_MB'] == '2048'
        assert env['FORMAT_HUB_MEDIA_THREADS'] == '2'
        assert env['FORMAT_HUB_JOB_MB'] == '1024'


def test_server_uses_submitted_timeout_snapshot_and_reaps_process(client, monkeypatch, tmp_path):
    admin(client)
    update(client, timeout_seconds=60)
    original = asyncio.create_subprocess_exec
    started = tmp_path / 'started'
    processes = []
    async def controlled(*args, **kwargs):
        # Submission has completed its immutable snapshot before this boundary.
        directory = Path(args[-1])
        spec = json.loads((directory / 'spec.json').read_text())
        assert spec['runtime']['timeout_seconds'] == 60
        # Real process execution verifies the environment and process reaping.
        started.write_text(kwargs['env']['FORMAT_HUB_TIMEOUT'])
        proc = await original(sys.executable, '-c', 'import time; time.sleep(60)', **kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', controlled)
    # Hold dispatch to observe submission before allowing a real process.
    async def set_limit_and_release():
        async with s.worker_condition:
            s.WORKERS = 0
    client.portal.call(set_limit_and_release)
    response = submit(client)
    assert response.status_code == 202
    jid = response.json()['id']
    spec_path = s.DATA / 'jobs' / jid / 'spec.json'
    spec = json.loads(spec_path.read_text())
    assert spec['runtime']['timeout_seconds'] == 60
    # Control the clock after spawn, preserving the saved snapshot and env.
    original_monotonic = time.monotonic
    class FastClock:
        def __init__(self):
            self.first = None
        def __call__(self):
            now = original_monotonic()
            if started.exists():
                if self.first is None:
                    self.first = now
                # Keep asyncio's clock untouched: only server.time is replaced.
                return now + (120 if now - self.first > .2 else 0)
            return now
    from types import SimpleNamespace
    monkeypatch.setattr(s, 'time', SimpleNamespace(time=time.time, monotonic=FastClock()))
    async def start():
        async with s.worker_condition:
            s.WORKERS = 1
            s.worker_condition.notify_all()
    client.portal.call(start)
    wait_until(lambda: started.exists())
    # Changing defaults after submission must not alter the per-job deadline.
    update(client, timeout_seconds=600)
    wait_until(lambda: client.get('/api/jobs').json()['jobs'][0]['state'] == 'failed')
    job = client.get('/api/jobs').json()['jobs'][0]
    assert '60 秒' in job['error'] and job['timeout_seconds'] == 60
    assert started.read_text() == '60'
    assert processes[0].returncode is not None
    assert not s.active and not s.reserved_slots and s.slots.used == 0
    assert isinstance(job['elapsed_seconds'], int) and job['queue_position'] is None
