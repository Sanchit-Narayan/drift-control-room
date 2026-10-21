"""'Shop API': the example workload. Its page changes visibly when its config drifts."""
import os
import socket
import time

from flask import Flask, jsonify

app = Flask(__name__)
STARTED = time.strftime("%H:%M:%S")

PAGE = """<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=3>
<title>Shop API</title>
<body style="margin:0;font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a">
<div style="background:{bg};color:#fff;padding:10px 16px;font-weight:700;letter-spacing:.04em">{banner}</div>
<div style="padding:16px">
<h2 style="margin:0 0 8px">Shop API <span style="font-weight:400;color:#64748b;font-size:14px">v1.0</span></h2>
<table style="font-size:14px;border-collapse:collapse">
<tr><td style="padding:2px 14px 2px 0;color:#64748b">APP_ENV</td><td><b>{env}</b></td></tr>
<tr><td style="color:#64748b">New checkout</td><td><b style="color:{fc}">{flag}</b></td></tr>
<tr><td style="color:#64748b">LOG_LEVEL</td><td><b>{log}</b></td></tr>
<tr><td style="color:#64748b">Container</td><td>{host} (since {started})</td></tr>
</table></div></body>"""


@app.get("/")
def home():
    env = os.environ.get("APP_ENV", "production")
    flag = os.environ.get("FEATURE_NEW_CHECKOUT", "off")
    debug = env != "production"
    return PAGE.format(
        bg="#dc2626" if debug else "#059669",
        banner="DEBUG MODE: verbose errors and stack traces are exposed" if debug else "PRODUCTION: healthy",
        env=env, flag=flag.upper(), fc="#dc2626" if flag == "on" else "#059669",
        log=os.environ.get("LOG_LEVEL", "info"), host=socket.gethostname(), started=STARTED)


@app.get("/health")
def health():
    return jsonify(status="ok", env=os.environ.get("APP_ENV"), host=socket.gethostname())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
