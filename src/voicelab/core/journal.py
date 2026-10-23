"""Durable lifecycle metadata beside task effects; no audio or transcripts."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterator


class LifecycleJournal:
    """First terminal wins. Restart never fabricates a missing trace terminal."""

    def __init__(self, path: Path):
        self.path = path
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS relay_sessions(
              session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, trial_id TEXT NOT NULL,
              arm TEXT NOT NULL, mode TEXT NOT NULL, boot_id TEXT NOT NULL,
              started_ns INTEGER NOT NULL, started_utc TEXT NOT NULL,
              terminal_outcome TEXT, terminal_reason TEXT, terminal_source TEXT,
              trace_hash TEXT, trace_complete INTEGER NOT NULL DEFAULT 0,
              run_manifest_hash TEXT);
            CREATE TABLE IF NOT EXISTS relay_turns(
              session_id TEXT NOT NULL REFERENCES relay_sessions(session_id),
              transport_turn_id TEXT NOT NULL, logical_turn_id TEXT NOT NULL,
              connection_epoch INTEGER NOT NULL, response_epoch INTEGER NOT NULL,
              started_ns INTEGER NOT NULL, terminal_outcome TEXT, terminal_reason TEXT,
              PRIMARY KEY(session_id, transport_turn_id));
            CREATE TABLE IF NOT EXISTS relay_artifacts(
              session_id TEXT NOT NULL REFERENCES relay_sessions(session_id),
              content_hash TEXT NOT NULL, metadata TEXT NOT NULL,
              PRIMARY KEY(session_id, content_hash));
            CREATE TABLE IF NOT EXISTS relay_admission(
              session_id TEXT PRIMARY KEY REFERENCES relay_sessions(session_id),
              metadata TEXT NOT NULL);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(relay_sessions)")}
            if "run_manifest_hash" not in columns:
                db.execute("ALTER TABLE relay_sessions ADD COLUMN run_manifest_hash TEXT")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    def recover(self, boot_id: str) -> int:
        """Called once by the sole localhost relay owner before serving sessions."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result = db.execute("""UPDATE relay_sessions SET terminal_outcome='service_restart',
                  terminal_reason='previous_process_ended_without_terminal', terminal_source='restart_reconciliation'
                  WHERE terminal_outcome IS NULL AND boot_id != ?""", (boot_id,))
                db.execute("""UPDATE relay_turns SET terminal_outcome='service_restart',
                  terminal_reason='previous_process_ended_without_terminal'
                  WHERE terminal_outcome IS NULL AND session_id IN
                  (SELECT session_id FROM relay_sessions WHERE terminal_source='restart_reconciliation')""")
                db.execute("COMMIT")
                return result.rowcount
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def start(self, *, session_id: str, run_id: str, trial_id: str, arm: str,
              mode: str, boot_id: str, started_ns: int, run_manifest_hash: str | None = None) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO relay_sessions
              (session_id,run_id,trial_id,arm,mode,boot_id,started_ns,started_utc,run_manifest_hash)
              VALUES(?,?,?,?,?,?,?,?,?)""", (session_id, run_id, trial_id, arm, mode, boot_id,
                started_ns, datetime.now(timezone.utc).isoformat(), run_manifest_hash))

    def start_turn(self, session: str, transport: str, logical: str, connection: int,
                   response: int, started_ns: int) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO relay_turns
              (session_id,transport_turn_id,logical_turn_id,connection_epoch,response_epoch,started_ns)
              VALUES(?,?,?,?,?,?)""", (session, transport, logical, connection, response, started_ns))

    def finish_turn(self, session: str, transport: str, outcome: str, reason: str) -> bool:
        with self._connect() as db:
            result = db.execute("""UPDATE relay_turns SET terminal_outcome=?,terminal_reason=?
              WHERE session_id=? AND transport_turn_id=? AND terminal_outcome IS NULL""",
                (outcome, reason, session, transport))
            return result.rowcount == 1

    def finish(self, session: str, outcome: str, reason: str, source: str,
               trace_hash: str | None, trace_complete: bool) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""UPDATE relay_turns SET terminal_outcome=?,terminal_reason=?
                  WHERE session_id=? AND terminal_outcome IS NULL""", (outcome, reason, session))
                db.execute("""UPDATE relay_sessions SET terminal_outcome=?,terminal_reason=?,
                  terminal_source=?,trace_hash=?,trace_complete=?
                  WHERE session_id=? AND terminal_outcome IS NULL""",
                    (outcome, reason, source, trace_hash, int(trace_complete), session))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def artifact(self, session: str, digest: str, metadata: dict[str, object]) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("invalid_artifact_digest")
        encoded = json.dumps(metadata, sort_keys=True, allow_nan=False)
        if len(encoded) > 4096:
            raise ValueError("artifact_metadata_bound")
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO relay_artifacts VALUES(?,?,?)", (session, digest, encoded))

    def admission(self, session: str, metadata: dict) -> None:
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode('utf-8')) > 32768:
            raise ValueError('admission_metadata_bound')
        with self._connect() as db:
            db.execute('INSERT INTO relay_admission VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET metadata=excluded.metadata', (session, encoded))

    def export(self, session: str) -> dict[str, object] | None:
        with self._connect() as db:
            db.execute('BEGIN')
            row = db.execute("SELECT * FROM relay_sessions WHERE session_id=?", (session,)).fetchone()
            if row is None:
                return None
            turns = [dict(item) for item in db.execute(
                "SELECT * FROM relay_turns WHERE session_id=? ORDER BY started_ns,transport_turn_id", (session,))]
            artifacts = [{"content_hash": item["content_hash"], **json.loads(item["metadata"])}
                for item in db.execute("SELECT * FROM relay_artifacts WHERE session_id=? ORDER BY content_hash", (session,))]
            admission = db.execute('SELECT metadata FROM relay_admission WHERE session_id=?', (session,)).fetchone()
            return {"schema_version": 1, "run_id": row["run_id"], "trial_id": row["trial_id"],
                "session_id": row["session_id"], "boot_id": row["boot_id"],
                "session": dict(row), "turns": turns, "artifacts": artifacts,
                "admission": json.loads(admission['metadata']) if admission is not None else None,
                "clock_domain": "server_monotonic_ns", "clock_scope": "boot_id",
                "trace_reference": f"traces/{session}.jsonl", "spoken_task_acceptance": "unknown"}
