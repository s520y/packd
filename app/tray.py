"""Tray completion logic. No camera or AI code here, so it can be tested alone."""

from dataclasses import dataclass
import time

MISSES_BEFORE_REMOVED = 2  # Scans in a row an item must be gone before it counts as removed.
SCANS_BEFORE_WRONG_ITEM = 2  # Scans in a row before an extra item raises an alert.


@dataclass
class Detection:
    """What one camera scan saw."""

    counts: dict[str, int]
    extra_items: list[str]
    hands_over_tray: bool = False


def clean_order(items: list[dict]) -> list[dict]:
    """Tidy receipt or typed items: drop blank names and merge duplicates."""
    merged: dict[str, dict] = {}
    for line in items:
        name = line["item"].strip()
        if not name:
            continue
        qty = max(1, line.get("qty", 1))
        looks_like = line.get("looks_like", "").strip()
        note = line.get("note", "").strip()
        existing = merged.get(name.casefold())
        if existing:
            existing["qty"] += qty
            existing["looks_like"] = existing["looks_like"] or looks_like
            existing["note"] = "; ".join(filter(None, [existing["note"], note]))
        else:
            merged[name.casefold()] = {
                "item": name,
                "qty": qty,
                "looks_like": looks_like,
                "note": note,
            }
    return list(merged.values())


class TrayTracker:
    def __init__(self, order: list[dict] | None = None, clock=time.monotonic):
        self.clock = clock
        self.generation = 0  # Lets the server drop a scan that finishes after a reset.
        self.set_order(order or [])

    def set_order(self, order: list[dict]) -> None:
        self.order = order
        self.reset()

    def reset(self) -> None:
        self.generation += 1
        self.confirmed = {line["item"]: 0 for line in self.order}
        self.low_streak = {line["item"]: 0 for line in self.order}
        self.extra_streak = 0
        self.extra_items: list[str] = []
        self.first_item_at: float | None = None
        self.was_complete = False
        self.last_scan_skipped = False

    def apply(self, detection: Detection) -> None:
        self.last_scan_skipped = detection.hands_over_tray
        if detection.hands_over_tray:
            return

        for name in self.confirmed:
            seen = max(0, detection.counts.get(name, 0))
            if seen >= self.confirmed[name]:
                self.confirmed[name] = seen
                self.low_streak[name] = 0
            else:
                self.low_streak[name] += 1
                if self.low_streak[name] >= MISSES_BEFORE_REMOVED:
                    self.confirmed[name] = seen
                    self.low_streak[name] = 0

        if detection.extra_items:
            self.extra_streak += 1
        else:
            self.extra_streak = 0
        self.extra_items = (
            detection.extra_items if self.extra_streak >= SCANS_BEFORE_WRONG_ITEM else []
        )

        something_on_tray = any(self.confirmed.values()) or bool(detection.extra_items)
        if something_on_tray and self.first_item_at is None:
            self.first_item_at = self.clock()

        if self._is_complete():
            self.was_complete = True

    def _is_complete(self) -> bool:
        return bool(self.order) and not self.extra_items and all(
            self.confirmed[line["item"]] == line["qty"] for line in self.order
        )

    def _status(self) -> str:
        if not self.order:
            return "no_order"
        if self._is_complete():
            return "complete"
        if self.first_item_at is None:
            return "waiting"
        return "incomplete" if self.was_complete else "packing"

    def snapshot(self) -> dict:
        lines = []
        for line in self.order:
            have = self.confirmed[line["item"]]
            lines.append(
                {
                    "item": line["item"],
                    "qty": line["qty"],
                    "have": have,
                    "missing": max(0, line["qty"] - have),
                    "too_many": max(0, have - line["qty"]),
                    "note": line.get("note", ""),
                }
            )
        elapsed = None if self.first_item_at is None else self.clock() - self.first_item_at
        return {
            "status": self._status(),
            "lines": lines,
            "extra_items": self.extra_items,
            "elapsed_seconds": elapsed,
            "scan_skipped": self.last_scan_skipped,
        }
