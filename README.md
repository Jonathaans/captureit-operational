# Capture It Operations

A self-hosted operational workspace for photobooth events. This MVP includes role-scoped dashboards, event assignments, Freelancer event attendance and payroll, separate In-house attendance and monthly payroll with individual payslips, cash-advance status, warehouse readiness/return status, a design kanban, KPI reviews, per-account fee rates, and read-only Google Calendar sync.

Latest update: **Persistent notifications, design briefs, and device push (5 October 2026)**. See `UPDATE-NOTIFIKASI-PUSH.md` for recipients, preferences, migration, Firebase/HTTPS activation, and Android integration. The notification inbox works without Firebase; real device push requires configuration and staging verification. Automated coverage is 113 backend + 55 JavaScript tests, with FCM transport simulated.

Compensation history and advisory same-day scheduling remain available as described in `UPDATE-HISTORI-GAJI-JADWAL.md`. Scheduling warns on shared event days and overlapping event hours; the coordinator may confirm either warning. Loading and travel buffers do not restrict Crew/PIC scheduling.

Closing drafts remain recoverable as described in `UPDATE-JADWAL-DRAFT.md`. The scheduling section in that older document is superseded by this update. `QA-PRA-VPS.md` retains the earlier QA/deployment checklist; the deployment gates remain unresolved. This build is for staging validation, not a production security certification.

## Start a local demo

Requires Python 3.10 or newer. The local demo and inbox use the Python standard library. Sending device push additionally requires `python3 -m pip install -r requirements-push.txt` and Firebase configuration. The Docker image installs the push dependency.

```bash
cd captureit-ops
python3 server.py
```

Open `http://127.0.0.1:8000`.

Demo password: `demo1234`

| Role | Email |
|---|---|
| Administrator | `admin@captureit.local` |
| Head Operations | `head.ops@captureit.local` |
| Event Coordinator | `coordinator@captureit.local` |
| Head Finance | `head.finance@captureit.local` |
| Finance | `finance@captureit.local` |
| Admin Finance | `admin.finance@captureit.local` |
| Warehouse Head | `warehouse.head@captureit.local` |
| Warehouse Staff | `warehouse@captureit.local` |
| Team Design | `design@captureit.local` |
| Crew | `crew@captureit.local` |
| PIC Event | `pic@captureit.local` |

The app creates `ops.sqlite3` beside `server.py`. Each account's seeded base fee is Rp0 because the agreed real rates have not been entered yet; set them individually in Tarif & Skill. The seed includes the provided rules for meal allowance (Rp35.000 full day, Rp25.000 non-full day) and Hologram (Rp50.000).

## Workflows included

- **Calendar and event:** new Google Calendar events appear in the code queue. Event Coordinator can enter a CRM code when scheduling, or leave it blank to get a generated `OPS-YYMMDD-XXXXXX` operational code for a last-minute event; Admin can also confirm/pair the CRM code. The coordinator can choose Full day or Non-full day while scheduling and change it later from event details. Manual duration choices are retained during future Calendar syncs and drive the meal allowance. The temporary code stays linked as `operational_code`; Admin can attach the CRM code later, and the original operational code remains stored in the event and audit history. `events.project_code` is unique and `google_event_id` stays linked for read-only updates from Calendar. Event Coordinators can export their own coordinated schedule to Excel or CSV for a selected date range, using the supplied 21-column Events format. Vehicle names can be typed directly in the event logistics form; new names are saved automatically and existing vehicle identities are reused for scheduling checks. See `UPDATE-2026-09-29.md` for notifications, vehicle mapping, export fields, and upgrade steps.
- **Crew and PIC:** Event Coordinators select the account, event role (Crew/PIC), team position, and any paid special skills together when scheduling. The role is selected per event and can differ from the account's ACL role; PIC-only actions such as submitting uang jalan are granted only for an event where that user is assigned as PIC. The selected account's base fee and each skill's name and extra fee are snapshotted on the assignment. Each Crew/PIC dashboard and event-detail endpoint only exposes events assigned to that account. Staff take a face photo through the device camera at check-in and check-out; the server records both timestamps. This captures a photo; it does not perform face recognition.
- **Profile and Crew/PIC directory:** Every user can set their display name, WhatsApp number, profile photo, and upload front/back KTP images from **Profil Saya**. Administrator and Head Operations can view KTP files; Event Coordinators see the Crew/PIC directory and document-completion status without opening the documents. Profile/KTP files are stored outside the public static directory and served through authenticated, permission-checked endpoints. Keep the private upload folder with the database when backing up or moving a local install.
- **WhatsApp coordination:** the coordinator records whether a group was created and invitations were sent, can save an invitation link, and can copy an event message template. Group creation itself remains manual.
- **Cash advance:** PIC submits; Finance/Head Finance approves or rejects, then records the transfer. No amount is duplicated because the nominal remains in CRM. After transfer, the assigned PIC can upload expense accountability documents in PDF, XLS, or XLSX (maximum 10 MB each). Finance, Head Finance, and Admin Finance can view and download the documents from **Uang Jalan** or the event detail. Files are stored in a private directory with permission-checked download routes.
- **Warehouse:** one event-level status moves through `Perlu disiapkan → Sedang disiapkan → Siap → Menunggu kembali → Sudah dikembalikan`; it does not track individual stock items.
- **Design:** cards can be moved between stages by drag and drop. Status changes are audited.
- **Payroll:** Finance can choose a date range, defaulting to the current Saturday–Friday pay week, and export checked-out assignments with account, check-in/out time, role, base fee, selected skills, meal allowance, and total. The primary Excel export highlights skill and meal components in yellow; CSV is also available. Fee dasar is taken from the per-account tariff when the assignment is scheduled, and skill fees are captured in that same action. Uang makan is Rp35.000 for full-day events or Rp25.000 otherwise, and is counted only after check-out. Admin, Head Operations, and Head Finance can set per-account rates and manage skills. Exported batches retain amount snapshots if rates change later.
- **Freelancer payslips:** Crew and PIC have a **Slip Gaji Saya** page for their own event honor. Finance exports the weekly payroll, then records the transfer and optional bank reference; that publishes a modern event-by-event payslip only for Crew/PIC included in the exported batch. The page has a year filter and print/save PDF. The app records transfer confirmation; it does not send money to the bank.
- **In-house attendance and payroll:** In-house staff check in and out with a camera photo and server timestamp, separately from Freelancer event attendance. Finance, Head Finance, and Admin Finance can see the attendance register for every In-house account and export any inclusive date range as Excel or CSV. Finance and Head Finance manage monthly payroll; Admin Finance can view the payroll but cannot edit salary or record transfers. Each account has its own monthly base salary. The default payroll period is the previous calendar month, with payment date on the 25th; Finance can change the date range and pay date, and enter allowances or deductions manually. Attendance is shown for review and does not automatically deduct salary. Finance records each transfer individually; that account's payslip then appears in its **Slip Gaji Saya** page, with a print/save as PDF option. This MVP records the transfer and reference; it does not initiate bank transfers.
- **Closing reports:** The assigned PIC records actual times, service delivery, prints and failures, optional ribbon rolls and optional frame/lenticular/magnet/keychain quantities, incidents/resolution, open follow-up, and optional private photos. Draft → submit → coordinator review → accept or revision. Accepted reports supply ribbon totals and closing notes to the existing 21-column event export. See `UPDATE-BAHAN-TRANSPORTASI.md` for the latest form and transport changes, and `UPDATE-PENUTUPAN-EVENT.md` for the report workflow.
- **Performance per event:** After accepting the PIC closing report, the assigned Event Coordinator marks the event clear; this opens an evaluation for each assigned Crew/PIC. Previously completed events retain their existing evaluation flow. Four criteria (quality, punctuality, teamwork, and SOP/equipment care) are scored 1–5, and the coordinator can add a note. The server stores the percentage and criteria as snapshots tied to that exact assignment. Every evaluation appears in that person’s Performance dashboard with per-event history and improvement areas; below 75% is red and 75% or above is green. Submitted evaluations cannot be edited or submitted twice. Head Operations evaluates Event Coordinators and Warehouse through the separate KPI form; Finance evaluates Admin Finance. Crew/PIC can read only their own history.
- **Accounts and configuration:** The **Configure** menu is Administrator-only and centralizes theme colors, logo/favicon uploads, individual user accounts, role assignment, and role permission settings. Passwords must be at least 12 characters. Administrator permissions stay locked to protect recovery access. At least one active Administrator is kept to prevent locking everyone out.

## Data relationships

Compensation changes are append-only in `compensation_history`. **Tarif & Skill → Riwayat** shows Crew/PIC fee history to users with `fees.manage`; **Payroll In-house → Riwayat** shows monthly salary history to users with `inhouse_payroll.manage`. Each record retains previous/new amounts, delta, effective date, optional reason, actor ID/name and timestamp. Same-date corrections add records, while an identical retry creates no duplicate. Future changes appear in history and become active on their effective date. New payroll drafts use the salary effective at the **period start**, without automatic proration; existing drafts change only when explicitly saved again, and transferred payouts remain fixed.

Migration imports known fee records and the last known In-house salary balance once. Unknown salary effective dates remain unknown, clearly labeled as migration balances. They preserve the previous fallback behavior for periods without a known dated salary; Finance should review those periods. Historical corrections absent from the old database are not reconstructed.

Schedule confirmations retain the coordinator, time, signature and warning snapshot in the assignment/audit data. The check runs again inside the save transaction: a changed schedule displays an updated warning before the coordinator confirms again. The same person can work two events on the same day, including overlapping hours, after that explicit confirmation.

The schema is normalized and uses foreign keys throughout. A CRM code identifies exactly one event, so `events.project_code` is unique rather than being copied into a separate project table.

```text
users ──< user_roles >── roles ──< role_permissions >── permissions
roles ──0..1 role_acl_settings ── Administrator updates
users ──< app_settings (updated_by)
users ──0..1 user_profiles
events ──< event_assignments >── users
event_assignments ──1 attendance
event_assignments ──< assignment_skills >── skills
event_assignments ──0..1 event_performance_reviews ──< event_performance_items
events ──1 cash_advances
events ──< cash_advance_documents >── users (uploader)
events ──1 warehouse_checks
events ──1 event_communications
events ──< design_tasks ──< design_comments
calendar_code_queue ── Admin confirms CRM code ──> events
users ──< user_fee_rates
event_assignments ──< payroll_lines >── payroll_batches
payroll_batches ──0..1 payroll_batch_transfers
users ──0..1 inhouse_salary_rates
users ──< inhouse_attendance
users ──< inhouse_payroll_payouts >── inhouse_payroll_batches
users ──< kpi_reviews >── users
users ──< event_performance_reviews (reviewer)
users ──< audit_logs
```

See `schema.sql` for table definitions, uniqueness rules, check constraints, and indexes. `event_assignments.base_fee_snapshot_rupiah` records the selected account's base fee at scheduling; `assignment_skills` stores both the selected skill name and its price as snapshots. Payroll rows then retain the paid amounts for each exported batch. Default role permissions come from `ROLE_PERMISSIONS` in `server.py`; when an Administrator customizes a role in Configure, `role_acl_settings` marks it as customized and its `role_permissions` become the runtime ACL. Route checks enforce the same permissions as the menus. Admin Finance has no access to the Tarif & Skill menu or fee data.

In-house attendance is unique per account and work date. An In-house payroll batch is unique per date range, and each account has at most one payout per batch. Payouts snapshot salary, attendance counts, allowances, deductions, account and department names; changing a salary later does not change an existing draft until Finance saves it again, and never changes a transferred payout or issued payslip. `employment_type` separates Freelancer and In-house accounts; newly created Crew/PIC accounts default to Freelancer and other roles default to In-house.

When upgrading an earlier MVP database, initialization migrates the old position-based fee history to each account using that account's latest assigned position, then adds the assignment fee snapshot columns. Existing assignments without a base-fee snapshot continue to use the account rate effective on their event date; new assignments capture the rate at scheduling. Review migrated individual fees before exporting payroll.

## Configure Google Calendar read-only sync

### Local trial

1. From the `captureit-ops` folder, copy the template and open it in Notepad (PowerShell):

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   On macOS/Linux, use `cp .env.example .env` and edit `.env` with your preferred text editor. Keep `DEMO_MODE=true` for local testing.

2. In [Google Cloud Console](https://console.cloud.google.com/), create/select a project and enable **Google Calendar API**. Configure Google Auth Platform. For a personal Gmail account, use **External**, set the publishing status to **Testing**, and add the Google account that owns or can view the test calendar under **Test users**. If using a managed Google Workspace account, **Internal** is limited to users in that Workspace organization.

3. Create an OAuth client ID with application type **Web application**. Add this authorized redirect URI:

   ```text
   https://developers.google.com/oauthplayground
   ```

4. Open [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/). In its settings panel, select **Use your own OAuth credentials** and enter the OAuth client ID and secret you just created. In Step 1, request only this scope, authorize with the Google account that can access the calendar, then exchange the authorization code for tokens in Step 2. Copy the returned **refresh token** into `.env`. The Playground warns that refresh tokens from its default credentials are revoked after 24 hours; using your own client avoids that Playground-specific revocation.

   ```text
   https://www.googleapis.com/auth/calendar.events.readonly
   ```

5. Fill these values in `.env`. Do not add spaces around `=` and do not send the secrets in chat or commit `.env` to source control:

   ```dotenv
   GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-client-secret
   GOOGLE_REFRESH_TOKEN=your-refresh-token
   GOOGLE_CALENDAR_ID=primary
   GOOGLE_SYNC_INTERVAL_HOURS=24
   ```

   `primary` means the main calendar of the Google account that authorized access. For a dedicated trial calendar, use its Calendar ID from Google Calendar **Settings → Integrate calendar** instead. The account that authorized OAuth must be able to access that calendar.

6. Save `.env`, stop the running app with `Ctrl+C`, then run `py server.py` again on Windows or `python3 server.py` on macOS/Linux. Once the app starts with valid Google credentials, it runs an initial sync and then automatically syncs every 24 hours, measured from the last attempt. Manual **Sync Calendar** remains available. Set `GOOGLE_SYNC_INTERVAL_HOURS=0` to disable automatic sync. The app reads events from now through the next 366 days. The event page shows when the last sync ran and whether it succeeded. The queue and schedule views default to a month containing upcoming events; use **Semua bulan** to see the full range.

   A newly imported Calendar item first appears in the Admin code-confirmation queue; it does not enter the operational schedule until Admin enters/confirms its CRM project code. Keep one unique CRM code per event. This connector reads Calendar; it does not create or edit Calendar events. For an External app left in Testing, Google says refresh tokens expire after seven days for scopes beyond basic profile identity, so re-authorize if the trial stops working after that period.

If only a quick end-to-end test is needed, make a separate Google Calendar named for the trial, set its ID in `.env`, add one future test event there, then Sync. That avoids importing unrelated events from a primary calendar.

### Google OAuth scope

The app needs only this read-only permission:

```text
https://www.googleapis.com/auth/calendar.events.readonly
```

The sync recognizes a suggested code in the event title or description formatted like `PROJECT_CODE: CI-OPS-26001` (also accepts `KODE PROJECT:`). New Calendar events wait in the queue until Admin pairs the code from CRM or an Event Coordinator schedules one with a temporary operational code. Admin can later replace a temporary code with its CRM code from the event list. Later syncs update event details without overwriting a CRM code, temporary operational code, or a duration set manually by an Event Coordinator. Calendar entries marked as all-day or lasting at least eight hours are suggested as Full day; the coordinator can correct that choice in the queue or event details.

The current implementation uses Calendar API event list calls and OAuth refresh-token exchange. The Google credentials are not included in this package; configure them on the VPS before using Sync Calendar.

## Empty operational data while keeping accounts

Stop the local server first, then run:

```powershell
py reset_operational_data.py
```

On macOS/Linux use `python3 reset_operational_data.py`. The script reports the rows it plans to clear and only continues after you type `RESET DATA OPERASIONAL`. It makes a timestamped SQLite backup beside the database before deleting anything. It clears Calendar intake, events and their assignments/attendance/design/warehouse/cash-advance records, KPI history, payroll exports, audit logs, and login sessions. It keeps users, their ACL roles, tariff history, positions, and skills. Users will need to sign in again because sessions are cleared. Since users remain, demo sample events are not automatically seeded again on the next start.

For Docker on the VPS, stop the service and run the reset utility against the persistent volume, then start the app again:

```bash
docker compose stop captureit-ops
docker compose run --rm --no-deps captureit-ops python3 reset_operational_data.py
docker compose up -d captureit-ops
```

## Self-host on a VPS with Docker Compose

Read `QA-PRA-VPS.md` first for the tested scope, unresolved deployment gates,
manual acceptance checklist, and rollback procedure. This release is approved
for staging QA only, not a production security certification.

1. Copy the `captureit-ops` folder to the VPS.
2. For a fresh installation, create `.env` from `.env.production.example`. Preserve an existing `.env` when upgrading.
3. Set `DEMO_MODE=false`, `HOST=0.0.0.0`, `COOKIE_SECURE=1`, and configure a unique bootstrap administrator email and a password of at least 12 characters. Add the Google Calendar credentials if sync is needed.
4. Start the service:

   ```bash
   docker compose up -d --build
   ```

5. Put Nginx or Caddy in front of `127.0.0.1:8000` and enable HTTPS. Set `COOKIE_SECURE=1` only when HTTPS is active.
6. After the first start has created the administrator, remove `BOOTSTRAP_ADMIN_PASSWORD` from `.env` and run `docker compose up -d --force-recreate` so the old password is no longer in the container environment.

`deploy/nginx.conf.example` provides an HTTPS reverse-proxy and login rate-limit
starting point. Replace the example domain/certificate paths and validate it
with `nginx -t` on the VPS. It has not been exercised against a live VPS here.
Keep the service behind your organization's access policy while completing
the staging checklist; do not expose port 8000 directly.

The database lives in the Docker volume `ops-data`. Back it up regularly and test restoring a backup. Attendance photos are saved in private directories next to the SQLite database (inside `/data` in Docker), never in the public static folder. Profile documents stay in `private-profile-uploads`; cash advance files stay in `cash-advance-documents`; branding images stay in `branding-assets`. Back up all these directories with the database. Browser camera and GPS access require HTTPS on the VPS; `localhost` works for local development. Event and In-house check-in/check-out each allow one state transition only. A camera photo and a fresh browser GPS fix with reported accuracy of 500 m or better are required. The app stores the server receipt time and GPS coordinates/accuracy; the photo watermark is a convenience record, not cryptographic proof. Browser location can be spoofed, so this is not a face match or anti-fraud geofence. Keep the VPS clock synchronized with NTP and keep the app private behind HTTPS and your organization's access policy.

The Google Calendar auto-sync worker runs inside the application process and persists its last attempt in SQLite, so no separate cron job is needed. Run only one application instance for a given SQLite database; multiple instances would each start a scheduler.

Accounts can be created in **Configure → Akun** by an Administrator. To add an account from the server command line instead:

```bash
docker compose exec captureit-ops python3 server.py create-user --email crew1@yourdomain.id --name "Nama Crew" --role crew
```

The command prompts for a password. Valid role codes are shown by `python3 server.py create-user --help`. Reset a password with `python3 server.py reset-password --email crew1@yourdomain.id`.

## Database note

This initial package uses SQLite with foreign keys and WAL mode to keep a single VPS deployment easy to operate. It is suitable for a single app instance with modest write concurrency. Before running multiple app instances or expecting sustained concurrent writes, move the same relational model to PostgreSQL and use a multi-worker application server.

## Important deployment boundary

Freelancer assignments are globally claimed by the first exported payroll batch, so overlapping exports omit assignments already in a batch; a unique database key also prevents two claims for the same assignment. In-house payout transfer is limited to one transfer per account and calendar month, guarded by a unique claim and a transaction lock. Historical audit warnings are printed at startup if an assignment appears in multiple old batches or an In-house account-month appears in multiple transferred periods; investigate these records and reconcile real bank transfers before go-live. A wrong export currently needs finance review before continuing, because reserved claims are intentionally not silently reassigned.

This is a tested MVP, not a certified 100% production rollout. Before go-live, configure a real VPS/domain and HTTPS, disable demo mode, provision actual accounts and approved fee rates, configure and test Google Calendar credentials, synchronize the VPS clock, verify database and private-file backups by restoring them, check historical payroll duplicates against bank records, and run a production smoke test with real role permissions. Python's documentation explicitly does not recommend `http.server` for production. Keep this build restricted to staging/internal evaluation; replacing the HTTP serving layer with a production-grade application server and validating the deployment is a separate production gate, not something a reverse proxy alone guarantees. SQLite remains single-instance; assess PostgreSQL before multi-instance deployment or sustained write concurrency. Never deploy with `DEMO_MODE=true` or the demo password. Turning demo mode off does not delete demo accounts already present in an old database.
