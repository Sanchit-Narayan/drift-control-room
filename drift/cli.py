"""CLI: python -m drift.cli {check,plan,reconcile,inject,report}"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import docker

from . import engine
from .report import to_markdown

DEFAULT = str(Path(__file__).resolve().parent.parent / "desired" / "state.yaml")
G, R, Y, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _print(report):
    for r in report:
        mark = {"in_sync": f"{G}✔ in sync{X}", "drifted": f"{R}✖ drifted{X}", "missing": f"{Y}? missing{X}"}[r["status"]]
        print(f"{mark:<22} {r['name']}")
        for c in r["changes"]:
            print(f"{D}      {c['field']}: desired={c['desired']!r} actual={c['actual']!r}{X}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="drift")
    p.add_argument("--state", default=os.environ.get("DESIRED_STATE", DEFAULT))
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="exit 1 if drift exists (CI-friendly)")
    c.add_argument("--json", action="store_true")
    c.add_argument("--report", help="also write a Markdown report to this path")
    sub.add_parser("plan", help="dry run")
    sub.add_parser("reconcile", help="fix drift").add_argument("--dry-run", action="store_true")
    i = sub.add_parser("inject", help="create drift on purpose")
    i.add_argument("name"); i.add_argument("kind", choices=["stop", "delete", "port", "env"])
    i.add_argument("value", nargs="?", help="env: KEY=VALUE   port: new host port")
    sub.add_parser("report").add_argument("--format", choices=["md", "json"], default="md")
    a = p.parse_args(argv)

    client, desired = docker.from_env(), engine.load_desired(a.state)

    if a.cmd in ("check", "report"):
        report = engine.diff(desired, engine.read_actual(client))
        drifted = any(r["status"] != "in_sync" for r in report)
        if a.cmd == "report":
            print(to_markdown(report) if a.format == "md" else json.dumps(report, indent=2))
            return 0
        if a.report:
            Path(a.report).write_text(to_markdown(report))
        if a.json:
            print(json.dumps(report, indent=2))
        else:
            _print(report)
            print(f"\n{R}DRIFT DETECTED{X}" if drifted else f"\n{G}No drift. Running state matches Git.{X}")
        return 1 if drifted else 0

    if a.cmd in ("plan", "reconcile"):
        dry = a.cmd == "plan" or a.dry_run
        actions = engine.reconcile(client, desired, dry_run=dry)
        if not actions:
            print(f"{G}Nothing to do.{X}")
        for x in actions:
            print(f"{Y if dry else G}{'would ' if dry else ''}{x['action']:<9}{X} {x['name']}  {D}{'; '.join(x['reason'])}{X}")
        return 0

    if a.cmd == "inject":
        key, value = None, a.value
        if a.kind == "env":
            key, _, value = (a.value or "").partition("=")
        print(engine.inject(client, desired, a.name, a.kind, key, value))
        return 0


if __name__ == "__main__":
    sys.exit(main())
