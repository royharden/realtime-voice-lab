"""Independent file-reader regressions over newly generated synthetic fixtures only.

Replacement/mutation deliberately affects only children of pytest's owned tmp_path.
No project, credential, private custody or external artifact bytes are accessed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from voicelab.evaluation import artifacts


RAW = b'{"synthetic_public_metadata":true}\n'


def fixture(root, *, raw=RAW, name='record.json', expected=None):
    path = root / name
    path.write_bytes(raw)
    reader = artifacts.ArtifactReader(root, {'public': {'kind': 'manifest', 'path': name,
        'sha256': expected or hashlib.sha256(raw).hexdigest()}})
    return path, reader


def test_actual_stable_file_positive_read_and_verified_hash(tmp_path):
    """what_bug_this_catches: lstat/fstat API timestamp disagreement rejecting unchanged bytes."""
    path, reader = fixture(tmp_path)
    assert reader.read('public', 'manifest') == RAW
    assert reader.verified == {'public': hashlib.sha256(RAW).hexdigest()}
    assert path.read_bytes() == RAW


@pytest.mark.skipif(sys.platform != 'win32', reason='actual Windows path/descriptor timestamp semantics')
def test_windows_stable_after_in_place_write_has_consistent_read_identity(tmp_path):
    """what_bug_this_catches: creation-time path ctime compared to descriptor change/mtime.

    Actual Windows APIs are used. The file remains unchanged during reader.read.
    """
    path, reader = fixture(tmp_path, raw=b'0' * len(RAW), expected=hashlib.sha256(RAW).hexdigest())
    time.sleep(.03)
    path.write_bytes(RAW)
    before = path.stat()
    assert reader.read('public', 'manifest') == RAW
    after = path.stat()
    assert (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) == (
        after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns)


def test_actual_wrong_hash_refuses_after_stable_positive_precondition(tmp_path):
    """what_bug_this_catches: treating successful file identity as successful byte pinning."""
    _, reader = fixture(tmp_path)
    assert reader.read('public', 'manifest') == RAW
    reader.references['public']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='hash mismatch'):
        reader.read('public', 'manifest')


@pytest.mark.parametrize('relative', ['../record.json', 'nested/../../record.json',
                                     'C:/record.json', 'record.json:stream'])
def test_escape_and_stream_names_refuse_before_os_open(tmp_path, monkeypatch, relative):
    """what_bug_this_catches: path/ADS escape inspected after opening unowned bytes."""
    _, reader = fixture(tmp_path)
    reader.references['public']['path'] = relative

    def forbidden(*args, **kwargs):
        pytest.fail('path refusal must precede byte open')
    monkeypatch.setattr(artifacts.os, 'open', forbidden)
    with pytest.raises(ValueError):
        reader.read('public', 'manifest')


def test_size_bound_and_kind_refusals_remain_closed(tmp_path):
    """what_bug_this_catches: a portability repair dropping size or kind checks."""
    _, reader = fixture(tmp_path)
    with pytest.raises(ValueError):
        reader.read('public', 'wrong-kind')
    with pytest.raises(ValueError):
        reader.read('public', 'manifest', maximum=len(RAW) - 1)
    assert reader.read('public', 'manifest', maximum=len(RAW)) == RAW


def test_actual_hardlink_refuses_before_os_open(tmp_path, monkeypatch):
    """what_bug_this_catches: allowing multi-link bytes to be relabeled as exclusively owned."""
    path, reader = fixture(tmp_path)
    os.link(path, tmp_path / 'owned-synthetic-hardlink.json')
    def forbidden(*args, **kwargs):
        pytest.fail('hardlink refusal must precede byte open')
    monkeypatch.setattr(artifacts.os, 'open', forbidden)
    with pytest.raises(ValueError):
        reader.read('public', 'manifest')


def test_directory_is_not_an_artifact(tmp_path, monkeypatch):
    """what_bug_this_catches: a directory opened through an artifact reference."""
    (tmp_path / 'directory.json').mkdir()
    reader = artifacts.ArtifactReader(tmp_path, {'public': {
        'kind': 'manifest', 'path': 'directory.json', 'sha256': '0' * 64}})
    def forbidden(*args, **kwargs):
        pytest.fail('type refusal must precede byte open')
    monkeypatch.setattr(artifacts.os, 'open', forbidden)
    with pytest.raises(ValueError):
        reader.read('public', 'manifest')


@pytest.mark.skipif(sys.platform != 'win32', reason='actual Windows directory junction/reparse guard')
def test_windows_actual_junction_component_refuses_before_os_open(tmp_path, monkeypatch):
    """what_bug_this_catches: following a junction inside an otherwise owned artifact root."""
    target = tmp_path / 'synthetic-target'
    target.mkdir()
    (target / 'record.json').write_bytes(RAW)
    link = tmp_path / 'synthetic-junction'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                            capture_output=True, check=False, timeout=10)
    assert result.returncode == 0, 'actual junction prerequisite unavailable; do not claim guard pass'
    reader = artifacts.ArtifactReader(tmp_path, {'public': {'kind': 'manifest',
        'path': 'synthetic-junction/record.json', 'sha256': hashlib.sha256(RAW).hexdigest()}})
    def forbidden(*args, **kwargs):
        pytest.fail('junction refusal must precede byte open')
    monkeypatch.setattr(artifacts.os, 'open', forbidden)
    with pytest.raises(ValueError):
        reader.read('public', 'manifest')


def test_actual_same_size_same_mtime_replacement_before_open_refuses(tmp_path, monkeypatch):
    """what_bug_this_catches: comparing only size/mtime and missing a same-byte new inode."""
    path, reader = fixture(tmp_path)
    replacement = tmp_path / 'synthetic-replacement.json'
    replacement.write_bytes(RAW)
    stamp = path.stat()
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    original = artifacts.os.open
    hit = []

    def replace_before_open(name, flags, *args, **kwargs):
        assert Path(name) == path and path.parent == replacement.parent == tmp_path
        os.replace(replacement, path)
        hit.append(True)
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, 'open', replace_before_open)
    with pytest.raises(ValueError):
        reader.read('public', 'manifest')
    assert hit == [True]
    assert reader.verified == {}


@pytest.mark.parametrize('phase', ['during_read', 'after_descriptor_close'])
def test_actual_in_place_mutation_with_restored_size_mtime_refuses(tmp_path, monkeypatch, phase):
    """what_bug_this_catches: dropping change detection while fixing cross-API metadata.

    Change bytes without changing size; restore mtime. During-read case mutates after
    the stream returns original bytes, so hash equality alone cannot catch the race.
    """
    path, reader = fixture(tmp_path)
    stamp = path.stat()
    changed = b'X' * len(RAW)
    original = artifacts.os.fdopen
    hit = []

    def mutate():
        assert path.parent == tmp_path
        path.write_bytes(changed)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        hit.append(True)

    class Stream:
        def __init__(self, *args, **kwargs):
            self.stream = original(*args, **kwargs)

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            result = self.stream.__exit__(*args)
            if phase == 'after_descriptor_close':
                mutate()
            return result

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            raw = self.stream.read(size)
            if phase == 'during_read':
                mutate()
            return raw

    monkeypatch.setattr(artifacts.os, 'fdopen', Stream)
    with pytest.raises(ValueError):
        reader.read('public', 'manifest')
    assert hit == [True], 'must reach the intended race, not pass via unrelated prerequisite refusal'
    assert path.read_bytes() == changed and reader.verified == {}
