"""Bounded, single-flight own-process metadata probes in a disposable helper.

The helper receives only the relay's PID. It publishes scalar metadata one metric
at a time, so a later blocked OS reader cannot erase earlier observations. No OS
reader runs on the server loop or in a thread that cannot be stopped.
"""
from __future__ import annotations

import json
import math
import sys
import time


UNITS = {"rss_bytes": "bytes", "open_sockets": "count", "open_files": "count"}


def _worker(pid: int) -> None:
    try:
        import psutil
        process = psutil.Process(pid)
        readers = {"rss_bytes": lambda: process.memory_info().rss,
                   "open_sockets": lambda: len(process.net_connections(kind="inet"))}
        # Observed Windows open_files calls exceed the sampling deadline;
        # unsupported until safe bounded execution is established.
        if sys.platform != "win32":
            readers["open_files"] = lambda: len(process.open_files())
    except Exception:
        readers = {}
    for name in UNITS:
        value = stamp = None
        reason = ("own_process_probe_unsupported_windows_open_files"
                  if name == "open_files" and sys.platform == "win32"
                  else "own_process_probe_unsupported")
        if name in readers:
            try:
                value = readers[name]()
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or value < 0 or isinstance(value, float) and not math.isfinite(value)):
                    raise ValueError("probe_value_invalid")
                stamp = time.monotonic_ns()
                reason = None
            except (NotImplementedError, AttributeError):
                reason = "own_process_probe_unsupported"
            except Exception:
                value = stamp = None
                reason = "own_process_probe_failed"
        print(json.dumps({"name": name, "value": value, "observed_ns": stamp,
                          "reason": reason}, allow_nan=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3 or not sys.argv[1].isascii() or not sys.argv[1].isdigit():
        raise SystemExit(2)
    sys.path.insert(0, sys.argv[2])
    _worker(int(sys.argv[1]))
    raise SystemExit(0)


# The worker exits before importing the server's async/process machinery.
import asyncio
from contextlib import suppress
import os
from pathlib import Path
import sysconfig
from typing import Callable


PROBE_TIMEOUT = 1.0
CACHE_TTL = 1.0
REAP_TIMEOUT = 0.2


def _unknown(reason: str) -> dict[str, dict[str, object]]:
    return {name: {"value": None, "observation": "unknown", "unit": unit,
                   "reason": reason} for name, unit in UNITS.items()}


def _unique_record(pairs: list[tuple[str, object]]) -> dict[str, object]:
    record: dict[str, object] = {}
    for name, value in pairs:
        if name in record:
            raise ValueError("probe_record_invalid")
        record[name] = value
    return record


async def _launch_probe(pid: int) -> asyncio.subprocess.Process:
    """Injection seam: stdout.readline, returncode, terminate/kill and async wait.

    Use the base interpreter directly: Windows venv redirector executables can
    create a descendant that outlives termination of the redirector. Isolated
    mode ignores startup customization; only our known venv's package directory
    is supplied for the optional psutil dependency.
    """
    return await asyncio.create_subprocess_exec(
        sys._base_executable, "-I", str(Path(__file__).resolve()), str(pid),
        sysconfig.get_path("purelib"),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=2048,
    )


class OwnProcessSampler:
    """Request-driven cache; at most one launch/helper/cleanup per server instance.

    A launch that exceeds its deadline remains owned until its completion callback
    kills/reaps it. Unconfirmed cleanup closes the gate to further launches rather
    than accumulating workers. aclose owns cancellation and shutdown cleanup.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 clock_ns: Callable[[], int] = time.monotonic_ns) -> None:
        self._clock = clock
        self._clock_ns = clock_ns
        self._task: asyncio.Task | None = None
        self._launch_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._cache: tuple[dict[str, dict[str, object]], dict[str, int]] | None = None
        self._cached_at = 0.0
        self._closed = False
        self._launch_expired = False

    async def sample(self) -> dict[str, dict[str, object]]:
        if self._closed:
            return _unknown("own_process_sampler_closed")
        if self._task is not None and not self._task.done():
            result, observed_ns = await asyncio.shield(self._task)
        elif self._cache is not None and 0 <= self._clock() - self._cached_at < CACHE_TTL:
            result, observed_ns = self._cache
        else:
            self._task = asyncio.create_task(self._sample_once())
            result, observed_ns = await asyncio.shield(self._task)
        snapshot = {name: dict(metric) for name, metric in result.items()}
        for name, metric in snapshot.items():
            if metric["observation"] == "observed":
                age = self._clock_ns() - observed_ns[name]
                if not 0 <= age <= int(CACHE_TTL * 1_000_000_000):
                    snapshot[name] = _unknown("own_process_probe_stale")[name]
        return snapshot

    def _launch_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            self._process = task.result()
        except Exception:
            return
        if self._closed or self._launch_expired:
            self._start_cleanup()

    def _start_cleanup(self) -> asyncio.Task:
        # The late-launch callback, collection finally and shutdown share one
        # cleanup owner. None may clear ownership while another is still waiting.
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._reap_process())
        return self._cleanup_task

    async def _reap(self) -> bool:
        return await asyncio.shield(self._start_cleanup())

    async def _reap_process(self) -> bool:
        process = self._process
        if process is None:
            return self._launch_task is None or self._launch_task.done()
        reaped = False
        for stop in (process.terminate, process.kill):
            if process.returncode is None:
                with suppress(Exception):
                    stop()
            try:
                async with asyncio.timeout(REAP_TIMEOUT):
                    await process.wait()
            except Exception:
                continue
            if process.returncode is not None:
                reaped = True
                break
        if not reaped:
            return False
        self._process = None
        self._launch_task = None
        return True

    def _metric(self, raw: bytes, seen: set[str],
                observed_ns: dict[str, int]) -> tuple[str, dict[str, object]]:
        record = json.loads(raw, object_pairs_hook=_unique_record)
        if not isinstance(record, dict):
            raise ValueError("probe_record_invalid")
        name = record.get("name")
        if not isinstance(name, str) or name not in UNITS:
            raise ValueError("probe_record_invalid")
        invalid = _unknown("own_process_probe_failed")[name]
        if set(record) != {"name", "value", "observed_ns", "reason"} or name in seen:
            observed_ns.pop(name, None)
            return name, invalid
        value, stamp, reason = record["value"], record["observed_ns"], record["reason"]
        if isinstance(reason, str) and reason in {
                "own_process_probe_unsupported", "own_process_probe_failed",
                "own_process_probe_unsupported_windows_open_files"}:
            if value is not None or stamp is not None:
                return name, invalid
            return name, _unknown(reason)[name]
        if (reason is not None or isinstance(value, bool) or not isinstance(value, (int, float))
                or isinstance(value, float) and not math.isfinite(value) or value < 0
                or isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0):
            return name, invalid
        age = self._clock_ns() - stamp
        if not 0 <= age <= int(CACHE_TTL * 1_000_000_000):
            return name, _unknown("own_process_probe_stale")[name]
        observed_ns[name] = stamp
        return name, {"value": value, "observation": "observed", "unit": UNITS[name],
                      "reason": f"isolated own-process observation at server_monotonic_ns={stamp}"}

    async def _sample_once(self) -> tuple[dict[str, dict[str, object]], dict[str, int]]:
        result = _unknown("own_process_probe_timeout")
        seen: set[str] = set()
        observed_ns: dict[str, int] = {}
        failure: str | None = None
        if (self._cleanup_task is not None and not self._cleanup_task.done()
                or self._launch_task is not None and not self._launch_task.done()
                or self._process is not None):
            return _unknown("own_process_probe_cleanup_pending"), observed_ns
        self._launch_expired = False
        try:
            async with asyncio.timeout(PROBE_TIMEOUT):
                self._launch_task = asyncio.create_task(_launch_probe(os.getpid()))
                self._launch_task.add_done_callback(self._launch_done)
                process = await asyncio.shield(self._launch_task)
                self._process = process
                if process.stdout is None:
                    raise ValueError("probe_stdout_unavailable")
                for _ in UNITS:
                    raw = await process.stdout.readline()
                    if not raw or len(raw) > 2048:
                        raise ValueError("probe_record_unavailable")
                    try:
                        name, metric = self._metric(raw, seen, observed_ns)
                    except (ValueError, TypeError, OverflowError):
                        # A bounded bad record cannot certify a metric, but also
                        # cannot discard independent valid records that follow.
                        failure = "own_process_probe_failed"
                        continue
                    seen.add(name)
                    result[name] = metric
        except TimeoutError:
            failure = "own_process_probe_timeout"
        except asyncio.CancelledError:
            raise
        except Exception:
            failure = "own_process_probe_failed"
        finally:
            self._launch_expired = True
            if self._process is not None:
                if not await self._reap():
                    failure = "own_process_probe_cleanup_pending"
        if failure is not None:
            if failure == "own_process_probe_cleanup_pending":
                result, observed_ns = _unknown(failure), {}
            else:
                for name in UNITS.keys() - seen:
                    result[name] = _unknown(failure)[name]
        self._cache = result, observed_ns
        self._cached_at = self._clock()
        return result, observed_ns

    async def aclose(self) -> None:
        """Bound cleanup; report unconfirmed ownership rather than hiding it.

        A late launch remains retained and its callback still stops/reaps it.
        The caller must treat a shutdown exception as incomplete cleanup, never
        as a successful resource/soak observation.
        """
        self._closed = True
        self._launch_expired = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._launch_task is not None and not self._launch_task.done():
            with suppress(TimeoutError, Exception):
                async with asyncio.timeout(PROBE_TIMEOUT):
                    await asyncio.shield(self._launch_task)
        if self._cleanup_task is not None and not self._cleanup_task.done():
            await self._cleanup_task
        if self._process is not None:
            await self._reap()
        self._cache = None
        if (self._launch_task is not None and not self._launch_task.done()
                or self._process is not None):
            raise RuntimeError("own_process_probe_cleanup_unconfirmed")
