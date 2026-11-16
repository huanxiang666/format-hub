"""Linux worker confinement: Landlock filesystem/TCP and seccomp IP sockets.

Restrictions are inherited by every converter child and cannot be relaxed by it.
Other operating systems should deploy the provided Linux container for confinement.
"""
from __future__ import annotations
import ctypes
import ctypes.util
import errno
import os
from pathlib import Path
import sys


def confine(directory: Path):
    if sys.platform != 'linux':
        return 'platform-preflight-only'
    library = ctypes.util.find_library('seccomp')
    if not library:
        raise RuntimeError('缺少 libseccomp；转换进程未启动')
    sec = ctypes.CDLL(library)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 4:
        raise RuntimeError('Linux 转换隔离需要 Landlock ABI 4+（内核6.7+）；请使用支持的Linux主机或容器')
    # All filesystem mutations and reads are handled, except ABI-specific IOCTL.
    fs = (1 << 15) - 1
    net = 3
    class Ruleset(ctypes.Structure):
        _fields_ = [('handled_access_fs', ctypes.c_uint64), ('handled_access_net', ctypes.c_uint64)]
    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]
    rules = Ruleset(fs, net)
    fd = libc.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), '创建文件隔离规则失败')
    read = (1 << 0) | (1 << 2) | (1 << 3)
    root = Path(__file__).resolve().parents[1]
    paths = [(directory, fs), (root / 'app', read), (root / 'runtime', read), (Path(sys.prefix), read), (Path(sys.base_prefix), read)]
    paths += [(Path(p), read) for p in ['/usr', '/lib', '/lib64', '/bin', '/sbin', '/sys/devices', '/etc/fonts', '/etc/ld.so.cache', '/etc/ld.so.conf', '/etc/localtime', '/etc/nsswitch.conf', '/etc/passwd', '/etc/group', '/proc/meminfo', '/proc/cpuinfo', '/proc/self/stat', '/proc/self/status']]
    ipc = os.environ.get('FORMAT_HUB_IPC_DIR')
    if ipc:
        paths.append((Path(ipc), fs))
    paths += [(Path(p), (1 << 1) | (1 << 2)) for p in ['/dev/null', '/dev/urandom', '/dev/random', '/dev/zero']]
    try:
        for path, access in paths:
            if not path.exists():
                continue
            if not path.is_dir():
                access &= ~((1 << 3) | (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7) | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 12) | (1 << 13))
            pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(access, pfd)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) < 0:
                    raise OSError(ctypes.get_errno(), f'设置文件隔离失败：{path.name}')
            finally:
                os.close(pfd)
        if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(446, fd, 0) < 0:
            raise OSError(ctypes.get_errno(), '启用文件隔离失败')
    finally:
        os.close(fd)
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]
    class Arg(ctypes.Structure):
        _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int), ('a', ctypes.c_uint64), ('b', ctypes.c_uint64)]
    sec.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(Arg)]
    context = sec.seccomp_init(0x7fff0000)
    if not context:
        raise RuntimeError('创建网络隔离失败')
    try:
        number = sec.seccomp_syscall_resolve_name(b'socket')
        for family in (2, 10, 17):
            condition = Arg(0, 4, family, 0)  # SCMP_CMP_EQ
            if sec.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM, number, 1, ctypes.byref(condition)) != 0:
                raise RuntimeError('设置网络隔离失败')
        if sec.seccomp_load(context) != 0:
            raise RuntimeError('启用网络隔离失败')
    finally:
        sec.seccomp_release(context)
    return f'landlock-abi-{abi}+seccomp'
