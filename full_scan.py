# -*- coding: utf-8 -*-
"""全盘扫描底层能力:卷枚举 + NTFS 分配大小(ctypes) + 硬链接文件ID去重
====================================================================
- list_volumes(): 固定盘列表(字母/卷标/剩余/总量),剩余最少在前
- file_alloc_links(): 单文件 GetFileInformationByHandleEx(FileStandardInfo)
  一次拿 (AllocationSize, NumberOfLinks)——固定 24 字节结构,无变长名解析问题;
  失败返回 None 由调用方退化 st_size(表观大小)并在界面标注口径
- get_file_id(): FileIdInfo (卷序列号+文件ID) —— 仅对 NumberOfLinks>1 的文件
  调用,用于硬链接去重
轻依赖:纯 stdlib + ctypes,无第三方。
"""
import os
import ctypes
from ctypes import wintypes

# ---------- 常量 ----------
DRIVE_FIXED = 3
FILE_SHARE_ALL = 0x7
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_READ_ATTRIBUTES = 0x80
# GetFileInformationByHandleEx 信息类
FILE_STANDARD_INFO_CLASS = 1        # FILE_STANDARD_INFO
FILE_ID_INFO_CLASS = 18             # FILE_ID_INFO


def _api(name, restype, *argtypes):
    """声明 kernel32 原型并启用 use_last_error,
    保证 ctypes.get_last_error() 正确捕获错误码"""
    proto = ctypes.WINFUNCTYPE(restype, *argtypes, use_last_error=True)
    return proto((name, ctypes.windll.kernel32))


_CreateFileW = _api("CreateFileW", wintypes.HANDLE,
                    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                    wintypes.HANDLE)
_GFIHBE = _api("GetFileInformationByHandleEx", wintypes.BOOL,
               wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
               wintypes.DWORD)
_CloseHandle = _api("CloseHandle", wintypes.BOOL, wintypes.HANDLE)


class FILE_STANDARD_INFO(ctypes.Structure):
    """固定 24 字节:分配大小(磁盘实际占用)+ 硬链接数"""
    _fields_ = [
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.ULONG),
        ("DeletePending", wintypes.BOOLEAN),
        ("Directory", wintypes.BOOLEAN),
    ]


class FILE_ID_INFO(ctypes.Structure):
    _fields_ = [
        ("VolumeSerialNumber", ctypes.c_ulonglong),
        ("FileId", ctypes.c_ubyte * 16),   # FILE_ID_128
    ]


def list_volumes():
    """固定盘卷列表: [(盘符'C', 卷标, 剩余字节, 总字节)],剩余最少在前"""
    drives = []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return drives
    buf = ctypes.create_unicode_buffer(261)
    for i in range(26):
        if not (bitmask & (1 << i)):
            continue
        letter = chr(ord("A") + i)
        root = letter + ":\\"
        try:
            dtype = ctypes.windll.kernel32.GetDriveTypeW(root)
        except Exception:
            continue
        if dtype != DRIVE_FIXED:
            continue
        label = ""
        try:
            ctypes.windll.kernel32.GetVolumeInformationW(
                root, buf, 261, None, None, None, None, 0)
            label = buf.value
        except Exception:
            pass
        free = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        tf = ctypes.c_ulonglong(0)
        try:
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                root, ctypes.byref(free), ctypes.byref(total),
                ctypes.byref(tf))
        except Exception:
            continue
        drives.append((letter, label, free.value, total.value))
    drives.sort(key=lambda t: t[2])   # 剩余空间最少(最满)在前
    return drives


def _open_handle(path, access, flags=0):
    handle = _CreateFileW(path, access, FILE_SHARE_ALL, None, OPEN_EXISTING,
                          flags, None)
    if handle in (wintypes.HANDLE(-1).value, None) or handle < 0:
        raise ctypes.WinError()
    return handle


def file_alloc_links(path):
    """(AllocationSize, NumberOfLinks) —— 磁盘实际占用字节与硬链接数。
    非 NTFS / 无权限时返回 None,调用方退化 st_size 并在界面标注口径。"""
    try:
        handle = _open_handle(path, FILE_READ_ATTRIBUTES)
    except OSError:
        return None
    try:
        info = FILE_STANDARD_INFO()
        ok = _GFIHBE(handle, FILE_STANDARD_INFO_CLASS, ctypes.byref(info),
                     ctypes.sizeof(FILE_STANDARD_INFO))
        if not ok:
            return None
        return (info.AllocationSize, info.NumberOfLinks)
    finally:
        _CloseHandle(handle)


def get_file_id(path):
    """文件/目录唯一 ID:(VolumeSerialNumber, 16字节FileId);失败返回 None。
    用于硬链接去重——仅对 NumberOfLinks>1 的文件调用,避免每个文件都开句柄。"""
    try:
        handle = _open_handle(path, FILE_READ_ATTRIBUTES)
    except OSError:
        return None
    try:
        info = FILE_ID_INFO()
        ok = _GFIHBE(handle, FILE_ID_INFO_CLASS, ctypes.byref(info),
                     ctypes.sizeof(FILE_ID_INFO))
        if not ok:
            return None
        return (info.VolumeSerialNumber, bytes(info.FileId))
    finally:
        _CloseHandle(handle)


def alloc_supported(root):
    """探测该卷是否支持分配大小查询:随便找一个文件试 FileStandardInfo。
    NTFS 必支持;失败(FAT/exFAT/网络盘/权限)即退化表观大小。"""
    try:
        with os.scandir(root) as it:
            for e in it:
                try:
                    if not e.is_dir(follow_symlinks=False):
                        got = file_alloc_links(e.path)
                        if got is not None and got[0] > 0:
                            return True
                except OSError:
                    continue
    except OSError:
        pass
    return False


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"
