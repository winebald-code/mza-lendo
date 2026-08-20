#!/usr/bin/env python3
"""
Container entrypoint for Bacaan.

Two jobs, in order:

1. Make the data directory writable. Railway (and most platforms) mount a
   volume owned by root. If the image dropped to an unprivileged user with a
   USER directive at build time, the first write to that volume fails with a
   permissions error and the container dies before it can say why.

2. Drop privileges anyway. Schema creation, seeding and every request run as an
   unprivileged user; only the ownership fix needs root, and it happens before
   any application code is imported.

Written in Python rather than shell because Python is guaranteed present in
this image, whereas setpriv/runuser/gosu depend on which base you are on.
"""
import grp
import os
import pwd
import sys

APP_USER = os.environ.get("APP_USER", "bacaan")
DATA_DIR = os.environ.get("DATA_DIR", "/data")


def log(message: str) -> None:
    print(f"[entrypoint] {message}", flush=True)


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        log("no command given")
        raise SystemExit(2)

    if os.geteuid() != 0:
        # Already unprivileged (a plain `docker run --user`, or a platform that
        # starts us as non-root). Nothing to hand over.
        os.execvp(argv[0], argv)

    try:
        entry = pwd.getpwnam(APP_USER)
    except KeyError:
        log(f"user {APP_USER!r} does not exist in this image")
        raise SystemExit(1)

    os.makedirs(DATA_DIR, exist_ok=True)

    # Only touch ownership when it is actually wrong, so a large existing
    # volume is not walked on every boot.
    try:
        if os.stat(DATA_DIR).st_uid != entry.pw_uid:
            log(f"taking ownership of {DATA_DIR} for {APP_USER}")
            for root, dirs, files in os.walk(DATA_DIR):
                os.chown(root, entry.pw_uid, entry.pw_gid)
                for name in dirs + files:
                    os.chown(os.path.join(root, name), entry.pw_uid, entry.pw_gid)
    except OSError as exc:
        log(f"could not take ownership of {DATA_DIR}: {exc}")
        log("refusing to start against a database directory that cannot be written")
        raise SystemExit(1)

    # Order matters: groups, then gid, then uid. After setuid there is no way
    # back, so anything needing root must already be done.
    try:
        os.initgroups(APP_USER, entry.pw_gid)
        os.setgid(entry.pw_gid)
        os.setuid(entry.pw_uid)
    except OSError as exc:
        log(f"could not drop privileges: {exc}")
        raise SystemExit(1)

    os.environ.setdefault("HOME", entry.pw_dir)
    log(f"running as {APP_USER} (uid {os.getuid()})")
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
