"""Background monitor: continuous detection, event timeline, optional auto-heal."""
from __future__ import annotations

import threading
import time

from . import engine
from .engine import IN_SYNC, load_desired


class Monitor:
    def __init__(self, client, desired_path, interval: float = 2.0):
        self.client, self.path, self.interval = client, desired_path, interval
        self.autoheal = False
        self.events: list[dict] = []
        self.drift_since: dict[str, float] = {}
        self.recoveries: list[float] = []
        self.report: list[dict] = []
        self.last_tick = None
        self.error = None
        self.lock = threading.RLock()

    # ------------------------------------------------------------ helpers
    def _log(self, kind, resource, message, **extra):
        self.events.append({"ts": time.time(), "kind": kind, "resource": resource, "message": message, **extra})
        del self.events[:-200]

    @staticmethod
    def _summary(r):
        return "; ".join(f"{c['field']}: {c['actual']} -> {c['desired']}" for c in r["changes"])

    def _recovered(self, name, now, how):
        ttr = now - self.drift_since.pop(name)
        self.recoveries.append(ttr)
        self._log("reconciled", name, f"back in sync, time to recovery {ttr:.1f}s ({how})", ttr=round(ttr, 2))

    def _observe(self):
        desired = load_desired(self.path)  # re-read every tick: a git commit is picked up live
        report = engine.diff(desired, engine.read_actual(self.client))
        now = time.time()
        for r in report:
            n = r["name"]
            if r["status"] != IN_SYNC and n not in self.drift_since:
                self.drift_since[n] = now
                self._log("detected", n, self._summary(r))
            elif r["status"] == IN_SYNC and n in self.drift_since:
                self._recovered(n, now, "converged")
        self.report, self.last_tick, self.error = report, now, None
        return desired, report

    def _heal(self, desired, source):
        try:
            actions = engine.reconcile(self.client, desired)
        except Exception as e:  # image missing, port already taken, ...
            self.error = str(e)
            self._log("error", None, f"{source} failed: {e}")
            return []
        for a in actions:
            self._log("action", a["name"], f"{source}: {a['action']}")
        self._observe()
        return actions

    # --------------------------------------------------------------- API
    def tick(self):
        with self.lock:
            desired, report = self._observe()
            if self.autoheal and any(r["status"] != IN_SYNC for r in report):
                self._heal(desired, "auto-heal")

    def reconcile(self, source="manual"):
        with self.lock:
            desired, _ = self._observe()
            return self._heal(desired, source)

    def plan(self):
        with self.lock:
            _, report = self._observe()
            actions = engine.plan(report)
            self._log("plan", None, f"dry run: {len(actions)} change(s) would be applied")
            return actions

    def inject(self, name, kind, key=None, value=None):
        with self.lock:
            desired = load_desired(self.path)
            msg = engine.inject(self.client, desired, name, kind, key, value)
            self._log("injected", name, msg)
            self.tick()
            return msg

    def set_autoheal(self, on: bool):
        with self.lock:
            self.autoheal = bool(on)
            self._log("config", None, f"auto-heal {'enabled' if on else 'disabled'}")

    def snapshot(self):
        with self.lock:
            now = time.time()
            res = [{**r, "drift_for": round(now - self.drift_since[r["name"]], 1) if r["name"] in self.drift_since else None}
                   for r in self.report]
            counts = {s: sum(1 for r in res if r["status"] == s) for s in ("in_sync", "drifted", "missing")}
            mttr = round(sum(self.recoveries) / len(self.recoveries), 2) if self.recoveries else None
            return {"resources": res, "summary": {**counts, "total": len(res), "mttr": mttr, "recoveries": len(self.recoveries)},
                    "events": list(self.events), "autoheal": self.autoheal, "last_tick": self.last_tick, "error": self.error}

    def start(self):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:
                    with self.lock:
                        self.error = str(e)
                time.sleep(self.interval)
        threading.Thread(target=loop, daemon=True).start()
