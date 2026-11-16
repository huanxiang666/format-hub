"""Windows-only synthetic Office differential probe; never reads existing user inputs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from docx import Document

ENV_KEYS = ['URE_BOOTSTRAP', 'UNO_PATH', 'SAL_USE_VCLPLUGIN', 'SAL_LOG', 'PYINSTALLER_RESET_ENVIRONMENT', '_PYI_APPLICATION_HOME_DIR', '_PYI_ARCHIVE_FILE', '_PYI_PARENT_PROCESS_LEVEL', 'SOFFICE', 'TMP', 'TEMP', 'TMPDIR', 'HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME']


def run_case(label, command, env, cwd):
    result = {'label': label, 'command': [str(x) for x in command], 'env': {key: env[key] for key in ENV_KEYS if key in env}}
    try:
        process = subprocess.run([str(x) for x in command], env=env, cwd=cwd, capture_output=True, timeout=90)
        result.update(returncode=process.returncode, stdout=process.stdout.decode('utf-8', errors='replace'), stderr=process.stderr.decode('utf-8', errors='replace'))
    except subprocess.TimeoutExpired as exc:
        result.update(timeout=True, stdout=(exc.stdout or b'').decode('utf-8', errors='replace'), stderr=(exc.stderr or b'').decode('utf-8', errors='replace'))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('binary', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    source = Path(__file__).resolve().parents[1]
    console = binary.parent / 'runtime/libreoffice/program/soffice.com'
    report = {'binary': str(binary), 'console': str(console), 'env': {key: os.environ[key] for key in ENV_KEYS if key in os.environ}, 'office_files': {}, 'cases': []}
    for relative in ['program/soffice.com', 'program/soffice.exe', 'program/fundamental.ini', 'program/bootstrap.ini', 'share/registry/main.xcd', 'share/registry/writer.xcd', 'program/types/offapi.rdb', 'program/services/services.rdb']:
        path = console.parent.parent / relative
        report['office_files'][relative] = {'exists': path.exists(), 'bytes': path.stat().st_size if path.is_file() else None}
    with tempfile.TemporaryDirectory(prefix='format-hub-win-differential-') as temp:
        temp = Path(temp)
        document = Document(); document.add_paragraph('Synthetic Windows Office conversion test 中文')
        fixture = temp / 'sample.docx'; document.save(fixture)
        base = os.environ.copy(); base.pop('FORMAT_HUB_TOKEN', None)
        for label, explicit in [('frozen-worker-explicit', True), ('frozen-worker-deep', True)]:
            directory = temp / label
            if label.endswith('-deep'):
                directory = temp / 'data/jobs' / ('a' * 32)
            (directory / 'input').mkdir(parents=True); (directory / 'output').mkdir()
            shutil.copy2(fixture, directory / 'input/sample.docx')
            (directory / 'spec.json').write_text(json.dumps({'operation': 'office_convert', 'files': ['sample.docx'], 'options': {'target': 'pdf'}}))
            env = base.copy(); env['FORMAT_HUB_PARENT_PID'] = str(os.getpid())
            if explicit:
                env['SOFFICE'] = str(console)
            else:
                env.pop('SOFFICE', None)
            command = [sys.executable, '-m', 'app.worker', str(directory)] if label.startswith('source') else [str(binary), '--worker', str(directory)]
            result = run_case(label, command, env, source if label.startswith('source') or label.endswith('source-cwd') else binary.parent)
            result['output_files'] = [p.name for p in (directory / 'output').iterdir()]
            result['job_directory_characters'] = len(str(directory.resolve()))
            result['estimated_office_profile_characters'] = len(str(directory.resolve() / 'temp/format-hub-office-12345678/profile'))
            record = directory / 'result.json'
            result['result'] = json.loads(record.read_text()) if record.exists() else None
            report['cases'].append(result)
        for label, worker_env, working in [('direct-office-clean', False, source)]:
            out = temp / label; out.mkdir()
            env = base.copy()
            if worker_env:
                for key in ['TMPDIR', 'HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_RUNTIME_DIR']:
                    env[key] = str(out)
                env['GSETTINGS_BACKEND'] = 'memory'
            result = run_case(label, [console, '-env:UserInstallation=' + (out / 'profile').as_uri(), '--headless', '--norestore', '--nofirststartwizard', '--convert-to', 'pdf:writer_pdf_Export', '--outdir', out, fixture], env, working)
            result['cwd'] = str(working)
            result['pdf_exists'] = (out / 'sample.pdf').exists()
            report['cases'].append(result)
    # Whitelist process metadata only, no command lines or environment dumps.
    import psutil
    report['office_processes'] = []
    for process in psutil.process_iter(['pid', 'ppid', 'name']):
        if (process.info.get('name') or '').lower().startswith(('soffice', 'oosplash')):
            report['office_processes'].append(process.info)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    main()
