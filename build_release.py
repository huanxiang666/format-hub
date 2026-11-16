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
APPROVED_TOP = ('run.py', 'bootstrap.py', 'engine-sources.json', 'requirements.txt', 'requirements.lock', 'THIRD-PARTY-NOTICES.md', 'LICENSE', 'README.md')
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
    if system == 'macos':
        parser.error('macOS 请使用源码 bootstrap；尚无实机二进制验证')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    name = f'format-hub-v1.1.0-{system}-x64-{stamp}'
    if platform.machine().lower() not in ('x86_64', 'amd64'):
        parser.error('此构建目标仅为 x64')
    with tempfile.TemporaryDirectory(prefix='format-hub-build-') as temporary:
        temp = Path(temporary)
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--contents-directory', '.',
                   '--name', 'format-hub', '--distpath', str(temp / 'dist'), '--workpath', str(temp / 'work'), '--specpath', str(temp),
                   '--paths', str(ROOT), '--add-data', f'{ROOT / "app/static"}{os.pathsep}app/static', '--hidden-import', 'bootstrap']
        for module in MODULES:
            command += ['--collect-all', module]
        command += ['--collect-submodules', 'app']
        if system == 'windows':
            native = ROOT / 'runtime/native'
            if not native.is_dir():
                raise RuntimeError('Windows 构建需先准备私有 Cairo DLL')
            for dll in native.glob('*.dll'):
                command += ['--add-binary', f'{dll}{os.pathsep}.']
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
        else:
            copytree(ROOT / 'runtime/dwg', runtime / 'dwg')
            copytree(ROOT / 'runtime/native-licenses', bundle / 'licenses/native')
            shutil.copy2(ROOT / 'packaging/windows-start.cmd', bundle / '启动格式工坊.cmd')
        readme = ROOT / 'packaging/RELEASE-README.txt'
        if readme.exists():
            shutil.copy2(readme, bundle / '使用说明.txt')
        source_archive = bundle / 'corresponding-source.zip'
        with zipfile.ZipFile(source_archive, 'w', zipfile.ZIP_DEFLATED) as archive:
            for filename in APPROVED_TOP:
                source = ROOT / filename
                if source.is_file():
                    archive.write(source, filename)
            for source in sorted((ROOT / 'app').rglob('*')):
                if source.is_file() and '__pycache__' not in source.parts and '.bak-' not in source.name:
                    archive.write(source, source.relative_to(ROOT))
        metadata = {
            'name': name, 'platform': system, 'architecture': 'x64', 'python': platform.python_version(),
            'build_host': platform.platform(), 'office_bundled': system == 'linux' and not args.skip_office,
            'compatibility': 'Ubuntu 26.04 x64; glibc 2.43; Linux kernel >=6.7 (Landlock ABI>=4)' if system == 'linux' else 'Windows x64; Office optional private engine download',
            'signing': 'unsigned local verification artifact; public signing not configured',
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
    archive = output / (name + ('.zip' if system == 'windows' else '.tar.gz'))
    if system == 'windows':
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
