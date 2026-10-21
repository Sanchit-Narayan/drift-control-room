from pathlib import Path

import pytest

from drift import engine
from drift.monitor import Monitor
from tests.fakes import FakeClient

STATE = Path(__file__).resolve().parent.parent / "desired" / "state.yaml"


@pytest.fixture
def world():
    client, desired = FakeClient(), engine.load_desired(STATE)
    engine.reconcile(client, desired)  # bootstrap
    return client, desired


def status(client, desired):
    return {r["name"]: r["status"] for r in engine.diff(desired, engine.read_actual(client))}


def test_empty_world_is_all_missing_then_bootstraps():
    client, desired = FakeClient(), engine.load_desired(STATE)
    assert set(status(client, desired).values()) == {"missing"}
    assert {a["action"] for a in engine.reconcile(client, desired)} == {"create"}
    assert set(status(client, desired).values()) == {"in_sync"}


def test_clean_state_has_no_drift_and_no_actions(world):
    client, desired = world
    assert engine.reconcile(client, desired, dry_run=True) == []


@pytest.mark.parametrize("kind,key,value,field", [
    ("stop", None, None, "running"),
    ("port", None, None, "ports.5000/tcp"),
    ("env", "APP_ENV", "debug", "env.APP_ENV"),
    ("env", "SECRET_FLAG", "1", "env.SECRET_FLAG"),   # extra var nobody declared
])
def test_injected_drift_is_detected_and_fixed(world, kind, key, value, field):
    client, desired = world
    engine.inject(client, desired, "shop-api", kind, key, value)
    r = next(r for r in engine.diff(desired, engine.read_actual(client)) if r["name"] == "shop-api")
    assert r["status"] == "drifted" and field in {c["field"] for c in r["changes"]}
    engine.reconcile(client, desired)
    assert set(status(client, desired).values()) == {"in_sync"}


def test_deleted_container_is_missing_and_recreated(world):
    client, desired = world
    engine.inject(client, desired, "web", "delete")
    assert status(client, desired)["web"] == "missing"
    engine.reconcile(client, desired)
    assert status(client, desired)["web"] == "in_sync"


def test_stop_only_needs_a_start_not_a_recreate(world):
    client, desired = world
    engine.inject(client, desired, "cache", "stop")
    assert [a["action"] for a in engine.plan(engine.diff(desired, engine.read_actual(client)))] == ["start"]


def test_dry_run_changes_nothing(world):
    client, desired = world
    engine.inject(client, desired, "web", "stop")
    assert engine.reconcile(client, desired, dry_run=True)
    assert status(client, desired)["web"] == "drifted"


def test_unmanaged_containers_are_never_touched(world):
    client, desired = world
    client.containers.run("postgres:16", name="someone-elses-db", labels={})
    engine.reconcile(client, desired)
    assert "someone-elses-db" in client.containers.store


def test_monitor_timeline_and_ttr(world):
    client, desired = world
    m = Monitor(client, STATE)
    m.tick()
    m.inject("web", "stop")
    kinds = [e["kind"] for e in m.events]
    assert "injected" in kinds and "detected" in kinds
    m.reconcile()
    rec = [e for e in m.events if e["kind"] == "reconciled"]
    assert rec and "ttr" in rec[-1]
    assert m.snapshot()["summary"]["drifted"] == 0 and m.snapshot()["summary"]["mttr"] is not None


def test_autoheal_fixes_drift_on_next_tick(world):
    client, desired = world
    m = Monitor(client, STATE)
    m.set_autoheal(True)
    m.inject("shop-api", "env", "APP_ENV", "debug")   # inject() ticks, auto-heal reacts immediately
    assert m.snapshot()["summary"]["drifted"] == 0
    assert any(e["message"].startswith("auto-heal") for e in m.events)


def test_api_end_to_end(world):
    from dashboard.server import create_app
    client, _ = world
    app = create_app(client=client, desired_path=STATE, start=False, bootstrap=False)
    c = app.test_client()
    assert c.get("/status").json["summary"]["in_sync"] == 3
    assert c.post("/inject", json={"name": "web", "kind": "stop"}).status_code == 200
    assert c.get("/status").json["summary"]["drifted"] == 1
    assert len(c.post("/plan").json["actions"]) == 1
    c.post("/reconcile")
    assert c.get("/status").json["summary"]["drifted"] == 0
    assert "Infrastructure drift report" in c.get("/report?format=md").get_data(as_text=True)
    assert c.get("/report?format=json").json["summary"]["total"] == 3
    assert c.post("/inject", json={"name": "nope", "kind": "stop"}).status_code == 400
    assert b"Drift Control Room" in c.get("/").data
