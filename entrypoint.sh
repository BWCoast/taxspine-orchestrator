#!/bin/sh
# entrypoint.sh — fix bind-mount ownership then drop to the app user.
#
# Problem: the Dockerfile pre-creates /data/* with app:app ownership, but a
# bind-mount at runtime replaces /data with the host directory (typically
# root-owned).  The non-root app user therefore cannot write to /data on first
# start, causing PermissionError: [Errno 13] Permission denied: '/data/prices'.
#
# Solution: this entrypoint runs as root, (re-)creates the required
# subdirectories, chowns them to app (UID 1000), then execs the CMD as app.
# gosu is used for a clean privilege drop with no residual root capabilities.
set -e

mkdir -p \
    /data/output \
    /data/tmp \
    /data/uploads \
    /data/state/dedup \
    /data/prices

chown -R app:app /data

exec gosu app "$@"
