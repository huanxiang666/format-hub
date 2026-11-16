#!/usr/bin/env python3
"""Build an allowlisted PyInstaller onedir distribution; never includes user state."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parent
APPROVED_TOP = ('run.py', 'bootstrap.py', 'engine-sources.json', 'requirements.txt', 'requirements.lock', 'requirements-macos.txt', 'THIRD-PARTY-NOTICES.md', 'LICENSE', 'README.md')
MODULES = ('uvicorn', 'fastapi', 'starlette', 'multipart', 'PIL', 'pymupdf', 'fitz', 'pdf2docx', 'docx', 'openpyxl', 'ezdxf', 'cairosvg', 'defusedxml', 'imageio_ffmpeg')
SOFFICE_SH = '''#!/bin/sh
set -eu
RUNTIME=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROGRAM="$RUNTIME/office/usr/lib/libreoffice/program"
export LD_LIBRARY_PATH="$PROGRAM:$RUNTIME/office/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH_ORIG:-}"
export SAL_USE_VCLPLUGIN=svp
uri() { printf 'file://'; printf '%s' "$1" | od -An -v -tx1 | awk '{for(i=1;i<=NF;i++) if($i=="2f") printf "/"; else printf "%%%s", $i}'; }
export URE_BOOTSTRAP="$(uri "$PROGRAM/fundamentalrc")"
case "${LC_ALL:-${LANG:-C}}" in C|POSIX) export LC_ALL=C.UTF-8 ;; esac
PROFILE=$(mktemp -d "${TMPDIR:-/tmp}/format-hub-office-XXXXXX")
cleanup() { case "$PROFILE" in "${TMPDIR:-/tmp}"/format-hub-office-*) rm -rf -- "$PROFILE" ;; *) printf '%s\\n' 'Unexpected Office profile path' >&2; return 1 ;; esac; }
trap cleanup EXIT
export XDG_CACHE_HOME="$PROFILE/cache"
"$PROGRAM/soffice" "-env:BRAND_BASE_DIR=$(uri "$PROGRAM/..")" "-env:UserInstallation=$(uri "$PROFILE/profile")" "$@"
'''


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def copytree(source, destination):
    if source.exists():
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.bak-*'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'packaging' / 'dist')
    parser.add_argument('--skip-office', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    system = {'Linux': 'linux', 'Windows': 'windows', 'Darwin': 'macos'}[platform.system()]
    from app.version import VERSION
    machine = platform.machine().lower()
    architecture = {'x86_64': 'x64', 'amd64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(machine)
    if architecture is None or (system != 'macos' and architecture != 'x64'):
        parser.error('目标架构不受支持')
    if args.skip_office and system != 'linux':
        parser.error('完整 Windows/macOS 发行包必须内置 LibreOffice')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    name = f'format-hub-v{VERSION}-{system}-{architecture}-{stamp}'
    with tempfile.TemporaryDirectory(prefix='format-hub-build-') as temporary:
        temp = Path(temporary)
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--contents-directory', '.',
                   '--name', 'format-hub', '--distpath', str(temp / 'dist'), '--workpath', str(temp / 'work'), '--specpath', str(temp),
                   '--paths', str(ROOT), '--add-data', f'{ROOT / "app/static"}{os.pathsep}app/static', '--hidden-import', 'bootstrap']
        for module in MODULES:
            command += ['--collect-all', module]
        command += ['--collect-submodules', 'app']
        if system in ('windows', 'macos'):
            native = ROOT / 'runtime/native'
            libraries = list(native.glob('*.dll' if system == 'windows' else '*.dylib'))
            if not libraries:
                raise RuntimeError('构建需先准备私有 Cairo native 依赖闭包')
            for library in libraries:
                command += ['--add-binary', f'{library}{os.pathsep}.']
        if system == 'macos':
            command += ['--target-architecture', 'arm64' if architecture == 'arm64' else 'x86_64']
        for module in ('pytest', 'onnxruntime', 'pymupdf_layout', 'pymupdf4llm', 'matplotlib', 'tkinter', 'IPython'):
            command += ['--exclude-module', module]
        command.append(str(ROOT / 'run.py'))
        subprocess.run(command, cwd=ROOT, check=True)
        bundle = temp / 'dist' / 'format-hub'
        # Embedded app modules and static assets are supplemented by corresponding source.
        copytree(ROOT / 'app', bundle / 'app')
        for filename in APPROVED_TOP:
            source = ROOT / filename
            if source.is_file():
                shutil.copy2(source, bundle / filename)
        copytree(ROOT / 'packaging' / 'licenses', bundle / 'licenses')
        python_license = Path(sys.base_prefix) / 'lib/python3.12/LICENSE.txt'
        if python_license.is_file():
            shutil.copy2(python_license, bundle / 'licenses/Python-LICENSE.txt')
        licenses = bundle / 'licenses' / 'python'
        licenses.mkdir(parents=True, exist_ok=True)
        for dist in importlib.metadata.distributions():
            for item in dist.files or []:
                if any(part.lower().startswith(('license', 'copying', 'notice', 'authors')) for part in item.parts):
                    source = dist.locate_file(item)
                    if source.is_file():
                        target = licenses / (dist.metadata['Name'] or 'unknown') / str(item)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
        runtime = bundle / 'runtime'
        runtime.mkdir(exist_ok=True)
        copytree(ROOT / 'runtime/fonts', runtime / 'fonts')
        (runtime / 'fonts').mkdir(exist_ok=True)
        font = ROOT / 'packaging/NotoSansCJK-Regular.ttc'
        if font.is_file():
            if digest(font) != 'b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a':
                raise RuntimeError('发行中文字体 SHA256 校验失败')
            shutil.copy2(font, runtime / 'fonts' / font.name)
        import imageio_ffmpeg
        ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
        shutil.copy2(ffmpeg, runtime / ('ffmpeg.exe' if system == 'windows' else 'ffmpeg'))
        if system == 'linux':
            for filename in ('dwg2dxf', 'liboffice-ipc.so', 'office-ipc.c'):
                source = ROOT / 'runtime' / filename
                if not source.is_file():
                    raise RuntimeError(f'发行必需引擎缺失：{source}')
                shutil.copy2(source, runtime / filename)
            if not args.skip_office:
                copytree(ROOT / 'runtime/office', runtime / 'office')
                for link in (runtime / 'office').rglob('*'):
                    if link.is_symlink() and not link.resolve().is_relative_to(runtime / 'office'):
                        link.unlink()
                (runtime / 'soffice').write_text(SOFFICE_SH, encoding='utf-8')
                (runtime / 'soffice').chmod(0o755)
            # ctypes.util.find_library needs the SONAME visible beside the executable.
            import ctypes.util
            library = ctypes.util.find_library('seccomp')
            if not library:
                raise RuntimeError('Linux 打包需要 libseccomp')
            result = subprocess.run(['ldconfig', '-p'], text=True, capture_output=True, check=True)
            paths = [line.split('=>', 1)[1].strip() for line in result.stdout.splitlines() if library in line and 'x86-64' in line]
            if not paths:
                raise RuntimeError('未定位 libseccomp 实体文件')
            shutil.copy2(paths[0], bundle / library)
        elif system == 'windows':
            for directory, entrypoint in [('dwg', 'dwg2dxf.exe'), ('libreoffice', 'program/soffice.com')]:
                if not (ROOT / 'runtime' / directory / entrypoint).is_file():
                    raise RuntimeError(f'完整包必需引擎缺失：{directory}/{entrypoint}')
                copytree(ROOT / 'runtime' / directory, runtime / directory)
            copytree(ROOT / 'runtime/native-licenses', bundle / 'licenses/native')
            shutil.copy2(ROOT / 'packaging/windows-start.cmd', bundle / '启动格式工坊.cmd')
        else:
            office = ROOT / 'runtime/LibreOffice.app'
            if not (office / 'Contents/MacOS/soffice').is_file() or not (ROOT / 'runtime/dwg2dxf').is_file():
                raise RuntimeError('macOS 完整包缺少 LibreOffice 或 LibreDWG')
            subprocess.run(['ditto', str(office), str(runtime / 'LibreOffice.app')], check=True)
            copytree(ROOT / 'runtime/native', runtime / 'native')
            copytree(ROOT / 'runtime/native-licenses', bundle / 'licenses/native')
            shutil.copy2(ROOT / 'runtime/dwg2dxf', runtime / 'dwg2dxf')
            shutil.copy2(ROOT / 'packaging/macos-start.command', bundle / '启动格式工坊.command')
            (bundle / '启动格式工坊.command').chmod(0o755)
            # Sign only FormatHub's executable. Never re-sign upstream LibreOffice.app.
            subprocess.run(['codesign', '--force', '--sign', '-', str(bundle / 'format-hub')], check=True)
            subprocess.run(['codesign', '--verify', '--deep', '--strict', str(runtime / 'LibreOffice.app')], check=True)
        readme = ROOT / 'packaging/RELEASE-README.txt'
        if readme.exists():
            shutil.copy2(readme, bundle / '使用说明.txt')
        source_archive = bundle / 'corresponding-source.zip'
        with zipfile.ZipFile(source_archive, 'w', zipfile.ZIP_DEFLATED) as archive:
            for filename in APPROVED_TOP:
                source = ROOT / filename
                if source.is_file():
                    archive.write(source, filename)
            for filename in ('build_release.py', 'packaging/stage_macos.py', 'packaging/smoke_release.py', 'packaging/windows-start.cmd', 'packaging/macos-start.command'):
                archive.write(ROOT / filename, filename)
            for source in sorted((ROOT / 'app').rglob('*')):
                if source.is_file() and '__pycache__' not in source.parts and '.bak-' not in source.name:
                    archive.write(source, source.relative_to(ROOT))
        metadata = {
            'name': name, 'version': VERSION, 'platform': system, 'architecture': architecture, 'python': platform.python_version(),
            'build_host': platform.platform(), 'office_bundled': not args.skip_office,
            'compatibility': {'linux': 'Ubuntu 26.04 x64; glibc 2.43; Linux kernel >=6.7 (Landlock ABI>=4)', 'windows': 'Windows 10/11 x64; all engines private and bundled', 'macos': 'macOS 15+; native architecture; all engines private and bundled'}[system],
            'signing': 'FormatHub executable ad-hoc signed; no Developer ID certificate or notarization configured; upstream LibreOffice signature preserved' if system == 'macos' else 'unsigned; code signing certificate not configured',
            'engines': json.loads((ROOT / 'engine-sources.json').read_text(encoding='utf-8')),
            'build_commit': os.environ.get('GITHUB_SHA', ''),
            'excluded': ['data', 'backups', 'evidence', '.env', '.venv', 'runtime/packages', 'API tokens'],
            'files': {},
        }
        for path in sorted(bundle.rglob('*')):
            relative = str(path.relative_to(bundle))
            if path.is_symlink():
                target = path.resolve()
                # Upstream Debian office contains system symlinks. Record external ones;
                # never dereference them into potentially private host files.
                metadata['files'][relative] = {'symlink': os.readlink(path), 'external': not target.is_relative_to(bundle)}
            elif path.is_file():
                metadata['files'][relative] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
        (bundle / 'release-manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        destination = output / name
        if destination.exists():
            raise RuntimeError('发行输出目录已存在')
        shutil.move(str(bundle), destination)
    archive = output / (name + ('.tar.gz' if system == 'linux' else '.zip'))
    if system == 'macos':
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(destination), str(archive)], check=True)
    elif system == 'windows':
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
            for path in sorted(destination.rglob('*')):
                if path.is_file():
                    stream.write(path, path.relative_to(output))
    else:
        with tarfile.open(archive, 'w:gz', compresslevel=6, dereference=False) as stream:
            stream.add(destination, arcname=name)
    report = {'directory': str(destination), 'archive': str(archive), 'bytes': archive.stat().st_size,
              'sha256': digest(archive), 'unpacked_bytes': sum(p.stat().st_size for p in destination.rglob('*') if p.is_file() and not p.is_symlink())}
    (output / (name + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / (name + '.sha256')).write_text(f'{report["sha256"]}  {archive.name}\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
