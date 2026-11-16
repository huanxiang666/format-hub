"""Cross-platform launcher: python run.py --host 0.0.0.0 --port 8765."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import socket
import sys

from app.paths import ROOT, default_data


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        from app.worker import main as worker_main
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        raise SystemExit(worker_main())
    parser = argparse.ArgumentParser(description='格式工坊：一台部署，团队共用。Ctrl+C 停止。')
    parser.add_argument('--host', default=os.environ.get('FORMAT_HUB_HOST', '0.0.0.0'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('FORMAT_HUB_PORT', '8765')))
    parser.add_argument('--data-dir', type=Path, help='任务数据目录，默认项目下 data')
    parser.add_argument('--install-engine', choices=['windows-office', 'windows-dwg'], help='下载并校验应用专用转换引擎')
    parser.add_argument('--version', action='version', version='格式工坊 1.1.0')
    args = parser.parse_args()
    if args.install_engine:
        from bootstrap import install_engine
        print('正在下载并校验引擎，请保持网络连接。', flush=True)
        print(install_engine(args.install_engine, root=ROOT), flush=True)
        return
    if not 1 <= args.port <= 65535:
        parser.error('端口应在1至65535之间')
    if args.data_dir:
        os.environ['FORMAT_HUB_DATA'] = str(args.data_dir.resolve())
    os.environ.setdefault('FORMAT_HUB_DATA', str(default_data().resolve()))
    os.environ.setdefault('SOFFICE', str(ROOT / 'runtime' / 'soffice') if (ROOT / 'runtime' / 'soffice').exists() else '')
    os.environ.setdefault('DWG2DXF', str(ROOT / 'runtime' / 'dwg2dxf') if (ROOT / 'runtime' / 'dwg2dxf').exists() else '')
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    os.environ['OMP_NUM_THREADS'] = '1'
    os.chdir(ROOT)
    print(f'格式工坊  http://127.0.0.1:{args.port}  | 数据目录：{os.environ.get("FORMAT_HUB_DATA", str(ROOT / "data"))}', flush=True)
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(('192.0.2.1', 80))
        print(f'团队地址：http://{probe.getsockname()[0]}:{args.port}', flush=True)
        probe.close()
    except OSError as exc:
        print(f'局域网地址检测失败：{exc}；可使用机器实际IP', flush=True)
    import uvicorn
    uvicorn.run('app.server:app', host=args.host, port=args.port, workers=1, access_log=False)


if __name__ == '__main__':
    main()
