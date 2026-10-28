"""Durable fictional task effects; provider tool calls are proposals only."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any

SLOTS = ({"id": "slot-a", "label": "Tuesday 10:00 AM"}, {"id": "slot-b", "label": "Wednesday 2:00 PM"})
COMMANDS = frozenset({"get_hours", "list_slots", "select_slot", "confirm_slot", "get_status"})


class TaskAuthority:
    def __init__(self, sqlite_path: str | Path):
        self.path = str(sqlite_path)
        # One persistent connection also preserves :memory: for explicitly local callers.
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS task_trials(
          namespace TEXT PRIMARY KEY, state TEXT NOT NULL, version INTEGER NOT NULL,
          slot_id TEXT, challenge TEXT, epoch INTEGER NOT NULL DEFAULT 0,
          selection_turn TEXT);
        CREATE TABLE IF NOT EXISTS task_operations(
          namespace TEXT NOT NULL, operation_id TEXT NOT NULL, turn_id TEXT NOT NULL,
          command TEXT NOT NULL, arguments TEXT NOT NULL, outcome TEXT NOT NULL,
          PRIMARY KEY(namespace, operation_id), UNIQUE(namespace, turn_id, command));
        CREATE TABLE IF NOT EXISTS task_bookings(
          namespace TEXT PRIMARY KEY, operation_id TEXT NOT NULL, slot_id TEXT NOT NULL,
          confirmation_turn TEXT NOT NULL, epoch INTEGER NOT NULL, state_version INTEGER NOT NULL,
          UNIQUE(namespace, operation_id));
        CREATE TABLE IF NOT EXISTS task_attempts(
          id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL,
          operation_id TEXT NOT NULL, turn_id TEXT NOT NULL, command TEXT NOT NULL,
          epoch INTEGER NOT NULL, duplicate INTEGER NOT NULL, outcome TEXT NOT NULL);
        """)
        # Existing journals keep their effects/outcomes. Legacy unconfirmed selections
        # have unknown provenance and need a fresh logical selection turn first.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(task_trials)")}
            if "selection_turn" not in columns:
                self.db.execute("ALTER TABLE task_trials ADD COLUMN selection_turn TEXT")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.db.close()

    def _ensure(self, namespace: str) -> None:
        if not namespace or len(namespace) > 128:
            raise ValueError("invalid_trial_namespace")
        self.db.execute("INSERT OR IGNORE INTO task_trials(namespace,state,version) VALUES(?, 'information', 0)", (namespace,))

    def set_epoch(self, trial_namespace: str, response_epoch: int) -> None:
        self._ensure(trial_namespace)
        row = self.db.execute("SELECT epoch FROM task_trials WHERE namespace=?", (trial_namespace,)).fetchone()
        if response_epoch < row["epoch"]:
            raise ValueError("stale_epoch")
        self.db.execute("UPDATE task_trials SET epoch=? WHERE namespace=?", (response_epoch, trial_namespace))

    def snapshot(self, trial_namespace: str) -> dict[str, Any]:
        self._ensure(trial_namespace)
        row = dict(self.db.execute("SELECT * FROM task_trials WHERE namespace=?", (trial_namespace,)).fetchone())
        booking = self.db.execute("SELECT * FROM task_bookings WHERE namespace=?", (trial_namespace,)).fetchone()
        return {"state": row["state"], "state_version": row["version"], "slot_id": row["slot_id"], "challenge": row["challenge"], "response_epoch": row["epoch"], "booking": dict(booking) if booking else None}

    def execute(self, trial_namespace: str, command: str, arguments: dict[str, Any], *, logical_turn_id: str, response_epoch: int, explicit_confirmation: bool = False) -> dict[str, Any]:
        if command not in COMMANDS or not logical_turn_id or len(logical_turn_id) > 96:
            raise ValueError("invalid_task_command")
        if not isinstance(arguments, dict):
            raise ValueError("invalid_arguments")
        allowed = {"select_slot": {"slot_id"}, "confirm_slot": {"slot_id", "challenge", "state_version"}}.get(command, set())
        if set(arguments) - allowed:
            raise ValueError("unexpected_task_arguments")
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded) > 1024:
            raise ValueError("arguments_too_large")
        operation = "op-" + hashlib.sha256(f"{trial_namespace}\0{logical_turn_id}\0{command}".encode()).hexdigest()[:32]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._ensure(trial_namespace)
            row = self.db.execute("SELECT * FROM task_trials WHERE namespace=?", (trial_namespace,)).fetchone()
            saved = self.db.execute("SELECT * FROM task_operations WHERE namespace=? AND operation_id=?", (trial_namespace, operation)).fetchone()
            duplicate = saved is not None
            if saved:
                outcome = json.loads(saved["outcome"])
                if saved["arguments"] != encoded:
                    outcome = {"ok": False, "code": "logical_operation_conflict", "logical_operation_id": operation}
            elif response_epoch != row["epoch"]:
                outcome = {"ok": False, "code": "stale_epoch", "logical_operation_id": operation}
            else:
                outcome = self._apply(trial_namespace, row, command, arguments, logical_turn_id, response_epoch, explicit_confirmation, operation)
                self.db.execute("INSERT INTO task_operations VALUES(?,?,?,?,?,?)", (trial_namespace, operation, logical_turn_id, command, encoded, json.dumps(outcome, sort_keys=True)))
            self.db.execute("INSERT INTO task_attempts(namespace,operation_id,turn_id,command,epoch,duplicate,outcome) VALUES(?,?,?,?,?,?,?)", (trial_namespace, operation, logical_turn_id, command, response_epoch, int(duplicate), json.dumps(outcome, sort_keys=True)))
            self.db.execute("COMMIT")
            return {**outcome, "duplicate": duplicate}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _apply(self, ns: str, row: sqlite3.Row, command: str, args: dict[str, Any], turn: str, epoch: int, confirmed: bool, operation: str) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": True, "logical_operation_id": operation}
        if command == "get_hours":
            result["hours"] = "Monday to Friday, 9:00 AM to 5:00 PM"
        elif command == "list_slots":
            result["slots"] = list(SLOTS)
            if row["state"] == "information":
                self.db.execute("UPDATE task_trials SET state='slots_offered',version=version+1 WHERE namespace=?", (ns,))
        elif command == "select_slot":
            if row["state"] in {"booked", "complete"}:
                result.update(ok=False, code="already_booked")
            elif row["state"] == "information" or args.get("slot_id") not in {s["id"] for s in SLOTS}:
                result.update(ok=False, code="select_offered_slot", slots=list(SLOTS))
            else:
                self.db.execute("UPDATE task_trials SET state='awaiting_confirmation',version=version+1,slot_id=?,challenge=?,selection_turn=? WHERE namespace=?", (args["slot_id"], secrets.token_urlsafe(24), turn, ns))
        elif command == "confirm_slot":
            if row["state"] != "awaiting_confirmation" or confirmed is not True or not row["selection_turn"] or turn == row["selection_turn"] or args.get("slot_id") != row["slot_id"] or args.get("challenge") != row["challenge"] or type(args.get("state_version")) is not int or args.get("state_version") != row["version"]:
                result.update(ok=False, code="explicit_current_confirmation_required")
            else:
                self.db.execute("INSERT INTO task_bookings VALUES(?,?,?,?,?,?)", (ns, operation, row["slot_id"], turn, epoch, row["version"]))
                self.db.execute("UPDATE task_trials SET state='booked',version=version+1,challenge=NULL WHERE namespace=?", (ns,))
                result["effect_committed"] = True
        elif command == "get_status" and row["state"] == "booked":
            self.db.execute("UPDATE task_trials SET state='complete',version=version+1 WHERE namespace=?", (ns,))
        result.update(self.snapshot(ns))
        return result
