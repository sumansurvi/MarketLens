"""Local, privacy-preserving product analytics (architecture Phase 5, PRD FR-23).

Events are stored in the local SQLite database only. Every event name and property key is
allowlisted here; values must be numbers, booleans or short lowercase codes. Anything else
is dropped, so free text, tickers, identifiers, secrets, transcripts and audio can never be
recorded, whatever a caller passes.
"""

from __future__ import annotations

import json
import re
import statistics
from typing import Any

from marketlens_core.db.connection import AnyDatabase
from marketlens_core.timeutil import iso_z, utc_now

CATEGORY_KEYS = ("category_market", "category_fundamentals", "category_filings", "category_news")
EVENTS: dict[str, frozenset[str]] = {
    "run_started": frozenset({"origin", "data_mode", "llm_mode"}),
    "run_finished": frozenset(
        {"origin", "status", "duration_ms", "data_mode", "llm_mode", "llm_calls", "withheld_claims", *CATEGORY_KEYS}
    ),
    "brief_saved": frozenset({"data_mode"}),
    "watchlist_changed": frozenset({"action", "outcome"}),
    "voice_session_created": frozenset(),
    "voice_session_ended": frozenset({"ended_by"}),
    "voice_connection_succeeded": frozenset(),
    "voice_connection_failed": frozenset({"reason"}),
    "periods_compared": frozenset({"data_mode", "periods_available"}),
}
# Events a browser may report (everything else is recorded server-side only).
CLIENT_EVENTS = frozenset({"voice_connection_succeeded", "voice_connection_failed"})
_CODE = re.compile(r"^[a-z0-9_]{1,40}$")


def sanitize(event: str, properties: dict[str, Any]) -> dict[str, Any]:
    allowed = EVENTS[event]
    clean: dict[str, Any] = {}
    for key, value in properties.items():
        if key not in allowed or value is None:
            continue
        if isinstance(value, bool) or (isinstance(value, (int, float)) and abs(value) < 1e12):
            clean[key] = value
        elif isinstance(value, str) and _CODE.fullmatch(value):
            clean[key] = value
    return clean


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


class AnalyticsRepository:
    def __init__(self, db: AnyDatabase) -> None:
        self.db = db

    def record(self, event: str, **properties: Any) -> None:
        if event not in EVENTS:
            raise ValueError(f"unknown analytics event {event!r}")
        payload = json.dumps(sanitize(event, properties), sort_keys=True, separators=(",", ":"))
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO analytics_events (occurred_at, event_name, properties_json) VALUES (?,?,?)",
                (iso_z(utc_now()), event, payload),
            )

    def events(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT occurred_at, event_name, properties_json FROM analytics_events ORDER BY event_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"occurred_at": r["occurred_at"], "event": r["event_name"], "properties": json.loads(r["properties_json"])}
            for r in rows
        ]

    def summary(self) -> dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT event_name, properties_json FROM analytics_events").fetchall()
        counts: dict[str, int] = dict.fromkeys(EVENTS, 0)
        finished: list[dict[str, Any]] = []
        categories: dict[str, dict[str, int]] = {}
        voice_failures: dict[str, int] = {}
        watchlist: dict[str, int] = {}
        for row in rows:
            name, props = row["event_name"], json.loads(row["properties_json"])
            counts[name] = counts.get(name, 0) + 1
            if name == "run_finished":
                finished.append(props)
                for key in CATEGORY_KEYS:
                    if key in props:
                        bucket = categories.setdefault(key.removeprefix("category_"), {})
                        bucket[props[key]] = bucket.get(props[key], 0) + 1
            elif name == "voice_connection_failed":
                reason = props.get("reason", "other")
                voice_failures[reason] = voice_failures.get(reason, 0) + 1
            elif name == "watchlist_changed":
                action = props.get("action", "unknown")
                watchlist[action] = watchlist.get(action, 0) + 1
        statuses: dict[str, int] = {}
        for props in finished:
            statuses[props.get("status", "unknown")] = statuses.get(props.get("status", "unknown"), 0) + 1
        durations = [float(p["duration_ms"]) for p in finished if isinstance(p.get("duration_ms"), (int, float))]
        total = len(finished)
        return {
            "event_counts": counts,
            "runs": {
                "started": counts["run_started"],
                "finished": total,
                "by_status": statuses,
                "completion_rate": round(statuses.get("completed", 0) / total, 4) if total else None,
                "partial_rate": round(statuses.get("partial", 0) / total, 4) if total else None,
                "median_duration_ms": statistics.median(durations) if durations else None,
                "p95_duration_ms": _percentile(durations, 0.95),
            },
            "categories": categories,
            "voice": {
                "sessions_created": counts["voice_session_created"],
                "connections_succeeded": counts["voice_connection_succeeded"],
                "connections_failed": counts["voice_connection_failed"],
                "failure_reasons": voice_failures,
            },
            "workspace": {"briefs_saved": counts["brief_saved"], "watchlist_changes": watchlist},
            "periods_compared": counts["periods_compared"],
        }
