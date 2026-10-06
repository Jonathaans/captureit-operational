PRAGMA foreign_keys = ON;

-- Closing reports and their private photos are included in the database backup.
CREATE TABLE IF NOT EXISTS event_closing_reports (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('draft','submitted','revision','accepted')),
    version INTEGER NOT NULL DEFAULT 1,
    data_json TEXT NOT NULL DEFAULT '{}',
    context_json TEXT NOT NULL DEFAULT '{}',
    submitted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    submitted_at TEXT,
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    review_note TEXT NOT NULL DEFAULT '',
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_closing_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES event_closing_reports(event_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('setup','event')),
    caption TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL,
    content BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_closing_photos_event ON event_closing_photos(event_id);
CREATE TABLE IF NOT EXISTS event_closing_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES event_closing_reports(event_id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    employment_type TEXT NOT NULL DEFAULT 'inhouse' CHECK (employment_type IN ('freelancer','inhouse')),
    department TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Personal crew/PIC records are separated from login/ACL data. Files stay in a
-- private storage directory and are served only through authenticated routes.
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    phone TEXT NOT NULL DEFAULT '',
    profile_photo_path TEXT,
    profile_photo_mime TEXT,
    ktp_front_path TEXT,
    ktp_front_mime TEXT,
    ktp_back_path TEXT,
    ktp_back_mime TEXT,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- Role ACL defaults remain in code until an Administrator customizes a role.
-- This flag lets an intentionally empty/custom permission set differ from defaults.
CREATE TABLE IF NOT EXISTS role_acl_settings (
    role_id INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    customized INTEGER NOT NULL DEFAULT 0 CHECK (customized IN (0,1)),
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One persisted scheduler state per app database. The daily worker resumes
-- from the last attempt after restarts and avoids importing duplicate events.
CREATE TABLE IF NOT EXISTS google_calendar_sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_attempt_at TEXT,
    last_success_at TEXT,
    status TEXT NOT NULL DEFAULT 'never' CHECK (status IN ('never','running','success','error')),
    trigger TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    queued_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO google_calendar_sync_state(id) VALUES (1);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CRM project codes map to one event. Events can be scheduled with an
-- explicit temporary operational code until CRM issues the final code.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT NOT NULL UNIQUE,
    operational_code TEXT,
    project_code_is_temporary INTEGER NOT NULL DEFAULT 0 CHECK (project_code_is_temporary IN (0,1)),
    is_full_day_manual INTEGER NOT NULL DEFAULT 0 CHECK (is_full_day_manual IN (0,1)),
    google_event_id TEXT UNIQUE,
    title TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'Photobooth',
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    is_full_day INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','completed','cancelled')),
    coordinator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    completed_at TEXT,
    completed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    calendar_updated_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Calendar events wait here until an administrator confirms the CRM project code
-- or an Event Coordinator schedules them with a temporary operational code.
CREATE TABLE IF NOT EXISTS calendar_code_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_event_id TEXT NOT NULL UNIQUE,
    suggested_project_code TEXT,
    title TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'Photobooth',
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    is_full_day INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','cancelled')),
    calendar_updated_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Fleet records survive event removal; event-specific logistics do not.
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    plate_number TEXT NOT NULL DEFAULT '',
    ownership TEXT NOT NULL DEFAULT 'internal' CHECK (ownership IN ('internal','rental')),
    vendor_name TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicle_plate ON vehicles(plate_number) WHERE plate_number!='';

CREATE TABLE IF NOT EXISTS event_operations (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    transport_modes TEXT NOT NULL DEFAULT '[]',
    courier_name TEXT NOT NULL DEFAULT '',
    courier_note TEXT NOT NULL DEFAULT '',
    other_transport TEXT NOT NULL DEFAULT '',
    transport_note TEXT NOT NULL DEFAULT '',
    vehicle_id INTEGER REFERENCES vehicles(id) ON DELETE RESTRICT,
    driver_name TEXT NOT NULL DEFAULT '',
    loading_date TEXT NOT NULL DEFAULT '',
    loading_time TEXT NOT NULL DEFAULT '',
    vehicle_return_at TEXT NOT NULL DEFAULT '',
    client_name TEXT NOT NULL DEFAULT '',
    client_phone TEXT NOT NULL DEFAULT '',
    service_type TEXT NOT NULL DEFAULT '',
    equipment_setup TEXT NOT NULL DEFAULT '',
    sales_name TEXT NOT NULL DEFAULT '',
    ribbon_start INTEGER CHECK (ribbon_start IS NULL OR ribbon_start>=0),
    ribbon_end INTEGER CHECK (ribbon_end IS NULL OR ribbon_end>=0),
    notes TEXT NOT NULL DEFAULT '',
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (ribbon_start IS NULL OR ribbon_end IS NULL OR ribbon_end<=ribbon_start)
);
CREATE INDEX IF NOT EXISTS idx_event_operations_vehicle ON event_operations(vehicle_id);

CREATE TABLE IF NOT EXISTS notification_reads (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    notification_key TEXT NOT NULL,
    read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id,notification_key)
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS fee_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE RESTRICT,
    base_fee_rupiah INTEGER NOT NULL DEFAULT 0 CHECK (base_fee_rupiah >= 0),
    effective_from TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(position_id, effective_from)
);

-- Individual base fee history. Payroll resolves the user-specific rate by event date.
CREATE TABLE IF NOT EXISTS user_fee_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    base_fee_rupiah INTEGER NOT NULL DEFAULT 0 CHECK (base_fee_rupiah >= 0),
    effective_from TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, effective_from)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Immutable record of every known compensation change; caches may still be updated.
CREATE TABLE IF NOT EXISTS compensation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN ('fee','salary')),
    old_amount_rupiah INTEGER CHECK(old_amount_rupiah IS NULL OR old_amount_rupiah>=0),
    new_amount_rupiah INTEGER NOT NULL CHECK(new_amount_rupiah>=0),
    effective_from TEXT,
    reason TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    actor_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('change','migration'))
);
CREATE INDEX IF NOT EXISTS idx_compensation_history_user ON compensation_history(user_id,kind,effective_from,id);
CREATE TRIGGER IF NOT EXISTS compensation_history_no_update BEFORE UPDATE ON compensation_history
BEGIN SELECT RAISE(ABORT,'Riwayat gaji tidak dapat ditimpa; catat perubahan baru.'); END;
CREATE TRIGGER IF NOT EXISTS compensation_history_no_delete BEFORE DELETE ON compensation_history
BEGIN SELECT RAISE(ABORT,'Riwayat gaji tidak dapat dihapus.'); END;

CREATE TABLE IF NOT EXISTS event_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assignment_type TEXT NOT NULL CHECK (assignment_type IN ('crew','pic')),
    position_id INTEGER REFERENCES positions(id) ON DELETE SET NULL,
    base_fee_snapshot_rupiah INTEGER CHECK (base_fee_snapshot_rupiah IS NULL OR base_fee_snapshot_rupiah >= 0),
    travel_buffer_minutes INTEGER NOT NULL DEFAULT 60 CHECK (travel_buffer_minutes BETWEEN 0 AND 720),
    travel_ack_signature TEXT NOT NULL DEFAULT '',
    schedule_ack_signature TEXT NOT NULL DEFAULT '',
    schedule_acknowledged_at TEXT,
    schedule_acknowledged_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id, user_id, assignment_type)
);
CREATE INDEX IF NOT EXISTS idx_assignment_user ON event_assignments(user_id,event_id);


-- Multi-day events: which WIB dates each Crew/PIC assignment covers, and attendance per day.
-- An assignment without rows covers every day of its event (single-day and legacy data).
CREATE TABLE IF NOT EXISTS event_assignment_days (
    assignment_id INTEGER NOT NULL REFERENCES event_assignments(id) ON DELETE CASCADE,
    work_date TEXT NOT NULL,
    PRIMARY KEY (assignment_id, work_date)
);
CREATE TABLE IF NOT EXISTS attendance_days (
    assignment_id INTEGER NOT NULL REFERENCES event_assignments(id) ON DELETE CASCADE,
    work_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started','checked_in','checked_out','absent')),
    check_in_at TEXT,
    check_out_at TEXT,
    check_in_photo_path TEXT,
    check_out_photo_path TEXT,
    check_in_latitude REAL,
    check_in_longitude REAL,
    check_in_accuracy_m REAL,
    check_out_latitude REAL,
    check_out_longitude REAL,
    check_out_accuracy_m REAL,
    note TEXT,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (assignment_id, work_date)
);

CREATE TABLE IF NOT EXISTS event_communications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'not_created' CHECK (status IN ('not_created','group_created','invites_sent')),
    group_link TEXT NOT NULL DEFAULT '',
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL UNIQUE REFERENCES event_assignments(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started','checked_in','checked_out','absent')),
    check_in_at TEXT,
    check_out_at TEXT,
    check_in_photo_path TEXT,
    check_out_photo_path TEXT,
    check_in_latitude REAL,
    check_in_longitude REAL,
    check_in_accuracy_m REAL,
    check_out_latitude REAL,
    check_out_longitude REAL,
    check_out_accuracy_m REAL,
    note TEXT,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inhouse_attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    work_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started','checked_in','checked_out')),
    check_in_at TEXT,
    check_out_at TEXT,
    check_in_photo_path TEXT,
    check_out_photo_path TEXT,
    check_in_latitude REAL,
    check_in_longitude REAL,
    check_in_accuracy_m REAL,
    check_out_latitude REAL,
    check_out_longitude REAL,
    check_out_accuracy_m REAL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, work_date)
);

CREATE TABLE IF NOT EXISTS inhouse_salary_rates (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
    monthly_salary_rupiah INTEGER NOT NULL DEFAULT 0 CHECK (monthly_salary_rupiah >= 0),
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inhouse_payroll_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    pay_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','transferred')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(period_start, period_end),
    CHECK (period_start <= period_end)
);

CREATE TABLE IF NOT EXISTS inhouse_payroll_payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES inhouse_payroll_batches(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    user_name_snapshot TEXT NOT NULL,
    email_snapshot TEXT NOT NULL,
    department_snapshot TEXT NOT NULL DEFAULT '',
    monthly_salary_rupiah INTEGER NOT NULL DEFAULT 0 CHECK (monthly_salary_rupiah >= 0),
    attendance_days INTEGER NOT NULL DEFAULT 0 CHECK (attendance_days >= 0),
    completed_days INTEGER NOT NULL DEFAULT 0 CHECK (completed_days >= 0),
    open_days INTEGER NOT NULL DEFAULT 0 CHECK (open_days >= 0),
    allowance_rupiah INTEGER NOT NULL DEFAULT 0 CHECK (allowance_rupiah >= 0),
    deduction_rupiah INTEGER NOT NULL DEFAULT 0 CHECK (deduction_rupiah >= 0),
    total_rupiah INTEGER NOT NULL DEFAULT 0 CHECK (total_rupiah >= 0),
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','transferred')),
    transferred_at TEXT,
    transfer_reference TEXT NOT NULL DEFAULT '',
    recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_id, user_id)
);

-- One monthly salary payment per In-house account and calendar month.
CREATE TABLE IF NOT EXISTS inhouse_monthly_salary_claims (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    payroll_month TEXT NOT NULL,
    batch_id INTEGER NOT NULL REFERENCES inhouse_payroll_batches(id) ON DELETE RESTRICT,
    payout_id INTEGER NOT NULL REFERENCES inhouse_payroll_payouts(id) ON DELETE RESTRICT,
    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id,payroll_month)
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    extra_fee_rupiah INTEGER NOT NULL DEFAULT 0 CHECK (extra_fee_rupiah >= 0),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS assignment_skills (
    assignment_id INTEGER NOT NULL REFERENCES event_assignments(id) ON DELETE CASCADE,
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE RESTRICT,
    extra_fee_rupiah INTEGER NOT NULL DEFAULT 0 CHECK (extra_fee_rupiah >= 0),
    skill_name_snapshot TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (assignment_id, skill_id)
);

-- One allowance workflow per event. Nominal remains in CRM and is not duplicated here.
CREATE TABLE IF NOT EXISTS cash_advances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
    requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'not_submitted' CHECK (status IN ('not_submitted','submitted','approved','rejected','transferred')),
    requested_at TEXT,
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    transfer_recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    transferred_at TEXT,
    note TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cash_advance_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    uploaded_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    original_filename TEXT NOT NULL,
    storage_path TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL CHECK (mime_type IN ('application/pdf','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.ms-excel')),
    file_size INTEGER NOT NULL CHECK (file_size > 0 AND file_size <= 10485760),
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- A simple event-level readiness/return board, not an inventory ledger.
CREATE TABLE IF NOT EXISTS warehouse_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'needs_prep' CHECK (status IN ('needs_prep','preparing','ready','waiting_return','returned','issue')),
    prepared_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    prepared_at TEXT,
    returned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    returned_at TEXT,
    note TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS design_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'Desain event',
    status TEXT NOT NULL DEFAULT 'brief_needed' CHECK (status IN ('brief_needed','in_progress','client_review','revision','approved')),
    assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    due_at TEXT,
    brief_text TEXT NOT NULL DEFAULT '',
    brief_version INTEGER NOT NULL DEFAULT 0,
    brief_sent_at TEXT,
    brief_sent_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    final_url TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id, title)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dedupe_key TEXT NOT NULL,
    category TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
    event_title TEXT NOT NULL DEFAULT '',
    event_at TEXT NOT NULL DEFAULT '',
    tab TEXT NOT NULL DEFAULT 'overview',
    target_kind TEXT NOT NULL DEFAULT 'event',
    target_id INTEGER,
    required_permission TEXT NOT NULL DEFAULT '',
    urgent INTEGER NOT NULL DEFAULT 0,
    live_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE(user_id,dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_notification_inbox ON notifications(user_id,id DESC);
CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    categories TEXT NOT NULL,
    quiet_start TEXT NOT NULL DEFAULT '',
    quiet_end TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS push_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_hash TEXT NOT NULL,
    token TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    device_key TEXT NOT NULL,
    platform TEXT NOT NULL CHECK(platform IN ('web','android')),
    label TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_devices(user_id,active);
CREATE TABLE IF NOT EXISTS push_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    device_id INTEGER NOT NULL REFERENCES push_devices(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','failed','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    lease_until TEXT,
    provider_id TEXT,
    last_error TEXT,
    UNIQUE(notification_id,device_id)
);
CREATE INDEX IF NOT EXISTS idx_push_due ON push_deliveries(status,next_attempt_at);

CREATE TABLE IF NOT EXISTS design_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES design_tasks(id) ON DELETE CASCADE,
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payroll_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','exported')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Snapshot amounts per assignment so later fee edits cannot rewrite prior payroll.
CREATE TABLE IF NOT EXISTS payroll_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES payroll_batches(id) ON DELETE CASCADE,
    assignment_id INTEGER NOT NULL REFERENCES event_assignments(id) ON DELETE RESTRICT,
    base_fee_rupiah INTEGER NOT NULL DEFAULT 0,
    meal_rupiah INTEGER NOT NULL DEFAULT 0,
    skill_fee_rupiah INTEGER NOT NULL DEFAULT 0,
    adjustment_rupiah INTEGER NOT NULL DEFAULT 0,
    total_rupiah INTEGER NOT NULL DEFAULT 0,
    UNIQUE(batch_id, assignment_id)
);

-- Global claim prevents a checked-out event assignment entering a second batch.
CREATE TABLE IF NOT EXISTS payroll_assignment_claims (
    assignment_id INTEGER PRIMARY KEY REFERENCES event_assignments(id) ON DELETE RESTRICT,
    batch_id INTEGER NOT NULL REFERENCES payroll_batches(id) ON DELETE RESTRICT,
    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- A Freelancer payslip is released only after Finance records the weekly transfer.
CREATE TABLE IF NOT EXISTS payroll_batch_transfers (
    batch_id INTEGER PRIMARY KEY REFERENCES payroll_batches(id) ON DELETE CASCADE,
    pay_date TEXT NOT NULL,
    transfer_reference TEXT NOT NULL DEFAULT '',
    recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kpi_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    subject_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    category TEXT NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    note TEXT NOT NULL DEFAULT '',
    review_period TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Event Coordinator evaluations are attached to the actual event assignment.
CREATE TABLE IF NOT EXISTS event_performance_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL UNIQUE REFERENCES event_assignments(id) ON DELETE CASCADE,
    reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    score_percent INTEGER NOT NULL CHECK (score_percent BETWEEN 0 AND 100),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Criterion labels are saved with the review so historical score meaning stays stable.
CREATE TABLE IF NOT EXISTS event_performance_items (
    review_id INTEGER NOT NULL REFERENCES event_performance_reviews(id) ON DELETE CASCADE,
    criterion_code TEXT NOT NULL,
    criterion_name TEXT NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    PRIMARY KEY (review_id, criterion_code)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_starts_at ON events(starts_at);
CREATE INDEX IF NOT EXISTS idx_calendar_code_queue_starts ON calendar_code_queue(starts_at);
CREATE INDEX IF NOT EXISTS idx_assignments_user ON event_assignments(user_id, event_id);
CREATE INDEX IF NOT EXISTS idx_design_status ON design_tasks(status, due_at);
CREATE INDEX IF NOT EXISTS idx_cash_status ON cash_advances(status);
CREATE INDEX IF NOT EXISTS idx_cash_advance_documents_event ON cash_advance_documents(event_id, uploaded_at);
CREATE INDEX IF NOT EXISTS idx_warehouse_status ON warehouse_checks(status);
CREATE INDEX IF NOT EXISTS idx_kpi_period ON kpi_reviews(review_period);
CREATE INDEX IF NOT EXISTS idx_user_fee_rate_date ON user_fee_rates(user_id, effective_from);
CREATE INDEX IF NOT EXISTS idx_performance_assignment ON event_performance_reviews(assignment_id);
CREATE INDEX IF NOT EXISTS idx_inhouse_attendance_user_date ON inhouse_attendance(user_id, work_date);
CREATE INDEX IF NOT EXISTS idx_inhouse_payroll_user_status ON inhouse_payroll_payouts(user_id, status);

-- Failed sign-ins, kept briefly so repeated guessing is slowed down (see Handler.login).
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    ip TEXT NOT NULL,
    attempted_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email, attempted_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip, attempted_at);
