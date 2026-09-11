"""Regression tests for the lazy model loaders' check-then-act race.

Both loaders set `_load_attempted = True` BEFORE the slow `torch.load`, with
no lock. A second request arriving inside that window saw `_model is None`
and `_load_attempted is True` and was told no trained model exists — a
spurious "model unavailable" for a model that was loading fine.
"""
import os
import re
import sys
import threading
import time

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC_DIR)


def _loader_shape(filename):
    with open(os.path.join(SRC_DIR, filename), encoding="utf-8") as handle:
        return handle.read()


def test_both_loaders_hold_a_lock():
    for filename in ("plant_disease_model.py", "land_cover_model.py"):
        body = _loader_shape(filename)
        assert "_model_lock = threading.Lock()" in body, f"{filename} has no load lock"
        loader = body[body.index("def _load_model"):]
        assert "with _model_lock:" in loader, f"{filename} does not take the lock"
        # The re-check inside the lock is what makes the second caller wait
        # rather than give up.
        guarded = loader[loader.index("with _model_lock:"):]
        assert "if _model is not None:" in guarded, f"{filename} lacks the in-lock re-check"


def test_both_loaders_request_weights_only():
    """torch.load unpickles arbitrary objects unless weights_only is set."""
    for filename in ("plant_disease_model.py", "land_cover_model.py"):
        body = _loader_shape(filename)
        assert "weights_only=True" in body, f"{filename} loads its checkpoint unguarded"


def test_the_race_pattern_is_reproducible_and_the_lock_fixes_it():
    """Model the two implementations directly — importing torch is not
    needed to show the ordering bug."""
    def make(locked):
        state = {"model": None, "attempted": False}
        lock = threading.Lock()

        def load():
            if state["model"] is not None:
                return state["model"]
            if locked:
                with lock:
                    if state["model"] is not None:
                        return state["model"]
                    if state["attempted"]:
                        return None
                    state["attempted"] = True
                    time.sleep(0.05)
                    state["model"] = "loaded"
                    return state["model"]
            if state["attempted"]:
                return None
            state["attempted"] = True
            time.sleep(0.05)
            state["model"] = "loaded"
            return state["model"]
        return load

    for locked, expect_none in ((False, True), (True, False)):
        load = make(locked)
        results = []
        threads = [threading.Thread(target=lambda: results.append(load())) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        got_none = any(r is None for r in results)
        assert got_none is expect_none, f"locked={locked}: results={results}"
