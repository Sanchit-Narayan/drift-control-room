"""Flask API + Drift Control Room UI.  Routes: /status /diff /plan /reconcile /inject /autoheal /report /git /ci"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from drift.engine import load_desired
from drift.monitor import Monitor
from drift.report import to_markdown

ROOT = Path(__file__).resolve().parent.parent
DESIRED = os.environ.get("DESIRED_STATE", str(ROOT / "desired" / "state.yaml"))
REPO = os.environ.get("REPO_PATH", str(ROOT))


def _git(*args):
    r = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, timeout=4)
    return r.stdout.strip() if r.returncode == 0 else None


def git_info():
    try:
        line = _git("log", "-1", "--format=%h%x1f%an%x1f%ar%x1f%s")
        if not line:
            return {"available": False}
        h, author, when, subject = line.split("\x1f")
        dirty = bool(_git("status", "--porcelain", "--", "desired"))
        return {"available": True, "commit": h, "author": author, "when": when, "subject": subject, "dirty": dirty}
    except Exception:
        return {"available": False}


_ci_cache = {"t": 0, "data": None}


def ci_status():
    if time.time() - _ci_cache["t"] < 60 and _ci_cache["data"]:
        return _ci_cache["data"]
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        try:
            m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", _git("config", "--get", "remote.origin.url") or "")
            repo = m.group(1) if m else None
        except Exception:
            repo = None
    if not repo:
        data = {"available": False, "hint": "Set GITHUB_REPOSITORY=owner/repo to show the latest workflow run."}
    else:
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs?per_page=1",
                                         headers={"Accept": "application/vnd.github+json", "User-Agent": "drift-control-room"})
            if os.environ.get("GITHUB_TOKEN"):
                req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
            runs = json.load(urllib.request.urlopen(req, timeout=5)).get("workflow_runs") or []
            r = runs[0] if runs else None
            data = {"available": bool(r), "repo": repo, "hint": None if r else "No workflow runs yet."}
            if r:
                data.update(name=r["name"], status=r["status"], conclusion=r["conclusion"], url=r["html_url"],
                            sha=r["head_sha"][:7], when=r["updated_at"])
        except Exception as e:
            data = {"available": False, "repo": repo, "hint": f"GitHub API unreachable ({e.__class__.__name__})"}
    _ci_cache.update(t=time.time(), data=data)
    return data


def create_app(client=None, desired_path=DESIRED, start=True, bootstrap=None):
    app = Flask(__name__)
    if client is None:
        import docker
        client = docker.from_env()
    mon = Monitor(client, desired_path, interval=float(os.environ.get("POLL_INTERVAL", "2")))
    if bootstrap is None:
        bootstrap = os.environ.get("BOOTSTRAP") == "1"
    if bootstrap:
        mon.reconcile("bootstrap")
    mon.tick()
    if start:
        mon.start()
    app.monitor = mon

    def err(e, code=400):
        return jsonify(error=str(e)), code

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/status")
    def status():
        return jsonify({**mon.snapshot(), "preview": os.environ.get("PREVIEW_RESOURCE", "shop-api"),
                        "desired_commit": git_info().get("commit")})

    @app.get("/diff")
    def diff_view():
        return jsonify([{k: r[k] for k in ("name", "status", "rows", "changes")} for r in mon.snapshot()["resources"]])

    @app.post("/plan")
    def plan_route():
        return jsonify(actions=mon.plan())

    @app.post("/reconcile")
    def reconcile_route():
        return jsonify(actions=mon.reconcile("manual"))

    @app.post("/inject")
    def inject_route():
        b = request.get_json(force=True)
        try:
            return jsonify(message=mon.inject(b["name"], b["kind"], b.get("key"), b.get("value")))
        except Exception as e:
            return err(e)

    @app.post("/autoheal")
    def autoheal_route():
        mon.set_autoheal(request.get_json(force=True).get("enabled", False))
        return jsonify(autoheal=mon.autoheal)

    @app.get("/report")
    def report_route():
        snap, git = mon.snapshot(), git_info()
        fmt = request.args.get("format", "md")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if fmt == "json":
            body = json.dumps({"generated_at": stamp, "desired_commit": git.get("commit"), "summary": snap["summary"],
                               "resources": [{k: r[k] for k in ("name", "status", "changes")} for r in snap["resources"]],
                               "events": snap["events"]}, indent=2)
            mime, ext = "application/json", "json"
        else:
            body, mime, ext = to_markdown(snap["resources"], snap["events"], git.get("commit")), "text/markdown", "md"
        return Response(body, mimetype=mime, headers={"Content-Disposition": f"attachment; filename=drift-report-{stamp}.{ext}"})

    @app.get("/git")
    def git_route():
        return jsonify(git_info())

    @app.get("/ci")
    def ci_route():
        return jsonify(ci_status())

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, threaded=True)
