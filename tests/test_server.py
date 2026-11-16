"""Real subprocess/API tests; data always uses pytest's isolated temporary folder."""
import concurrent.futures
import io
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'DATA', tmp_path / 'service-data')
    monkeypatch.setattr(server, 'TEAM_TOKEN', '')
    with TestClient(server.app) as c:
        c.get('/api/session')
        yield c


def image_bytes(color='blue'):
    buffer = io.BytesIO()
    Image.new('RGB', (240, 160), color).save(buffer, 'PNG')
    return buffer.getvalue()


def submit(c, data=None, operation='image_convert', options=None, name='同名图片.png'):
    return c.post('/api/jobs', data={'operation': operation, 'options': json.dumps(options or {'target': 'jpg'})}, files={'files': (name, data if data is not None else image_bytes(), 'image/png')})


def terminal(c, job_id):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        job = next(j for j in c.get('/api/jobs').json()['jobs'] if j['id'] == job_id)
        if job['state'] not in ('queued', 'running'):
            return job
        time.sleep(.1)
    pytest.fail('conversion did not reach terminal state')


def test_upload_convert_download_and_delete(client):
    response = submit(client)
    assert response.status_code == 202, response.text
    job = terminal(client, response.json()['id'])
    assert job['state'] == 'succeeded', job
    output = client.get(job['outputs'][0]['url'])
    assert output.status_code == 200
    image = Image.open(io.BytesIO(output.content))
    assert image.format == 'JPEG' and image.size == (240, 160)
    bundle = client.get(f"/api/jobs/{job['id']}/bundle")
    import zipfile
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as z:
        assert len(z.namelist()) == 1
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert client.get(job['outputs'][0]['url']).status_code == 404


def test_concurrent_same_name_no_overwrite(client):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda color: submit(client, image_bytes(color)), ['red', 'green', 'blue', 'yellow']))
    assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
    jobs = [terminal(client, r.json()['id']) for r in responses]
    assert len({j['id'] for j in jobs}) == 4
    assert all(j['state'] == 'succeeded' for j in jobs), jobs
    pixels = []
    for job in jobs:
        pixels.append(Image.open(io.BytesIO(client.get(job['outputs'][0]['url']).content)).getpixel((1, 1)))
    assert len(set(pixels)) == 4


def test_cross_session_isolation(client):
    job = terminal(client, submit(client).json()['id'])
    client.cookies.clear()
    client.get('/api/session')
    assert client.get('/api/jobs').json()['jobs'] == []
    assert client.get(job['outputs'][0]['url']).status_code == 404
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 404
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 404


def test_bad_inputs_and_parser_failure(client):
    assert submit(client, b'').status_code == 422
    assert submit(client, name='file.exe').status_code == 422
    assert submit(client, operation='missing').status_code == 422
    response = client.post('/api/jobs', data={'operation': 'image_convert', 'options': '['}, files={'files': ('x.png', b'x')})
    assert response.status_code == 422
    job = terminal(client, submit(client, b'not an image').json()['id'])
    assert job['state'] == 'failed' and job['error']
    assert str(server.DATA) not in job['error']
    assert client.get(f"/api/jobs/{job['id']}/bundle").status_code == 409


def test_path_cleaning_and_origin(client):
    response = submit(client, name='../../outside.png')
    assert response.status_code == 202
    job = terminal(client, response.json()['id'])
    assert job['state'] == 'succeeded'
    assert job['files'][0]['name'] == 'outside.png'
    response = client.post('/api/jobs', headers={'Origin': 'https://another.example'})
    assert response.status_code == 403
    assert not (server.DATA / 'outside.png').exists()


def test_size_and_capacity_limits(client, monkeypatch):
    monkeypatch.setattr(server, 'MAX_MB', 0)
    assert submit(client).status_code == 413
    monkeypatch.setattr(server, 'MAX_MB', 200)
    class Full:
        def locked(self):
            return True
    monkeypatch.setattr(server, 'slots', Full())
    assert submit(client).status_code == 429


def test_cancel_queued_and_delete_guard(client):
    # Block worker entry with an event rather than timing a long conversion.
    import asyncio
    async def enqueue_cancel():
        job_id = 'a' * 32
        directory = server.DATA / 'jobs' / job_id
        directory.mkdir()
        owner = next(iter(client.cookies.values()))
        with server.db() as c:
            c.execute('INSERT INTO jobs(id,owner,operation,tool_name,state,created_at,files,options) VALUES (?,?,?,?,?,?,?,?)', (job_id, owner, 'image_convert', '图片转换', 'queued', time.time(), '[]', '{}'))
        return job_id
    job_id = client.portal.call(enqueue_cancel)
    assert client.delete(f'/api/jobs/{job_id}').status_code == 409
    assert client.post(f'/api/jobs/{job_id}/cancel').json()['state'] == 'cancelled'
    assert client.delete(f'/api/jobs/{job_id}').status_code == 200


def test_password_login_and_session_rotation(client, monkeypatch):
    monkeypatch.setattr(server, 'TEAM_TOKEN', 'test-team-key')
    assert client.get('/api/catalog').status_code == 401
    assert client.post('/api/login', json={'token': 'wrong'}).status_code == 401
    before = client.cookies.get(server.COOKIE)
    assert client.post('/api/login', json={'token': 'test-team-key'}).status_code == 200
    assert before != client.cookies.get(server.COOKIE)
    assert client.get('/api/catalog').status_code == 200
    assert client.post('/api/logout').status_code == 200
    assert client.get('/api/catalog').status_code == 401


def test_cleanup_only_expired_terminal_jobs(client):
    job = terminal(client, submit(client).json()['id'])
    server.patch(job['id'], created_at=time.time() - 100 * 3600)
    server.cleanup()
    assert client.get('/api/jobs').json()['jobs'] == []
    assert not (server.DATA / 'jobs' / job['id']).exists()


def test_headers_and_openapi(client):
    response = client.get('/')
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers['Content-Security-Policy']
    spec = client.get('/openapi.json').json()
    assert '/api/jobs' in spec['paths']
