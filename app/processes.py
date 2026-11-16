"""Run external engines without inheriting PyInstaller's private DLL search path."""
import os
import subprocess
import sys
import threading

_dll_lock = threading.Lock()


def run_external(args, **kwargs):
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        environment = dict(kwargs.pop('env', os.environ))
        for key in ('DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'):
            original = environment.pop(key + '_ORIG', None)
            if original is None:
                environment.pop(key, None)
            else:
                environment[key] = original
        return subprocess.run(args, env=environment, **kwargs)
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return subprocess.run(args, **kwargs)
    import ctypes
    with _dll_lock:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel.SetDllDirectoryW.restype = ctypes.c_int
        if not kernel.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return subprocess.run(args, **kwargs)
        finally:
            if not kernel.SetDllDirectoryW(str(sys._MEIPASS)):
                raise ctypes.WinError(ctypes.get_last_error())
