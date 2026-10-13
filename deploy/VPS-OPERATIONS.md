# Capture It Ops: deploy and recovery

For the existing `/var/www/captureit-operational` installation, use Compose project
`captureit-operational` on every command so the existing `ops-data` volume is reused.
The new image includes the sales, inventory, manual-schedule and closing workflows.
Startup applies additive database migrations while retaining existing records.
See [workflow notes](../UPDATE-WORKFLOW-2026-10-12.md) for features and access.

## Before updating

Keep the current HTTPS preview gate until role checks, uploads and backup recovery
have been verified. Preserve `.env`; do not copy the example over an existing file.
Confirm `DEMO_MODE=false` and `COOKIE_SECURE=1` without printing credentials.

After merging the update into `main`, run this whole block as root. It also works
when the original VPS checkout has diverged from `origin/main`:

```bash
bash -se <<'BASH'
cd /var/www/captureit-operational
git fetch origin main
git show origin/main:deploy/update-vps.sh > /tmp/captureit-update.sh
OPS_SOURCE_ROOT="$PWD" bash /tmp/captureit-update.sh
BASH
```

The update script verifies the existing named data volume, builds from a separate
clean worktree, backs up the running service, and verifies the full running commit
SHA through `/api/health`. It does not reset the VPS branch. Use the printed release
directory for subsequent Compose commands. Continue editing `.env` in the original
source directory so future releases receive those settings. A failed fetch/build
stops deployment; do not continue manually by rebuilding the old checkout.

The backup script briefly stops only `captureit-ops`, copies its whole `/data`
directory, verifies the copied SQLite database, and restarts the same container.
An EXIT trap also attempts restart on error. The backup includes private uploads,
SQLite WAL files and a root-only copy of `.env`; do not publish it or paste it into
chat. It creates a timestamped rollback image tag and a `BACKUP_COMPLETE` marker
only on success. It neither stops CRM nor touches the shared PostgreSQL service.
Copy completed backups to another machine/disk; a backup on this VPS cannot
protect against loss of the VPS. Keep older backups until recovery is verified;
this script does not delete any existing backup or image.

## Server and resource settings

Docker runs `production.py` with Waitress 3.0.2: one process, four request threads,
64 connections, 15 MiB request bodies and 32 KiB headers. The existing API logic,
sessions, CSRF checks and administrator CLI remain in `server.py`. Background
sync/notification tasks start once in this process. Do not scale this service or
run another app process against the same SQLite volume.

Compose limits this container to 512 MiB RAM, one CPU and 64 tasks; Docker logs
rotate at 10 MiB, keeping three files. These are initial limits for the 2 GB VPS,
not a guarantee that 55 simultaneous users will fit. Check actual usage with
`docker stats --no-stream` and `free -h`, including during attendance photo uploads
and payroll export. CPU, storage and network are still shared with CRM. Review a
4 GB RAM upgrade if ordinary peak usage approaches the host/container limits.

Only port `127.0.0.1:8000` is published. `OPS_TRUSTED_PROXY=*` trusts the single
Nginx hop on this private path; do not publish port 8000 on all interfaces or add
untrusted containers to this network. Waitress keeps only the final trusted
forwarded address. Nginx should append `$remote_addr` with
`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`. For a non-Docker
deployment set `OPS_TRUSTED_PROXY` to the exact proxy peer or leave it unset.

The current Nginx upstream using HTTP/1.0 and `Connection ""` remains compatible;
no Nginx or CRM change is needed for this update. Waitress also supports HTTP/1.1.
`python3 server.py` remains a local development server; production uses
`python3 production.py` after installing `requirements-server.txt` (and
`requirements-push.txt` if push is needed).

## Acceptance checks

1. Check HTTPS login, logout and refresh. Create one test crew account and verify
   it cannot open administrator or other users' private records.
2. Use a test event to check assignments, attendance camera/GPS, photo retrieval,
   closing, and the intended finance permissions. Mark test data clearly.
3. Verify data survives `docker compose -p captureit-operational restart captureit-ops`.
4. Test restoring a completed backup into a separate directory/volume before
   relying on it. Do not point this rehearsal at `/data` of the running app.
5. Keep Google sync and push disabled until each is configured and tested with
   the intended external account/device. No external sync runs in the test suite.

After checking the administrator account works, remove the bootstrap password
from `.env` and recreate only this service. To inspect only whether it remains:

```bash
docker compose -p captureit-operational exec -T captureit-ops python3 -c 'import os; print("Bootstrap password still set:", bool(os.getenv("BOOTSTRAP_ADMIN_PASSWORD")))'
```

## Administrator account controls

In **Configure → Akun**, an Administrator can now use **Ganti password** for any
account. The new password must be at least 12 characters; saving it revokes every
existing session for that account. If the Administrator resets their own password,
the current browser session is also revoked and must log in again.

The **Hapus** button is intentionally conservative. The account must first be
nonaktif, the confirmation text `HAPUS` is required, and the account cannot have
attendance, assignment, payroll, KPI, performance-review, or other operational
history. Accounts with history should remain nonaktif so the historical audit and
exports keep the original user identity. The current account and the last active
Administrator can never be deleted.

For an emergency password reset from the VPS, use the same Compose project and do
not start a second application process against the SQLite volume:

```bash
cd /var/www/captureit-operational
docker compose -p captureit-operational exec -T captureit-ops \
  python3 server.py reset-password --email user@yourdomain.id
```

The command prompts for the new password and also revokes the account's existing
sessions. It does not change the CRM container or the shared PostgreSQL service.

## Stop the old ops service after acceptance

Confirm `ops.captureitphotobooth.id` still proxies to `127.0.0.1:8000` and the new
app works through HTTPS. These exact commands affect only the old app container:

```bash
docker update --restart=no eventops-manager
docker stop eventops-manager
```

Keep this old container, `/opt/stacks/ops-manager`, and PostgreSQL/its volume until
any needed historical records are accounted for. A future `docker compose up`
in the old stack can restore its Compose restart policy; do not run it casually.
Do not use global prune commands, `down -v`, or stop/restart the Docker daemon.

## Roll back this update

This update adds document relationships, serialized assets and schedule metadata.
The backup's `running-image.txt` and `captureit-ops-rollback:<timestamp>` retain
the previous image for an explicit Compose image override. Preserve the project
name and `/data` volume. Before running old code on migrated data, verify its
compatibility with any documents and asset movements created since deployment;
old code does not enforce the new asset and source-document rules. Retain a backup
of the current data before a recovery operation. Restoring an older database
discards records created afterward, so it is not an automatic rollback step.

`backup.py` is also included in the new image. Its SQLite online backup is
transactionally consistent, but files and database are copied at different times.
For a coordinated snapshot of records and uploads, use the stopped-service backup
above. Never raw-copy just `ops.sqlite3` while the application is writing.

## Verification in development

```bash
python3 -m pip install -r requirements-server.txt
python3 tests/run_wsgi_suite.py
```

This runs the backend tests against real Waitress HTTP connections in temporary
databases, including secure login cookies, CSRF, roles, private uploads, payroll,
backup restore and request transport checks. It is not a production load test.
Docker build and VPS acceptance are separate checks; this repo change does not
mean the application has already been updated on the VPS.
