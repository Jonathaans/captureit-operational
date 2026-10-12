/* In-house office hours: late / early-leave labels, schedule settings (Configure), event-date rules for crew. */

function punctualityInfo(row, schedule) {
  const lateGrace = Number(row?.late_grace_minutes ?? schedule?.late_grace_minutes ?? 0), earlyGrace = Number(row?.early_grace_minutes ?? schedule?.early_grace_minutes ?? 0);
  const out = [];
  if (row?.late_minutes != null && row.late_minutes > lateGrace) out.push({ text: `Terlambat ${row.late_minutes} menit`, tone: 'red' });
  if (row?.early_leave_minutes != null && row.early_leave_minutes > earlyGrace) out.push({ text: `Pulang lebih awal ${row.early_leave_minutes} menit`, tone: 'gold' });
  if (!out.length && (row?.late_minutes != null || row?.early_leave_minutes != null)) out.push({ text: 'Tepat waktu', tone: 'green' });
  return out;
}

function punctualityBadges(row, schedule) {
  return punctualityInfo(row, schedule).map((item) => `<span class="badge badge-${item.tone}">${escapeHtml(item.text)}</span>`).join(' ');
}

/* Crew may only check in on the event date; check-out also the day after (shifts that end past midnight). */
function eventDayRule(d, wibTodayValue) {
  const days = d.event_days || [];
  const today = wibTodayValue || wibToday();
  const yesterday = new Date(Date.parse(today) - 86400000).toISOString().slice(0, 10);
  return {
    canCheckIn: days.includes(today),
    canCheckOut: days.includes(today) || days.includes(yesterday),
    window: days.length > 1 ? `${days[0]} s/d ${days[days.length - 1]}` : (days[0] || ''),
  };
}

function renderInhouseScheduleSettings() {
  const s = state.data.inhouse_schedule || { work_start: '09:00', work_end: '18:00', late_grace_minutes: 0, early_grace_minutes: 0 };
  return `<section class="panel configure-panel"><div class="panel-head"><div><h3>Jam kerja In-house</h3><p>Dipakai untuk menandai keterlambatan check-in dan pulang lebih awal. Pengelola absensi menerima notifikasi, dan detailnya masuk ke export.</p></div></div>
    <form data-inhouse-schedule-form class="manual-event-form">
      <label class="field-label">Jam masuk<input class="field-input" type="time" name="work_start" value="${escapeHtml(s.work_start)}" required></label>
      <label class="field-label">Jam pulang<input class="field-input" type="time" name="work_end" value="${escapeHtml(s.work_end)}" required></label>
      <label class="field-label">Toleransi terlambat (menit)<input class="field-input" type="number" name="late_grace_minutes" min="0" max="120" value="${Number(s.late_grace_minutes)}" required></label>
      <label class="field-label">Toleransi pulang awal (menit)<input class="field-input" type="number" name="early_grace_minutes" min="0" max="120" value="${Number(s.early_grace_minutes)}" required></label>
      <p class="inline-form-error" data-schedule-error role="alert" hidden></p>
      <div class="manual-event-actions"><button class="button button-primary" type="submit">Simpan jam kerja</button></div>
      <small class="muted">Jam reguler untuk karyawan tanpa jadwal shift khusus. Atur shift per karyawan di Absensi In-house. Jam pulang yang lebih kecil dari jam masuk berarti pulang keesokan hari (WIB). Riwayat menyimpan jam dan toleransi saat absen masuk.</small>
    </form></section>`;
}

document.addEventListener('submit', async (event) => {
  const form = event.target.closest && event.target.closest('[data-inhouse-schedule-form]');
  if (!form) return;
  event.preventDefault();
  const err = form.querySelector('[data-schedule-error]'); err.hidden = true;
  const body = Object.fromEntries(['work_start', 'work_end', 'late_grace_minutes', 'early_grace_minutes'].map((name) => [name, form.elements[name].value]));
  const button = form.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    const result = await api('/api/settings/inhouse-schedule', { method: 'POST', body: JSON.stringify(body) });
    state.data.inhouse_schedule = result.schedule;
    toast('Jam kerja In-house disimpan.');
  } catch (error) { err.textContent = error.message; err.hidden = false; } finally { button.disabled = false; }
});

function shiftLabel(s) {
  return `${s.shift_name || 'Reguler'} · ${s.work_start}–${s.work_end}${s.overnight ? ' (+1 hari)' : ''} WIB`;
}

function renderOwnAttendance(result) {
  if (!has('attendance.inhouse.self') || state.data.user.employment_type !== 'inhouse') return '';
  const own = result.own_today, s = result.own_schedule || result.schedule || {};
  const action = !own || own.status === 'not_started' ? 'check_in' : own.status === 'checked_in' ? 'check_out' : null;
  const workDate = own?.work_date || result.today;
  const options = result.checkin_options || [];
  const selection = action === 'check_in' && options.length > 1 ? `<label class="field-label">Tanggal mulai shift<select class="field-select" id="attendance-shift-date">${options.map(o => `<option value="${escapeHtml(o.work_date)}">${escapeHtml(o.work_date)} · ${escapeHtml(shiftLabel(o))}</option>`).join('')}</select></label>` : '';
  return `<section class="panel inhouse-own-card"><div class="panel-head"><div><h3>Absensi saya</h3><p>Tanggal mulai ${escapeHtml(workDate)} · ${escapeHtml(shiftLabel(s))}</p></div>${badge(own?.status === 'checked_out' ? 'Sudah selesai' : own?.status === 'checked_in' ? 'Sedang bekerja' : 'Belum absen', own?.status === 'checked_out' ? 'green' : own?.status === 'checked_in' ? 'gold' : 'gray')}</div>
    <div class="panel-body inhouse-own-body"><div><b>${escapeHtml(state.data.user.full_name)}</b><small>${escapeHtml(state.data.user.department || 'In-house')}</small>
    <div class="attendance-own-times"><span>Masuk · ${own?.check_in_at ? escapeHtml(attendanceTimeLabel(own.check_in_at)) : 'belum tercatat'}</span><span>Pulang · ${own?.check_out_at ? escapeHtml(attendanceTimeLabel(own.check_out_at)) : 'belum tercatat'}</span></div>
    ${own?.status === 'checked_in' && workDate !== result.today ? `<p class="shift-overnight-note">Absen pulang akan menutup shift tanggal ${escapeHtml(workDate)}.</p>` : ''}
    <div class="punctuality-row">${punctualityBadges(own, s)}</div>${selection}</div>
    ${action ? `<button class="button ${action === 'check_in' ? 'button-primary' : 'button-gold'}" data-action="inhouse-attendance" data-attendance-kind="inhouse" data-attendance-action="${action}" data-work-date="${escapeHtml(workDate)}">${action === 'check_in' ? 'Absen masuk' : 'Absen pulang'} · buka kamera</button>` : '<span class="badge badge-green">Absensi hari ini lengkap</span>'}</div></section>`;
}

function renderShiftManager(result) {
  if (!result.can_schedule) return '';
  state.shiftRoster = result.shift_roster || [];
  const s = result.schedule || {}, day = result.selected_date || result.today;
  return `<section class="panel shift-manager"><div class="panel-head"><div><h3>Jadwal shift karyawan</h3><p>Atur satu shift per tanggal mulai. Jam pulang yang lebih kecil berarti keesokan hari. Tanpa shift khusus, karyawan mengikuti jam reguler.</p></div></div>
    <div class="panel-body"><form id="inhouse-shift-form" class="shift-form">
      <label class="field-label">Karyawan<select class="field-select" name="user_id" required><option value="">Pilih karyawan</option>${(result.shift_employees || []).map(p => `<option value="${p.id}">${escapeHtml(p.full_name)} · ${escapeHtml(p.department)}</option>`).join('')}</select></label>
      <label class="field-label">Nama shift<input class="field-input" name="shift_name" maxlength="80" placeholder="Contoh: Pagi / Sore / Malam" required></label>
      <label class="field-label">Tanggal mulai<input class="field-input" type="date" name="start" min="${escapeHtml(result.today)}" value="${escapeHtml(day)}" required></label>
      <label class="field-label">Sampai tanggal<input class="field-input" type="date" name="end" min="${escapeHtml(result.today)}" value="${escapeHtml(day)}" required></label>
      <label class="field-label">Jam masuk (WIB)<input class="field-input" type="time" name="work_start" value="${escapeHtml(s.work_start || '09:00')}" required></label>
      <label class="field-label">Jam pulang (WIB)<input class="field-input" type="time" name="work_end" value="${escapeHtml(s.work_end || '18:00')}" required></label>
      <label class="field-label">Toleransi terlambat (menit)<input class="field-input" type="number" name="late_grace_minutes" min="0" max="120" value="${Number(s.late_grace_minutes || 0)}" required></label>
      <label class="field-label">Toleransi pulang awal (menit)<input class="field-input" type="number" name="early_grace_minutes" min="0" max="120" value="${Number(s.early_grace_minutes || 0)}" required></label>
      <p class="inline-form-error" role="alert" hidden></p><div class="shift-form-actions"><button class="button button-primary" type="submit">Simpan shift</button><button class="button button-light" type="button" data-shift-reset>Kembali ke jam reguler</button></div>
    </form></div>
    <div class="table-toolbar"><label class="field-label">Lihat jadwal tanggal<input class="filter-select" type="date" data-shift-roster-date value="${escapeHtml(day)}"></label></div>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>KARYAWAN</th><th>SHIFT</th><th>AKSI</th></tr></thead><tbody>${state.shiftRoster.map(r => `<tr><td>${escapeHtml(r.full_name)}</td><td>${escapeHtml(shiftLabel(r))}</td><td><button class="button button-light button-small" type="button" data-edit-shift="${r.user_id}">Atur</button></td></tr>`).join('')}</tbody></table></div></section>`;
}

async function saveInhouseShift(form, reset = false) {
  if (form.dataset.busy) return;
  const body = Object.fromEntries(new FormData(form));
  const error = form.querySelector('[role="alert"]'); error.hidden = true;
  if (reset) {
    if (!body.user_id || !body.start || !body.end) { error.textContent = 'Pilih karyawan dan rentang tanggal.'; error.hidden = false; return; }
    if (!window.confirm('Kembalikan jadwal karyawan pada rentang tanggal ini ke jam reguler?')) return;
  } else if (!form.reportValidity()) return;
  body.user_id = Number(body.user_id); body.reset = reset;
  form.dataset.busy = '1'; const buttons = [...form.querySelectorAll('button')]; buttons.forEach(b => b.disabled = true);
  try {
    await api('/api/inhouse-shifts', {method:'POST', body:JSON.stringify(body)});
    toast(reset ? 'Jadwal kembali ke jam reguler.' : 'Jadwal shift tersimpan.'); await refreshData();
  } catch (exc) { error.textContent = exc.message; error.hidden = false; }
  finally { delete form.dataset.busy; buttons.forEach(b => b.disabled = false); }
}

document.addEventListener('submit', event => {
  if (event.target.id !== 'inhouse-shift-form') return;
  event.preventDefault(); saveInhouseShift(event.target);
});
document.addEventListener('click', event => {
  const reset = event.target.closest('[data-shift-reset]');
  if (reset) saveInhouseShift(reset.closest('form'), true);
  const edit = event.target.closest('[data-edit-shift]');
  if (!edit) return;
  const row = (state.shiftRoster || []).find(r => String(r.user_id) === edit.dataset.editShift);
  const form = document.querySelector('#inhouse-shift-form');
  if (!row || !form) return;
  for (const key of ['user_id','shift_name','work_start','work_end','late_grace_minutes','early_grace_minutes']) form.elements[key].value = row[key];
  form.elements.start.value = form.elements.end.value = row.work_date;
  form.scrollIntoView({behavior:'smooth', block:'center'}); form.elements.shift_name.focus();
});
document.addEventListener('change', event => {
  if (event.target.matches('[data-shift-roster-date]')) { state.inhouseDate = event.target.value; renderPage(); }
});
