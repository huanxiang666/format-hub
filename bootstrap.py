#!/usr/bin/env python3
"""Verified private engine installation and source bootstrap; standard library only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import venv
import zipfile

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
PIP_INDEXES = ('https://pypi.tuna.tsinghua.edu.cn/simple', 'https://pypi.org/simple')
CHUNK = 1024 * 1024


def manifest(root=ROOT):
    return json.loads((Path(root) / 'engine-sources.json').read_text(encoding='utf-8'))


def https_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('下载地址必须为 HTTPS，且不得包含账号密码')
    return url


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_verified(source, destination, proxy=None):
    """A failed download never replaces the destination; redirects stay HTTPS."""
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError('下载目标为符号链接')
    expected = source['sha256']
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('SHA256 清单值无效')
    limit = int(source['max_download_bytes'])
    if not 0 < limit <= 2 * 1024**3:
        raise ValueError('下载预算无效')
    destination.parent.mkdir(parents=True, exist_ok=True)
    handlers = [HTTPSRedirect(), urllib.request.ProxyHandler({'https': https_url(proxy)} if proxy else {})]
    opener = urllib.request.build_opener(*handlers)
    failures = []
    for url in source['urls']:
        https_url(url)
        temporary = None
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'FormatHub-Bootstrap/1'})
            with opener.open(request, timeout=45) as response:
                https_url(response.geturl())
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise ValueError('下载内容超过大小预算')
                digest, size = hashlib.sha256(), 0
                with tempfile.NamedTemporaryFile(prefix='.format-hub-', dir=destination.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    while block := response.read(CHUNK):
                        size += len(block)
                        if size > limit:
                            raise ValueError('下载内容超过大小预算')
                        digest.update(block)
                        stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
            if not size or digest.hexdigest() != expected:
                raise ValueError('下载文件 SHA256 校验失败')
            os.replace(temporary, destination)
            return destination
        except (OSError, ValueError) as exc:
            failures.append(f'{urllib.parse.urlsplit(url).hostname}: {exc}')
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    raise RuntimeError('所有固定下载源均失败：' + '; '.join(failures))


def safe_member(name):
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    if not name or '\x00' in name or path.is_absolute() or '..' in path.parts or any(':' in part for part in path.parts):
        raise ValueError('归档路径越界或名称无效')
    return path


def extract_verified(archive, destination, max_bytes, max_files=20000):
    """Only regular files/directories, with bounded expanded bytes and member count."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError('解包目录必须是新的私有目录')
    if not 0 < max_bytes <= 4 * 1024**3:
        raise ValueError('解包预算无效')
    destination.mkdir(mode=0o700)
    seen, total, count = set(), 0, 0

    def target(name, size, directory=False):
        nonlocal total, count
        relative = safe_member(name)
        key = str(relative).casefold()
        if key in seen:
            raise ValueError('归档包含重复名称')
        seen.add(key)
        count += 1
        total += size
        if size < 0 or total > max_bytes or count > max_files:
            raise ValueError('归档解包超过预算')
        path = destination.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(destination.resolve()):
            raise ValueError('归档路径越界')
        if directory:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def copy(stream, output, size):
        actual = 0
        with output.open('xb') as sink:
            while data := stream.read(CHUNK):
                actual += len(data)
                if actual > size:
                    raise ValueError('归档文件实际长度超过声明')
                sink.write(data)
        if actual != size:
            raise ValueError('归档文件长度与声明不一致')

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                mode = item.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1:
                    raise ValueError('归档包含链接、特殊文件或加密项')
                output = target(item.filename, item.file_size, item.is_dir())
                if not item.is_dir():
                    with bundle.open(item) as stream:
                        copy(stream, output, item.file_size)
    else:
        with tarfile.open(archive, 'r:*') as bundle:
            for item in bundle:
                if not (item.isdir() or item.isfile()):
                    raise ValueError('归档包含链接或特殊文件')
                output = target(item.name, item.size, item.isdir())
                if item.isfile():
                    with bundle.extractfile(item) as stream:
                        copy(stream, output, item.size)
    return destination


def engine_path(engine_id, root=ROOT):
    root = Path(root)
    return root / manifest(root)['engines'][engine_id]['entrypoint']


def install_engine(engine_id, root=ROOT, proxy=None):
    """Install a fixed manifest engine. No caller-provided URL or command is accepted."""
    root = Path(root).resolve()
    source = manifest(root)['engines'][engine_id]
    if source['platform'] not in (sys.platform, 'any'):
        raise ValueError('该引擎不适用于当前平台')
    runtime = root / 'runtime'
    if runtime.is_symlink():
        raise ValueError('runtime 不得为符号链接')
    runtime.mkdir(exist_ok=True)
    target = root / source['install_dir']
    if not target.resolve().is_relative_to(runtime.resolve()) or target.is_symlink():
        raise ValueError('引擎目标超出 runtime')
    expected_entry = engine_path(engine_id, root)
    if target.exists():
        if expected_entry.is_file() and not expected_entry.is_symlink() and expected_entry.resolve().is_relative_to(runtime.resolve()):
            return expected_entry
        raise RuntimeError(f'引擎目录已存在但不完整，请保留后移走再重试：{target}')
    lock = runtime / ('.' + engine_id + '.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError('该引擎正在安装；若上次进程中断，请先确认后移除对应 .lock 文件') from exc
    os.close(descriptor)
    try:
        with tempfile.TemporaryDirectory(prefix='.install-', dir=runtime) as temporary:
            staging = Path(temporary)
            archive = download_verified(source, staging / source['filename'], proxy)
            unpacked = staging / 'engine'
            if source['archive'] == 'msi-admin':
                if sys.platform != 'win32':
                    raise RuntimeError('MSI 管理安装仅支持 Windows')
                # Official MSI administrative image: no registration or machine installation.
                unpacked.mkdir()
                log = staging / 'msi.log'
                result = subprocess.run(['msiexec.exe', '/a', str(archive), '/qn', f'TARGETDIR={unpacked}', '/L*v', str(log)], timeout=600, capture_output=True)
                if result.returncode not in (0, 3010):
                    detail = log.read_text(encoding='utf-16', errors='replace')[-1600:] if log.exists() else str(result.returncode)
                    raise RuntimeError('LibreOffice 私有管理安装失败：' + detail)
                entries = list(unpacked.rglob('program/soffice.exe'))
                if len(entries) != 1:
                    raise RuntimeError('MSI 未产生唯一 soffice.exe')
                office = entries[0].parent.parent
                normalized = staging / 'normalized'
                office.rename(normalized)
                unpacked = normalized
                paths = list(unpacked.rglob('*'))
                if any(p.is_symlink() for p in paths) or sum(p.stat().st_size for p in paths if p.is_file()) > source['max_extract_bytes'] or len(paths) > 30000:
                    raise ValueError('MSI 管理安装结果超过预算或含链接')
                subprocess.run([str(unpacked / 'program/soffice.exe'), '--headless', '--version'], check=True, capture_output=True, timeout=30)
            elif source['archive'] == 'file':
                unpacked.mkdir()
                shutil.copy2(archive, unpacked / source['filename'])
                for extra in source.get('license_files', []):
                    download_verified(extra, unpacked / extra['filename'], proxy)
            else:
                extract_verified(archive, unpacked, source['max_extract_bytes'])
                if engine_id == 'windows-dwg':
                    matches = list(unpacked.rglob('dwg2dxf.exe'))
                    if len(matches) != 1:
                        raise RuntimeError('LibreDWG 归档中未找到唯一 dwg2dxf.exe')
                    normalized = staging / 'normalized'
                    shutil.copytree(matches[0].parent, normalized)
                    for license_file in unpacked.rglob('*'):
                        if license_file.is_file() and license_file.name.upper().startswith(('COPYING', 'LICENSE')):
                            shutil.copy2(license_file, normalized / license_file.name)
                    unpacked = normalized
            relative_entry = Path(source['entrypoint']).relative_to(source['install_dir'])
            if not (unpacked / relative_entry).is_file():
                raise RuntimeError('引擎文件不完整')
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(unpacked, target)
            return expected_entry
    finally:
        lock.unlink()


def source_setup(root=ROOT):
    root = Path(root)
    environment = root / '.venv'
    if not environment.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    last = None
    for index in PIP_INDEXES:
        result = subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--index-url', index, '-r', str(root / 'requirements.lock')])
        if result.returncode == 0:
            return python
        last = result.returncode
    raise RuntimeError(f'固定 Python 包源均安装失败（退出码 {last}），请检查网络后重试')


def main(argv=None):
    parser = argparse.ArgumentParser(description='格式工坊：校验下载引擎，或为源码版准备 Python 依赖')
    parser.add_argument('--engine', choices=sorted(manifest()['engines']))
    parser.add_argument('--proxy', help='用户自选 HTTPS 正向代理地址；默认直接连接官方源')
    parser.add_argument('--source', action='store_true', help='在项目 .venv 安装依赖，清华源失败后尝试官方 PyPI')
    parser.add_argument('--run', action='store_true', help='准备源码依赖后启动 run.py')
    args = parser.parse_args(argv)
    if args.engine:
        print(install_engine(args.engine, proxy=args.proxy))
    elif args.source or args.run:
        python = source_setup()
        if args.run:
            raise SystemExit(subprocess.call([str(python), str(ROOT / 'run.py')], cwd=ROOT))
        print(python)
    else:
        parser.print_help()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'准备失败：{exc}', file=sys.stderr)
        raise SystemExit(1)
