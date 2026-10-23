"""Explicit clock arithmetic for saved spans; never infer native internal stages."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def stage_rows(row: dict) -> list[dict]:
    normalized = []
    for index, stage in enumerate(row.get("stages", [])):
        name = str(stage.get("name", "unknown"))
        domain = stage.get("clock_domain", "unknown")
        observation = stage.get("observation", "unknown")
        if not isinstance(domain, str):
            domain = 'unknown'
        if not isinstance(observation, str):
            observation = 'unknown'
        reason, value = None, None
        if stage.get("no_op") is True or name in {"english_identity_translation", "translation_identity"}:
            reason = "identity/no-op marker; no fabricated duration"
        elif observation not in {"observed", "estimated"}:
            reason = "stage observation unknown"
        elif (stage.get("start_clock_domain", domain) != domain
              or stage.get("end_clock_domain", domain) != domain):
            reason = "mixed clock domains; subtraction refused"
        elif domain not in {"browser_performance_ms", "server_monotonic_ns", "audio_context_samples"}:
            reason = "unmapped/provider clock; duration unknown"
        elif not isinstance(stage.get('clock_scope'), str) or not stage['clock_scope']:
            reason = "stage clock scope/session/boot binding missing"
        elif (stage.get('start_clock_scope', stage['clock_scope']) != stage['clock_scope']
              or stage.get('end_clock_scope', stage['clock_scope']) != stage['clock_scope']):
            reason = 'different clock scopes/boots; subtraction refused'
        else:
            try:
                start, end = Decimal(str(stage["start"])), Decimal(str(stage["end"]))
                if not start.is_finite() or not end.is_finite() or end < start:
                    raise ValueError("invalid anchors")
                value = end - start
                if domain == "server_monotonic_ns":
                    value /= 1_000_000
                elif domain == "audio_context_samples":
                    rate = Decimal(str(stage["sample_rate"]))
                    if not rate.is_finite() or rate <= 0:
                        raise ValueError("invalid sample rate")
                    value = value * 1000 / rate
            except (KeyError, ValueError, InvalidOperation, TypeError):
                value, reason = None, "missing/nonfinite/reversed anchors or invalid sample rate"
        normalized.append({"logical_turn_id": row["logical_turn_id"], "index": index, "name": name,
                           "clock_domain": domain, "clock_scope": stage.get("clock_scope"),
                           "observation": observation, "value_ms": str(value) if value is not None else None,
                           "reason": reason, "raw": stage})
    return normalized
