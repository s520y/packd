from app.tray import Detection, TrayTracker, clean_order

ORDER = [{"item": "burger", "qty": 1}, {"item": "napkin", "qty": 2}]
FULL = {"burger": 1, "napkin": 2}


class FakeClock:
    now = 100.0

    def __call__(self):
        return self.now


def make_tracker():
    return TrayTracker(ORDER, clock=FakeClock())


def scan(tracker, counts, extras=None, hands=False):
    tracker.apply(Detection(counts, extras or [], hands))
    return tracker.snapshot()


def test_starts_waiting_with_no_timer():
    state = make_tracker().snapshot()
    assert state["status"] == "waiting"
    assert state["elapsed_seconds"] is None


def test_timer_starts_at_first_item():
    tracker = make_tracker()
    scan(tracker, {"burger": 1})
    tracker.clock.now += 30
    state = tracker.snapshot()
    assert state["status"] == "packing"
    assert state["elapsed_seconds"] == 30


def test_complete_needs_every_item_and_quantity():
    tracker = make_tracker()
    assert scan(tracker, {"burger": 1, "napkin": 1})["status"] == "packing"
    assert scan(tracker, FULL)["status"] == "complete"


def test_one_missed_scan_does_not_flip_status():
    tracker = make_tracker()
    scan(tracker, FULL)
    assert scan(tracker, {"burger": 1, "napkin": 2 - 1})["status"] == "complete"


def test_two_missed_scans_mark_item_removed():
    tracker = make_tracker()
    scan(tracker, FULL)
    scan(tracker, {"napkin": 2})
    state = scan(tracker, {"napkin": 2})
    assert state["status"] == "incomplete"
    assert state["lines"][0]["missing"] == 1


def test_a_good_scan_clears_the_miss_streak():
    tracker = make_tracker()
    scan(tracker, FULL)
    scan(tracker, {"napkin": 2})
    scan(tracker, FULL)
    assert scan(tracker, {"napkin": 2})["status"] == "complete"


def test_returning_an_item_completes_the_tray_again():
    tracker = make_tracker()
    scan(tracker, FULL)
    scan(tracker, {"napkin": 2})
    scan(tracker, {"napkin": 2})
    assert scan(tracker, FULL)["status"] == "complete"


def test_scans_with_hands_are_ignored():
    tracker = make_tracker()
    scan(tracker, FULL)
    scan(tracker, {}, hands=True)
    state = scan(tracker, {}, hands=True)
    assert state["status"] == "complete"
    assert state["scan_skipped"] is True


def test_wrong_item_blocks_complete_after_two_scans():
    tracker = make_tracker()
    assert scan(tracker, FULL, ["sauce"])["status"] == "complete"
    state = scan(tracker, FULL, ["sauce"])
    assert state["status"] == "incomplete"
    assert state["extra_items"] == ["sauce"]
    assert scan(tracker, FULL)["status"] == "complete"


def test_too_many_of_an_item_is_not_complete():
    tracker = make_tracker()
    state = scan(tracker, {"burger": 2, "napkin": 2})
    assert state["status"] == "packing"
    assert state["lines"][0]["too_many"] == 1


def test_reset_clears_everything():
    tracker = make_tracker()
    scan(tracker, FULL)
    tracker.reset()
    state = tracker.snapshot()
    assert state["status"] == "waiting"
    assert state["elapsed_seconds"] is None
    assert all(line["have"] == 0 for line in state["lines"])


def test_no_order_is_never_complete():
    tracker = TrayTracker()
    tracker.apply(Detection({}, []))
    assert tracker.snapshot()["status"] == "no_order"


def test_new_order_replaces_the_old_tray():
    tracker = make_tracker()
    scan(tracker, FULL)
    tracker.set_order([{"item": "soup", "qty": 1}])
    state = tracker.snapshot()
    assert state["status"] == "waiting"
    assert [line["item"] for line in state["lines"]] == ["soup"]


def test_clean_order_merges_duplicates_and_drops_blanks():
    items = clean_order(
        [
            {"item": " Fries ", "qty": 1, "note": "extra salt"},
            {"item": "fries", "qty": 2, "note": "no salt"},
            {"item": "   ", "qty": 1},
            {"item": "drink", "qty": 0},
        ]
    )
    assert items == [
        {"item": "Fries", "qty": 3, "looks_like": "", "note": "extra salt; no salt"},
        {"item": "drink", "qty": 1, "looks_like": "", "note": ""},
    ]
