"""Drift report rendering (Markdown). JSON is just the dict."""
from __future__ import annotations

import time


def _ts(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def to_markdown(report, events=(), commit=None):
    drifted = [r for r in report if r["status"] != "in_sync"]
    out = ["# Infrastructure drift report", "",
           f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
           f"- Desired-state commit: `{commit or 'n/a'}`",
           f"- Resources: {len(report)} total, **{len(drifted)} drifted/missing**", "",
           "| Resource | Status | Changes |", "|---|---|---|"]
    for r in report:
        ch = "<br>".join(f"`{c['field']}`: actual `{c['actual']}` vs desired `{c['desired']}`" for c in r["changes"]) or "none"
        out.append(f"| {r['name']} | {r['status']} | {ch} |")
    if events:
        out += ["", "## Timeline", ""]
        out += [f"- `{_ts(e['ts'])}` **{e['kind']}** {e.get('resource') or ''}: {e['message']}" for e in events]
    return "\n".join(out) + "\n"
