const state = {
  data: null,
  page: "dashboard",
  search: "",
  payrollStart: currentPayrollWeek().start,
  payrollEnd: currentPayrollWeek().end,
  inhousePayrollStart: previousInhousePayrollMonth().start,
  inhousePayrollEnd: previousInhousePayrollMonth().end,
  inhousePayrollPayDate: previousInhousePayrollMonth().payDate,
  payslipYear: "",
  inhouseDate: null,
  inhouseExportStart: `${localDateISO().slice(0,7)}-01`,
  inhouseExportEnd: localDateISO(),
  drawer: null,
  drawerTab: "overview",
  score: 5,
  rateUserId: "",
  kpiSubjectId: "",
  performanceSubjectId: "",
  designDragId: null,
  attendanceCapture: null,
  attendanceStream: null,
  configureTab: "appearance",
  configureRoleCode: "event_coordinator",
  eventMonth: null,
  queueMonth: null,
  eventStatus: "all",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function initials(name) {
  return String(name || "CI").split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase();
}

function idr(value) {
  return new Intl.NumberFormat("id-ID", { style: "currency", currency: "IDR", maximumFractionDigits: 0 }).format(Number(value || 0));
}

function datePart(value, options = { day: "2-digit", month: "short" }) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : new Intl.DateTimeFormat("id-ID", { ...options, timeZone: "Asia/Jakarta" }).format(date);
}

function wibDateISO(value = new Date()) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Jakarta", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function localDateISO() { return wibDateISO(); }

function currentPayrollWeek() {
  const start = new Date(`${localDateISO()}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 1) % 7));
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 6);
  const toISO = (value) => value.toISOString().slice(0, 10);
  return { start: toISO(start), end: toISO(end) };
}

function previousInhousePayrollMonth() {
  const today = new Date(`${localDateISO()}T00:00:00Z`);
  const end = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 0));
  const start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), 1));
  const payDate = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 25));
  const toISO = (value) => value.toISOString().slice(0, 10);
  return { start: toISO(start), end: toISO(end), payDate: toISO(payDate) };
}

function timePart(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : new Intl.DateTimeFormat("id-ID", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" }).format(date);
}

function dateLong(value) {
  return datePart(value, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

function relativeLabel(value) {
  const dayDelta = (new Date(wibDateISO(value)) - new Date(localDateISO())) / 86400000;
  if (dayDelta === 0) return "Hari ini";
  if (dayDelta === 1) return "Besok";
  if (dayDelta === -1) return "Kemarin";
  return datePart(value, { day: "numeric", month: "short" });
}

function cookieValue(name) {
  const prefix = `${name}=`;
  const item = document.cookie.split(";").map((v) => v.trim()).find((v) => v.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (method !== "GET" && method !== "HEAD") {
    headers.set("Content-Type", "application/json");
    const csrf = cookieValue("ops_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(path, { ...options, method, headers, credentials: "same-origin" });
  if (options.download) {
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || "Tidak dapat mengekspor file.");
    }
    return response.blob();
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error=new Error(result.error || result.message || "Permintaan gagal.");
    error.status=response.status;error.details=result;throw error;
  }
  return result;
}

function toast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  $("#toast-root").append(node);
  setTimeout(() => node.remove(), 3600);
}

function has(permission) {
  return Boolean(state.data?.user?.permissions?.includes(permission));
}

function showLogin(error = "") {
  stopNotifications();
  $("#app-shell").hidden = true;
  $("#login-screen").hidden = false;
  $("#login-error").hidden = !error;
  $("#login-error").textContent = error;
}

async function loadApp() {
  try {
    state.data = await api("/api/bootstrap");
    applyBranding(state.data.branding);
    $("#login-screen").hidden = true;
    $("#app-shell").hidden = false;
    renderShell();
    renderPage();
    startNotifications();
    startPushSession();
    resumeNoticeLink();
  } catch (error) {
    closeCompensationHistory();
    await clearPushSession().catch(()=>{});
    stopNotifications();
    state.data = null;
    showLogin();
  }
}

function applyBranding(branding) {
  if (!branding) return;
  const colors = branding.colors || {};
  const root = document.documentElement;
  if (colors.primary) { root.style.setProperty("--purple", colors.primary); }
  if (colors.accent) { root.style.setProperty("--gold", colors.accent); }
  if (colors.sidebar) {
    root.style.setProperty("--purple-dark", colors.sidebar);
    root.style.setProperty("--purple-deep", colors.sidebar);
  }
  if (colors.background) root.style.setProperty("--canvas", colors.background);
  const logoUrl = branding.logo_url || "/logo.png";
  $$(".auth-logo, .auth-mobile-brand img, .sidebar-brand img").forEach((img) => { img.src = logoUrl; });
  const icon = $("#app-favicon");
  if (icon) icon.href = branding.favicon_url || "/favicon.ico";
  const themeColor = $("meta[name='theme-color']");
  if (themeColor) themeColor.content = colors.sidebar || "#21123b";
}

function navGroups() {
  const p = state.data.user.permissions;
  const groups = [
    { label: "RINGKASAN", items: [{ id: "dashboard", label: "Dashboard", icon: "⌂" }] },
    { label: "OPERASIONAL", items: [] },
    { label: "KEUANGAN OPERASIONAL", items: [] },
    { label: "PENGELOLAAN", items: [] },
  ];
  if (p.includes("attendance.inhouse.self") || p.includes("attendance.inhouse.read_all")) groups[1].items.push({ id: "inhouse-attendance", label: "Absensi In-house", icon: "◷" });
  if (p.includes("events.read_all") || p.includes("events.read_own")) groups[1].items.push({ id: "events", label: "Jadwal Event", icon: "▦", count: state.data.dashboard.total_upcoming });
  if (p.includes("events.project_code.manage") || p.includes("events.assign")) groups[1].items.push({ id: "crm-queue", label: "Kode CRM", icon: "◫", count: (state.data.calendar_code_queue || []).length || null });
  if (p.includes("events.assign")) groups[1].items.push({ id: "vehicles", label: "Transportasi Event", icon: "▱" });
  if (p.includes("design.read")) groups[1].items.push({ id: "design", label: "Board Design", icon: "✳", count: state.data.dashboard.design_active || null });
  if (p.includes("warehouse.update")) groups[1].items.push({ id: "warehouse", label: "Kesiapan Alat", icon: "▣", count: state.data.dashboard.warehouse_pending || null });
  if (p.includes("advances.read") || p.includes("advances.request")) groups[2].items.push({ id: "advances", label: "Uang Jalan", icon: "↗", count: state.data.dashboard.needs_advance || null });
  if (p.includes("payroll.view")) groups[2].items.push({ id: "payroll", label: "Penggajian", icon: "▤" });
  if (p.includes("inhouse_payroll.view")) groups[2].items.push({ id: "inhouse-payroll", label: "Payroll In-house", icon: "▤" });
  if ((p.includes("inhouse_payroll.read_own") && state.data.user.employment_type === "inhouse") || (p.includes("payroll.read_own") && state.data.user.employment_type === "freelancer")) groups[2].items.push({ id: "inhouse-payslips", label: "Slip Gaji Saya", icon: "▧" });
  if (p.includes("fees.manage")) groups[3].items.push({ id: "rates", label: "Tarif & Skill", icon: "◈" });
  if (p.includes("kpi.read") || p.includes("kpi.read_own")) groups[3].items.push({ id: "kpi", label: "KPI & Evaluasi", icon: "☆" });
  if (p.includes("staff.directory.read")) groups[3].items.push({ id: "staff", label: "Data Crew/PIC", icon: "♧" });
  if (p.includes("app.configure")) groups[3].items.push({ id: "configure", label: "Configure", icon: "⚙" });
  if (p.includes("profile.read_own")) groups[3].items.push({ id: "profile", label: "Profil Saya", icon: "◎" });
  return groups.filter((group) => group.items.length);
}

function renderShell() {
  renderNotificationBadge();
  const user = state.data.user;
  $("#user-name").textContent = user.full_name;
  $("#user-role").textContent = user.role_names.join(" · ");
  const avatar = $("#user-avatar");
  avatar.innerHTML = state.data.profile?.profile_photo_url
    ? `<img src="${escapeHtml(state.data.profile.profile_photo_url)}" alt="Foto ${escapeHtml(user.full_name)}">`
    : initials(user.full_name);
  const nav = navGroups();
  $("#navigation").innerHTML = nav.map((group) => `
    <div class="nav-group">
      <div class="nav-group-label">${escapeHtml(group.label)}</div>
      ${group.items.map((item) => `<button class="nav-link ${state.page === item.id ? "active" : ""}" data-action="navigate" data-page="${item.id}">
        <span class="nav-icon">${item.icon}</span><span>${escapeHtml(item.label)}</span>${item.count ? `<span class="nav-count">${item.count}</span>` : ""}
      </button>`).join("")}
    </div>`).join("");
  $("#sync-button").hidden = !has("google.sync");
  $("#global-search").value = state.search;
}

function updatePageTitle(title) {
  $("#page-title").textContent = title;
}

function setPage(page) {
  state.page = page;
  $("#sidebar").classList.remove("open");
  renderShell();
  renderPage();
}

function renderPage() {
  const host = $("#page-content");
  switch (state.page) {
    case "vehicles": updatePageTitle("Transportasi Event"); host.innerHTML = renderVehiclesPage(); break;
    case "events": updatePageTitle("Jadwal Event"); host.innerHTML = renderEventsPage(); break;
    case "crm-queue": updatePageTitle("Kode CRM"); host.innerHTML = renderCrmQueuePage(); break;
    case "design": updatePageTitle("Board Design"); host.innerHTML = renderDesignPage(); break;
    case "warehouse": updatePageTitle("Kesiapan Alat"); host.innerHTML = renderWarehousePage(); break;
    case "advances": updatePageTitle("Uang Jalan"); host.innerHTML = renderAdvancesPage(); break;
    case "payroll": updatePageTitle("Penggajian"); renderPayrollPage().then((html) => { if (state.page === "payroll") host.innerHTML = html; }); break;
    case "inhouse-payroll": updatePageTitle("Payroll In-house"); host.innerHTML = `<div class="empty-state">Memuat payroll In-house…</div>`; renderInhousePayrollPage().then((html) => { if (state.page === "inhouse-payroll") host.innerHTML = html; }); break;
    case "inhouse-payslips": updatePageTitle("Slip Gaji Saya"); host.innerHTML = `<div class="empty-state">Memuat slip gaji…</div>`; renderInhousePayslipsPage().then((html) => { if (state.page === "inhouse-payslips") host.innerHTML = html; }); break;
    case "inhouse-attendance": updatePageTitle("Absensi In-house"); host.innerHTML = `<div class="empty-state">Memuat absensi…</div>`; renderInhouseAttendancePage().then((html) => { if (state.page === "inhouse-attendance") host.innerHTML = html; }); break;
    case "rates": updatePageTitle("Tarif & Skill"); host.innerHTML = renderRatesPage(); break;
    case "kpi": updatePageTitle("KPI & Evaluasi"); host.innerHTML = renderKpiPage(); break;
    case "staff": updatePageTitle("Data Crew/PIC"); host.innerHTML = renderStaffDirectoryPage(); break;
    case "profile": updatePageTitle("Profil Saya"); host.innerHTML = renderProfilePage(); break;
    case "configure": updatePageTitle("Configure"); host.innerHTML = renderConfigurePage(); break;
    default: updatePageTitle("Dashboard"); host.innerHTML = renderDashboard(); break;
  }
}

function pageHead(eyebrow, title, description, actions = "") {
  return `<div class="page-head"><div><div class="eyebrow-dark">${escapeHtml(eyebrow)}</div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></div><div class="page-head-actions">${actions}</div></div>`;
}

function renderGoogleSyncStatus() {
  const sync = state.data.google_sync || {};
  const hours = Number(sync.interval_hours ?? 24);
  if (!state.data.google_configured) {
    return `<div class="calendar-sync-status is-muted">Auto-sync belum aktif: lengkapi kredensial Google Calendar di file .env.</div>`;
  }
  if (!hours) return `<div class="calendar-sync-status is-muted">Auto-sync dinonaktifkan. Sinkronisasi manual tetap tersedia.</div>`;
  const error = sync.status === "error";
  const stateLabel = error ? "Sinkronisasi terakhir gagal" : sync.status === "running" ? "Sinkronisasi sedang berjalan" : sync.status === "success" ? "Sinkronisasi terakhir berhasil" : "Menunggu sinkronisasi pertama";
  const lastRun = sync.last_attempt_at ? ` · ${new Intl.DateTimeFormat("id-ID", { dateStyle: "medium", timeStyle: "short" }).format(new Date(sync.last_attempt_at))}` : "";
  const detail = sync.message ? `<span>${escapeHtml(sync.message)}</span>` : "";
  return `<div class="calendar-sync-status ${error ? "is-error" : ""}"><b>${escapeHtml(stateLabel)}${lastRun}</b><small>Otomatis setiap ${hours} jam.</small>${detail}</div>`;
}

function badge(label, tone = "gray") {
  return `<span class="badge badge-${tone}">${escapeHtml(label)}</span>`;
}

const advanceMeta = {
  not_submitted: ["Belum diajukan", "gray"], submitted: ["Diajukan", "gold"], approved: ["Disetujui", "green"],
  rejected: ["Ditolak", "red"], transferred: ["Sudah ditransfer", "blue"],
};
const warehouseMeta = {
  needs_prep: ["Perlu disiapkan", "gold"], preparing: ["Sedang disiapkan", "purple"], ready: ["Siap", "green"],
  waiting_return: ["Menunggu kembali", "blue"], returned: ["Sudah dikembalikan", "green"], issue: ["Ada kendala", "red"],
};
const designMeta = {
  brief_needed: ["Brief dibutuhkan", "gray"], in_progress: ["Dikerjakan", "purple"], client_review: ["Menunggu review", "gold"],
  revision: ["Revisi", "red"], approved: ["Clear", "green"],
};
const groupMeta = { not_created: ["Belum dibuat", "gray"], group_created: ["Grup dibuat", "gold"], invites_sent: ["Undangan terkirim", "green"] };
const designStages = [
  ["brief_needed", "Brief"], ["in_progress", "Dikerjakan"], ["client_review", "Review"], ["revision", "Revisi"], ["approved", "Clear"],
];

function statusBadge(meta, status) {
  const item = meta[status] || [status || "—", "gray"];
  return badge(item[0], item[1]);
}

function eventAvatarRow(event) {
  const crew = String(event.crew_names || "").split(", ").filter(Boolean);
  const avatars = crew.slice(0, 3).map((name, i) => `<span class="mini-avatar ${i === 2 ? "gold" : ""}" title="${escapeHtml(name)}">${initials(name)}</span>`).join("");
  return `<div class="mini-avatars">${avatars}${crew.length > 3 ? `<span class="mini-avatar more">+${crew.length - 3}</span>` : ""}${event.pic_name ? `<span class="mini-avatar gold" title="PIC ${escapeHtml(event.pic_name)}">${initials(event.pic_name)}</span>` : ""}</div>`;
}

function eventRows() {
  const today = localDateISO();
  return state.data.events.filter((event) => event.status === "scheduled" && wibDateISO(event.starts_at) >= today).slice(0, 6);
}

function focusItems() {
  const events = state.data.events;
  const items = [];
  if (has("advances.approve")) {
    const count = events.filter((e) => e.advance_status === "submitted").length;
    if (count) items.push({ tone: "gold", title: `${count} pengajuan uang jalan menunggu review`, sub: "Periksa status sebelum jadwal event.", page: "advances" });
  }
  if (has("warehouse.update")) {
    const first = events.find((e) => ["needs_prep", "preparing", "waiting_return"].includes(e.warehouse_status));
    if (first) items.push({ tone: "green", title: `Kesiapan alat · ${first.project_code}`, sub: `${warehouseMeta[first.warehouse_status][0]} · ${first.title}`, page: "warehouse" });
  }
  if (has("design.read")) {
    const task = state.data.design_tasks.find((t) => t.status === "revision" || t.status === "brief_needed");
    if (task) items.push({ tone: "red", title: `${designMeta[task.status][0]} · ${task.project_code}`, sub: `${task.event_title} · ${datePart(task.due_at)}`, page: "design" });
  }
  const ownEvent = events.find((e) => e.status === "scheduled");
  if (has("attendance.self") && ownEvent) items.push({ tone: "purple", title: `Jadwal berikutnya · ${ownEvent.project_code}`, sub: `${relativeLabel(ownEvent.starts_at)} · ${timePart(ownEvent.starts_at)}`, page: "events" });
  if (!items.length) items.push({ tone: "purple", title: "Semua terlihat siap", sub: "Belum ada tindak lanjut mendesak.", page: "events" });
  return items.slice(0, 4);
}

function renderDashboard() {
  const user = state.data.user;
  const firstName = user.full_name.split(" ")[0];
  const upcoming = eventRows();
  const items = focusItems();
  const metrics = [
    { label: "Event mendatang", value: state.data.dashboard.total_upcoming, icon: "▦", tint: "#eeeaff", color: "#7353e8", show: true, caption: "Dari jadwal operasional" },
    { label: "Uang jalan perlu proses", value: state.data.dashboard.needs_advance, icon: "↗", tint: "#fff3d5", color: "#a87700", show: has("advances.read"), caption: "Pengajuan / transfer" },
    { label: "Desain aktif", value: state.data.dashboard.design_active, icon: "✳", tint: "#e8f7f2", color: "#218b68", show: has("design.read"), caption: "Belum berstatus clear" },
    { label: "Kesiapan alat", value: state.data.dashboard.warehouse_pending, icon: "▣", tint: "#eaf0ff", color: "#4d75c4", show: has("warehouse.update"), caption: "Menunggu persiapan / kembali" },
  ].filter((x) => x.show).slice(0, 4);
  const greeting = new Intl.DateTimeFormat("id-ID", { weekday: "long", day: "numeric", month: "long" }).format(new Date());
  return `${pageHead("OPERATION OVERVIEW", `Halo, ${firstName}`, `${greeting} · Berikut ringkasan operasional Capture It.`, `<button class="button button-primary" data-action="navigate" data-page="events"><span>＋</span> Lihat jadwal</button>`)}
    <div class="metrics-grid">${metrics.map((m) => `<article class="metric-card" style="--metric-glow:${m.tint};--metric-bg:${m.tint};--metric-color:${m.color}"><div class="metric-top"><span>${escapeHtml(m.label)}</span><span class="metric-icon">${m.icon}</span></div><div class="metric-value">${m.value}</div><div class="metric-caption">${escapeHtml(m.caption)}</div></article>`).join("")}</div>
    <div class="dashboard-grid">
      <section class="panel"><div class="panel-head"><div><h3>Jadwal mendatang</h3><p>Event yang segera berjalan</p></div><button class="text-link" data-action="navigate" data-page="events">Lihat semua ↗</button></div>
        <div class="event-list">${upcoming.length ? upcoming.map((event) => `<div class="event-list-row" data-action="open-event" data-id="${event.id}">
          <div class="date-tile"><b>${datePart(event.starts_at, { day: "2-digit" })}</b><span>${datePart(event.starts_at, { month: "short" })}</span></div>
          <div><div class="event-row-title">${escapeHtml(event.title)}</div><div class="event-row-sub"><span>${escapeHtml(event.project_code)}</span><span class="dotsep"></span><span>${escapeHtml(event.location || "Lokasi belum ada")}</span><span class="dotsep"></span><span>${timePart(event.starts_at)}</span></div></div>
          <div class="event-row-trailing">${eventAvatarRow(event)}${statusBadge(groupMeta, event.group_status)}</div>
        </div>`).join("") : `<div class="empty-state"><div class="empty-icon">▦</div><b>Belum ada event mendatang</b>Jadwal akan tampil setelah Google Calendar disinkronkan.</div>`}</div>
      </section>
      <section class="panel"><div class="panel-head"><div><h3>Perlu tindak lanjut</h3><p>Hal yang perlu diperhatikan hari ini</p></div><span class="badge badge-gold">${items.length} item</span></div>
        <div class="panel-body"><div class="focus-list">${items.map((item) => `<button class="focus-item" data-action="navigate" data-page="${item.page}" style="text-align:left;width:100%;border:1px solid #f1eef6"><span class="focus-mark ${item.tone}"></span><span class="focus-text"><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.sub)}</small></span><span class="focus-arrow">›</span></button>`).join("")}</div>
        <div class="progress-summary"><div class="progress-summary-top"><span>Workspace</span><b>Live</b></div><div class="progress-track"><span class="progress-fill" style="width:${Math.max(18, Math.min(100, 100 - items.length * 14))}%"></span></div><div class="progress-caption">Data terhubung dengan akun operasional Anda.</div></div></div>
      </section>
    </div>
    <section class="panel" style="margin-top:17px"><div class="panel-head"><div><h3>Jalan pintas</h3><p>Akses cepat sesuai role Anda</p></div></div><div class="panel-body quick-actions">${quickActions()}</div></section>`;
}

function quickActions() {
  const actions = [];
  if (has("attendance.self")) actions.push(["events", "✓", "Absensi event", "Lihat penugasan saya"]);
  if (has("events.assign")) actions.push(["events", "＋", "Atur crew", "Tugaskan tim ke event"]);
  if (has("warehouse.update")) actions.push(["warehouse", "▣", "Kesiapan alat", "Konfirmasi alat event"]);
  if (has("design.read")) actions.push(["design", "✳", "Review desain", "Lihat tahapan desain"]);
  if (has("advances.read") || has("advances.request")) actions.push(["advances", "↗", "Uang jalan", "Lihat status pengajuan"]);
  if (has("payroll.view")) actions.push(["payroll", "▤", "Penggajian", "Rekap fee dan tunjangan"]);
  return actions.map(([page, icon, title, detail]) => `<button class="quick-action" data-action="navigate" data-page="${page}"><span class="quick-icon">${icon}</span><span><b>${title}</b><small>${detail}</small></span><span class="quick-arrow">↗</span></button>`).join("");
}

function eventMonths(rows) {
  return [...new Set(rows.map((row) => wibDateISO(row.starts_at || "").slice(0, 7)).filter((month) => /^\d{4}-\d{2}$/.test(month)))].sort();
}

function defaultEventMonth(months) {
  const current = localDateISO().slice(0, 7);
  if (months.includes(current)) return current;
  return months.find((month) => month > current) || months[months.length - 1] || "all";
}

function monthLabel(month) {
  const date = new Date(`${month}-01T12:00:00`);
  return new Intl.DateTimeFormat("id-ID", { month: "long", year: "numeric" }).format(date);
}

function monthFilterOptions(months, selected) {
  return `<option value="all" ${selected === "all" ? "selected" : ""}>Semua bulan</option>${months.map((month) => `<option value="${month}" ${selected === month ? "selected" : ""}>${escapeHtml(monthLabel(month))}</option>`).join("")}`;
}

function filteredEvents() {
  const query = ($('[data-filter="events"]')?.value ?? state.search).trim().toLowerCase();
  return state.data.events.filter((row) => {
    const matchesQuery = `${row.title} ${row.project_code} ${row.location}`.toLowerCase().includes(query);
    const matchesMonth = state.eventMonth === "all" || wibDateISO(row.starts_at).slice(0, 7) === state.eventMonth;
    const matchesStatus = state.eventStatus === "all" || row.status === state.eventStatus;
    return matchesQuery && matchesMonth && matchesStatus;
  });
}

function renderEventsPage() {
  const allEvents = state.data.events;
  const months = eventMonths(allEvents);
  if (state.eventMonth === null || (state.eventMonth !== "all" && !months.includes(state.eventMonth))) state.eventMonth = defaultEventMonth(months);
  const events = filteredEvents();
  const toolbar = `<div class="table-toolbar"><label class="table-search"><span>⌕</span><input data-filter="events" placeholder="Cari nama event, kode project, lokasi" value="${escapeHtml(state.search)}"></label><label class="month-filter"><span>Bulan</span><select class="filter-select" id="event-month-filter">${monthFilterOptions(months, state.eventMonth)}</select></label><select class="filter-select" data-filter-status><option value="all" ${state.eventStatus === "all" ? "selected" : ""}>Semua status</option><option value="scheduled" ${state.eventStatus === "scheduled" ? "selected" : ""}>Mendatang</option><option value="completed" ${state.eventStatus === "completed" ? "selected" : ""}>Clear</option><option value="cancelled" ${state.eventStatus === "cancelled" ? "selected" : ""}>Dibatalkan</option></select><span class="table-count" id="event-count">${events.length} dari ${allEvents.length} event</span></div>`;
  const queueCount = has("events.project_code.manage") || has("events.assign") ? (state.data.calendar_code_queue || []).length : 0;
  const codeQueue = queueCount ? `<div class="crm-queue-notice"><span><b>${queueCount} event</b> dari Calendar menunggu kode CRM.</span><button class="button button-light button-small" data-action="navigate" data-page="crm-queue">Buka menu Kode CRM →</button></div>` : "";
  const syncStatus = has("google.sync") ? renderGoogleSyncStatus() : "";
  const exportActions = has("events.export_own") ? `<div class="event-export-tools"><label>Dari<input type="date" id="events-export-start" value="${localDateISO().slice(0,7)}-01"></label><label>Sampai<input type="date" id="events-export-end" value="${localDateISO()}"></label><button class="button button-light" data-action="export-events" data-format="xlsx">Export Excel</button><button class="button button-light" data-action="export-events" data-format="csv">CSV</button></div>` : "";
  return `${pageHead("OPERATIONS", "Jadwal event", has("events.assign") ? "Pantau jadwal, penugasan crew, dan kesiapan event." : "Event yang ditugaskan kepada Anda.", `${exportActions}${has("google.sync") ? `<button class="button button-light" data-action="sync-calendar">↻ Sync Calendar</button>` : ""}`)}
    ${syncStatus}${renderManualEventPanel()}${codeQueue}<section class="panel table-panel">${toolbar}<div class="table-wrap"><table class="data-table"><thead><tr><th>EVENT</th><th>KODE PROJECT</th><th>JADWAL</th><th>LOKASI</th><th>CREW / PIC</th><th>WORKFLOW</th><th></th></tr></thead><tbody id="events-body">${renderEventTableRows(events)}</tbody></table></div></section>`;
}

function renderCrmQueuePage() {
  const syncStatus = has("google.sync") ? renderGoogleSyncStatus() : "";
  const syncButton = has("google.sync") ? `<button class="button button-light" data-action="sync-calendar">↻ Sync Calendar</button>` : "";
  return `${pageHead("OPERATIONS", "Kode CRM", "Event dari Google Calendar yang menunggu kode project CRM sebelum dijadwalkan.", syncButton)}
    ${syncStatus}${renderCalendarCodeQueue()}`;
}

function renderCalendarCodeQueue() {
  const allQueue = state.data.calendar_code_queue || [];
  const months = eventMonths(allQueue);
  if (state.queueMonth === null || (state.queueMonth !== "all" && !months.includes(state.queueMonth))) state.queueMonth = defaultEventMonth(months);
  const queue = allQueue.filter((item) => state.queueMonth === "all" || wibDateISO(item.starts_at).slice(0, 7) === state.queueMonth);
  const monthControl = `<label class="month-filter"><span>Bulan</span><select class="filter-select" id="queue-month-filter">${monthFilterOptions(months, state.queueMonth)}</select></label>`;
  return `<section class="panel calendar-code-panel"><div class="panel-head"><div><h3>Event Calendar · menunggu kode CRM</h3><p>Masukkan kode CRM jika sudah ada; jika belum, jadwalkan dengan kode sementara. Pilih durasi yang sesuai; Calendar hanya memberi nilai awal.</p></div><div class="queue-panel-tools"><label class="table-search"><span>⌕</span><input data-filter="queue" placeholder="Cari judul atau lokasi" autocomplete="off"></label>${monthControl}${badge(`${queue.length} / ${allQueue.length} menunggu`, queue.length ? "gold" : "green")}</div></div>
    ${queue.length ? `<div class="calendar-code-list">${queue.map((item) => `<article class="calendar-code-row" data-queue-row data-search="${escapeHtml(`${item.title} ${item.location || ""} ${item.suggested_project_code || ""}`.toLowerCase())}"><div class="calendar-code-meta"><b>${escapeHtml(item.title)}</b><small>${eventRangeLabel(item.starts_at, item.ends_at)} · ${escapeHtml(item.location || "Lokasi belum diisi")}</small><span class="calendar-code-suggestion">Saran Calendar: ${item.is_full_day ? "Full day" : "Non-full day"}</span>${item.suggested_project_code ? `<span class="calendar-code-suggestion">Kode dari Calendar: ${escapeHtml(item.suggested_project_code)} · pastikan sesuai CRM</span>` : ""}</div><div class="calendar-code-actions">${has("events.project_code.manage") ? `<form class="calendar-code-form" data-intake-id="${item.id}"><label class="field-label">Kode project CRM<input class="field-input" name="project_code" maxlength="50" pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,49}" value="${escapeHtml(item.suggested_project_code || "")}" placeholder="Masukkan kode project" required></label><button class="button button-primary button-small" type="submit">Pasangkan kode</button></form>` : ""}${has("events.assign") ? `<div class="calendar-promotion-tools">${!has("events.project_code.manage") ? `<label class="field-label">Kode project CRM <span>(opsional jika belum ada)</span><input class="field-input" data-project-code-for="${item.id}" maxlength="50" pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,49}" placeholder="Masukkan kode CRM"></label>` : ""}<label class="field-label">Durasi event<select class="field-input" data-duration-for="${item.id}"><option value="0" ${item.is_full_day ? "" : "selected"}>Non-full day</option><option value="1" ${item.is_full_day ? "selected" : ""}>Full day</option></select></label><button class="button button-light button-small" data-action="schedule-without-code" data-id="${item.id}">${has("events.project_code.manage") ? "Jadwalkan tanpa kode CRM" : "Jadwalkan event"}</button></div>` : ""}</div></article>`).join("")}</div>` : `<div class="empty-state calendar-code-empty"><b>${allQueue.length ? "Tidak ada event menunggu kode pada bulan ini." : "Tidak ada event yang menunggu kode."}</b>${allQueue.length ? "Pilih bulan lain atau Semua bulan untuk melihat antrean lainnya." : "Event Calendar baru akan muncul di sini setelah Sync Calendar."}</div>`}</section>`;
}

function renderEventTableRows(events) {
  if (!events.length) return `<tr><td colspan="7"><div class="empty-state"><div class="empty-icon">⌕</div><b>Tidak ada event ditemukan</b>Coba ubah kata kunci atau filter.</div></td></tr>`;
  return events.map((event) => `<tr>
    <td><div class="table-event"><b>${escapeHtml(event.title)}</b><small>${escapeHtml(event.event_type)}${event.is_manual ? " · Manual" : ""}</small></div></td>
    <td><span class="table-code">${escapeHtml(event.project_code)}</span>${event.project_code_is_temporary ? `<small class="temporary-code-note">Kode operasional sementara</small>${has("events.assign") ? `<button class="detail-button" data-action="change-temp-code" data-id="${event.id}" data-code="${escapeHtml(event.project_code)}">Ubah kode</button>` : ""}${has("events.project_code.manage") ? `<button class="detail-button" data-action="attach-project-code" data-id="${event.id}">Pasangkan CRM</button>` : ""}` : ""}</td>
    <td><div class="table-date"><b>${dateLong(event.starts_at)}</b><small>${timePart(event.starts_at)} – ${timePart(event.ends_at)} ${event.is_full_day ? "· Full day" : ""}${isMultiDaySpan(event.starts_at, event.ends_at) ? ` · s/d ${datePart(new Date(new Date(event.ends_at).valueOf() - 1))} · ${eventSpanDays(event.starts_at, event.ends_at)} hari` : ""}</small></div></td>
    <td>${escapeHtml(event.location || "Belum ditentukan")}</td>
    <td><div class="table-person"><span class="avatar avatar-purple">${initials(event.pic_name || "PIC")}</span><span>${escapeHtml(event.pic_name || "PIC belum ditentukan")}<small style="display:block;color:#a29bae;margin-top:3px">${event.crew_count || 0} crew</small></span></div></td>
    <td><div class="table-statuses">${canSeeDesignStatus(event) ? statusBadge(designMeta,event.design_status) : ""}${has("advances.read") || has("advances.request") ? statusBadge(advanceMeta,event.advance_status) : ""}${has("warehouse.update") ? statusBadge(warehouseMeta,event.warehouse_status) : ""}</div></td>
    <td><button class="detail-button" data-action="open-event" data-id="${event.id}">Detail ↗</button></td>
  </tr>`).join("");
}

function renderDesignPage() {
  const tasks = state.data.design_tasks;
  const columns = designStages.map(([stage, title]) => {
    const cards = tasks.filter((task) => task.status === stage);
    return `<section class="kanban-column" data-stage="${stage}"><div class="kanban-head"><b>${title}</b><span class="kanban-count">${cards.length}</span></div>
      ${cards.map((task) => `<article class="kanban-card" draggable="${has("design.update")}" data-task-id="${task.id}">
        <span class="kanban-card-code">${escapeHtml(task.project_code)}</span><h4>${escapeHtml(task.event_title)}</h4><p>${escapeHtml(task.location || "Lokasi belum ada")} · ${datePart(task.starts_at)}</p>
        <div class="kanban-card-foot"><span class="due">${task.due_at ? `Deadline ${datePart(task.due_at)}` : "Belum ada deadline"}</span><button class="detail-button button-small" data-action="open-event" data-id="${task.event_id}">Event ↗</button></div>
      </article>`).join("")}${cards.length ? "" : `<div class="empty-state" style="padding:34px 8px;font-size:9px">Belum ada kartu</div>`}</section>`;
  }).join("");
  return `${pageHead("DESIGN STUDIO", "Alur kerja desain", "Geser kartu antar tahap. Riwayat perubahan status tercatat.", `<span class="badge badge-purple">${tasks.length} task aktif</span>`)}
    <div class="kanban-scroll"><div class="kanban">${columns}</div></div><div class="drag-note">${has("design.update") ? "Tarik kartu ke kolom berikutnya untuk memperbarui tahap." : "Mode lihat saja · Hubungi Team Design untuk perubahan status."}</div>`;
}

function warehouseNext(status) {
  const map = { needs_prep: ["start", "Mulai persiapan"], preparing: ["ready", "Alat siap"], ready: ["dispatch", "Dibawa ke event"], waiting_return: ["returned", "Sudah dikembalikan"], issue: ["resume", "Lanjutkan"], returned: [null, "Selesai"] };
  return map[status] || [null, "—"];
}

function renderWarehousePage() {
  const events = state.data.events;
  const rows = events.filter((e) => e.status !== "cancelled").map((event) => {
    const [action, label] = warehouseNext(event.warehouse_status);
    const stepIndex = ["needs_prep", "preparing", "ready", "waiting_return", "returned"].indexOf(event.warehouse_status);
    return `<div class="status-row"><div class="status-row-title"><b>${escapeHtml(event.title)}</b><small>${escapeHtml(event.project_code)} · ${datePart(event.starts_at)} · ${escapeHtml(event.location)}</small></div>
      <div class="status-track-mini">${[0,1,2,3,4].map((n) => `<i class="track-step ${n < stepIndex ? "done" : n === stepIndex ? "current" : ""}"></i>`).join("")}</div>
      <span class="status-track-label">${statusBadge(warehouseMeta,event.warehouse_status)}</span>
      <div class="assignment-actions"><button class="detail-button" data-action="open-event" data-id="${event.id}">Detail</button>${action && has("warehouse.update") ? `<button class="button button-primary button-small" data-action="warehouse" data-id="${event.id}" data-warehouse-action="${action}">${label}</button>` : ""}</div></div>`;
  }).join("");
  return `${pageHead("WAREHOUSE", "Kesiapan alat", "Konfirmasi alat untuk setiap jadwal dan catat saat sudah kembali.", `<span class="badge badge-green">${events.filter((e) => e.warehouse_status === "returned").length} selesai kembali</span>`)}
    <div class="role-note"><b>Alur event:</b> Perlu disiapkan → Sedang disiapkan → Siap → Menunggu kembali → Sudah dikembalikan. Jika ada alat bermasalah, catat di detail event.</div>
    <section class="panel"><div class="panel-head"><div><h3>Checklist per event</h3><p>${events.length} jadwal operasional</p></div></div><div>${rows || `<div class="empty-state">Belum ada jadwal.</div>`}</div></section>`;
}

function advanceActionButtons(event) {
  const buttons = [];
  const ownPicAssignment = Number(event.pic_user_id) === Number(state.data.user.id)
    || (event.assignments || []).some((assignment) => Number(assignment.user_id) === Number(state.data.user.id) && assignment.assignment_type === "pic");
  if (ownPicAssignment && ["not_submitted", "rejected"].includes(event.advance_status)) buttons.push(`<button class="button button-primary button-small" data-action="advance" data-id="${event.id}" data-advance-action="submit">Ajukan</button>`);
  if (has("advances.approve") && event.advance_status === "submitted") {
    buttons.push(`<button class="button button-primary button-small" data-action="advance" data-id="${event.id}" data-advance-action="approve">Setujui</button>`);
    buttons.push(`<button class="button button-danger button-small" data-action="advance" data-id="${event.id}" data-advance-action="reject">Tolak</button>`);
  }
  if (has("advances.transfer") && event.advance_status === "approved") buttons.push(`<button class="button button-gold button-small" data-action="advance" data-id="${event.id}" data-advance-action="transfer">Catat transfer</button>`);
  return buttons.join("");
}

function renderAdvancesPage() {
  const events = state.data.events;
  const documents = state.data.advance_documents || [];
  const rows = events.filter((e) => e.status !== "cancelled").map((event) => { const documentCount=documents.filter((doc)=>Number(doc.event_id)===Number(event.id)).length; return `<div class="status-row"><div class="status-row-title"><b>${escapeHtml(event.title)}</b><small>${escapeHtml(event.project_code)} · PIC ${escapeHtml(event.pic_name || "belum ditentukan")} · ${datePart(event.starts_at)}</small></div>
    <span>${statusBadge(advanceMeta,event.advance_status)}${event.advance_status === "transferred" ? ` ${badge(documentCount ? `Dokumen diterima · ${documentCount}` : "Menunggu dokumen",documentCount?"green":"gold")}` : ""}</span><span class="status-track-label">Nominal tersimpan di CRM</span>
    <div class="assignment-actions">${advanceActionButtons(event)}<button class="detail-button" data-action="open-event" data-id="${event.id}">Detail</button></div></div>`; }).join("");
  return `${pageHead("FINANCE OPS", "Uang jalan", "Lacak pengajuan PIC, persetujuan Finance, dan pencatatan transfer.", `<span class="badge badge-blue">Nominal dikelola di CRM</span>`)}
    <div class="role-note"><b>Workflow status:</b> Belum diajukan → Diajukan → Disetujui → Sudah ditransfer. Aplikasi ini menyimpan status dan jejak tindakan, bukan nominal uang jalan.</div>
    <section class="panel"><div class="panel-head"><div><h3>Status per event</h3><p>Finance dapat menyetujui dan mencatat transfer.</p></div></div><div>${rows || `<div class="empty-state">Belum ada event.</div>`}</div></section>
    ${has("advances.documents.read_all") ? `<section class="panel table-panel advance-documents-panel"><div class="panel-head"><div><h3>Pertanggungjawaban uang jalan</h3><p>File PDF dan Excel yang diunggah PIC setelah uang jalan ditransfer.</p></div><span class="badge badge-blue">${documents.length} dokumen</span></div>${documents.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>EVENT / KODE PROJECT</th><th>PIC PENGUNGGAH</th><th>FILE</th><th>DIUNGGAH</th><th></th></tr></thead><tbody>${documents.map((doc) => `<tr><td><div class="table-event"><b>${escapeHtml(doc.event_title)}</b><small>${escapeHtml(doc.project_code)} · ${datePart(doc.starts_at)}</small></div></td><td>${escapeHtml(doc.uploader_name)}</td><td><div class="table-event"><b>${escapeHtml(doc.original_filename)}</b><small>${(doc.file_size/1024/1024).toFixed(2)} MB</small></div></td><td>${escapeHtml(attendanceTimeLabel(doc.uploaded_at))}</td><td><a class="button button-light button-small" href="/api/advance-documents/${doc.id}/download">Unduh file</a></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><b>Belum ada dokumen</b>Dokumen pertanggungjawaban yang diunggah PIC akan terkumpul di sini.</div>`}</section>` : ""}`;
}

async function renderPayrollPage() {
  let preview = { rows: [], total: 0 };
  try { preview = await api(`/api/payroll?start=${encodeURIComponent(state.payrollStart)}&end=${encodeURIComponent(state.payrollEnd)}`); } catch (err) { return `<div class="empty-state">${escapeHtml(err.message)}</div>`; }
  const batchAction = preview.batch?.id ? (preview.batch.recorded_at
    ? `<span class="badge badge-green">Transfer dicatat · ${escapeHtml(preview.batch.pay_date)}</span>`
    : `<button class="button button-gold" data-action="record-freelancer-payroll-transfer" data-id="${preview.batch.id}">Catat transfer</button>`) : "";
  return `${pageHead("PAYROLL", "Rekap penggajian", "Rekap mingguan untuk Finance sebelum pembayaran crew dan PIC.", `<div class="payroll-export-actions"><button class="button button-primary" data-action="export-payroll">↓ Export Excel</button><button class="button button-light" data-action="export-payroll" data-format="csv">CSV</button></div>`)}
    <div class="role-note"><b>Periode payroll: Sabtu–Jumat.</b> Fee dasar mengikuti tarif akun saat jadwal dibuat. Fee skill diambil dari skill yang dipilih saat penjadwalan. Uang makan Rp35.000 untuk full day atau Rp25.000 untuk non-full day, dihitung setelah absensi check-out selesai. Di file Excel, Fee Skill dan UM ditandai kuning.</div>
    <section class="panel table-panel"><div class="table-toolbar payroll-toolbar"><label class="field-label">Dari (Sabtu)<input type="date" class="filter-select" id="payroll-start" value="${escapeHtml(state.payrollStart)}"></label><label class="field-label">Sampai (Jumat)<input type="date" class="filter-select" id="payroll-end" value="${escapeHtml(state.payrollEnd)}"></label><span class="table-count">${preview.rows.length} baris · total ${idr(preview.total)}</span></div>
      ${preview.rows.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>NAMA / ABSENSI</th><th>EVENT / PROJECT</th><th>POSISI</th><th>FEE DASAR</th><th>SKILL TAMBAHAN</th><th>UANG MAKAN</th><th>TOTAL</th></tr></thead><tbody>${preview.rows.map((row) => `<tr><td><div class="table-event"><b>${escapeHtml(row.full_name)}</b><small>${escapeHtml(row.attendance_status === "checked_out" ? "Absensi selesai" : row.attendance_status || "—")}</small><small>Masuk ${row.check_in_at ? `${datePart(row.check_in_at)} ${timePart(row.check_in_at)}` : "—"} · Pulang ${row.check_out_at ? `${datePart(row.check_out_at)} ${timePart(row.check_out_at)}` : "—"}</small></div></td><td><div class="table-event"><b>${escapeHtml(row.event_title)}</b><small class="table-code">${escapeHtml(row.project_code)} · ${datePart(row.starts_at)}${row.work_days > 1 ? ` · ${row.work_days} hari kerja` : ""}</small></div></td><td>${escapeHtml(row.position_name || "—")}</td><td>${idr(row.base_fee)}</td><td><div class="table-event"><b>${idr(row.skill_fee)}</b><small>${escapeHtml(row.skill_names || "Tanpa skill tambahan")}</small></div></td><td>${idr(row.meal_fee)}</td><td><b>${idr(row.total)}</b></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><div class="empty-icon">▤</div><b>Belum ada absensi selesai pada periode ini</b>Baris payroll muncul setelah crew/PIC check-out.</div>`}</section>
    <div class="freelancer-transfer-bar"><div><b>${preview.batch?.recorded_at ? "Transfer periode ini sudah dicatat" : preview.batch?.id ? "Payroll sudah diekspor" : "Slip terbit setelah transfer dicatat"}</b><small>${preview.batch?.transfer_reference ? `Referensi: ${escapeHtml(preview.batch.transfer_reference)}` : "Setelah transfer dilakukan, catat di sini agar slip Crew/PIC tersedia di akun masing-masing."}</small></div>${batchAction}</div>
    <div class="rate-info"><b>Export mengunci nilai payroll periode ini.</b><span class="rate-note">Perubahan rate setelah ekspor tidak mengubah batch yang sudah dibuat. Tanggal periode dapat disesuaikan sebelum ekspor.</span></div>`;
}

async function renderInhouseAttendancePage() {
  try {
    const selected = state.inhouseDate || localDateISO();
    const result = await api(`/api/inhouse-attendance?date=${encodeURIComponent(selected)}`);
    const canSelf = has("attendance.inhouse.self") && state.data.user.employment_type === "inhouse";
    const own = result.own_today;
    const schedule = result.schedule || state.data.inhouse_schedule || {};
    const action = own?.status === "not_started" ? "check_in" : own?.status === "checked_in" ? "check_out" : null;
    const ownCard = canSelf ? `<section class="panel inhouse-own-card"><div class="panel-head"><div><h3>Absensi saya</h3><p>${dateLong(result.today)} · waktu dicatat otomatis oleh server · jam kerja ${escapeHtml(schedule.work_start || "09:00")}–${escapeHtml(schedule.work_end || "18:00")}</p></div>${badge(own?.status === "checked_out" ? "Sudah selesai" : own?.status === "checked_in" ? "Sedang bekerja" : "Belum absen", own?.status === "checked_out" ? "green" : own?.status === "checked_in" ? "gold" : "gray")}</div><div class="panel-body inhouse-own-body"><div><b>${escapeHtml(state.data.user.full_name)}</b><small>${escapeHtml(state.data.user.department || "In-house")}</small><div class="attendance-own-times"><span>Masuk · ${own?.check_in_at ? escapeHtml(attendanceTimeLabel(own.check_in_at)) : "belum tercatat"}</span><span>Pulang · ${own?.check_out_at ? escapeHtml(attendanceTimeLabel(own.check_out_at)) : "belum tercatat"}</span></div><div class="punctuality-row">${punctualityBadges(own, schedule)}</div></div>${action ? `<button class="button ${action === "check_in" ? "button-primary" : "button-gold"}" data-action="inhouse-attendance" data-attendance-kind="inhouse" data-attendance-action="${action}">${action === "check_in" ? "Absen masuk" : "Absen pulang"} · buka kamera</button>` : `<span class="badge badge-green">Absensi hari ini lengkap</span>`}</div></section>` : "";
    const rows = (result.records || []).map((row) => {
      const statusLabel = row.status === "checked_out" ? ["Selesai","green"] : row.status === "checked_in" ? ["Belum absen pulang","gold"] : ["Belum absen","gray"];
      const photo = (kind,hasPhoto) => hasPhoto ? `<a href="/api/inhouse-attendance/${row.id}/photo/${kind}" target="_blank" rel="noopener">Foto</a>` : "";
      return `<tr><td><div class="table-event"><b>${escapeHtml(row.full_name)}</b><small>${escapeHtml(row.department || "In-house")}</small></div></td><td>${badge(...statusLabel)}</td><td>${row.check_in_at ? `${escapeHtml(attendanceTimeLabel(row.check_in_at))} ${photo("check_in",row.has_check_in_photo)}` : "—"}</td><td>${row.check_out_at ? `${escapeHtml(attendanceTimeLabel(row.check_out_at))} ${photo("check_out",row.has_check_out_photo)}` : "—"}</td><td>${punctualityBadges(row, schedule) || "—"}</td></tr>`;
    }).join("");
    const managerPanel = result.is_manager ? `<section class="panel table-panel inhouse-manager-panel"><div class="panel-head"><div><h3>Rekap absensi In-house</h3><p>${result.records.length} akun aktif pada tanggal terpilih.</p></div></div><div class="table-toolbar"><label class="field-label">Tanggal<input class="filter-select" id="inhouse-date" type="date" value="${escapeHtml(result.selected_date)}"></label></div><div class="table-wrap"><table class="data-table"><thead><tr><th>NAMA / DEPARTEMEN</th><th>STATUS</th><th>MASUK</th><th>PULANG</th><th>KETERANGAN</th></tr></thead><tbody>${rows || `<tr><td colspan="5"><div class="empty-state">Belum ada akun In-house.</div></td></tr>`}</tbody></table></div></section>` : "";
    const exportPanel = result.is_manager ? `<section class="panel inhouse-export-panel"><div class="panel-head"><div><h3>Export rentang absensi</h3><p>Rentang inklusif untuk rekonsiliasi payroll; ekspor berisi jadwal, keterlambatan, dan pulang lebih awal untuk setiap catatan.</p></div><span class="badge badge-blue">Excel / CSV</span></div><div class="panel-body inhouse-export-controls"><label class="field-label">Dari<input class="field-input" id="inhouse-export-start" type="date" value="${escapeHtml(state.inhouseExportStart)}"></label><label class="field-label">Sampai<input class="field-input" id="inhouse-export-end" type="date" value="${escapeHtml(state.inhouseExportEnd)}"></label><div class="inhouse-export-buttons"><button class="button button-primary" data-action="export-inhouse-attendance" data-format="xlsx">Export Excel</button><button class="button button-light" data-action="export-inhouse-attendance" data-format="csv">CSV</button></div></div></section>` : "";
    const history = canSelf ? `<section class="panel table-panel inhouse-history-panel"><div class="panel-head"><div><h3>Riwayat absensi saya</h3><p>14 hari terakhir, hanya terlihat oleh akun Anda.</p></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>TANGGAL</th><th>STATUS</th><th>MASUK</th><th>PULANG</th><th>KETERANGAN</th></tr></thead><tbody>${(result.history || []).map((row) => `<tr><td>${escapeHtml(row.work_date)}</td><td>${badge(row.status === "checked_out" ? "Selesai" : row.status === "checked_in" ? "Belum absen pulang" : "Belum absen", row.status === "checked_out" ? "green" : row.status === "checked_in" ? "gold" : "gray")}</td><td>${row.check_in_at ? escapeHtml(attendanceTimeLabel(row.check_in_at)) : "—"}</td><td>${row.check_out_at ? escapeHtml(attendanceTimeLabel(row.check_out_at)) : "—"}</td><td>${punctualityBadges(row, schedule) || "—"}</td></tr>`).join("") || `<tr><td colspan="5"><div class="empty-state">Belum ada riwayat absensi.</div></td></tr>`}</tbody></table></div></section>` : "";
    return `${pageHead("IN-HOUSE", "Absensi harian", "Absensi kerja kantor terpisah dari absensi event Freelancer.", `<span class="badge badge-blue">Foto wajah + waktu server</span>`)}${ownCard}${managerPanel}${exportPanel}${history}`;
  } catch (error) { return `<div class="empty-state">${escapeHtml(error.message || "Absensi In-house tidak dapat dimuat.")}</div>`; }
}

async function renderInhousePayrollPage() {
  try {
    const start = state.inhousePayrollStart || previousInhousePayrollMonth().start;
    const end = state.inhousePayrollEnd || previousInhousePayrollMonth().end;
    const result = await api(`/api/inhouse-payroll?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
    const canManage = has("inhouse_payroll.manage");
    const accounts = result.salary_accounts || [];
    const rows = result.rows || [];
    const salaryRows = accounts.map((person) => `<tr><td><div class="table-event"><b>${escapeHtml(person.full_name)}</b><small>${escapeHtml(person.email)} · ${escapeHtml(person.department || "In-house")}</small></div></td><td>${canManage ? `<input class="filter-select inhouse-payroll-money" type="number" min="0" max="2000000000000" step="1" data-inhouse-salary="${person.user_id}" data-original-salary="${person.current_salary_rupiah}" value="${person.current_salary_rupiah}" aria-label="Gaji ${escapeHtml(person.full_name)}">` : idr(person.current_salary_rupiah)}</td><td>${canManage?`<button type="button" class="button button-light button-small" data-action="compensation-history" data-kind="salary" data-id="${person.user_id}">Riwayat</button>`:''}</td></tr>`).join("");
    const payrollRows = rows.map((row) => {
      const disabled = !canManage || row.status === "transferred";
      const salary = Number(row.monthly_salary_rupiah || 0);
      const allowance = Number(row.allowance_rupiah || 0);
      const deduction = Number(row.deduction_rupiah || 0);
      return `<tr data-inhouse-payout-row="${row.user_id}" data-salary="${salary}"><td><div class="table-event"><b>${escapeHtml(row.full_name)}</b><small>${escapeHtml(row.department || "In-house")} · ${escapeHtml(row.email)}</small></div></td><td><div class="table-event"><b>${row.attendance_days} hari tercatat</b><small>${row.completed_days} selesai · ${row.open_days} belum absen pulang</small></div></td><td>${idr(salary)}</td><td><input class="filter-select inhouse-payroll-money" type="number" min="0" step="1000" data-inhouse-allowance="${row.user_id}" value="${allowance}" ${disabled ? "disabled" : ""}></td><td><input class="filter-select inhouse-payroll-money" type="number" min="0" step="1000" data-inhouse-deduction="${row.user_id}" value="${deduction}" ${disabled ? "disabled" : ""}></td><td><b data-inhouse-total>${idr(row.total_rupiah)}</b></td><td><input class="filter-select inhouse-payroll-note" type="text" maxlength="500" data-inhouse-note="${row.user_id}" value="${escapeHtml(row.note || "")}" placeholder="Catatan opsional" ${disabled ? "disabled" : ""}></td><td>${row.status === "transferred" ? `<div class="table-event"><span class="badge badge-green">Ditransfer</span><small>${escapeHtml(row.transfer_reference || "Tanpa referensi")}</small></div>` : `<span class="badge badge-gold">Menunggu</span>${canManage && row.payout_id ? `<button class="button button-gold button-small" data-action="record-inhouse-payroll-transfer" data-id="${row.payout_id}">Catat transfer</button>` : ""}`}</td></tr>`;
    }).join("");
    const status = result.batch_status === "transferred" ? badge("Semua sudah ditransfer", "green") : result.batch_id ? badge("Draft tersimpan", "purple") : badge("Pratinjau", "blue");
    return `${pageHead("PAYROLL BULANAN", "Payroll In-house", "Atur gaji bulanan, cek ringkasan absensi, lalu catat transfer agar slip muncul di akun masing-masing.", status)}
      <div class="role-note"><b>Periode default: 1 sampai akhir bulan sebelumnya; jadwal bayar tanggal 25.</b> Absensi menjadi bahan pemeriksaan dan tidak otomatis memotong gaji. Finance memasukkan tunjangan dan potongan secara manual.</div>
      <section class="panel table-panel inhouse-salary-panel"><div class="panel-head"><div><h3>Gaji pokok bulanan per akun</h3><p>Tarif aktif hari ini. Riwayat menyimpan perubahan dan tarif terjadwal.</p></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>NAMA / DEPARTEMEN</th><th>GAJI POKOK BULANAN</th><th>RIWAYAT</th></tr></thead><tbody>${salaryRows || `<tr><td colspan="3"><div class="empty-state">Belum ada akun In-house aktif.</div></td></tr>`}</tbody></table></div>${canManage?`<div class="salary-change-controls"><label class="field-label">Berlaku mulai<input class="field-input" type="date" id="salary-effective-from" value="${localDateISO()}" required></label><label class="field-label">Alasan perubahan (opsional)<input class="field-input" id="salary-change-reason" maxlength="1000" placeholder="Misalnya evaluasi atau penyesuaian gaji"></label><button type="button" class="button button-primary button-small" data-action="save-inhouse-salaries">Simpan perubahan gaji</button><p>Hanya angka yang diubah akan disimpan. Payroll memakai tarif pada tanggal mulai periode, tanpa prorata otomatis. Koreksi periode berjalan dapat dicatat pada tunjangan/potongan. Payroll yang sudah ditransfer tidak berubah.</p></div>`:''}</section>
      <section class="panel table-panel inhouse-monthly-panel"><div class="panel-head"><div><h3>Perhitungan dan transfer</h3><p>Sesuaikan periode dengan cutoff payroll kantor bila diperlukan.</p></div>${status}</div><div class="table-toolbar inhouse-payroll-toolbar"><label class="field-label">Periode mulai<input type="date" class="filter-select" id="inhouse-payroll-start" value="${escapeHtml(result.period_start)}"></label><label class="field-label">Periode akhir<input type="date" class="filter-select" id="inhouse-payroll-end" value="${escapeHtml(result.period_end)}"></label><label class="field-label">Tanggal bayar<input type="date" class="filter-select" id="inhouse-payroll-pay-date" value="${escapeHtml(result.pay_date)}" ${rows.some((row) => row.status === "transferred") ? "disabled" : ""}></label><span class="table-count">${rows.length} akun · total ${idr(result.total_rupiah)}</span></div>
        <div class="table-wrap"><table class="data-table inhouse-payroll-table"><thead><tr><th>NAMA / DEPARTEMEN</th><th>ABSENSI</th><th>GAJI POKOK</th><th>TUNJANGAN</th><th>POTONGAN</th><th>DITERIMA</th><th>CATATAN</th><th>STATUS</th></tr></thead><tbody>${payrollRows || `<tr><td colspan="8"><div class="empty-state">Belum ada akun In-house aktif.</div></td></tr>`}</tbody></table></div>
        ${canManage ? `<div class="inhouse-payroll-footer"><label class="field-label">Catatan payroll<input class="field-input" id="inhouse-payroll-note" maxlength="500" placeholder="Opsional; diterapkan ke akun yang belum ditransfer"></label><button class="button button-primary" data-action="save-inhouse-payroll" ${result.batch_status === "transferred" ? "disabled" : ""}>Simpan draft payroll</button></div>` : ""}</section>`;
  } catch (error) { return `<div class="empty-state">${escapeHtml(error.message || "Payroll In-house tidak dapat dimuat.")}</div>`; }
}

async function renderInhousePayslipsPage() {
  try {
    const freelancer = state.data.user.employment_type === "freelancer";
    const result = freelancer
      ? await api(`/api/payroll/slips${state.payslipYear ? `?year=${encodeURIComponent(state.payslipYear)}` : ""}`)
      : await api("/api/inhouse-payroll/slips");
    const slips = result.slips || [];
    const years = freelancer ? (result.years || []) : [...new Set(slips.map((s) => String(s.pay_date || s.period_end).slice(0,4)))].sort().reverse();
    const year = freelancer ? result.selected_year : (state.payslipYear === "all" ? "" : (state.payslipYear || years[0] || ""));
    const visible = freelancer ? slips : slips.filter((s) => !year || String(s.pay_date || s.period_end).startsWith(year));
    const cards = freelancer ? visible.map((slip) => `<article class="panel payslip-card freelancer-payslip" data-payslip-id="${slip.batch_id}"><div class="payslip-head"><div><div class="eyebrow-dark">SLIP HONOR EVENT · CREW / PIC</div><h3>Periode ${escapeHtml(slip.period.replace(".."," – "))}</h3><p>Dibayar ${escapeHtml(slip.pay_date)}${slip.transfer_reference ? ` · Ref. ${escapeHtml(slip.transfer_reference)}` : ""}</p></div><button class="button button-light button-small no-print" data-action="print-payslips">Cetak / Simpan PDF</button></div><div class="payslip-total"><span>Total honor diterima</span><b>${idr(slip.total_rupiah)}</b></div><div class="table-wrap"><table class="data-table payslip-table"><thead><tr><th>EVENT / PROJECT</th><th>FEE DASAR</th><th>SKILL</th><th>UANG MAKAN</th><th>TOTAL</th></tr></thead><tbody>${slip.rows.map((row) => `<tr><td><div class="table-event"><b>${escapeHtml(row.event_title)}</b><small>${escapeHtml(row.project_code)} · ${datePart(row.starts_at)}</small></div></td><td>${idr(row.base_fee_rupiah)}</td><td><div class="table-event"><b>${idr(row.skill_fee_rupiah)}</b><small>${escapeHtml(row.skill_names || "Tanpa skill")}</small></div></td><td>${idr(row.meal_rupiah)}</td><td><b>${idr(row.total_rupiah)}</b></td></tr>`).join("")}</tbody></table></div><div class="payslip-foot"><span>Capture It Photobooth · Honor event Freelancer</span><b>Status: Sudah ditransfer · ${escapeHtml(attendanceTimeLabel(slip.transferred_at))}</b></div></article>`).join("") : visible.map((slip) => `<article class="panel payslip-card" data-payslip-id="${slip.id}"><div class="payslip-head"><div><div class="eyebrow-dark">SLIP GAJI IN-HOUSE</div><h3>Periode ${escapeHtml(slip.period_start)} – ${escapeHtml(slip.period_end)}</h3><p>${escapeHtml(slip.user_name_snapshot)} · ${escapeHtml(slip.department_snapshot || "In-house")}</p><p>Dibayar ${escapeHtml(slip.pay_date)} · Ditransfer ${escapeHtml(attendanceTimeLabel(slip.transferred_at))}${slip.transfer_reference ? ` · Referensi ${escapeHtml(slip.transfer_reference)}` : ""}</p></div><button class="button button-light button-small no-print" data-action="print-payslips">Cetak / Simpan PDF</button></div><div class="payslip-total"><span>Total diterima</span><b>${idr(slip.total_rupiah)}</b></div><div class="table-wrap"><table class="data-table payslip-table"><thead><tr><th>GAJI POKOK</th><th>TUNJANGAN</th><th>POTONGAN</th><th>ABSENSI TERCATAT</th></tr></thead><tbody><tr><td>${idr(slip.monthly_salary_rupiah)}</td><td>${idr(slip.allowance_rupiah)}</td><td>${idr(slip.deduction_rupiah)}</td><td>${slip.attendance_days} hari · ${slip.completed_days} lengkap${slip.open_days ? ` · ${slip.open_days} belum absen pulang` : ""}</td></tr></tbody></table></div>${slip.note ? `<div class="payslip-note">${escapeHtml(slip.note)}</div>` : ""}<div class="payslip-foot"><span>Capture It Photobooth · Payroll bulanan In-house</span><b>Status pembayaran: Sudah ditransfer</b></div></article>`).join("");
    const filter = `<section class="payslip-toolbar panel"><div><b>Arsip slip gaji</b><small>${freelancer ? "Honor event hanya untuk penugasan Anda yang sudah ditransfer." : "Slip bulanan diterbitkan setelah Finance mencatat transfer."}</small></div><label class="field-label">Tahun<select id="payslip-year" class="filter-select"><option value="all" ${(freelancer ? year === "all" : !year) ? "selected" : ""}>Semua tahun</option>${years.map((y) => `<option value="${escapeHtml(y)}" ${y === year ? "selected" : ""}>${escapeHtml(y)}</option>`).join("")}</select></label><span class="badge badge-green">${visible.length} slip</span></section>`;
    return `${pageHead("PAYROLL SAYA", "Slip gaji", "Riwayat pembayaran Anda, dengan rincian dan bukti periode yang sudah dibayar.", `<span class="badge badge-blue">${freelancer ? "Freelancer" : "In-house"}</span>`)}${filter}${cards || `<section class="panel"><div class="empty-state"><div class="empty-icon">▧</div><b>Belum ada slip pada tahun ini</b>${freelancer ? "Slip akan tampil setelah Finance mengekspor payroll dan mencatat transfer periode Anda." : "Slip akan muncul setelah transfer payroll In-house Anda dicatat."}</div></section>`}`;
  } catch (error) { return `<div class="empty-state">${escapeHtml(error.message || "Slip gaji tidak dapat dimuat.")}</div>`; }
}

function renderRatesPage() {
  const rates = state.data.rates || [];
  const skills = state.data.skills || [];
  const selectedRate = rates.find((rate) => String(rate.user_id) === String(state.rateUserId)) || rates[0];
  if (selectedRate && !rates.some((rate) => String(rate.user_id) === String(state.rateUserId))) state.rateUserId = selectedRate.user_id;
  const today = localDateISO();
  return `${pageHead("MASTER DATA", "Tarif & skill", "Atur fee dasar secara terpisah untuk setiap akun. Perubahan berlaku mulai tanggal yang dipilih.", `<span class="badge badge-gold">Riwayat perubahan tercatat</span>`)}
    <div class="settings-grid"><section class="panel"><div class="panel-head"><div><h3>Base fee per akun</h3><p>Pilih nama akun lalu tetapkan fee dasar yang berlaku.</p></div></div><div class="panel-body">
      ${rates.length ? `<form id="rate-form" class="rate-account-form">
        <label class="field-label">Nama akun<select id="rate-user" class="field-select" name="user_id" required>${rates.map((rate) => `<option value="${rate.user_id}" ${String(rate.user_id) === String(selectedRate?.user_id) ? "selected" : ""}>${escapeHtml(rate.full_name)} · ${escapeHtml(rate.role_names || "Staff")}</option>`).join("")}</select></label>
        <label class="field-label">Fee dasar<div class="rate-input-wrap"><span>Rp</span><input type="number" min="0" max="100000000" step="1000" name="base_fee_rupiah" data-rate-input value="${selectedRate?.base_fee_rupiah || 0}" required></div></label>
        <label class="field-label">Berlaku mulai<input class="field-input" type="date" name="effective_from" data-rate-date value="${today}" required></label>
        <label class="field-label">Alasan perubahan (opsional)<input class="field-input" name="reason" maxlength="1000" placeholder="Misalnya evaluasi performa"></label>
        <button class="button button-primary" type="submit">Simpan fee akun</button>
      </form>
      <div class="rate-account-list"><div class="rate-list-head"><b>Tarif aktif per akun</b><small>Klik Pilih untuk mengubah tarif; Riwayat untuk melihat perkembangan dan tarif terjadwal.</small></div>${rates.map((rate) => `<div class="rate-account-row"><div><b>${escapeHtml(rate.full_name)}</b><small>${escapeHtml(rate.role_names || "Staff")} · berlaku ${escapeHtml(rate.effective_from || "belum diatur")}</small></div><strong>${idr(rate.base_fee_rupiah)}</strong><div class="rate-history-actions"><button type="button" class="button button-light button-small" data-action="select-rate-user" data-id="${rate.user_id}">Pilih</button><button type="button" class="button button-light button-small" data-action="compensation-history" data-kind="fee" data-id="${rate.user_id}">Riwayat</button></div></div>`).join("")}</div>` : `<div class="empty-state">Belum ada akun aktif.</div>`}</div></section>
    <aside class="panel"><div class="panel-head"><div><h3>Skill khusus</h3><p>Fee tambahan diberikan pada crew yang ditugaskan dengan skill tersebut.</p></div></div><div class="panel-body"><div class="skill-rate-list">${skills.map((skill) => `<div class="skill-rate-row"><input class="field-input" value="${escapeHtml(skill.name)}" data-skill-name="${skill.id}" aria-label="Nama skill"><label class="rate-input-wrap"><span>Rp</span><input type="number" min="0" step="1000" value="${skill.extra_fee_rupiah}" data-skill-fee="${skill.id}" aria-label="Fee ${escapeHtml(skill.name)}"></label><button class="button button-light button-small" data-action="save-skill" data-id="${skill.id}">Simpan</button></div>`).join("")}</div>
      <form id="add-skill-form" class="skill-add-form"><input class="field-input" name="name" placeholder="Nama skill baru" required><label class="rate-input-wrap"><span>Rp</span><input name="extra_fee_rupiah" type="number" min="0" step="1000" value="0" aria-label="Fee tambahan" required></label><button class="button button-primary button-small" type="submit">＋ Tambah</button></form>
      <div class="focus-list" style="margin-top:18px"><div class="focus-item"><span class="focus-mark gold"></span><span class="focus-text"><b>Uang makan · Full day</b><small>Rp35.000 per assignment dengan kehadiran selesai.</small></span></div><div class="focus-item"><span class="focus-mark"></span><span class="focus-text"><b>Uang makan · Non-full day</b><small>Rp25.000 per assignment dengan kehadiran selesai.</small></span></div></div><div class="rate-info"><b>Fee dasar dikunci saat penugasan dibuat.</b><span class="rate-note">Perubahan tarif berlaku untuk penugasan baru. Skill dan fee yang dipilih juga disimpan sebagai snapshot; payroll yang sudah diekspor tetap tidak berubah.</span></div></div></aside></div>`;
}

function allowedKpiTargets() {
  const users = (state.data.employees || []).filter((person) => person.active !== 0);
  return users.filter((person) => {
    const codes = person.roles.map((role) => role.code);
    return (has("kpi.evaluate_operations") && codes.some((c) => ["event_coordinator", "warehouse_head", "warehouse_staff"].includes(c))) ||
      (has("kpi.evaluate_finance") && codes.includes("admin_finance"));
  });
}

function performanceTone(score) { return Number(score) < 75 ? "red" : "green"; }

function renderPerformanceHistory(reviews, subjectName) {
  if (!reviews.length) return `<div class="empty-state performance-empty"><b>Belum ada performance event</b>Setelah penugasan event selesai dan dinilai, hasil beserta catatan akan muncul di sini.</div>`;
  const average = Math.round(reviews.reduce((sum, review) => sum + Number(review.score_percent), 0) / reviews.length);
  const itemScores = new Map();
  for (const review of reviews) for (const item of review.criteria || []) {
    const entry = itemScores.get(item.code) || { name: item.name, total: 0, count: 0 };
    entry.total += Number(item.score); entry.count += 1; itemScores.set(item.code, entry);
  }
  const weakItems = [...itemScores.values()].map((item) => ({ ...item, percent: Math.round(item.total * 20 / item.count) })).filter((item) => item.percent < 75);
  return `<div class="performance-summary"><div class="performance-grade performance-${performanceTone(average)}"><b>${average}%</b><small>RATA-RATA</small></div><div><b>${escapeHtml(subjectName || "Performance event")}</b><p>${reviews.length} event sudah dinilai. Rating merah jika di bawah 75%, hijau jika 75% atau lebih.</p></div></div>
    <section class="performance-focus"><h4>Fokus pengembangan</h4>${weakItems.length ? weakItems.map((item) => `<div class="performance-focus-row"><span>${escapeHtml(item.name)}</span><b class="performance-text-${performanceTone(item.percent)}">${item.percent}%</b></div>`).join("") : `<p class="performance-focus-positive">Belum ada aspek di bawah 75%. Pertahankan hasil kerja yang konsisten.</p>`}</section>
    <div class="performance-history">${reviews.map((review) => `<article class="performance-review"><div class="performance-review-head"><div><b>${escapeHtml(review.event_title)}</b><small><span class="table-code">${escapeHtml(review.project_code)}</span> · ${dateLong(review.starts_at)} · Dinilai ${datePart(review.created_at)} oleh ${escapeHtml(review.reviewer_name)}</small></div><span class="performance-grade performance-grade-small performance-${performanceTone(review.score_percent)}"><b>${review.score_percent}%</b></span></div><div class="performance-criteria">${(review.criteria || []).map((item) => `<div class="performance-criterion"><span>${escapeHtml(item.name)}</span><b>${item.score}/5</b></div>`).join("")}</div><p class="performance-note"><b>Catatan Event Coordinator</b><br>${escapeHtml(review.note || "Tidak ada catatan tambahan.")}</p></article>`).join("")}</div>`;
}

function renderKpiPage() {
  const targets = allowedKpiTargets();
  const reviews = state.data.kpi_reviews || [];
  const reviewSubjects = state.data.kpi_review_subjects || [];
  const performanceReviews = state.data.performance_reviews || [];
  const ownOnly = has("kpi.read_own") && !has("kpi.read");
  if (reviewSubjects.length && !reviewSubjects.some((person) => String(person.id) === String(state.kpiSubjectId))) state.kpiSubjectId = String(reviewSubjects[0].id);
  const performanceSubjects = reviewSubjects.filter((person) => String(person.role_names || "").split(",").some((role) => ["Crew", "PIC Event"].includes(role.trim())));
  if (ownOnly) state.performanceSubjectId = String(state.data.user.id);
  else if (performanceSubjects.length && !performanceSubjects.some((person) => String(person.id) === String(state.performanceSubjectId))) state.performanceSubjectId = String(performanceSubjects[0].id);
  const selectedReviews = reviews.filter((review) => String(review.subject_id) === String(state.kpiSubjectId));
  const selectedPerformance = performanceReviews.filter((review) => String(review.subject_id) === String(state.performanceSubjectId));
  const selectedPerformancePerson = performanceSubjects.find((person) => String(person.id) === String(state.performanceSubjectId));
  const categoryOptions = ["Kualitas kerja", "Ketepatan waktu", "Komunikasi", "Koordinasi event", "Kelengkapan operasional", "Akurasi administrasi"];
  const canEvaluate = targets.length > 0;
  const eventScopedEvaluator = has("kpi.evaluate_crew");
  const showPerformance = ownOnly || eventScopedEvaluator || selectedPerformance.length > 0;
  const subjectFilter = ownOnly ? `<span class="badge badge-purple">Riwayat akun Anda</span>` : `<label class="review-account-filter">Riwayat akun<select class="field-select" data-kpi-subject-filter>${reviewSubjects.map((person) => `<option value="${person.id}" ${String(person.id) === String(state.kpiSubjectId) ? "selected" : ""}>${escapeHtml(person.full_name)} · ${escapeHtml(person.role_names || "Staff")}</option>`).join("")}</select></label>`;
  const performanceSubjectFilter = ownOnly ? `<span class="badge badge-purple">Riwayat akun Anda</span>` : `<label class="review-account-filter">Akun Performance<select class="field-select" data-performance-subject-filter>${performanceSubjects.map((person) => `<option value="${person.id}" ${String(person.id) === String(state.performanceSubjectId) ? "selected" : ""}>${escapeHtml(person.full_name)} · ${escapeHtml(person.role_names || "Crew/PIC")}</option>`).join("")}</select></label>`;
  const eventEvaluationHint = eventScopedEvaluator ? `<section class="panel performance-instruction"><div class="performance-hint-icon">✦</div><div><b>Penilaian Crew/PIC berdasarkan event</b><p>Buka jadwal event, pilih <strong>Performance</strong>, tandai event clear, lalu isi form penilaian yang muncul.</p></div></section>` : "";
  const genericForm = canEvaluate ? `<section class="panel kpi-form"><h3>Buat evaluasi</h3><p>Pilih staff yang sesuai dengan cakupan penilaian role Anda.</p><form id="kpi-form" class="form-stack">
      <label>Staff yang dinilai<select class="field-select" name="subject_id" required><option value="">Pilih staff</option>${targets.map((person) => `<option value="${person.id}">${escapeHtml(person.full_name)} · ${escapeHtml(person.role_names.join(", "))}</option>`).join("")}</select></label>
      <label>Kategori<select class="field-select" name="category" required>${categoryOptions.map((x) => `<option>${x}</option>`).join("")}</select></label>
      <label>Skor (1–5)<div class="kpi-score">${[1,2,3,4,5].map((n) => `<button type="button" class="score-button ${n <= state.score ? "selected" : ""}" data-action="score" data-score="${n}">${n}</button>`).join("")}</div></label>
      <label>Catatan<textarea class="field-textarea" name="note" placeholder="Tambahkan contoh atau konteks singkat"></textarea></label>
      <label>Periode<input class="field-input" type="month" name="review_period" value="${new Date().toISOString().slice(0,7)}" required></label>
      <button class="button button-primary" type="submit">Simpan evaluasi</button>
    </form></section>` : ownOnly ? "" : eventScopedEvaluator ? "" : `<section class="panel"><div class="empty-state"><div class="empty-icon">☆</div><b>Mode lihat KPI</b>Role Anda belum memiliki cakupan penilaian.</div></section>`;
  return `${pageHead("PERFORMANCE", "KPI & evaluasi", "Penilaian sesuai hubungan kerja yang sudah ditetapkan.", `<span class="badge badge-purple">${selectedReviews.length + selectedPerformance.length} riwayat</span>`)}
    ${eventEvaluationHint}${showPerformance ? `<section class="panel performance-panel"><div class="panel-head"><div><h3>${ownOnly ? "Performance saya" : `Performance ${escapeHtml(selectedPerformancePerson?.full_name || "Crew/PIC")}`}</h3><p>${ownOnly ? "Riwayat dan aspek yang dapat dikembangkan dari event Anda." : "Performance event per akun, berdasarkan penugasan yang sudah dinilai."}</p></div>${performanceSubjectFilter}</div>${renderPerformanceHistory(selectedPerformance, ownOnly ? state.data.user.full_name : selectedPerformancePerson?.full_name)}</section>` : ""}
    ${!eventScopedEvaluator || canEvaluate || selectedReviews.length ? `<div class="kpi-layout">${genericForm}
      <section class="panel"><div class="panel-head"><div><h3>${ownOnly ? "Penilaian saya" : "Riwayat penilaian per akun"}</h3><p>${ownOnly ? "Histori evaluasi yang diberikan kepada akun Anda." : "Pilih akun untuk melihat seluruh histori penilaiannya."}</p></div>${subjectFilter}</div><div class="review-list">${selectedReviews.length ? selectedReviews.map((review) => `<article class="review-row"><div class="review-score">${review.score}<small style="display:block;font-size:7px">/ 5</small></div><div class="review-content"><b>${escapeHtml(review.subject_name)} <span style="color:#a39bad;font-weight:500">· ${escapeHtml(review.category)}</span></b><small>Dinilai oleh ${escapeHtml(review.reviewer_name)} · ${escapeHtml(review.review_period)} ${review.project_code ? `· ${escapeHtml(review.project_code)}` : ""}</small><p>${escapeHtml(review.note || "Tidak ada catatan.")}</p></div></article>`).join("") : `<div class="empty-state"><div class="empty-icon">☆</div><b>Belum ada penilaian</b>${ownOnly ? "Penilaian Anda akan muncul di sini setelah diberikan." : "Akun ini belum memiliki histori penilaian."}</div>`}</div></section>
    </div>` : ""}`;
}

function renderConfigurePage() {
  const tabs = [["appearance","Tampilan & brand"],["accounts","Akun"],["roles","Roles & akses"],["hours","Jam kerja"]];
  const body = state.configureTab === "accounts" ? renderConfigureAccounts()
    : state.configureTab === "roles" ? renderConfigureRoles() : state.configureTab === "hours" ? renderInhouseScheduleSettings() : renderConfigureAppearance();
  return `${pageHead("PENGATURAN WORKSPACE", "Configure", "Atur tampilan, akun, dan hak akses Capture It Operations.", `<span class="badge badge-purple">Administrator</span>`)}
    <section class="panel configure-tabs-panel"><nav class="configure-tabs" aria-label="Bagian konfigurasi">${tabs.map(([id,label]) => `<button type="button" class="configure-tab ${state.configureTab === id ? "active" : ""}" data-action="configure-tab" data-tab="${id}">${label}</button>`).join("")}</nav></section>
    ${body}`;
}

function renderConfigureAppearance() {
  const branding = state.data.branding || { colors: { primary: "#7353e8", accent: "#ffb51b", sidebar: "#21123b", background: "#f5f3fa" }, logo_url: "/logo.png", favicon_url: "/favicon.ico" };
  const colors = branding.colors || {};
  const colorRows = [["primary","Warna utama"],["accent","Warna aksen"],["sidebar","Latar sidebar"],["background","Latar halaman"]];
  const assetCard = (kind, title, help, url) => `<form class="panel configure-asset-card" data-brand-upload data-kind="${kind}"><div class="panel-head"><div><h3>${title}</h3><p>${help}</p></div></div><div class="configure-asset-body"><div class="configure-asset-preview ${kind === "favicon" ? "favicon-preview" : ""}"><img src="${escapeHtml(url)}" alt="Pratinjau ${title}"></div><div class="configure-asset-controls"><label class="button button-light button-small profile-file-button">Pilih file<input type="file" name="image" accept="${kind === "favicon" ? "image/png,image/jpeg,.ico" : "image/png,image/jpeg"}" required></label><button class="button button-primary button-small" type="submit">Unggah ${kind === "logo" ? "logo" : "favicon"}</button><small>PNG/JPG${kind === "favicon" ? "/ICO" : ""} · maksimal 500 KB</small></div></div></form>`;
  return `<div class="configure-layout"><section class="panel configure-color-panel"><div class="panel-head"><div><h3>Warna aplikasi</h3><p>Pratinjau perubahan diterapkan pada tombol, aksen, sidebar, dan latar halaman.</p></div></div><form id="appearance-form" class="configure-color-form"><div class="configure-color-grid">${colorRows.map(([key,label]) => `<label class="configure-color-row"><span><b>${label}</b><small>${escapeHtml(colors[key] || "")}</small></span><input type="color" name="${key}" value="${escapeHtml(colors[key] || "#ffffff")}" aria-label="${label}"></label>`).join("")}</div><button class="button button-primary" type="submit">Simpan warna</button></form></section>
    <div class="configure-assets">${assetCard("logo","Logo aplikasi","Tampil pada halaman masuk dan sidebar.",branding.logo_url || "/logo.png")}${assetCard("favicon","Favicon","Ikon tab browser aplikasi.",branding.favicon_url || "/favicon.ico")}</div>
    <div class="rate-info configure-storage-note"><b>Aset tersimpan permanen</b><span class="rate-note">Logo, favicon, dan pilihan warna disimpan bersama data aplikasi sehingga tetap ada setelah aplikasi diperbarui.</span></div></div>`;
}

function renderConfigureAccounts() {
  const people = state.data.employees || [];
  const roles = state.data.roles || [];
  const activeCount = people.filter((person) => person.active).length;
  return `<div class="configure-section-head"><div><h2>Akun pengguna</h2><p>Buat akun individual, ubah role, dan kelola status akses.</p></div><span class="badge badge-purple">${activeCount} aktif · ${people.length - activeCount} nonaktif</span></div>
    <section class="panel account-create-panel"><div class="panel-head"><div><h3>Tambah akun</h3><p>Setiap orang memakai akun sendiri. Fee dasar dapat diatur kemudian di Tarif & Skill.</p></div></div><div class="panel-body"><form id="create-account-form" class="account-create-form">
      <label class="field-label">Nama lengkap<input class="field-input" name="full_name" maxlength="100" required></label>
      <label class="field-label">Email<input class="field-input" name="email" type="email" required></label>
      <label class="field-label">Role<select class="field-select" name="role" required>${roles.map((role) => `<option value="${escapeHtml(role.code)}">${escapeHtml(role.name)}</option>`).join("")}</select></label>
      <label class="field-label">Password awal<input class="field-input" name="password" type="password" minlength="12" autocomplete="new-password" required><small>Minimal 12 karakter</small></label>
      <button class="button button-primary" type="submit">＋ Buat akun</button>
    </form></div></section>
    <section class="panel table-panel account-list-panel"><div class="panel-head"><div><h3>Daftar akun</h3><p>Role dapat diubah, password dapat direset, dan akun nonaktif dapat dihapus jika belum memiliki riwayat operasional.</p></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>NAMA</th><th>EMAIL</th><th>ROLE</th><th>STATUS</th><th>AKSI</th></tr></thead><tbody>${people.map((person) => `<tr><td><div class="table-person"><span class="avatar avatar-purple">${initials(person.full_name)}</span><span>${escapeHtml(person.full_name)}</span></div></td><td>${escapeHtml(person.email)}</td><td><div class="account-role-cell"><select class="field-select" data-user-role="${person.id}">${roles.map((role) => `<option value="${escapeHtml(role.code)}" ${person.roles.some((current) => current.code === role.code) ? "selected" : ""}>${escapeHtml(role.name)}</option>`).join("")}</select><button class="button button-light button-small" data-action="save-user-role" data-id="${person.id}">Simpan role</button></div></td><td>${badge(person.active ? "Aktif" : "Nonaktif",person.active ? "green" : "gray")}</td><td><div class="account-actions"><button class="button button-light button-small" data-action="open-account-password" data-id="${person.id}" data-name="${escapeHtml(person.full_name)}" data-email="${escapeHtml(person.email)}">Ganti password</button><button class="button ${person.active ? "button-danger" : "button-light"} button-small" data-action="toggle-user-active" data-id="${person.id}" data-next-active="${person.active ? "false" : "true"}" ${person.id === state.data.user.id ? "disabled title=\"Akun yang sedang digunakan tidak dapat dinonaktifkan\"" : ""}>${person.active ? "Nonaktifkan" : "Aktifkan"}</button><button class="button button-danger button-small" data-action="delete-user" data-id="${person.id}" data-name="${escapeHtml(person.full_name)}" ${person.active || person.id === state.data.user.id ? "disabled title=\"Nonaktifkan akun terlebih dahulu; akun sendiri tidak dapat dihapus\"" : ""}>Hapus</button></div></td></tr>`).join("")}</tbody></table></div></section>
    <div class="rate-info"><b>Perlindungan akses</b><span class="rate-note">Reset password mencabut semua sesi akun tersebut. Hapus permanen hanya tersedia untuk akun nonaktif yang belum memiliki riwayat operasional agar payroll, absensi, dan audit tetap aman.</span></div>`;
}

function renderConfigureRoles() {
  const config = state.data.configure || { roles: [], permissions: [] };
  const roles = config.roles || [];
  const selected = roles.find((role) => role.code === state.configureRoleCode) || roles.find((role) => role.code !== "administrator");
  if (!selected) return `<div class="empty-state">Daftar role belum tersedia.</div>`;
  state.configureRoleCode = selected.code;
  const grouped = {};
  for (const permission of (config.permissions || [])) {
    const key = permission.code.split(".")[0];
    (grouped[key] ||= []).push(permission);
  }
  const labels = { events: "Jadwal & event", attendance: "Absensi", advances: "Uang jalan", warehouse: "Gudang", design: "Desain", payroll: "Penggajian", fees: "Tarif", skills: "Skill", kpi: "KPI & evaluasi", users: "Pengguna", app: "Konfigurasi", profile: "Profil", staff: "Data Crew/PIC", google: "Google Calendar" };
  const locked = new Set(["users.manage","app.configure"]);
  return `<div class="configure-section-head"><div><h2>Pengaturan role</h2><p>Atur izin tiap role; perubahan diperiksa di server.</p></div><span class="badge badge-gold">${selected.customized ? "Kustom" : "Default"}</span></div>
    <section class="panel role-select-panel"><label class="field-label">Pilih role<select class="field-select" id="configure-role-select">${roles.filter((role) => role.code !== "administrator").map((role) => `<option value="${role.code}" ${role.code === selected.code ? "selected" : ""}>${escapeHtml(role.name)}</option>`).join("")}</select></label><p>Hak akses Administrator dikunci agar selalu tersedia untuk pemulihan sistem dan pengelolaan konfigurasi.</p></section>
    <form id="role-acl-form" data-role="${escapeHtml(selected.code)}">${Object.entries(grouped).map(([group, permissions]) => `<section class="panel role-permission-panel"><div class="panel-head"><div><h3>${labels[group] || group}</h3><p>Hak akses untuk role ${escapeHtml(selected.name)}.</p></div></div><div class="role-permission-list">${permissions.map((permission) => {
      const isLocked = locked.has(permission.code);
      const checked = selected.permissions.includes(permission.code);
      return `<label class="role-permission-row ${isLocked ? "locked" : ""}"><span class="role-checkbox"><input type="checkbox" name="permission" value="${escapeHtml(permission.code)}" ${checked || isLocked ? "checked" : ""} ${isLocked ? "disabled" : ""}><i></i></span><span><b>${escapeHtml(permission.code)}</b><small>${escapeHtml(permission.description)}${isLocked ? " · khusus Administrator" : ""}</small></span></label>`;
    }).join("")}</div></section>`).join("")}
      <div class="configure-role-actions"><button class="button button-light" type="button" data-action="reset-role-default" data-role="${escapeHtml(selected.code)}">Kembalikan ke default</button><button class="button button-primary" type="submit">Simpan hak akses</button></div>
    </form><div class="rate-info"><b>Hak akses berlaku di server</b><span class="rate-note">Menu dan endpoint memeriksa izin role. Izin yang diberikan di sini tidak dapat memberikan akses Configure atau manajemen pengguna di luar Administrator.</span></div>`;
}

function renderProfilePage() {
  const user = state.data.user;
  const profile = state.data.profile || {};
  const avatar = profile.profile_photo_url
    ? `<img id="profile-photo-preview" src="${escapeHtml(profile.profile_photo_url)}" alt="Foto profil">`
    : `<span id="profile-photo-fallback">${initials(user.full_name)}</span>`;
  const ktpCard = (side, label, url) => `<form class="profile-document-card" data-ktp-form data-side="${side}">
    <div class="profile-document-icon">▤</div><div class="profile-document-info"><b>KTP ${label}</b><small>${url ? "File sudah tersimpan" : "Belum diunggah"}</small></div>
    ${url ? `<a class="button button-light button-small" href="${escapeHtml(url)}" target="_blank" rel="noopener">Lihat</a>` : ""}
    <label class="button button-light button-small profile-file-button">Pilih gambar<input type="file" accept="image/jpeg,image/png" name="document" required></label>
    <button class="button button-primary button-small" type="submit">Unggah</button>
    <small class="profile-document-hint">JPG/PNG · maksimal 650 KB</small>
  </form>`;
  return `${pageHead("AKUN SAYA", "Profil Saya", "Perbarui identitas dan dokumen profil Anda.")}
    <div class="profile-layout"><section class="panel profile-main-panel"><div class="panel-head"><div><h3>Informasi akun</h3><p>Nama dan nomor WhatsApp yang digunakan tim operasional.</p></div></div>
      <form id="profile-form" class="profile-form"><div class="profile-avatar-row"><div class="profile-avatar">${avatar}</div><label class="button button-light button-small profile-file-button">Ganti foto<input id="profile-photo-file" type="file" accept="image/jpeg,image/png"></label><small>Foto JPG/PNG, otomatis dikecilkan saat disimpan.</small></div>
        <label class="field-label">Nama lengkap<input class="field-input" name="full_name" maxlength="100" value="${escapeHtml(user.full_name)}" required></label>
        <label class="field-label">Email akun<input class="field-input" value="${escapeHtml(user.email)}" readonly></label>
        <label class="field-label">Nomor WhatsApp<input class="field-input" name="phone" type="tel" maxlength="30" value="${escapeHtml(profile.phone || "")}" placeholder="08xx atau +62..."><small>Nomor ini tampil di direktori Crew/PIC untuk tim operasional.</small></label>
        <button class="button button-primary" type="submit">Simpan profil</button>
      </form>
      <section class="profile-security-section"><div class="profile-security-head"><div><h3>Keamanan akun</h3><p>Ganti password Anda tanpa bantuan Administrator.</p></div><span class="badge badge-purple">Privat</span></div>
        <form id="profile-password-form" class="profile-password-form">
          <label class="field-label">Password saat ini<input class="field-input" name="current_password" type="password" autocomplete="current-password" required></label>
          <label class="field-label">Password baru<input class="field-input" name="new_password" type="password" minlength="12" maxlength="1024" autocomplete="new-password" required><small>Minimal 12 karakter.</small></label>
          <label class="field-label">Ulangi password baru<input class="field-input" name="password_confirmation" type="password" minlength="12" maxlength="1024" autocomplete="new-password" required></label>
          <p class="inline-form-error" data-profile-password-error role="alert" hidden></p>
          <button class="button button-primary" type="submit">Ganti password</button>
        </form>
        <p class="profile-security-note">Password baru langsung berlaku. Sesi akun di perangkat lain akan dicabut, sedangkan perangkat ini tetap masuk.</p>
      </section></section>
      <section class="panel profile-documents-panel"><div class="panel-head"><div><h3>Dokumen KTP</h3><p>Unggah sisi depan dan belakang agar pendataan Crew/PIC lengkap.</p></div><span class="badge badge-gold">Data pribadi</span></div>
        <div class="profile-documents">${ktpCard("front","Depan",profile.ktp_front_url)}${ktpCard("back","Belakang",profile.ktp_back_url)}</div>
        <div class="profile-privacy-note"><b>Akses terbatas</b><span>KTP hanya dapat dilihat oleh pemilik akun, Administrator, dan Head Operations. Direktori lain hanya menampilkan status kelengkapan.</span></div>
      </section></div>`;
}

function renderStaffDirectoryPage() {
  const people = state.data.staff_directory || [];
  const canReadDocuments = has("staff.documents.read");
  const fileAction = (person, side, uploaded, label) => uploaded
    ? (canReadDocuments ? `<a class="button button-light button-small" href="/api/profile-file/${person.id}/ktp_${side}" target="_blank" rel="noopener">KTP ${label}</a>` : badge(`KTP ${label} ada`, "green"))
    : badge(`KTP ${label} belum ada`, "gold");
  const rows = people.map((person) => `<tr data-staff-row data-search="${escapeHtml(`${person.full_name} ${person.email} ${person.phone} ${person.staff_type}`.toLowerCase())}">
    <td><div class="table-person staff-person">${person.photo_url ? `<img class="avatar staff-photo" src="${escapeHtml(person.photo_url)}" alt="">` : `<span class="avatar avatar-purple">${initials(person.full_name)}</span>`}<span><b>${escapeHtml(person.full_name)}</b><small>${escapeHtml(person.staff_type)}</small></span></div></td>
    <td><div class="staff-contact"><b>${escapeHtml(person.email)}</b><small>${escapeHtml(person.phone || "Nomor WhatsApp belum diisi")}</small></div></td>
    <td><div class="staff-doc-status">${fileAction(person,"front",person.has_ktp_front,"depan")}${fileAction(person,"back",person.has_ktp_back,"belakang")}</div></td>
    <td>${person.has_ktp_front && person.has_ktp_back ? badge("Lengkap","green") : badge("Perlu dilengkapi","gold")}</td></tr>`).join("");
  return `${pageHead("DATA STAFF", "Data Crew/PIC", "Daftar akun Crew dan PIC beserta kontak serta status kelengkapan profil.", `<span class="badge badge-purple">${people.length} staff aktif</span>`)}
    <section class="panel table-panel staff-directory-panel"><div class="table-toolbar"><label class="table-search"><span>⌕</span><input id="staff-search" placeholder="Cari nama, email, nomor WhatsApp"></label><span class="table-count">Dokumen KTP dibatasi sesuai akses</span></div>
      <div class="table-wrap"><table class="data-table staff-directory-table"><thead><tr><th>NAMA / PERAN</th><th>KONTAK</th><th>DOKUMEN</th><th>STATUS</th></tr></thead><tbody>${rows || `<tr><td colspan="4"><div class="empty-state">Belum ada akun Crew/PIC aktif.</div></td></tr>`}</tbody></table></div>
    </section><div class="rate-info"><b>Pengaturan profil</b><span class="rate-note">Setiap Crew/PIC memperbarui foto, nomor WhatsApp, dan KTP melalui menu Profil Saya. Event Coordinator dapat melihat direktori dan status KTP tanpa membuka dokumen.</span></div>`;
}

function statusNextButton(kind, event) {
  if (kind === "warehouse" && has("warehouse.update")) {
    const [action, label] = warehouseNext(event.warehouse_status);
    return action ? `<button class="button button-primary button-small" data-action="warehouse" data-id="${event.id}" data-warehouse-action="${action}">${label}</button>` : "";
  }
  return "";
}

let openEventSequence=0;
async function openEvent(eventId, tab = null) {
  const sequence=++openEventSequence,session=state.data;
  try {
    const detail = await api(`/api/events/${eventId}`);
    if(sequence!==openEventSequence||state.data?.user.id!==session.user.id) return;
    await prepareClosingDraft(detail,session);
    if(sequence!==openEventSequence||state.data?.user.id!==session.user.id) return;
    state.drawer = detail;
    state.drawerTab = tab || (has("attendance.self") ? "team" : "overview");
    renderDrawer();
  } catch (error) { toast(error.message, "error"); }
}

function attendanceTimeLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  return `${new Intl.DateTimeFormat("id-ID", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" }).format(date)} WIB`;
}

function attendanceEvidence(a) {
  if (!has("attendance.manage") && a.user_id !== state.data.user.id) return "";
  const record = (label, kind, stamp, photo, lat, lon, accuracy) => stamp ? `<div class="attendance-proof-row"><span>${label} · ${escapeHtml(attendanceTimeLabel(stamp))}${lat != null && lon != null ? `<small>GPS ±${Math.round(accuracy || 0)} m</small>` : ""}</span><span>${photo ? `<a href="/api/attendance/${a.assignment_id}/photo/${kind}" target="_blank" rel="noopener">Lihat foto</a>` : `<small>Dicatat pengelola</small>`}${lat != null && lon != null ? ` · <a href="https://maps.google.com/?q=${encodeURIComponent(`${lat},${lon}`)}" target="_blank" rel="noopener">Peta</a>` : ""}</span></div>` : "";
  const rows = `${record("Check-in", "check_in", a.check_in_at, a.has_check_in_photo, a.check_in_latitude, a.check_in_longitude, a.check_in_accuracy_m)}${record("Check-out", "check_out", a.check_out_at, a.has_check_out_photo, a.check_out_latitude, a.check_out_longitude, a.check_out_accuracy_m)}`;
  return rows ? `<div class="attendance-evidence">${rows}</div>` : "";
}

async function startAttendanceCapture(button) {
  const kind = button.dataset.attendanceKind === "inhouse" ? "inhouse" : "event";
  const eventId = kind === "event" ? Number(button.dataset.id) : null;
  const assignmentId = kind === "event" ? Number(button.dataset.assignmentId) : null;
  const action = button.dataset.attendanceAction;
  const assignment = kind === "event" ? state.drawer?.assignments.find((row) => row.assignment_id === assignmentId) : null;
  const capture = { kind, eventId, assignmentId, action, workDate: button.dataset.workDate || null, personName: assignment?.full_name || state.data.user.full_name, photo: null, location: null, serverOffsetMs: 0 };
  state.attendanceCapture = capture;
  renderAttendanceCamera();
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Browser ini tidak menyediakan akses kamera. Buka aplikasi melalui HTTPS atau localhost.");
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
    if (state.attendanceCapture !== capture) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    state.attendanceStream = stream;
    const video = $("#attendance-video");
    video.srcObject = state.attendanceStream;
    await video.play();
    $("#capture-selfie").disabled = false;
    $("#attendance-camera-message").textContent = "Posisikan wajah di tengah, lalu ambil foto.";
  } catch (error) {
    if (state.attendanceCapture !== capture) return;
    state.attendanceStream?.getTracks().forEach((track) => track.stop());
    state.attendanceStream = null;
    $("#attendance-camera-message").textContent = error.message || "Kamera tidak dapat dibuka. Periksa izin kamera browser.";
    toast(error.message || "Kamera tidak dapat dibuka. Periksa izin kamera browser.", "error");
  }
}

function renderAttendanceCamera() {
  const capture = state.attendanceCapture;
  const verb = capture.action === "check_in" ? "Check-in" : "Check-out";
  const inhouse = capture.kind === "inhouse";
  $("#modal-root").innerHTML = `<div class="camera-backdrop" data-action="attendance-camera-backdrop"><section class="camera-dialog" role="dialog" aria-modal="true" aria-label="Absensi dengan foto">
    <div class="camera-dialog-head"><div><span class="eyebrow-dark">${inhouse ? "ABSENSI HARIAN IN-HOUSE" : "ABSENSI EVENT"}</span><h2>${verb} · ${escapeHtml(capture.personName)}</h2><p>${inhouse ? "Absensi kerja kantor" : escapeHtml(state.drawer?.title || "Event")}</p></div><button class="drawer-close" data-action="close-attendance-camera" aria-label="Tutup kamera">×</button></div>
    <div class="camera-stage"><video id="attendance-video" autoplay playsinline muted></video><img id="attendance-preview" alt="Foto bukti absensi" hidden></div>
    <p class="camera-guidance" id="attendance-camera-message">Meminta izin kamera...</p>
    <p class="camera-timestamp-note">GPS wajib. Foto diberi waktu server; waktu resmi tetap dicatat server saat absensi dikirim.</p>
    <div class="camera-actions"><button class="button button-light" data-action="close-attendance-camera">Batal</button><button class="button button-light" id="retake-selfie" data-action="retake-selfie" hidden>Ambil ulang</button><button class="button button-primary" id="capture-selfie" data-action="capture-selfie" disabled>Ambil foto</button><button class="button button-primary" id="submit-attendance-photo" data-action="submit-attendance-photo" hidden disabled>Kirim ${verb}</button></div>
  </section></div>`;
}

async function captureAttendancePhoto() {
  const video=$("#attendance-video");
  const capture=state.attendanceCapture;
  const button=$("#capture-selfie");
  if (!capture || !video?.videoWidth || !video.videoHeight) { toast("Kamera belum siap. Coba lagi sebentar.","error"); return; }
  button.disabled=true;
  $("#attendance-camera-message").textContent="Mengambil lokasi GPS dan menyelaraskan jam server...";
  try {
    if (!navigator.geolocation) throw new Error("Browser tidak menyediakan GPS/lokasi. Aktifkan layanan lokasi pada perangkat.");
    const [serverTime,position]=await Promise.all([
      api("/api/server-time"),
      new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(resolve,reject,{enableHighAccuracy:true,maximumAge:0,timeout:25000})),
    ]);
    if (state.attendanceCapture!==capture) return;
    const accuracy=Number(position.coords.accuracy);
    if (!Number.isFinite(accuracy) || accuracy<=0 || accuracy>500) throw new Error(`GPS belum cukup akurat (±${Math.round(accuracy||0)} m). Aktifkan GPS dan coba di area lebih terbuka.`);
    capture.serverOffsetMs=Date.parse(serverTime.timestamp)-Date.now();
    if (!Number.isFinite(capture.serverOffsetMs)) throw new Error("Waktu server tidak dapat diselaraskan. Coba lagi.");
    capture.location={latitude:position.coords.latitude,longitude:position.coords.longitude,accuracy_m:accuracy,
      captured_at:new Date(position.timestamp+capture.serverOffsetMs).toISOString()};
    const scale=Math.min(1,720/Math.max(video.videoWidth,video.videoHeight));
    const width=Math.round(video.videoWidth*scale),height=Math.round(video.videoHeight*scale),bar=42;
    const canvas=document.createElement("canvas");canvas.width=width;canvas.height=height+bar;
    const ctx=canvas.getContext("2d");ctx.drawImage(video,0,0,width,height);
    ctx.fillStyle="rgba(20,13,31,.88)";ctx.fillRect(0,height,width,bar);
    const capturedAt=new Date(Date.now()+capture.serverOffsetMs);
    const label=new Intl.DateTimeFormat("id-ID",{day:"2-digit",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false,timeZone:"Asia/Jakarta"}).format(capturedAt);
    ctx.fillStyle="#fff";ctx.font="bold 14px Arial, sans-serif";ctx.textBaseline="middle";
    ctx.fillText(`${capture.action==="check_in"?"CHECK-IN":"CHECK-OUT"} · ${label} WIB`,12,height+bar/2);
    capture.photo=canvas.toDataURL("image/jpeg",.78);
    const preview=$("#attendance-preview");preview.src=capture.photo;preview.hidden=false;video.hidden=true;
    $("#attendance-camera-message").textContent=`GPS terkunci · akurasi ±${Math.round(accuracy)} m · periksa wajah dan timestamp.`;
    button.hidden=true;$("#retake-selfie").hidden=false;$("#submit-attendance-photo").hidden=false;$("#submit-attendance-photo").disabled=false;
  } catch(error) {
    button.disabled=false;
    $("#attendance-camera-message").textContent=error.code===1?"Izin lokasi ditolak. Izinkan lokasi browser untuk melanjutkan.":(error.message||"GPS tidak tersedia.");
    toast($("#attendance-camera-message").textContent,"error");
  }
}

function retakeAttendancePhoto() {
  if (!state.attendanceCapture) return;
  state.attendanceCapture.photo = null;
  $("#attendance-preview").hidden = true;
  $("#attendance-video").hidden = false;
  $("#capture-selfie").hidden = false;
  $("#retake-selfie").hidden = true;
  $("#submit-attendance-photo").hidden = true;
}

function closeAttendanceCamera() {
  const kind = state.attendanceCapture?.kind;
  state.attendanceStream?.getTracks().forEach((track) => track.stop());
  state.attendanceStream = null;
  state.attendanceCapture = null;
  if (state.drawer) renderDrawer();
  else if (kind === "inhouse") renderPage();
  else $("#modal-root").innerHTML = "";
}

async function submitAttendancePhoto() {
  const capture = state.attendanceCapture;
  if (!capture?.photo) return;
  const button = $("#submit-attendance-photo");
  button.disabled = true;
  button.innerHTML = `<span class="spinner"></span> Mengirim`;
  try {
    const endpoint = capture.kind === "inhouse" ? "/api/inhouse-attendance" : `/api/events/${capture.eventId}/attendance`;
    const body = capture.kind === "inhouse" ? { action: capture.action, photo: capture.photo, location: capture.location } : { assignment_id: capture.assignmentId, action: capture.action, photo: capture.photo, location: capture.location, ...(capture.workDate ? { work_date: capture.workDate } : {}) };
    const result = await api(endpoint, { method: "POST", body: JSON.stringify(body) });
    const base = `${capture.action === "check_in" ? "Check-in" : "Check-out"} tercatat ${attendanceTimeLabel(result.timestamp)}.`;
    if (result.punctuality) toast(`${base} ${result.punctuality} (jadwal ${result.scheduled}).`, "error");
    else toast(base);
    closeAttendanceCamera();
    await refreshData();
  } catch (error) {
    button.disabled = false;
    button.textContent = `Kirim ${capture.action === "check_in" ? "Check-in" : "Check-out"}`;
    toast(error.message || "Foto absensi gagal dikirim.", "error");
  }
}

function drawerTabs() {
  const tabs = [["overview", "Ringkasan"], ["team", "Crew & Absensi"]];
  tabs.push(["logistics", "Transportasi & Detail"]);
  tabs.push(["closing", "Penutupan"]);
  if (has("events.clear") || has("kpi.read_own")) tabs.push(["performance", "Performance"]);
  const ownPicAssignment = state.drawer?.assignments?.some((assignment) => Number(assignment.user_id) === Number(state.data.user.id) && assignment.assignment_type === "pic");
  if (has("advances.read") || has("advances.request") || ownPicAssignment) tabs.push(["advance", "Uang Jalan"]);
  if (has("warehouse.update") || has("events.read_all")) tabs.push(["warehouse", "Kesiapan Alat"]);
  if (has("design.read") && !state.drawer?.design_restricted) tabs.push(["design", "Design"]);
  return tabs;
}

function renderDrawer() {
  if (!state.drawer) return;
  const d = state.drawer;
  const host = $("#modal-root");
  host.innerHTML = `<div class="modal-backdrop" data-action="backdrop-close"><aside class="detail-drawer" role="dialog" aria-modal="true" aria-label="Detail event">
    <div class="drawer-head"><div class="drawer-head-line"><div><h2>${escapeHtml(d.title)}</h2><p><span class="table-code">${escapeHtml(d.project_code)}</span> · ${d.multi_day ? eventRangeLabel(d.starts_at, d.ends_at) : `${datePart(d.starts_at,{day:"numeric",month:"long",year:"numeric"})} · ${timePart(d.starts_at)}`} · ${escapeHtml(d.location)}</p></div>${d.is_manual && has("events.manual.delete") ? `<button class="detail-button" data-action="delete-manual-event" data-id="${d.id}">Hapus event manual</button>` : ""}<button class="drawer-close" data-action="close-drawer" aria-label="Tutup">×</button></div>
      <div class="drawer-tabs">${drawerTabs().map(([id,label]) => `<button class="drawer-tab ${state.drawerTab===id?"active":""}" data-action="drawer-tab" data-tab="${id}">${label}</button>`).join("")}</div></div>
    <div class="drawer-content">${renderDrawerTab(d)}</div>
  </aside></div>`;
  const logisticsForm=$("#event-logistics-form");
  if(logisticsForm) syncTransportFields(logisticsForm);
}

function renderDrawerTab(d) {
  if (state.drawerTab === "closing") return renderClosingTab(d);
  if (state.drawerTab === "logistics") return renderLogisticsTab(d);
  if (state.drawerTab === "team") return renderTeamTab(d);
  if (state.drawerTab === "performance") return renderEventPerformanceTab(d);
  if (state.drawerTab === "advance") return renderAdvanceTab(d);
  if (state.drawerTab === "warehouse") return renderWarehouseTab(d);
  if (state.drawerTab === "design") return renderDesignTab(d);
  return `<section class="detail-hero"><span class="eyebrow">${escapeHtml(d.event_type)} · OPERATION EVENT</span><h3>${escapeHtml(d.title)}</h3><p>${escapeHtml(d.project_code)} · ${datePart(d.starts_at,{day:"numeric",month:"long",year:"numeric"})}</p></section>
    <div class="detail-grid"><div class="detail-info"><small>Waktu event</small><b>${timePart(d.starts_at)} – ${timePart(d.ends_at)} ${d.is_full_day ? "· Full day" : "· Non-full day"}</b>${d.is_full_day_manual ? `<small>Durasi ditetapkan manual</small>` : ""}</div><div class="detail-info"><small>Event Coordinator</small><b>${escapeHtml(d.coordinator_name || "Belum ditugaskan")}</b></div><div class="detail-info"><small>Lokasi</small><b>${escapeHtml(d.location || "Belum ditentukan")}</b></div><div class="detail-info"><small>Status event</small><b>${d.status === "completed" ? "Clear" : d.status === "cancelled" ? "Dibatalkan" : "Terjadwal"}</b></div></div>
    ${has("events.assign") && d.status === "scheduled" ? `<form class="duration-edit-form" data-full-day-event="${d.id}"><label class="field-label">Ubah durasi event<select class="field-input" name="is_full_day"><option value="0" ${d.is_full_day ? "" : "selected"}>Non-full day</option><option value="1" ${d.is_full_day ? "selected" : ""}>Full day</option></select></label><button class="button button-light button-small" type="submit">Simpan durasi</button><small>Perubahan berlaku untuk perhitungan uang makan, juga bertahan saat Calendar sync.</small></form>` : ""}
    ${approvedDesignLink(d)}${renderClearEventAction(d)}
    <h3 class="drawer-section-title">Tim event <button class="text-link" data-action="drawer-tab" data-tab="team">Kelola crew ↗</button></h3>
    <div class="assignment-list">${d.assignments.length ? d.assignments.map((a) => `<div class="assignment-card"><span class="avatar ${a.assignment_type === "pic" ? "avatar-gold" : "avatar-purple"}">${initials(a.full_name)}</span><div class="assignment-meta"><b>${escapeHtml(a.full_name)}</b><small>${a.assignment_type === "pic" ? "PIC Event" : escapeHtml(a.position_name || "Crew")}${a.skills ? ` · Skill ${escapeHtml(a.skills)}` : ""}</small></div>${statusBadge({not_started:["Belum absen","gray"],checked_in:["Sedang bertugas","gold"],checked_out:["Selesai","green"],absent:["Tidak hadir","red"]},a.attendance_status)}</div>`).join("") : `<div class="empty-state">Belum ada penugasan.</div>`}</div>
    <h3 class="drawer-section-title">Checklist operasional</h3>
    <div class="focus-list"><div class="focus-item"><span class="focus-mark ${d.group_status === "invites_sent" ? "green" : "gold"}"></span><span class="focus-text"><b>Grup WhatsApp</b><small>${groupMeta[d.group_status]?.[0] || "Belum dibuat"}</small></span>${has("events.whatsapp.manage") ? `<button class="detail-button" data-action="drawer-tab" data-tab="team">Atur</button>` : ""}</div><div class="focus-item"><span class="focus-mark ${d.warehouse_status === "ready" || d.warehouse_status === "returned" ? "green" : "gold"}"></span><span class="focus-text"><b>Kesiapan alat</b><small>${warehouseMeta[d.warehouse_status]?.[0] || "Perlu disiapkan"}</small></span>${has("warehouse.update") ? `<button class="detail-button" data-action="drawer-tab" data-tab="warehouse">Atur</button>` : ""}</div><div class="focus-item"><span class="focus-mark ${d.design_status === "approved" ? "green" : "purple"}"></span><span class="focus-text"><b>Design event</b><small>${designMeta[d.design_status]?.[0] || "Brief dibutuhkan"}</small></span>${has("design.read") ? `<button class="detail-button" data-action="drawer-tab" data-tab="design">Atur</button>` : ""}</div></div>`;
}

function renderClearEventAction(d) {
  if (!has("events.clear")) return "";
  if (d.status === "scheduled") {
    if (!d.closing_report?.can_clear) return `<section class="performance-clear-step"><span class="performance-hint-icon">↗</span><div><b>Laporan penutupan ${d.closing_report?.status==='accepted'?'sudah diterima':'sebelum Clear'}</b><p>${d.closing_report?.status==='accepted'?'Clear dilakukan oleh coordinator yang ditugaskan pada event ini.':'PIC mengirim laporan hasil event, lalu coordinator mereviewnya.'}</p></div><button class="button button-light" data-action="drawer-tab" data-tab="closing">Lihat laporan</button></section>`;
    return `<section class="performance-clear-step"><span class="performance-hint-icon">✓</span><div><b>Tandai event clear</b><p>${has("kpi.evaluate_crew") ? "Setelah clear, form penilaian Crew dan PIC akan terbuka." : "Event Coordinator akan mengisi penilaian Crew dan PIC setelah event di-clear."}</p></div><button class="button button-primary" data-action="clear-event" data-id="${d.id}">Tandai clear</button></section>`;
  }
  const pendingReviews = d.status === "completed" && has("kpi.evaluate_crew") && d.assignments.some((assignment) =>
    ["crew", "pic"].includes(assignment.assignment_type) && !assignment.performance_review
  );
  if (pendingReviews) return `<section class="performance-clear-step"><span class="performance-hint-icon">✦</span><div><b>Event selesai · performance belum lengkap</b><p>Isi penilaian Crew dan PIC yang ditugaskan pada event ini.</p></div><button class="button button-primary" data-action="drawer-tab" data-tab="performance">Isi penilaian</button></section>`;
  return "";
}

function renderEventPerformanceReview(review, assignment) {
  const score = review.score_percent;
  return `<article class="performance-review"><div class="performance-review-head"><div><b>${escapeHtml(assignment.full_name)} <small>${assignment.assignment_type === "pic" ? "PIC Event" : escapeHtml(assignment.position_name || "Crew")}</small></b><small>Dinilai oleh ${escapeHtml(review.reviewer_name)} · ${datePart(review.created_at)}</small></div><span class="performance-grade performance-grade-small performance-${performanceTone(score)}"><b>${score}%</b></span></div><div class="performance-criteria">${(review.criteria || []).map((item) => `<div class="performance-criterion"><span>${escapeHtml(item.name)}</span><b>${item.score}/5</b></div>`).join("")}</div><p class="performance-note"><b>Catatan</b><br>${escapeHtml(review.note || "Tidak ada catatan tambahan.")}</p></article>`;
}

function renderEventPerformanceTab(d) {
  const canClear = has("events.clear");
  const canReview = has("kpi.evaluate_crew");
  const userId = state.data.user.id;
  const assignments = d.assignments.filter((assignment) => ["crew", "pic"].includes(assignment.assignment_type));
  const visibleAssignments = canReview ? assignments : assignments.filter((assignment) => assignment.user_id === userId);
  if (!canReview) {
    if (canClear) {
      return `<h3 class="drawer-section-title" style="margin-top:0">Performance · ${escapeHtml(d.title)}</h3>${renderClearEventAction(d)}${d.status === "completed" ? `<div class="performance-complete-banner">Event sudah clear. Form dan riwayat penilaian dikelola Event Coordinator.</div>` : d.status === "cancelled" ? `<div class="performance-complete-banner performance-cancelled-banner">Event dibatalkan. Penilaian tidak tersedia.</div>` : ""}`;
    }
    const own = visibleAssignments[0];
    const emptyMessage = d.status === "completed"
      ? ["Penilaian event sedang diproses", "Hasil akan muncul di dashboard Performance Anda setelah Event Coordinator mengirim penilaian."]
      : ["Belum ada penilaian untuk event ini", "Penilaian Event Coordinator akan muncul di halaman KPI & Evaluasi setelah event clear."];
    return `<h3 class="drawer-section-title" style="margin-top:0">Performance event</h3>${own?.performance_review ? renderEventPerformanceReview(own.performance_review, own) : `<div class="empty-state performance-empty"><b>${emptyMessage[0]}</b>${emptyMessage[1]}</div>`}`;
  }

  const pending = assignments.filter((assignment) => !assignment.performance_review);
  const completedReviews = assignments.filter((assignment) => assignment.performance_review);
  const criteria = state.data.performance_criteria || [];
  const clearStep = d.status === "scheduled" ? renderClearEventAction(d) : "";
  const cancelledStep = d.status === "cancelled" ? `<div class="performance-complete-banner performance-cancelled-banner">Event dibatalkan. Penilaian tidak tersedia.</div>` : "";
  const form = d.status === "completed" && pending.length ? `<form id="event-performance-form" class="event-performance-form" data-event-id="${d.id}">
      <p class="performance-guide">Nilai tiap aspek dari 1 sampai 5. Rating keseluruhan dihitung otomatis; di bawah 75% merah, mulai 75% hijau. Catatan disimpan ke riwayat performance akun Crew/PIC.</p>
      <div class="performance-assignment-forms">${pending.map((assignment) => `<section class="performance-assignment-form" data-performance-assignment="${assignment.assignment_id}"><div class="performance-assignment-title"><span class="avatar ${assignment.assignment_type === "pic" ? "avatar-gold" : "avatar-purple"}">${initials(assignment.full_name)}</span><div><b>${escapeHtml(assignment.full_name)}</b><small>${assignment.assignment_type === "pic" ? "PIC Event" : escapeHtml(assignment.position_name || "Crew")}</small></div></div><div class="performance-score-grid">${criteria.map((criterion) => `<label>${escapeHtml(criterion.name)}<select class="field-select" name="score_${assignment.assignment_id}_${criterion.code}" required><option value="">Pilih 1–5</option>${[1,2,3,4,5].map((score) => `<option value="${score}">${score} · ${["", "Perlu banyak perbaikan", "Perlu ditingkatkan", "Cukup", "Baik", "Sangat baik"][score]}</option>`).join("")}</select></label>`).join("")}</div><label class="field-label">Catatan tambahan untuk ${escapeHtml(assignment.full_name)}<textarea class="field-textarea" name="note_${assignment.assignment_id}" maxlength="1000" placeholder="Contoh spesifik hal yang sudah baik atau perlu ditingkatkan"></textarea></label></section>`).join("")}</div>
      <div class="performance-form-footer"><span>Penilaian ini akan langsung masuk ke dashboard Performance akun terkait.</span><button class="button button-primary" type="submit">Simpan penilaian</button></div>
    </form>` : "";
  const completeBanner = d.status === "completed" && !pending.length ? `<div class="performance-complete-banner">✓ Event clear${d.completed_at ? ` · ${dateLong(d.completed_at)}` : ""}${d.completed_by_name ? ` oleh ${escapeHtml(d.completed_by_name)}` : ""}${assignments.length ? " · Semua penilaian tersimpan" : " · Tidak ada Crew/PIC yang dijadwalkan"}</div>` : "";
  return `<h3 class="drawer-section-title" style="margin-top:0">Performance · ${escapeHtml(d.title)}</h3>${clearStep}${cancelledStep}${completedReviews.length ? `<section class="completed-performance-list"><h4>Penilaian tersimpan</h4>${completedReviews.map((assignment) => renderEventPerformanceReview(assignment.performance_review, assignment)).join("")}</section>` : ""}${form}${completeBanner}`;
}

function renderTeamTab(d) {
  const userId = state.data.user.id;
  const canAssign = has("events.assign") && d.status === "scheduled" && !["submitted", "accepted"].includes(d.closing_report?.status);
  const assignments = d.assignments.map((a) => {
    let attendanceAction = "";
    if (!d.multi_day && a.user_id === userId && has("attendance.self")) {
      const rule = eventDayRule(d);
      if (a.attendance_status === "not_started" && !rule.canCheckIn) attendanceAction = `<small class="muted">Check-in dibuka pada ${escapeHtml(rule.window)}</small>`;
      else if (a.attendance_status === "checked_in" && !rule.canCheckOut) attendanceAction = `<small class="muted">Check-out sudah lewat · hubungi Coordinator</small>`;
      if (a.attendance_status === "not_started" && rule.canCheckIn) attendanceAction = `<button class="button button-primary button-small" data-action="attendance" data-id="${d.id}" data-assignment-id="${a.assignment_id}" data-attendance-action="check_in">Check-in</button>`;
      if (a.attendance_status === "checked_in" && rule.canCheckOut) attendanceAction = `<button class="button button-gold button-small" data-action="attendance" data-id="${d.id}" data-assignment-id="${a.assignment_id}" data-attendance-action="check_out">Check-out</button>`;
    }
    return `<div class="assignment-card"><span class="avatar ${a.assignment_type === "pic" ? "avatar-gold" : "avatar-purple"}">${initials(a.full_name)}</span><div class="assignment-meta"><b>${escapeHtml(a.full_name)}</b><small>${a.assignment_type === "pic" ? "PIC Event" : escapeHtml(a.position_name || "Crew")}${a.skills ? ` · ${escapeHtml(a.skills)}` : ""}</small>${d.multi_day ? "" : attendanceEvidence(a)}</div>${statusBadge({not_started:["Belum absen","gray"],checked_in:["Sedang bertugas","gold"],checked_out:["Selesai","green"],absent:["Tidak hadir","red"]},a.attendance_status)}<div class="assignment-actions">${attendanceAction}${!d.multi_day && has("attendance.manage") && a.attendance_status !== "checked_out" ? `<button class="detail-button" data-action="mark-absent" data-id="${d.id}" data-assignment-id="${a.assignment_id}">Tidak hadir</button>` : ""}${canAssign ? `<button class="detail-button" title="Hapus penugasan" data-action="remove-assignment" data-id="${d.id}" data-assignment-id="${a.assignment_id}">×</button>` : ""}</div></div>${d.multi_day ? renderAssignmentDays(d, a) : renderSingleDayCorrection(d, a)}`;
  }).join("");
  const employees = (state.data.employees || []).filter((person) => person.active !== 0);
  const assignableEmployees = employees.filter((person) => person.roles.some((role) => ["crew", "pic_event"].includes(role.code)));
  const positions = state.data.positions || [];
  const skills = state.data.skills || [];
  const comm = groupMeta[d.group_status] || groupMeta.not_created;
  const mealFee = d.is_full_day ? 35000 : 25000;
  const staffOptions = assignableEmployees.map((person) => `<option value="${person.id}">${escapeHtml(person.full_name)} · ${escapeHtml(person.role_names || "Crew/PIC")}</option>`).join("");
  const positionOptions = positions.map((position) => {
    const kind = position.name.toLowerCase() === "pic event" ? "pic" : "crew";
    return `<option value="${position.id}" data-kind="${kind}">${escapeHtml(position.name)}</option>`;
  }).join("");
  const skillOptions = skills.map((skill) => `<label class="assignment-skill-option"><input type="checkbox" name="skill_ids" value="${skill.id}" data-fee="${skill.extra_fee_rupiah}"><span><b>${escapeHtml(skill.name)}</b><small>+ ${idr(skill.extra_fee_rupiah)}</small></span></label>`).join("");
  return `<h3 class="drawer-section-title" style="margin-top:0">Crew dan PIC <span>${d.assignments.length} penugasan${d.multi_day ? ` · event ${d.event_days.length} hari` : ""}</span></h3>${renderAttendanceBanner(d)}${renderStaffConflicts(d)}<div class="assignment-list">${assignments || `<div class="empty-state">Belum ada penugasan.</div>`}</div>
    ${canAssign ? `<form class="assignment-form" id="assignment-form" data-event-id="${d.id}">
      <div class="assignment-form-intro"><b>Jadwalkan staff</b><small>Pilih akun, peran event, dan skill khusus dalam satu langkah.</small></div>
      <div class="assignment-form-fields"><label>Nama akun<select name="user_id" required><option value="">Pilih staff</option>${staffOptions}</select></label><label>Peran event<select name="assignment_type"><option value="crew">Crew</option><option value="pic">PIC Event</option></select></label><label>Posisi tim<select name="position_id" required><option value="">Pilih posisi</option>${positionOptions}</select></label></div>
      ${renderDayPicker(d)}<fieldset class="assignment-skill-picker"><legend>Skill khusus <span>fee skill otomatis masuk ke rekap payroll</span></legend><div class="assignment-skill-choices">${skillOptions || `<span class="muted">Belum ada skill aktif. Admin dapat menambahkannya di Tarif & Skill.</span>`}</div></fieldset>
      <p class="assignment-schedule-note">Satu orang boleh menangani beberapa event sehari. Peringatan memakai jam event dalam WIB, tanpa loading atau batas jeda perjalanan.</p>
      <div class="staff-availability" data-staff-availability role="status" aria-live="polite">Pilih staff untuk memeriksa jadwal.</div><p class="inline-form-error" data-assignment-error role="alert" hidden></p>
      <div class="assignment-form-footer"><div class="assignment-fee-hint"><span>Fee dasar mengikuti tarif akun yang dipilih.</span><span>Uang makan ${idr(mealFee)} setelah check-out selesai · tambahan skill <b data-assignment-skill-total>${idr(0)}</b></span></div><button class="button button-primary button-small" type="submit">＋ Simpan penugasan</button></div>
    </form>` : ""}
    <h3 class="drawer-section-title">WhatsApp event</h3><div class="workflow-card"><div class="workflow-card-head"><b>Status grup</b>${statusBadge(groupMeta,d.group_status)}</div><div class="workflow-card-body"><div class="workflow-meta">${d.group_link ? `<a href="${escapeHtml(d.group_link)}" target="_blank" rel="noopener">Buka tautan undangan ↗</a>` : "Grup dibuat manual oleh Event Coordinator."}</div>
      ${has("events.whatsapp.manage") ? `<div class="group-form"><input id="group-link" value="${escapeHtml(d.group_link || "")}" placeholder="Tautan undangan grup (opsional)"><button class="button button-light button-small" data-action="group-status" data-id="${d.id}" data-group-status="${d.group_status === "not_created" ? "group_created" : "invites_sent"}">${d.group_status === "not_created" ? "Tandai grup dibuat" : "Tandai undangan terkirim"}</button></div>` : ""}
      <div style="margin-top:10px"><button class="text-link" data-action="copy-wa" data-id="${d.id}">Salin template pesan WhatsApp ↗</button></div>
    </div></div>`;
}

function renderAdvanceTab(d) {
  const actions = advanceActionButtons({ ...d, id: d.id });
  const current = advanceMeta[d.advance_status] || advanceMeta.not_submitted;
  const ownPic=d.assignments.some((a)=>Number(a.user_id)===Number(state.data.user.id)&&a.assignment_type==="pic");
  const canUpload=ownPic&&d.advance_status==="transferred";
  const docs=(d.advance_documents||[]).map((doc)=>`<div class="advance-doc-card"><span class="advance-doc-icon">${doc.mime_type==="application/pdf"?"PDF":"XLS"}</span><div><b>${escapeHtml(doc.original_filename)}</b><small>${escapeHtml(doc.uploader_name)} · ${escapeHtml(attendanceTimeLabel(doc.uploaded_at))} · ${(doc.file_size/1024/1024).toFixed(2)} MB</small></div><a class="button button-light button-small" href="/api/advance-documents/${doc.id}/download">Unduh</a></div>`).join("");
  const uploader=canUpload?`<section class="advance-return-panel"><div class="advance-return-copy"><span class="advance-return-mark">↩</span><div><b>Pengembalian / pertanggungjawaban</b><p>Unggah rekap penggunaan dan bukti pengeluaran uang jalan. Format PDF, XLS, atau XLSX; maksimal 10 MB per file.</p></div></div><div class="advance-upload-controls"><input id="advance-document-file" data-event-id="${d.id}" type="file" accept=".pdf,.xls,.xlsx,application/pdf,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"><button class="button button-primary button-small" data-action="upload-advance-document" data-id="${d.id}">Unggah dokumen</button></div></section>`:"";
  const documentPanel=(d.advance_documents||[]).length?`<section class="advance-documents-list"><div class="advance-documents-head"><b>Dokumen pertanggungjawaban</b><span class="badge badge-green">${d.advance_documents.length} file</span></div>${docs}</section>`:"";
  return `<h3 class="drawer-section-title" style="margin-top:0">Status uang jalan</h3><div class="workflow-card"><div class="workflow-card-head"><b>PIC: ${escapeHtml(d.assignments.find((a) => a.assignment_type === "pic")?.full_name || "Belum ditugaskan")}</b>${badge(current[0],current[1])}</div><div class="workflow-card-body"><div class="progress-track"><span class="progress-fill" style="width:${({not_submitted:8,submitted:38,approved:68,rejected:42,transferred:100}[d.advance_status] || 8)}%"></span></div><div class="workflow-meta">${d.requested_at ? `Diajukan ${dateLong(d.requested_at)} · ` : ""}${d.reviewed_at ? `Direview ${datePart(d.reviewed_at)} · ` : ""}${d.transferred_at ? `Transfer dicatat ${dateLong(d.transferred_at)}` : "Nominal dicatat di CRM; aplikasi ini hanya menyimpan status."}</div><div class="workflow-buttons">${actions || `<span class="muted" style="font-size:10px">Belum ada tindakan untuk role ini.</span>`}</div></div></div>${uploader}${documentPanel}`;
}

function renderWarehouseTab(d) {
  const [action,label] = warehouseNext(d.warehouse_status);
  const detail = warehouseMeta[d.warehouse_status] || warehouseMeta.needs_prep;
  const buttons = has("warehouse.update") ? `<div class="workflow-buttons">${action ? `<button class="button button-primary button-small" data-action="warehouse" data-id="${d.id}" data-warehouse-action="${action}">${label}</button>` : ""}${d.warehouse_status !== "returned" ? `<button class="button button-danger button-small" data-action="warehouse" data-id="${d.id}" data-warehouse-action="issue">Tandai kendala</button>` : ""}</div>` : "";
  return `<h3 class="drawer-section-title" style="margin-top:0">Peralatan event</h3><div class="workflow-card"><div class="workflow-card-head"><b>Checklist kesiapan</b>${badge(detail[0],detail[1])}</div><div class="workflow-card-body"><div class="workflow-meta">Event-level check: petugas menandai alat siap dibawa lalu mengonfirmasi alat sudah kembali. Bukan pencatatan stok per item.</div><div class="workflow-meta">${d.prepared_at ? `Disiapkan ${dateLong(d.prepared_at)} · ` : ""}${d.returned_at ? `Kembali ${dateLong(d.returned_at)}` : ""}</div>${buttons}<div class="copy-box" style="margin-top:12px">Catatan: ${escapeHtml(d.warehouse_note || "Belum ada catatan kendala.")}</div></div></div>`;
}

function renderDesignTab(d) {
  const stage = designMeta[d.design_status] || designMeta.brief_needed;
  const task = state.data.design_tasks.find((t) => t.event_id === d.id);
  return `<h3 class="drawer-section-title" style="margin-top:0">Tahap desain</h3><div class="workflow-card"><div class="workflow-card-head"><b>${escapeHtml(task?.title || "Desain event")}</b>${badge(stage[0],stage[1])}</div><div class="workflow-card-body"><div class="status-track-mini" style="margin:7px 0 10px">${designStages.map(([code],i) => `<i class="track-step ${designStages.findIndex(([s]) => s===d.design_status) > i ? "done" : designStages[i][0]===d.design_status ? "current" : ""}"></i>`).join("")}</div><div class="workflow-meta">${task?.assignee_name ? `PIC design: ${escapeHtml(task.assignee_name)} · ` : ""}${task?.due_at ? `Deadline ${dateLong(task.due_at)}` : "Belum ada tenggat."}</div></div></div>${renderDesignForms(d)}`;
}

async function refreshData() {
  state.data = await api("/api/bootstrap");
  renderShell();
  renderPage();
  if (state.drawer) await openEvent(state.drawer.id,state.drawerTab);
}

async function runAction(element) {
  const action = element.dataset.action;
  try {
    if (action === "navigate") { setPage(element.dataset.page); return; }
    if (action === "configure-tab") { state.configureTab = element.dataset.tab; renderPage(); return; }
    if (action === "reset-role-default") {
      if (!window.confirm("Kembalikan hak akses role ini ke pengaturan default?")) return;
      await api("/api/configure/roles", { method: "POST", body: JSON.stringify({ role: element.dataset.role, reset_default: true }) });
      toast("Hak akses role dikembalikan ke default."); await refreshData(); return;
    }
    if (action === "open-event") { await openEvent(element.dataset.id); return; }
    if (action === "close-drawer") { openEventSequence++;state.drawer = null; $("#modal-root").innerHTML = ""; return; }
    if (action === "close-attendance-camera" || action === "attendance-camera-backdrop") { closeAttendanceCamera(); return; }
    if (action === "capture-selfie") { await captureAttendancePhoto(); return; }
    if (action === "retake-selfie") { retakeAttendancePhoto(); return; }
    if (action === "submit-attendance-photo") { await submitAttendancePhoto(); return; }
    if (action === "drawer-tab") { state.drawerTab = element.dataset.tab; renderDrawer(); return; }
    if (action === "clear-event") {
      const confirmText = has("kpi.evaluate_crew")
        ? "Tandai event clear? Setelah ini, isi penilaian untuk setiap Crew dan PIC yang dijadwalkan."
        : "Tandai event clear? Event Coordinator akan mengisi penilaian Crew dan PIC setelahnya.";
      if (!window.confirm(confirmText)) return;
      await api(`/api/events/${element.dataset.id}/performance`, { method: "POST", body: JSON.stringify({ action: "clear" }) });
      state.drawerTab = "performance";
      toast(has("kpi.evaluate_crew") ? "Event ditandai clear. Form penilaian Crew/PIC sudah dibuka." : "Event ditandai clear. Event Coordinator dapat melanjutkan penilaian.");
      await refreshData(); return;
    }
    if (action === "sync-calendar") {
      await syncByButton(element); return;
    }
    if (action === "schedule-without-code") {
      const projectCode=$(`[data-project-code-for="${element.dataset.id}"]`)?.value.trim() || "";
      const prompt=projectCode ? `Jadwalkan event ini dengan kode CRM ${projectCode}?` : "Jadwalkan event ini dengan kode operasional sementara? Admin dapat memasangkan kode CRM nanti.";
      if (!window.confirm(prompt)) return;
      const duration=$(`[data-duration-for="${element.dataset.id}"]`)?.value;
      const result=await api(`/api/calendar-queue/${element.dataset.id}/promote`, {method:"POST",body:JSON.stringify({project_code:projectCode,is_full_day:duration === undefined ? undefined : duration === "1"})});
      toast(result.temporary ? "Event dijadwalkan dengan kode operasional sementara." : "Event dijadwalkan dengan kode CRM."); await refreshData(); return;
    }
    if (action === "attach-project-code") {
      const code=window.prompt("Masukkan kode project CRM untuk event ini:","");
      if (code===null) return;
      await api(`/api/events/${element.dataset.id}/project-code`,{method:"POST",body:JSON.stringify({project_code:code.trim()})});
      toast("Kode CRM dipasangkan; kode operasional tetap tersimpan."); await refreshData(); return;
    }
    if (action === "change-temp-code") {
      const code=window.prompt("Ubah kode operasional sementara. Kode CRM dapat dipasangkan Admin nanti:",element.dataset.code || "");
      if (code===null) return;
      const result=await api(`/api/events/${element.dataset.id}/temporary-code`,{method:"POST",body:JSON.stringify({project_code:code.trim()})});
      toast(`Kode operasional diperbarui menjadi ${result.project_code}.`); await refreshData(); return;
    }
    if (action === "export-events") {
      const start=$("#events-export-start")?.value || ""; const end=$("#events-export-end")?.value || "";
      if(!start || !end || start>end){toast("Pilih rentang tanggal jadwal yang valid.","error");return;}
      const format=element.dataset.format || "xlsx";
      const blob=await api(`/api/events/export?${new URLSearchParams({start,end,format})}`,{download:true});
      const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=`captureit-jadwal-event-${start}-to-${end}.${format}`;a.click();URL.revokeObjectURL(url);
      toast(`Jadwal event ${format==="xlsx"?"Excel":"CSV"} berhasil diekspor.`);return;
    }
    if (action === "advance") {
      const labels = { submit: "Ajukan uang jalan?", approve: "Setujui pengajuan ini?", reject: "Tolak pengajuan ini?", transfer: "Catat transfer selesai?" };
      if (!window.confirm(labels[element.dataset.advanceAction])) return;
      await api(`/api/events/${element.dataset.id}/advance`, { method: "POST", body: JSON.stringify({ action: element.dataset.advanceAction }) });
      toast("Status uang jalan diperbarui."); await refreshData(); return;
    }
    if (action === "upload-advance-document") {
      const input=$("#advance-document-file"); const file=input?.files?.[0];
      if(!file){toast("Pilih file Excel atau PDF terlebih dahulu.","error");return;}
      if(file.size>10*1024*1024){toast("Ukuran file maksimal 10 MB.","error");return;}
      const ext=file.name.split(".").pop().toLowerCase();
      const mimeByExt={pdf:"application/pdf",xlsx:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",xls:"application/vnd.ms-excel"};
      if(!mimeByExt[ext]){toast("Format yang didukung: PDF, XLS, atau XLSX.","error");return;}
      const dataUrl=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result||""));reader.onerror=()=>reject(new Error("File tidak dapat dibaca."));reader.readAsDataURL(file);});
      const encoded=dataUrl.slice(dataUrl.indexOf(","));
      await api(`/api/events/${element.dataset.id}/advance/documents`,{method:"POST",body:JSON.stringify({filename:file.name,file:`data:${mimeByExt[ext]};base64${encoded}`})});
      toast("Dokumen pertanggungjawaban berhasil diunggah."); await refreshData(); return;
    }
    if (action === "warehouse") {
      let note = "";
      if (element.dataset.warehouseAction === "issue") note = window.prompt("Catatan kendala alat:", "") || "";
      await api(`/api/events/${element.dataset.id}/warehouse`, { method: "POST", body: JSON.stringify({ action: element.dataset.warehouseAction, note }) });
      toast("Status kesiapan alat diperbarui."); await refreshData(); return;
    }
    if (action === "attendance") {
      await startAttendanceCapture(element); return;
    }
    if (action === "inhouse-attendance") {
      await startAttendanceCapture(element); return;
    }
    if (action === "mark-absent") {
      const note = window.prompt("Catatan tidak hadir:", "") || "";
      await api(`/api/events/${element.dataset.id}/attendance`, { method: "POST", body: JSON.stringify({ assignment_id: Number(element.dataset.assignmentId), action: "absent", note, ...(element.dataset.workDate ? { work_date: element.dataset.workDate } : {}) }) });
      toast("Status kehadiran diperbarui."); await refreshData(); return;
    }
    if (action === "remove-assignment") {
      if (!window.confirm("Hapus penugasan ini dari event?")) return;
      await api(`/api/events/${element.dataset.id}/assignment`, { method: "POST", body: JSON.stringify({ action: "remove", assignment_id: Number(element.dataset.assignmentId) }) });
      toast("Penugasan dihapus."); await refreshData(); return;
    }
    if (action === "group-status") {
      const link = $("#group-link")?.value || "";
      const result = await api(`/api/events/${element.dataset.id}/group`, { method: "POST", body: JSON.stringify({ status: element.dataset.groupStatus, group_link: link }) });
      toast(result.status === "invites_sent" ? "Undangan grup ditandai terkirim." : "Grup WhatsApp ditandai dibuat."); await refreshData(); return;
    }
    if (action === "copy-wa") {
      const d = state.drawer;
      const crew = d.assignments.filter((a) => a.assignment_type === "crew").map((a) => a.full_name).join(", ") || "(crew menyusul)";
      const pic = d.assignments.find((a) => a.assignment_type === "pic")?.full_name || "(PIC menyusul)";
      const message = `Halo team Capture It!\n\nGrup untuk event ${d.project_code} — ${d.title}.\nTanggal: ${dateLong(d.starts_at)} pukul ${timePart(d.starts_at)}\nLokasi: ${d.location}\nPIC: ${pic}\nCrew: ${crew}\n\nMohon konfirmasi kehadiran dan cek detail tugas masing-masing. Terima kasih!`;
      await navigator.clipboard.writeText(message);
      toast("Template pesan WhatsApp disalin."); return;
    }
    if (action === "design-status") {
      await api(`/api/design/${element.dataset.id}/status`, { method: "POST", body: JSON.stringify({ status: element.dataset.status }) });
      toast("Tahap desain diperbarui."); await refreshData(); return;
    }
    if (action === "select-rate-user") {
      const rate = (state.data.rates || []).find((row) => String(row.user_id) === String(element.dataset.id));
      if (rate) {
        state.rateUserId = String(rate.user_id);
        $("#rate-user").value = rate.user_id;
        $("[data-rate-input]").value = rate.base_fee_rupiah || 0;
        $("[data-rate-date]").value = localDateISO();
        $("#rate-form").scrollIntoView({ behavior: "smooth", block: "center" });
      }
      return;
    }
    if (action === "open-account-password") {
      openAccountPasswordDialog(element);
      return;
    }
    if (action === "close-account-password") {
      closeAccountPasswordDialog();
      return;
    }
    if (action === "delete-user") {
      const name = element.dataset.name || "akun ini";
      if (element.disabled) return;
      if (!window.confirm(`Hapus akun ${name} secara permanen? Riwayat operasional tidak ikut dihapus dan akun tidak dapat dipulihkan.`)) return;
      if (window.prompt(`Ketik HAPUS untuk menghapus akun ${name}.`) !== "HAPUS") {
        toast("Penghapusan dibatalkan.", "error");
        return;
      }
      await api(`/api/users/${element.dataset.id}/update`, { method: "POST", body: JSON.stringify({ action: "delete", confirm: "HAPUS" }) });
      toast("Akun berhasil dihapus.");
      await refreshData();
      return;
    }
    if (action === "save-user-role") {
      const role = $(`[data-user-role="${element.dataset.id}"]`).value;
      await api(`/api/users/${element.dataset.id}/update`, { method: "POST", body: JSON.stringify({ action: "role", role }) });
      toast("Role akun diperbarui."); await refreshData(); return;
    }
    if (action === "toggle-user-active") {
      const active = element.dataset.nextActive === "true";
      if (!window.confirm(active ? "Aktifkan kembali akun ini?" : "Nonaktifkan akun ini?")) return;
      await api(`/api/users/${element.dataset.id}/update`, { method: "POST", body: JSON.stringify({ action: "active", active }) });
      toast(active ? "Akun diaktifkan kembali." : "Akun dinonaktifkan."); await refreshData(); return;
    }
    if (action === "save-skill") {
      const id = Number(element.dataset.id);
      const name = $(`[data-skill-name="${id}"]`).value;
      const extra_fee_rupiah = Number($(`[data-skill-fee="${id}"]`).value);
      await api("/api/skills", { method: "POST", body: JSON.stringify({ id, name, extra_fee_rupiah }) });
      toast("Skill dan fee tambahan diperbarui."); await refreshData(); return;
    }
    if (action === "save-inhouse-salaries") {
      if(element.disabled)return;
      const inputs=$$('[data-inhouse-salary]').filter(input=>input.dataset.salaryEdited==='1'||Number(input.value)!==Number(input.dataset.originalSalary));
      if(!inputs.length){toast('Belum ada angka gaji yang diubah.');return;}
      if(inputs.some(input=>!input.value||!input.checkValidity()))throw new Error('Masukkan nominal gaji bulat yang valid.');
      const effective_from=$('#salary-effective-from').value;
      if(!effective_from)throw new Error('Pilih tanggal berlaku gaji.');
      const reason=$('#salary-change-reason').value;
      const entries=inputs.map(input=>({user_id:Number(input.dataset.inhouseSalary),monthly_salary_rupiah:Number(input.value)}));
      element.disabled=true;
      try {
        const saved=await api('/api/inhouse-payroll/salaries',{method:'POST',body:JSON.stringify({entries,effective_from,reason})});
        toast(`${saved.updated} perubahan gaji disimpan dalam riwayat.`);renderPage();
      } finally {element.disabled=false;}
      return;
    }
    if (action === "save-inhouse-payroll") {
      const start = $("#inhouse-payroll-start")?.value || "";
      const end = $("#inhouse-payroll-end")?.value || "";
      const pay_date = $("#inhouse-payroll-pay-date")?.value || "";
      if (!start || !end || start > end || !pay_date) { toast("Lengkapi periode payroll dan tanggal bayar.", "error"); return; }
      const entries = $$('[data-inhouse-payout-row]').map((row) => ({
        user_id: Number(row.dataset.inhousePayoutRow),
        allowance_rupiah: Number(row.querySelector("[data-inhouse-allowance]")?.value || 0),
        deduction_rupiah: Number(row.querySelector("[data-inhouse-deduction]")?.value || 0),
        note: row.querySelector("[data-inhouse-note]")?.value || "",
      }));
      await api("/api/inhouse-payroll", { method: "POST", body: JSON.stringify({ start, end, pay_date, entries }) });
      toast("Draft payroll In-house dan snapshot absensi disimpan."); await renderPage(); return;
    }
    if (action === "record-inhouse-payroll-transfer") {
      const referenceInput = window.prompt("Nomor referensi transfer (opsional):", "");
      if (referenceInput === null) return;
      if (!window.confirm("Anda sudah mentransfer gaji In-house ini? Konfirmasi akan menerbitkan slip di akun orang tersebut.")) return;
      await api(`/api/inhouse-payroll/payout/${element.dataset.id}/transfer`, { method: "POST", body: JSON.stringify({ transfer_reference: referenceInput.trim() }) });
      toast("Transfer dicatat. Slip gaji tersedia di akun In-house tersebut."); await renderPage(); return;
    }
    if (action === "record-freelancer-payroll-transfer") {
      const reference = window.prompt("Nomor referensi transfer (opsional):", "");
      if (reference === null) return;
      if (!window.confirm("Transfer payroll Crew/PIC periode ini sudah dilakukan? Pencatatan ini akan menerbitkan slip untuk akun yang masuk dalam batch.")) return;
      await api(`/api/payroll/batch/${element.dataset.id}/transfer`, {method:"POST",body:JSON.stringify({transfer_reference:reference.trim()})});
      toast("Transfer dicatat. Slip honor event tersedia untuk Crew/PIC terkait."); await renderPage(); return;
    }
    if (action === "export-payroll") {
      const start = $("#payroll-start")?.value || state.payrollStart;
      const end = $("#payroll-end")?.value || state.payrollEnd;
      const format = element.dataset.format || "xlsx";
      const blob = await api("/api/payroll/export", { method: "POST", body: JSON.stringify({ start, end, format }), download: true });
      const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `captureit-payroll-${start}-to-${end}.${format}`; a.click(); URL.revokeObjectURL(url);
      toast(`File ${format === "xlsx" ? "Excel" : "CSV"} payroll mingguan berhasil dibuat.`); await refreshData(); return;
    }
    if (action === "export-inhouse-attendance") {
      const start=$("#inhouse-export-start")?.value || state.inhouseExportStart;
      const end=$("#inhouse-export-end")?.value || state.inhouseExportEnd;
      if(!start || !end || start>end){toast("Pilih rentang tanggal absensi yang valid.","error");return;}
      const format=element.dataset.format || "xlsx";
      const blob=await api("/api/inhouse-attendance/export",{method:"POST",body:JSON.stringify({start,end,format}),download:true});
      const url=URL.createObjectURL(blob); const a=document.createElement("a"); a.href=url; a.download=`captureit-inhouse-attendance-${start}-to-${end}.${format}`; a.click(); URL.revokeObjectURL(url);
      toast(`Export absensi In-house ${format==="xlsx"?"Excel":"CSV"} berhasil diunduh.`); return;
    }
    if (action === "score") {
      state.score = Number(element.dataset.score);
      $$(".score-button").forEach((button) => button.classList.toggle("selected", Number(button.dataset.score) <= state.score));
      return;
    }
    if (action === "logout") { openLogoutDialog(); return; }
  } catch (error) {
    toast(error.message || "Tindakan gagal.", "error");
  }
}

async function submitAssignment(form) {
  syncAssignmentForm(form);
  await saveStaffAssignment(form);
}

function syncAssignmentForm(form) {
  const role = form.querySelector("[name=assignment_type]");
  const position = form.querySelector("[name=position_id]");
  [...position.options].forEach((option) => {
    if (!option.value) return;
    option.disabled = option.dataset.kind !== role.value;
  });
  if (!position.value || [...position.options].find((option) => option.value === position.value)?.disabled) {
    position.value = [...position.options].find((option) => option.value && !option.disabled)?.value || "";
  }
  const total = [...form.querySelectorAll("input[name=skill_ids]:checked")].reduce((sum, input) => sum + Number(input.dataset.fee || 0), 0);
  const totalNode = form.querySelector("[data-assignment-skill-total]");
  if (totalNode) totalNode.textContent = idr(total);
}

async function submitKpi(form) {
  const data = new FormData(form);
  await api("/api/kpi", { method: "POST", body: JSON.stringify({ subject_id: Number(data.get("subject_id")), category: data.get("category"), score: state.score, note: data.get("note"), review_period: data.get("review_period") }) });
  state.score = 5; toast("Evaluasi KPI tersimpan."); await refreshData();
}

async function submitEventPerformance(form) {
  const data = new FormData(form);
  const criteria = state.data.performance_criteria || [];
  const reviews = [...form.querySelectorAll("[data-performance-assignment]")].map((section) => {
    const scores = {};
    for (const criterion of criteria) scores[criterion.code] = data.get(`score_${section.dataset.performanceAssignment}_${criterion.code}`);
    return {
      assignment_id: Number(section.dataset.performanceAssignment),
      scores,
      note: data.get(`note_${section.dataset.performanceAssignment}`) || "",
    };
  });
  const result = await api(`/api/events/${form.dataset.eventId}/performance`, { method: "POST", body: JSON.stringify({ reviews }) });
  toast(result.reviews_created ? `Event clear. ${result.reviews_created} penilaian masuk ke Performance Crew/PIC.` : "Event berhasil ditandai clear.");
  await refreshData();
}

async function submitRate(form) {
  if(form.dataset.busy)return;
  const data = new FormData(form);
  form.dataset.busy='1';const button=form.querySelector('button[type="submit"]');button.disabled=true;
  try {
    await api("/api/rates", { method: "POST", body: JSON.stringify({ user_id: Number(data.get("user_id")), base_fee_rupiah: Number(data.get("base_fee_rupiah")), effective_from: data.get("effective_from"),reason:data.get('reason')||'' }) });
    toast("Fee dan riwayat disimpan; nilai penugasan serta payroll lama tetap.");
    await refreshData();
  } finally {delete form.dataset.busy;button.disabled=false;}
}

async function submitCreateAccount(form) {
  const data = new FormData(form);
  await api("/api/users", { method: "POST", body: JSON.stringify({ full_name: data.get("full_name"), email: data.get("email"), role: data.get("role"), password: data.get("password") }) });
  toast("Akun berhasil dibuat.");
  await refreshData();
}

function openAccountPasswordDialog(button) {
  const dialog = $("#account-password-dialog");
  const form = $("#account-password-form");
  if (!dialog || !form) return;
  form.dataset.userId = button.dataset.id;
  form.reset();
  $("#account-password-account").innerHTML = `<b>${escapeHtml(button.dataset.name)}</b><small>${escapeHtml(button.dataset.email)}</small>`;
  $("#account-password-error").hidden = true;
  dialog.showModal();
  form.querySelector("[name=password]").focus();
}

function closeAccountPasswordDialog() {
  const dialog = $("#account-password-dialog");
  if (dialog?.open) dialog.close();
}

async function submitAccountPassword(form) {
  const data = new FormData(form);
  const password = String(data.get("password") || "");
  const passwordConfirmation = String(data.get("password_confirmation") || "");
  if (password !== passwordConfirmation) throw new Error("Konfirmasi password tidak sama.");
  const result = await api(`/api/users/${form.dataset.userId}/update`, {
    method: "POST",
    body: JSON.stringify({ action: "password", password, password_confirmation: passwordConfirmation }),
  });
  closeAccountPasswordDialog();
  toast(result.current_session_revoked ? "Password diperbarui. Sesi Anda dicabut; silakan masuk kembali." : "Password akun berhasil diperbarui.");
  if (result.current_session_revoked) {
    setTimeout(() => window.location.reload(), 500);
  } else {
    await refreshData();
  }
}

async function imageFileAsDataUrl(file, maxDimension, maxDataUrlLength) {
  if (!file || !["image/jpeg", "image/png"].includes(file.type)) throw new Error("Pilih gambar JPG atau PNG.");
  let bitmap;
  try { bitmap = await createImageBitmap(file); }
  catch { throw new Error("Gambar tidak dapat dibuka. Coba simpan sebagai JPG atau PNG lalu pilih lagi."); }
  let scale = Math.min(1, maxDimension / Math.max(bitmap.width, bitmap.height));
  for (let resize = 0; resize < 7; resize++, scale *= 0.82) {
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext("2d", { alpha: false });
    context.drawImage(bitmap, 0, 0, width, height);
    for (const quality of [0.88, 0.78, 0.68, 0.58, 0.48]) {
      const dataUrl = canvas.toDataURL("image/jpeg", quality);
      if (dataUrl.length <= maxDataUrlLength) { bitmap.close?.(); return dataUrl; }
    }
  }
  bitmap.close?.();
  throw new Error("Ukuran gambar masih terlalu besar. Pilih foto dengan resolusi lebih kecil.");
}

async function submitProfile(form) {
  const data = new FormData(form);
  const file = $("#profile-photo-file")?.files?.[0];
  const payload = { full_name: data.get("full_name"), phone: data.get("phone") };
  if (file) payload.photo = await imageFileAsDataUrl(file, 640, 390_000);
  await api("/api/profile", { method: "POST", body: JSON.stringify(payload) });
  toast("Profil berhasil disimpan.");
  await refreshData();
}

async function submitProfilePassword(form) {
  const data = new FormData(form);
  const currentPassword = String(data.get("current_password") || "");
  const newPassword = String(data.get("new_password") || "");
  const confirmation = String(data.get("password_confirmation") || "");
  if (newPassword !== confirmation) throw new Error("Konfirmasi password baru tidak sama.");
  const result = await api("/api/profile/password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword, password_confirmation: confirmation }),
  });
  form.reset();
  toast(`Password berhasil diubah. ${result.sessions_revoked || 0} sesi perangkat lain dicabut.`);
}

async function submitKtp(form) {
  const file = form.querySelector('input[type="file"]')?.files?.[0];
  if (!file) throw new Error("Pilih gambar KTP terlebih dahulu.");
  const image = await imageFileAsDataUrl(file, 1600, 820_000);
  await api("/api/profile/ktp", { method: "POST", body: JSON.stringify({ side: form.dataset.side, image }) });
  toast(`KTP sisi ${form.dataset.side === "front" ? "depan" : "belakang"} berhasil diunggah.`);
  await refreshData();
}

async function submitProjectCode(form) {
  const data = new FormData(form);
  await api(`/api/calendar-code/${form.dataset.intakeId}`, { method: "POST", body: JSON.stringify({ project_code: data.get("project_code") }) });
  toast("Kode CRM dipasangkan. Event sekarang tersedia di jadwal operasional.");
  await refreshData();
}

async function submitNewSkill(form) {
  const data = new FormData(form);
  await api("/api/skills", { method: "POST", body: JSON.stringify({ name: data.get("name"), extra_fee_rupiah: Number(data.get("extra_fee_rupiah")) }) });
  toast("Skill khusus berhasil ditambahkan."); await refreshData();
}

async function submitAppearance(form) {
  const data = new FormData(form);
  const colors = Object.fromEntries(["primary", "accent", "sidebar", "background"].map((key) => [key, data.get(key)]));
  const result = await api("/api/configure/appearance", { method: "POST", body: JSON.stringify({ colors }) });
  applyBranding(result.branding);
  toast("Warna aplikasi berhasil disimpan.");
  await refreshData();
}

async function submitBrandAsset(form) {
  const file = form.querySelector('input[type="file"]')?.files?.[0];
  if (!file) throw new Error("Pilih file gambar terlebih dahulu.");
  if (file.size > 500_000) throw new Error("File harus berukuran maksimal 500 KB.");
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("File tidak dapat dibaca."));
    reader.readAsDataURL(file);
  });
  const kind = form.dataset.kind;
  const result = await api("/api/configure/asset", { method: "POST", body: JSON.stringify({ kind, image: dataUrl }) });
  applyBranding(result.branding);
  toast(`${kind === "logo" ? "Logo" : "Favicon"} berhasil diperbarui.`);
  await refreshData();
}

async function submitRoleAcl(form) {
  const permissions = $$('input[name="permission"]:checked', form).filter((input) => !input.disabled).map((input) => input.value);
  await api("/api/configure/roles", { method: "POST", body: JSON.stringify({ role: form.dataset.role, permissions }) });
  toast("Hak akses role berhasil disimpan.");
  await refreshData();
}

async function syncByButton(button) {
  if (button) button.disabled = true;
  try {
    const result = await api("/api/google/sync", { method: "POST", body: "{}" });
    toast(result.message, result.ok ? "success" : "error");
    if (result.ok) await refreshData();
  } catch (error) { toast(error.message,"error"); }
  finally { if (button) { button.disabled = false; button.innerHTML = `<span class="sync-icon">↻</span><span>Sync Calendar</span>`; } }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (button) {
    if (button.dataset.action === "backdrop-close" && event.target !== button) return;
    if (button.dataset.action === "attendance-camera-backdrop" && event.target !== button) return;
    if (button.dataset.action === "backdrop-close") { state.drawer = null; $("#modal-root").innerHTML = ""; return; }
    runAction(button);
  }
});

document.addEventListener("submit", async (event) => {
  if (event.target.id === "login-form") {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true; button.innerHTML = `<span class="spinner"></span> Memeriksa akun`;
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify({ email: $("#login-email").value, password: $("#login-password").value }) });
      $("#login-error").hidden = true;
      await loadApp();
    } catch (error) {
      $("#login-error").textContent = error.message; $("#login-error").hidden = false;
    } finally { button.disabled = false; button.innerHTML = `Masuk ke workspace <span>↗</span>`; }
  }
  if (event.target.id === "assignment-form") {
    event.preventDefault(); try { await submitAssignment(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.id === "kpi-form") {
    event.preventDefault(); try { await submitKpi(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.id === "event-performance-form") {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try { await submitEventPerformance(event.target); }
    catch (err) { toast(err.message,"error"); button.disabled = false; }
  }
  if (event.target.id === "rate-form") {
    event.preventDefault(); try { await submitRate(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.id === "create-account-form") {
    event.preventDefault(); try { await submitCreateAccount(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.id === "account-password-form") {
    event.preventDefault();
    const submit = event.target.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await submitAccountPassword(event.target);
    } catch (err) {
      const error = $("#account-password-error");
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      submit.disabled = false;
    }
  }
  if (event.target.id === "appearance-form") {
    event.preventDefault(); try { await submitAppearance(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.matches("[data-brand-upload]")) {
    event.preventDefault(); try { await submitBrandAsset(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.id === "role-acl-form") {
    event.preventDefault(); try { await submitRoleAcl(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.classList.contains("calendar-code-form")) {
    event.preventDefault(); try { await submitProjectCode(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.matches("[data-full-day-event]")) {
    event.preventDefault();
    try {
      const data=new FormData(event.target);
      await api(`/api/events/${event.target.dataset.fullDayEvent}/full-day`,{method:"POST",body:JSON.stringify({is_full_day:data.get("is_full_day")==="1"})});
      toast("Durasi event diperbarui; nilai ini akan dipakai untuk uang makan dan bertahan saat Calendar sync.");
      await openEvent(event.target.dataset.fullDayEvent);
    } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.id === "add-skill-form") {
    event.preventDefault(); try { await submitNewSkill(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.id === "profile-form") {
    event.preventDefault(); try { await submitProfile(event.target); } catch (err) { toast(err.message,"error"); }
  }
  if (event.target.id === "profile-password-form") {
    event.preventDefault();
    const form = event.target;
    const submit = form.querySelector("button[type=submit]");
    const error = form.querySelector("[data-profile-password-error]");
    submit.disabled = true;
    error.hidden = true;
    try {
      await submitProfilePassword(form);
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      submit.disabled = false;
    }
  }
  if (event.target.matches("[data-ktp-form]")) {
    event.preventDefault(); try { await submitKtp(event.target); } catch (err) { toast(err.message,"error"); }
  }
});

document.addEventListener("input", (event) => {
  if (event.target.matches("[data-inhouse-allowance], [data-inhouse-deduction]")) {
    const row = event.target.closest("[data-inhouse-payout-row]");
    const salary = Number(row?.dataset.salary || 0);
    const allowance = Number(row?.querySelector("[data-inhouse-allowance]")?.value || 0);
    const deduction = Number(row?.querySelector("[data-inhouse-deduction]")?.value || 0);
    const total = row?.querySelector("[data-inhouse-total]");
    if (total) total.textContent = idr(salary + allowance - deduction);
  }
  if (event.target.matches('[data-filter="queue"]')) {
    const query = event.target.value.trim().toLowerCase();
    $$("[data-queue-row]").forEach((row) => { row.hidden = !row.dataset.search.includes(query); });
  }
  if (event.target.id === "staff-search") {
    const query = event.target.value.trim().toLowerCase();
    $$('[data-staff-row]').forEach((row) => { row.hidden = !row.dataset.search.includes(query); });
  }
  if (event.target.matches('[data-filter="events"]')) {
    state.search = event.target.value;
    const filtered = filteredEvents();
    $("#events-body").innerHTML = renderEventTableRows(filtered);
    if ($("#event-count")) $("#event-count").textContent = `${filtered.length} dari ${state.data.events.length} event`;
  }
  if (event.target.id === "global-search") {
    state.search = event.target.value;
    if (state.page !== "events") setPage("events");
    else {
      const field = $('[data-filter="events"]');
      if (field) { field.value = state.search; field.dispatchEvent(new Event("input", { bubbles: true })); }
    }
  }
});

document.addEventListener("change", async (event) => {
  if (event.target.id === "payslip-year") { state.payslipYear=event.target.value; renderPage(); }
  if (event.target.id === "profile-photo-file" && event.target.files?.[0]) {
    const file = event.target.files[0];
    if (!["image/jpeg", "image/png"].includes(file.type)) { toast("Pilih foto JPG atau PNG.", "error"); event.target.value = ""; return; }
    const previewUrl = URL.createObjectURL(file);
    const holder = $(".profile-avatar");
    if (holder) holder.innerHTML = `<img id="profile-photo-preview" src="${previewUrl}" alt="Pratinjau foto profil">`;
  }
  if (event.target.id === "rate-user") {
    state.rateUserId = event.target.value;
    const rate = (state.data.rates || []).find((row) => String(row.user_id) === String(state.rateUserId));
    if (rate) {
      $("[data-rate-input]").value = rate.base_fee_rupiah || 0;
      $("[data-rate-date]").value = localDateISO();
    }
  }
  if (event.target.matches("[data-kpi-subject-filter]")) {
    state.kpiSubjectId = event.target.value;
    renderPage();
  }
  if (event.target.matches("[data-performance-subject-filter]")) {
    state.performanceSubjectId = event.target.value;
    renderPage();
  }
  if (event.target.id === "configure-role-select") {
    state.configureRoleCode = event.target.value;
    renderPage();
  }
  if (event.target.matches("[data-filter-status]")) {
    state.eventStatus = event.target.value;
    const filtered = filteredEvents();
    $("#events-body").innerHTML = renderEventTableRows(filtered);
    if ($("#event-count")) $("#event-count").textContent = `${filtered.length} dari ${state.data.events.length} event`;
  }
  if (event.target.id === "event-month-filter") {
    state.eventMonth = event.target.value;
    renderPage();
  }
  if (event.target.id === "queue-month-filter") {
    state.queueMonth = event.target.value;
    renderPage();
  }
  if (event.target.closest("#assignment-form") && ["user_id", "assignment_type", "skill_ids", "work_days"].includes(event.target.name)) {
    syncAssignmentForm(event.target.closest("#assignment-form"));
  }
  if (event.target.id === "payroll-start" || event.target.id === "payroll-end") {
    const start = $("#payroll-start")?.value || "";
    const end = $("#payroll-end")?.value || "";
    if (start && end && start > end) { toast("Tanggal awal harus sama atau sebelum tanggal akhir.", "error"); return; }
    state.payrollStart = start;
    state.payrollEnd = end;
    if (start && end) renderPage();
  }
  if (event.target.id === "inhouse-date") { state.inhouseDate=event.target.value || localDateISO(); renderPage(); }
  if (event.target.id === "inhouse-export-start") state.inhouseExportStart=event.target.value;
  if (event.target.id === "inhouse-export-end") state.inhouseExportEnd=event.target.value;
  if (event.target.id === "inhouse-payroll-pay-date") state.inhousePayrollPayDate = event.target.value;
  if (event.target.id === "inhouse-payroll-start" || event.target.id === "inhouse-payroll-end") {
    const start = $("#inhouse-payroll-start")?.value || "";
    const end = $("#inhouse-payroll-end")?.value || "";
    if (start && end && start > end) { toast("Tanggal awal harus sama atau sebelum tanggal akhir.", "error"); return; }
    state.inhousePayrollStart = start;
    state.inhousePayrollEnd = end;
    if (start && end) renderPage();
  }
});

document.addEventListener("dragstart", (event) => {
  const card = event.target.closest(".kanban-card");
  if (!card || !has("design.update")) return;
  state.designDragId = Number(card.dataset.taskId);
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", String(state.designDragId));
});
document.addEventListener("dragover", (event) => {
  const column = event.target.closest(".kanban-column");
  if (column && state.designDragId) { event.preventDefault(); column.classList.add("drag-over"); }
});
document.addEventListener("dragleave", (event) => {
  const column = event.target.closest(".kanban-column");
  if (column && !column.contains(event.relatedTarget)) column.classList.remove("drag-over");
});
document.addEventListener("drop", async (event) => {
  const column = event.target.closest(".kanban-column");
  if (!column || !state.designDragId) return;
  event.preventDefault(); column.classList.remove("drag-over");
  try {
    await api(`/api/design/${state.designDragId}/status`, { method: "POST", body: JSON.stringify({ status: column.dataset.stage }) });
    toast("Tahap desain diperbarui."); await refreshData();
  } catch (err) { toast(err.message,"error"); }
  state.designDragId = null;
});

$("#sync-button").addEventListener("click", (event) => syncByButton(event.currentTarget));
$("#logout-button").addEventListener("click", openLogoutDialog);
$("#mobile-menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#global-search").focus(); }
  if (event.key === "Escape" && state.drawer && !$("#logout-dialog").open && $("#notification-panel").hidden) { state.drawer = null; $("#modal-root").innerHTML = ""; }
});

api("/api/branding").then(applyBranding).catch(() => {});
loadApp();
