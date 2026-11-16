#!/usr/bin/env python3
"""Run a built release from an unrelated cwd with new, disposable test inputs."""
import argparse
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import zipfile

import httpx
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('binary', type=Path)
    parser.add_argument('--office', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    report = {'binary': str(binary), 'checks': []}
    with tempfile.TemporaryDirectory(prefix='format-hub-release-smoke-') as temporary:
        temporary = Path(temporary)
        help_result = subprocess.run([str(binary), '--help'], cwd=temporary, capture_output=True, text=True, timeout=20, check=True)
        assert '--port' in help_result.stdout
        report['checks'].append({'help': help_result.stdout})
        for filename, flag in [('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg', '-version'), ('dwg/dwg2dxf.exe' if os.name == 'nt' else 'dwg2dxf', '--version')]:
            engine = binary.parent / 'runtime' / filename
            result = subprocess.run([str(engine), flag], cwd=temporary, capture_output=True, text=True, timeout=30, check=True)
            report['checks'].append({'engine': filename, 'version': result.stdout[:600]})
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        log = temporary / 'server.log'
        env = {k: v for k, v in os.environ.items() if k not in ('FORMAT_HUB_TOKEN', 'SOFFICE', 'DWG2DXF', 'FFMPEG', 'FORMAT_HUB_DATA')}
        env['FORMAT_HUB_DATA'] = str(temporary / 'data')
        with log.open('w') as stream:
            process = subprocess.Popen([str(binary), '--host', '127.0.0.1', '--port', str(port), '--data-dir', str(temporary / 'data')], cwd=temporary, env=env, stdout=stream, stderr=stream)
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=30) as client:
                    for i in range(150):
                        if process.poll() is not None:
                            raise RuntimeError('成品服务提前退出：' + log.read_text(errors='replace'))
                        try:
                            response = client.get('/api/health')
                            if response.status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        time.sleep(0.2)
                    else:
                        raise RuntimeError('成品服务未就绪：' + log.read_text(errors='replace'))
                    health = response.json()
                    report['checks'].append({'health': health})
                    response = client.get('/')
                    response.raise_for_status()
                    assert '<html' in response.text.lower()
                    response = client.get('/api/catalog'); response.raise_for_status()
                    raw = response.json()
                    catalog = raw if isinstance(raw, list) else raw.get('tools', raw.get('catalog', []))
                    report['catalog'] = {item['id']: item['available'] for item in catalog}
                    image = io.BytesIO(); Image.new('RGB', (32, 24), '#2b7bda').save(image, 'PNG')
                    samples = [('image_convert', 'sample.png', image.getvalue(), {'target': 'webp'})]
                    # A real, tiny valid DXF; no project user input is read.
                    import ezdxf
                    drawing = ezdxf.new('R2010'); drawing.modelspace().add_line((0, 0), (10, 10))
                    dxf = io.StringIO(); drawing.write(dxf)
                    samples.append(('cad_convert', 'sample.dxf', dxf.getvalue().encode('utf-8'), {'target': 'svg'}))
                    if args.office:
                        from docx import Document
                        document = Document(); document.add_paragraph('Format Hub Office smoke 测试')
                        data = io.BytesIO(); document.save(data)
                        samples.append(('office_convert', 'sample.docx', data.getvalue(), {'target': 'pdf'}))
                    import wave
                    audio = io.BytesIO()
                    with wave.open(audio, 'wb') as wav:
                        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b'\0\0' * 800)
                    samples.append(('media_audio', 'sample.wav', audio.getvalue(), {'target': 'mp3'}))
                    for operation, filename, content, options in samples:
                        response = client.post('/api/jobs', data={'operation': operation, 'options': json.dumps(options)}, files={'files': (filename, content)})
                        if response.is_error:
                            raise RuntimeError(f'{operation}: HTTP {response.status_code}: {response.text}; catalog={report["catalog"]}')
                        job_id = response.json()['id']
                        for i in range(300):
                            response = client.get('/api/jobs'); response.raise_for_status()
                            rows = response.json()
                            if isinstance(rows, dict):
                                rows = rows.get('jobs', [])
                            job = next(row for row in rows if row['id'] == job_id)
                            if job['state'] in ('failed', 'cancelled'):
                                raise RuntimeError(json.dumps(job, ensure_ascii=False) + '\n' + log.read_text(errors='replace'))
                            if job['state'] == 'succeeded':
                                break
                            time.sleep(0.2)
                        else:
                            raise RuntimeError('转换超时：' + operation)
                        assert job['outputs']
                        result = client.get(job['outputs'][0]['url']); result.raise_for_status()
                        assert result.content
                        if operation == 'image_convert':
                            assert Image.open(io.BytesIO(result.content)).format == 'WEBP'
                        elif operation == 'office_convert':
                            assert result.content.startswith(b'%PDF')
                        elif operation == 'cad_convert':
                            assert b'<svg' in result.content
                        report['checks'].append({'operation': operation, 'state': job['state'], 'bytes': len(result.content)})
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
        report['server_log'] = log.read_text(errors='replace')
    if args.report:
        args.report.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
