"""Read only explicitly referenced, hash-pinned artifacts under one owned root."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path


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
    return path, file_identity(info)


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
                if file_identity(os.fstat(stream.fileno())) != identity:
                    raise ValueError('artifact changed before byte access')
                raw = stream.read(maximum + 1)
                if file_identity(os.fstat(stream.fileno())) != identity:
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
