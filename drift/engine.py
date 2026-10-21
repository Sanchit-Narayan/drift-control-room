"""Drift engine: compare desired state (YAML in Git) with actual state (Docker).

Shared by the CLI, the CI workflow and the Flask API, so there is exactly one
implementation of "what is drift" and "how do we fix it".
"""
from __future__ import annotations

from pathlib import Path

import yaml

LABEL = "drift.managed"      # only containers carrying this label are ever touched
NAME_LABEL = "drift.name"
PREFIX = "drift-"
IN_SYNC, DRIFTED, MISSING = "in_sync", "drifted", "missing"


# ---------------------------------------------------------------- desired ----
def normalize_spec(spec: dict) -> dict:
    return {
        "image": str(spec["image"]),
        "ports": {str(k): int(v) for k, v in (spec.get("ports") or {}).items()},
        "env": {str(k): str(v) for k, v in (spec.get("env") or {}).items()},
        "running": bool(spec.get("running", True)),
    }


def load_desired(path) -> dict:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return {n: normalize_spec(s) for n, s in (data.get("resources") or {}).items()}


# ----------------------------------------------------------------- actual ----
def _image_env(client, ref: str) -> dict:
    try:
        env = client.images.get(ref).attrs["Config"].get("Env") or []
    except Exception:
        env = []
    return dict(e.split("=", 1) for e in env if "=" in e)


def read_actual(client) -> dict:
    """Read the real world from Docker. Image-baked env vars are filtered out."""
    actual = {}
    for c in client.containers.list(all=True, filters={"label": f"{LABEL}=true"}):
        name = c.labels.get(NAME_LABEL)
        if not name:
            continue
        cfg = c.attrs["Config"]
        base = _image_env(client, cfg["Image"])
        env_all = dict(e.split("=", 1) for e in (cfg.get("Env") or []) if "=" in e)
        ports = {}
        for cport, binds in (c.attrs["HostConfig"].get("PortBindings") or {}).items():
            if binds:
                ports[cport] = int(binds[0]["HostPort"])
        actual[name] = {
            "image": cfg["Image"],
            "ports": ports,
            "env": {k: v for k, v in env_all.items() if base.get(k) != v},
            "env_all": env_all,
            "running": c.status == "running",
            "container": c.name,
        }
    return actual


def effective_env(have: dict, want_env: dict) -> dict:
    """User-set env, plus declared keys even when they equal the image default."""
    return {**have["env"], **{k: have["env_all"][k] for k in want_env if k in have["env_all"]}}


# ------------------------------------------------------------------- diff ----
def _flatten(spec):
    if spec is None:
        return {}
    flat = {"image": spec["image"], "running": spec["running"]}
    for k, v in sorted(spec["ports"].items()):
        flat[f"ports.{k}"] = v
    for k, v in sorted(spec["env"].items()):
        flat[f"env.{k}"] = v
    return flat


def _rows(want, have):
    fw, fh = _flatten(want), _flatten(have)
    keys = list(fw) + [k for k in fh if k not in fw]
    return [{"key": k, "desired": fw.get(k), "actual": fh.get(k), "same": fw.get(k) == fh.get(k)} for k in keys]


def diff(desired: dict, actual: dict) -> list:
    report = []
    for name, want in desired.items():
        have = actual.get(name)
        changes, pub = [], None
        if have is None:
            status = MISSING
            changes.append({"field": "container", "desired": "present", "actual": "absent"})
        else:
            env = effective_env(have, want["env"])
            pub = {"image": have["image"], "ports": have["ports"], "env": env, "running": have["running"]}
            if pub["image"] != want["image"]:
                changes.append({"field": "image", "desired": want["image"], "actual": pub["image"]})
            for p in sorted(set(want["ports"]) | set(pub["ports"])):
                if want["ports"].get(p) != pub["ports"].get(p):
                    changes.append({"field": f"ports.{p}", "desired": want["ports"].get(p), "actual": pub["ports"].get(p)})
            for k in sorted(set(want["env"]) | set(env)):
                if want["env"].get(k) != env.get(k):
                    changes.append({"field": f"env.{k}", "desired": want["env"].get(k), "actual": env.get(k)})
            if pub["running"] != want["running"]:
                changes.append({"field": "running", "desired": want["running"], "actual": pub["running"]})
            status = DRIFTED if changes else IN_SYNC
        report.append({"name": name, "status": status, "changes": changes,
                       "desired": want, "actual": pub, "rows": _rows(want, pub)})
    return report


# -------------------------------------------------------------- reconcile ----
def plan(report: list) -> list:
    actions = []
    for r in report:
        if r["status"] == IN_SYNC:
            continue
        fields = {c["field"] for c in r["changes"]}
        if r["status"] == MISSING:
            act = "create"
        elif fields == {"running"}:
            act = "start" if r["desired"]["running"] else "stop"
        else:
            act = "recreate"
        why = [f"{c['field']}: {c['actual']} -> {c['desired']}" for c in r["changes"]]
        actions.append({"name": r["name"], "action": act, "reason": why})
    return actions


def _find(client, name):
    return client.containers.list(all=True, filters={"label": f"{NAME_LABEL}={name}"})


def _remove(client, name):
    for c in _find(client, name):
        try:
            c.stop(timeout=2)
        except Exception:
            pass
        c.remove(force=True)


def _ensure_image(client, image):
    try:
        client.images.get(image)
    except Exception:
        client.images.pull(image)


def run_container(client, name, spec):
    _ensure_image(client, spec["image"])
    c = client.containers.run(
        spec["image"], name=PREFIX + name, detach=True,
        ports=spec["ports"] or None, environment=spec["env"] or None,
        labels={LABEL: "true", NAME_LABEL: name},
    )
    if not spec["running"]:
        c.stop(timeout=2)
    return c


def _execute(client, name, spec, action):
    if action in ("start", "stop"):
        found = _find(client, name)
        if found:
            found[0].start() if action == "start" else found[0].stop(timeout=2)
            return
        action = "create"
    if action == "recreate":
        _remove(client, name)
    run_container(client, name, spec)


def reconcile(client, desired: dict, dry_run: bool = False) -> list:
    actions = plan(diff(desired, read_actual(client)))
    if not dry_run:
        for a in actions:
            _execute(client, a["name"], desired[a["name"]], a["action"])
    return actions


# ----------------------------------------------------------------- inject ----
def inject(client, desired, name, kind, key=None, value=None) -> str:
    """Create drift on purpose (demo + CI self-test). kind: stop|delete|port|env"""
    if kind == "delete":
        _remove(client, name)
        return f"{name}: container deleted by hand"
    have = read_actual(client).get(name)
    if have is None:
        raise ValueError(f"{name} is not running; nothing to drift")
    if kind == "stop":
        _find(client, name)[0].stop(timeout=2)
        return f"{name}: container stopped by hand"
    spec = {"image": have["image"], "ports": dict(have["ports"]),
            "env": effective_env(have, desired[name]["env"]), "running": have["running"]}
    if kind == "port":
        cport, old = next(iter(spec["ports"].items()), (None, None))
        if cport is None:
            raise ValueError(f"{name} publishes no ports")
        spec["ports"][cport] = int(value) if value else old + 1000
        msg = f"{name}: host port {old} changed to {spec['ports'][cport]} by hand"
    elif kind == "env":
        if not key:
            raise ValueError("env injection needs KEY=VALUE")
        spec["env"][key] = str(value)
        msg = f"{name}: env {key} set to {value!r} by hand"
    else:
        raise ValueError(f"unknown drift kind: {kind}")
    _remove(client, name)
    run_container(client, name, spec)
    return msg
