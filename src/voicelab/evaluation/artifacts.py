"""Read only explicitly referenced, hash-pinned artifacts under one owned root."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class ArtifactReader:
    def __init__(self, root: str | Path, references: dict[str, dict]):
        self.root = Path(root).resolve(strict=True)
        self.references = references
        self.verified: dict[str, str] = {}

    def read(self, name: str, kind: str, *, maximum: int = 32_000_000) -> bytes:
        reference = self.references[name]
        if reference.get("kind") != kind:
            raise ValueError("artifact kind mismatch: " + name)
        relative = Path(reference["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact must be a relative owned path")
        path = (self.root / relative).resolve(strict=True)
        if not path.is_relative_to(self.root) or not path.is_file() or path.stat().st_size > maximum:
            raise ValueError("artifact escapes root or exceeds size bound")
        raw = path.read_bytes()
        if len(raw) > maximum:
            raise ValueError("artifact grew beyond size bound")
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
