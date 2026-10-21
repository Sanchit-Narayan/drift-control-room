#!/usr/bin/env bash
# Terminal version of the demo (no browser needed). Run after `docker compose up --build`.
set -u
run() { echo; echo "\$ $*"; "$@"; }
run python -m drift.cli check
run python -m drift.cli inject web stop
run python -m drift.cli inject shop-api env APP_ENV=debug
run python -m drift.cli check
run python -m drift.cli plan
run python -m drift.cli reconcile
run python -m drift.cli check
