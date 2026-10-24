"""D5 guarded dev reads. No reader, JSON flag or cached receipt grants authority."""
from __future__ import annotations

from pathlib import Path


def public_reader(root, references: dict, access):
    from voicelab.evaluation.exposure import PublicReadAccess
    if not isinstance(access, PublicReadAccess):
        raise ValueError('actual_D5_PublicReadAccess_required_before_fixture_or_plan_read')
    return access.reader(root, references)


def read_owned(reader, root, relative: str, *, maximum: int, kind: str | None = None) -> bytes:
    from voicelab.evaluation.exposure import validate_read_access
    validate_read_access(reader)
    if Path(root).resolve(strict=True) != reader.root:
        raise ValueError('fixture_root_differs_from_authenticated_inventory_root')
    requested = Path(relative)
    if requested.is_absolute() or requested.drive or '..' in requested.parts:
        raise ValueError('owned_inventory_relative_name_required')
    matches = [(name, ref) for name, ref in reader.references.items()
               if Path(ref['path']) == requested and (kind is None or ref['kind'] == kind)
               and name not in {'freeze', 'release'}]
    if len(matches) != 1:
        raise ValueError('exact_unique_authenticated_artifact_reference_required')
    name, ref = matches[0]
    return reader.read(name, ref['kind'], maximum=maximum)
