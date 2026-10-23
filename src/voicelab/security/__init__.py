from __future__ import annotations

import hmac
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit


class SecurityRefused(ValueError):
    """Fixed code only; request/configuration contents never appear in the exception."""


def _authority(authority: str, *, port_required: bool) -> None:
    try:
        if (not isinstance(authority, str) or not 1 <= len(authority) <= 255
                or any(ord(c) <= 32 or ord(c) >= 127 for c in authority)
                or any(c in authority for c in '/\\?#@%') or authority.endswith(':')):
            raise ValueError
        if not re.fullmatch(r'(?:localhost|(?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-f:]+\])(?::[1-9]\d{0,4})?', authority, re.IGNORECASE | re.ASCII):
            raise ValueError
        parsed = urlsplit('http://' + authority)
        hostname = parsed.hostname
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError
        if hostname != 'localhost' and not ipaddress.ip_address(hostname or '').is_loopback:
            raise ValueError
        if port_required and parsed.port is None:
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise SecurityRefused('loopback_authority_refused') from None


def validate_origin(allowed_origin: str) -> None:
    try:
        if not isinstance(allowed_origin, str) or not allowed_origin.startswith('http://'):
            raise ValueError
        authority = allowed_origin[len('http://'):]
        _authority(authority, port_required=True)
    except (ValueError, TypeError):
        raise SecurityRefused('origin_configuration_refused') from None


def validate_host(host: str) -> None:
    _authority(host, port_required=False)


def validate_nonce(nonce: str) -> None:
    if not isinstance(nonce, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', nonce):
        raise SecurityRefused('nonce_format_refused')


def validate_boundary(host: str, origin: str, nonce: str, expected_nonce: str, allowed_origin: str) -> None:
    """Host is loopback; Origin equals a separately validated loopback frontend origin."""
    validate_host(host)
    validate_origin(allowed_origin)
    if not isinstance(origin, str) or origin != allowed_origin:
        raise SecurityRefused('origin_refused')
    validate_nonce(expected_nonce)
    validate_nonce(nonce)
    if not hmac.compare_digest(nonce.encode('ascii'), expected_nonce.encode('ascii')):
        raise SecurityRefused('nonce_refused')


def safe_artifact_path(root: str | Path, path: str | Path) -> Path:
    try:
        original = Path(root).absolute()
        for ancestor in (original, *original.parents):
            if ancestor.is_symlink() or ancestor.is_junction():
                raise SecurityRefused('artifact_root_link_refused')
        base = original.resolve(strict=True)
        if not base.is_dir():
            raise SecurityRefused('artifact_root_refused')
        raw = str(path)
        candidate = Path(raw.replace('\\', '/'))
        if (not raw or len(raw) > 512 or candidate.is_absolute() or candidate.drive or candidate.root
                or any(ord(c) < 32 or ord(c) == 127 for c in raw)):
            raise SecurityRefused('artifact_path_refused')
        for part in candidate.parts:
            if (part in {'.', '..'} or ':' in part or part.endswith(('.', ' '))
                    or any(c in part for c in '<>"|?*')
                    or re.fullmatch(r'(?i)(con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?', part)):
                raise SecurityRefused('artifact_path_refused')
        cursor = base
        for part in candidate.parts:
            cursor /= part
            if cursor.is_symlink() or cursor.is_junction():
                raise SecurityRefused('artifact_link_refused')
            if cursor.is_file() and cursor.stat().st_nlink > 1:
                raise SecurityRefused('artifact_hardlink_refused')
        resolved = cursor.resolve()
        if not resolved.is_relative_to(base) or resolved == base:
            raise SecurityRefused('artifact_escape_refused')
        return resolved
    except SecurityRefused:
        raise
    except (OSError, ValueError, TypeError, RuntimeError):
        raise SecurityRefused('artifact_path_unavailable') from None
