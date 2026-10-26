"""Read only explicitly referenced, hash-pinned artifacts under one owned root."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path


if os.name == 'nt':
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in
                    ('created', 'accessed', 'written', 'changed')] + [('attributes', wintypes.DWORD)]

    class _FileStandardInfo(ctypes.Structure):
        _fields_ = [('allocation', ctypes.c_longlong), ('size', ctypes.c_longlong),
                    ('links', wintypes.DWORD), ('deleted', ctypes.c_ubyte),
                    ('directory', ctypes.c_ubyte)]

    class _FileIdInfo(ctypes.Structure):
        _fields_ = [('volume', ctypes.c_ulonglong), ('file_id', ctypes.c_ubyte * 16)]

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    wintypes.LPVOID, wintypes.DWORD]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    _kernel32.GetFileType.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _windows_file_identity(handle: int, *, maximum: int) -> tuple:
    """One metadata vocabulary for path handles and CRT descriptors.

    Python 3.12 Windows lstat/fstat ctime can mean creation/change respectively.
    Keep BOTH native times (100 ns ticks), and the full volume/file identifier.
    Unsupported metadata APIs fail closed rather than falling back to ctime.
    """
    if _kernel32.GetFileType(handle) != 1:  # FILE_TYPE_DISK
        raise ValueError('artifact handle is not a disk file')

    def snapshot():
        basic, standard, identity = _FileBasicInfo(), _FileStandardInfo(), _FileIdInfo()
        for kind, info in ((0, basic), (1, standard), (18, identity)):
            if not _kernel32.GetFileInformationByHandleEx(
                    handle, kind, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
        file_id = int.from_bytes(bytes(identity.file_id), 'little')
        if (not file_id or basic.attributes & (0x400 | 0x10) or standard.directory
                or standard.deleted or standard.links != 1
                or standard.size < 0 or standard.size > maximum):
            raise ValueError('artifact handle identity/type/link/size refused')
        return (identity.volume, file_id, standard.size, basic.written, basic.changed,
                basic.created, standard.links, basic.attributes)

    before = snapshot()
    if snapshot() != before:
        raise ValueError('artifact changed during handle metadata inspection')
    return before


def _windows_path_identity(path: Path, *, maximum: int) -> tuple:
    # FILE_READ_ATTRIBUTES only: retention metadata inspection never reads bytes.
    # OPEN_REPARSE_POINT ensures the final component itself is inspected.
    handle = _kernel32.CreateFileW(str(path), 0x80, 0x7, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _windows_file_identity(handle, maximum=maximum)
    finally:
        _kernel32.CloseHandle(handle)


def _descriptor_identity(descriptor: int, *, maximum: int) -> tuple:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
        raise ValueError('artifact descriptor type/link/size refused')
    if os.name == 'nt':
        return _windows_file_identity(msvcrt.get_osfhandle(descriptor), maximum=maximum)
    return file_identity(info)


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def owned_file_metadata(root: Path, relative: str, *, maximum: int) -> tuple[Path, tuple]:
    name = Path(relative)
    if name.is_absolute() or name.drive or '..' in name.parts or not name.parts or ':' in relative:
        raise ValueError('relative owned artifact path required')
    path = root
    for component in name.parts:
        path = path / component
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('artifact symlink/reparse component refused')
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum
            or not path.resolve(strict=True).is_relative_to(root)):
        raise ValueError('artifact escapes root, is not a regular file or exceeds bound')
    identity = (_windows_path_identity(path, maximum=maximum)
                if os.name == 'nt' else file_identity(info))
    return path, identity


def file_identity(info) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class ArtifactReader:
    def __init__(self, root: str | Path, references: dict[str, dict]):
        self.root = Path(root).resolve(strict=True)
        self.references = references
        self.verified: dict[str, str] = {}

    def read(self, name: str, kind: str, *, maximum: int = 32_000_000) -> bytes:
        reference = self.references[name]
        if reference.get("kind") != kind:
            raise ValueError("artifact kind mismatch: " + name)
        path, identity = owned_file_metadata(self.root, reference['path'], maximum=maximum)
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags)
        try:
            with os.fdopen(descriptor, 'rb') as stream:
                descriptor = None
                if (_descriptor_identity(stream.fileno(), maximum=maximum) != identity
                        or owned_file_metadata(self.root, reference['path'], maximum=maximum)[1] != identity):
                    raise ValueError('artifact changed before byte access')
                raw = stream.read(maximum + 1)
                if _descriptor_identity(stream.fileno(), maximum=maximum) != identity:
                    raise ValueError('artifact changed while reading')
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(raw) > maximum or owned_file_metadata(self.root, reference['path'], maximum=maximum)[1] != identity:
            raise ValueError('artifact identity/path changed after read')
        actual = hashlib.sha256(raw).hexdigest()
        if reference.get("sha256") != actual:
            raise ValueError("artifact hash mismatch: " + name)
        self.verified[name] = actual
        return raw

    def json(self, name: str, kind: str) -> object:
        return json.loads(self.read(name, kind))

    def jsonl(self, name: str, kind: str) -> list[dict]:
        return [json.loads(line) for line in self.read(name, kind).decode("utf-8").splitlines() if line.strip()]

    def audio(self, name: str) -> str:
        if self.references[name].get("approved_synthetic") is not True:
            raise ValueError("only approved synthetic audio may enter evaluation evidence")
        self.read(name, "audio", maximum=64_000_000)
        return self.verified[name]
