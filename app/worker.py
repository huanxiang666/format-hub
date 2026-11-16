"""One job per process; resource-heavy parsers never execute in the HTTP process."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import sys
import traceback


def main():
    directory = Path(sys.argv[1]).resolve()
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    ipc_directory = None
    parent_pid = int(os.environ.get('FORMAT_HUB_PARENT_PID', str(os.getppid())))
    if sys.platform == 'linux':
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        def parent_died(signum, frame):
            if os.getpgrp() == os.getpid():
                os.killpg(os.getpid(), signal.SIGKILL)
            os._exit(125)
        signal.signal(signal.SIGTERM, parent_died)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), '设置父进程退出回收失败')
        if os.getppid() != parent_pid:
            parent_died(None, None)
    try:
        if sys.platform == 'linux':
            import resource
            memory_mb = int(os.environ.get('FORMAT_HUB_MEMORY_MB', '2048'))
            for limit, desired in [(resource.RLIMIT_AS, memory_mb * 1024**2), (resource.RLIMIT_FSIZE, 1024**3), (resource.RLIMIT_NOFILE, 512)]:
                soft, hard = resource.getrlimit(limit)
                ceiling = desired if hard == resource.RLIM_INFINITY else min(desired, hard)
                resource.setrlimit(limit, (ceiling if soft == resource.RLIM_INFINITY else min(soft, ceiling), hard))
        temp = directory / 'temp'
        temp.mkdir(exist_ok=True)
        os.environ['TMPDIR'] = str(temp)
        os.environ['HOME'] = str(temp)
        os.environ['XDG_CACHE_HOME'] = str(temp / 'cache')
        os.environ['XDG_CONFIG_HOME'] = str(temp / 'config')
        os.environ['XDG_RUNTIME_DIR'] = str(temp)
        os.environ['GSETTINGS_BACKEND'] = 'memory'
        os.environ['SAL_USE_VCLPLUGIN'] = 'svp'
        import tempfile
        tempfile.tempdir = str(temp)
        if sys.platform == 'linux':
            import secrets
            ipc_directory = Path('/tmp') / ('fh-' + secrets.token_hex(6))
            ipc_directory.mkdir(mode=0o700)
            (directory / 'ipc-directory.txt').write_text(str(ipc_directory), encoding='utf-8')
            os.environ['FORMAT_HUB_IPC_DIR'] = str(ipc_directory)
            os.environ['OSL_SOCKET_PATH'] = str(ipc_directory)
            shim = Path(__file__).resolve().parents[1] / 'runtime/liboffice-ipc.so'
            if shim.is_file():
                os.environ['LD_PRELOAD'] = str(shim)
        from app.sandbox import confine
        confinement = confine(directory)
        print(f'worker isolation: {confinement}', flush=True)
        if sys.platform == 'linux':
            import threading
            import time
            def watch_resources():
                start = time.monotonic()
                while True:
                    time.sleep(0.5)
                    used = sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())
                    if os.getppid() != parent_pid or time.monotonic() - start > int(os.environ.get('FORMAT_HUB_TIMEOUT', '600')) or used > int(os.environ.get('FORMAT_HUB_JOB_MB', '1024')) * 1024**2:
                        parent_died(None, None)
            threading.Thread(target=watch_resources, daemon=True).start()
        from app.engines import execute
        spec = json.loads((directory / 'spec.json').read_text(encoding='utf-8'))
        files = [directory / 'input' / name for name in spec['files']]
        options = {k: v for k, v in spec['options'].items() if not k.startswith('_')}
        if (directory / 'fonts').is_dir():
            options['_font_dir'] = str(directory / 'fonts')
        import warnings
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter('always', UserWarning)
            if spec['operation'] == '__inspect_pdf':
                from app.inspection import inspect_files
                outputs = inspect_files(directory)
            else:
                outputs = execute(spec['operation'], files, directory / 'output', options)
        messages = list(dict.fromkeys(str(notice.message) for notice in notices if issubclass(notice.category, UserWarning)))
        for message in messages:
            print(f'conversion warning: {message}', flush=True)
        if sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) > int(os.environ.get('FORMAT_HUB_JOB_MB', '1024')) * 1024**2:
            raise ValueError('此任务生成的文件超过容量预算，请减小输入或输出尺寸')
        for path in outputs:
            if path.resolve().parent != (directory / 'output').resolve():
                raise ValueError('转换结果目录错误')
        if spec.get('file_metadata'):
            from app.names import output_names
            display_names = output_names(spec['operation'], spec['file_metadata'], [p.name for p in outputs])
            import secrets
            staged = []
            for path in outputs:
                temporary = directory / 'output' / ('rename-' + secrets.token_hex(16))
                path.rename(temporary)
                staged.append(temporary)
            renamed = []
            for path, name in zip(staged, display_names):
                target = directory / 'output' / name
                if target.exists():
                    raise ValueError('结果文件名称冲突')
                path.rename(target)
                renamed.append(target)
            outputs = renamed
        result = {'ok': True, 'outputs': [p.name for p in outputs], 'warnings': messages}
        code = 0
    except Exception as exc:
        traceback.print_exc()
        message = str(exc).replace(str(directory), '[任务目录]')
        result = {'ok': False, 'outputs': [], 'error': message[:500] or '文件内容解析失败，请检查格式'}
        code = 1
    if ipc_directory is not None:
        import shutil
        # Exactly the private IPC directory created above, never a caller-supplied path.
        for entry in ipc_directory.iterdir():
            entry.unlink()
    (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
