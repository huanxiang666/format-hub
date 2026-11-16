"""Platform release boundaries that can run independently of host engines."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from app import processes
from app.version import VERSION

ROOT=Path(__file__).resolve().parents[1]


def test_cli_version_and_offline_install_gate():
    result=subprocess.run([sys.executable,str(ROOT/'run.py'),'--version'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and VERSION in result.stdout
    result=subprocess.run([sys.executable,str(ROOT/'run.py'),'--offline','--install-engine','windows-office'],capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and '离线模式' in result.stderr


def test_macos_frozen_external_engine_cleans_private_loader_paths():
    environment={'PATH':'/usr/bin','DYLD_LIBRARY_PATH':'/bundle','DYLD_FRAMEWORK_PATH':'/private/framework','DYLD_FALLBACK_LIBRARY_PATH':'/private/fallback','DYLD_LIBRARY_PATH_ORIG':'/original'}
    with patch.object(processes.sys,'platform','darwin'), patch.object(processes.sys,'frozen',True,create=True), patch.object(processes.subprocess,'run') as call:
        processes.run_external(['/engine/soffice','--version'],env=environment,timeout=8)
    actual=call.call_args.kwargs['env']
    assert actual['DYLD_LIBRARY_PATH']=='/original'
    assert 'DYLD_FRAMEWORK_PATH' not in actual and 'DYLD_FALLBACK_LIBRARY_PATH' not in actual
    assert environment['DYLD_LIBRARY_PATH']=='/bundle'


def test_source_external_engine_preserves_environment():
    environment={'DYLD_LIBRARY_PATH':'/custom'}
    with patch.object(processes.sys,'platform','darwin'), patch.object(processes.sys,'frozen',False,create=True), patch.object(processes.subprocess,'run') as call:
        processes.run_external(['soffice'],env=environment)
    assert call.call_args.kwargs['env']==environment
