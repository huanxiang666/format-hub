"""User-facing upload, preview, naming and font boundary acceptance."""
import io
import json
import time
import zipfile
import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app import server
from app.names import output_names, safe_stem


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'DATA', tmp_path/'data')
    monkeypatch.setattr(server, 'TEAM_TOKEN', '')
    with TestClient(server.app) as c:
        c.get('/api/session')
        yield c


def pdf_bytes(pages=3, password=None):
    with fitz.open() as doc:
        for i in range(pages):
            p=doc.new_page();p.insert_text((20,40),f'Page {i+1}')
        return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,user_pw=password,owner_pw='owner') if password else doc.tobytes()


def wait(c,jid):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        j=next(j for j in c.get('/api/jobs').json()['jobs'] if j['id']==jid)
        if j['state'] not in ['queued','running']:return j
        time.sleep(.1)
    pytest.fail('job timeout')


def test_pdf_inspect_pages_thumbnails_and_no_job(client):
    r=client.post('/api/inspect',files=[('files',('甲.pdf',pdf_bytes(3))),('files',('乙.pdf',pdf_bytes(2)))])
    assert r.status_code==200,r.text
    files=r.json()['files'];assert [f['pages'] for f in files]==[3,2]
    assert files[0]['previews'][0]['data_url'].startswith('data:image/png;base64,')
    assert client.get('/api/jobs').json()['jobs']==[]
    assert list((server.DATA/'jobs').iterdir())==[]


def test_pdf_inspect_password_and_corrupt(client):
    r=client.post('/api/inspect',files={'files':('secret.pdf',pdf_bytes(2,'pw'))})
    assert r.status_code==200 and r.json()['files'][0]['encrypted']
    assert r.json()['files'][0]['pages']==0 and r.json()['files'][0]['error']
    r=client.post('/api/inspect',data={'password':'pw'},files={'files':('secret.pdf',pdf_bytes(2,'pw'))})
    assert r.json()['files'][0]['pages']==2
    r=client.post('/api/inspect',files={'files':('bad.pdf',b'bad')})
    assert r.json()['files'][0]['error']


def test_merge_multiple_files_and_related_download_name(client):
    r=client.post('/api/jobs',data={'operation':'pdf_merge','options':'{}'},files=[('files',('项目甲.pdf',pdf_bytes(2))),('files',('项目乙.pdf',pdf_bytes(3)))])
    assert r.status_code==202,r.text
    j=wait(client,r.json()['id']);assert j['state']=='succeeded',j
    assert '项目甲' in j['outputs'][0]['name'] and '合并' in j['outputs'][0]['name']
    content=client.get(j['outputs'][0]['url']).content
    with fitz.open(stream=content,filetype='pdf') as d:assert len(d)==5


def test_user_original_name_is_bounded_metadata(client):
    b=io.BytesIO();Image.new('RGB',(20,10),'blue').save(b,'PNG')
    r=client.post('/api/jobs',data={'operation':'image_convert','options':'{"target":"jpg"}'},files={'files':('../../客户#方案.png',b.getvalue())})
    assert r.status_code==202,r.text
    j=wait(client,r.json()['id']);assert j['state']=='succeeded',j
    name=j['outputs'][0]['name'];assert '客户' in name and '/' not in name and '#' not in name
    assert (server.DATA/'jobs'/j['id']/'input/001.png').exists()
    assert client.get(j['outputs'][0]['url']).status_code==200


def test_batch_original_names_do_not_collide_with_engine_names(client):
    b=io.BytesIO();Image.new('RGB',(20,10),'blue').save(b,'PNG')
    r=client.post('/api/jobs',data={'operation':'image_convert','options':'{"target":"jpg"}'},files=[('files',('002.png',b.getvalue())),('files',('客户.png',b.getvalue()))])
    assert r.status_code==202,r.text
    j=wait(client,r.json()['id']);assert j['state']=='succeeded',j
    assert {f['name'] for f in j['outputs']}=={'002_converted.jpg','客户_converted.jpg'}


def test_font_internal_options_and_fake_font_rejected(client):
    for opts in [{'_font_dir':'/etc'},{'_anything':1}]:
        r=client.post('/api/jobs',data={'operation':'cad_convert','options':json.dumps(opts)},files={'files':('x.dxf',b'x')})
        assert r.status_code==422
    r=client.post('/api/jobs',data={'operation':'cad_convert','options':'{}'},files=[('files',('x.dxf',b'x')),('fonts',('x.ttf',b'not a font'))])
    assert r.status_code==422 and r.json()['error']['code']=='invalid_font'
    assert not list((server.DATA/'jobs').iterdir())


def test_preview_spawn_failure_releases_capacity(client, monkeypatch):
    import asyncio
    async def fail(*args, **kwargs):
        raise OSError('test startup failure')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fail)
    before=server.slots._value
    r=client.post('/api/inspect',files={'files':('正常.pdf',pdf_bytes())})
    assert r.status_code==503,r.text
    assert server.slots._value==before
    assert not server.reserved_slots
    assert not list((server.DATA/'jobs').iterdir())


def test_names_unicode_reserved_duplicate_and_split():
    assert '/' not in safe_stem('../../CON.pdf')
    assert safe_stem('CON.pdf').startswith('_')
    from pathlib import PureWindowsPath
    for name in ['CON.foo.pdf','LPT1.报告.png','aux.tar.pdf']:
        assert not PureWindowsPath(safe_stem(name)+'.jpg').is_reserved()
    assert len(safe_stem('文'*1000+'.pdf').encode())<=140
    meta=[{'stored':'001.pdf','name':'原文.pdf'}]
    names=output_names('pdf_split',meta,['001_part_0001.pdf','001_part_0002.pdf'])
    assert names==['原文_part_0001.pdf','原文_part_0002.pdf']
