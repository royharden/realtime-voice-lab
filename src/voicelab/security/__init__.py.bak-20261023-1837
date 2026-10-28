from __future__ import annotations

import hmac
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit


def validate_boundary(host: str, origin: str, nonce: str, expected_nonce: str, allowed_origin: str) -> None:
    """Reject remote Host, nonexact Origin and absent/incorrect launch nonce."""
    try:
        parsed = urlsplit('http://' + host)
        hostname = parsed.hostname
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('invalid Host')
        if hostname != 'localhost' and not ipaddress.ip_address(hostname or '').is_loopback:
            raise ValueError('non-loopback Host')
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('invalid port')
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid loopback Host') from exc
    if not allowed_origin or '*' in allowed_origin or origin != allowed_origin:
        raise ValueError('Origin refused')
    if not expected_nonce or not nonce or not hmac.compare_digest(nonce.encode('utf-8'), expected_nonce.encode('utf-8')):
        raise ValueError('launch nonce refused')


def safe_artifact_path(root: str | Path, path: str | Path) -> Path:
    base = Path(root).resolve(strict=True)
    candidate = Path(path)
    if candidate.is_absolute() or '..' in candidate.parts or any(':' in part for part in candidate.parts):
        raise ValueError('artifact traversal refused')
    cursor = base
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError('artifact link refused')
    resolved = cursor.resolve()
    if not resolved.is_relative_to(base) or resolved == base:
        raise ValueError('artifact escape refused')
    return resolved
