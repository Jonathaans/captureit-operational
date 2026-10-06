/* Multi-day events: map Crew/PIC to specific days and record attendance per day. */
function wibToday() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Jakarta' }).format(new Date());
}

function dayLabel(value) {
  const [y, m, d] = value.split('-').map(Number);
  return new Intl.DateTimeFormat('id-ID', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, d)));
}

/* Day checkboxes for the "Jadwalkan staff" form. Every day is ticked by default. */
function renderDayPicker(d) {
  if (!d.multi_day) return '';
  const boxes = d.event_days.map((day) => `<label class="day-chip-option"><input type="checkbox" name="work_days" value="${day}" checked><span>${escapeHtml(dayLabel(day))}</span></label>`).join('');
  return `<fieldset class="assignment-day-picker"><legend>Hari kerja <span>event ${d.event_days.length} hari · centang hari yang dikerjakan orang ini</span></legend><div class="day-chip-row">${boxes}</div></fieldset>`;
}

const DAY_STATUS = {
  not_started: ['Belum absen', 'gray'], checked_in: ['Bertugas', 'gold'], checked_out: ['Selesai', 'green'], absent: ['Tidak hadir', 'red'],
};

/* Per-assignment day list: status chips, own check-in/out for today, manager editor. */
function renderAssignmentDays(d, a) {
  if (!d.multi_day) return '';
  const today = wibToday();
  const own = a.user_id === state.data.user.id && has('attendance.self');
  const manage = has('attendance.manage');
  const rows = a.days.map((day) => {
    const [label, tone] = DAY_STATUS[day.status] || DAY_STATUS.not_started;
    let action = '';
    if (own && day.work_date === today) {
      const common = `data-action="attendance" data-id="${d.id}" data-assignment-id="${a.assignment_id}" data-work-date="${day.work_date}"`;
      if (day.status === 'not_started') action = `<button class="button button-primary button-small" ${common} data-attendance-action="check_in">Check-in</button>`;
      if (day.status === 'checked_in') action = `<button class="button button-gold button-small" ${common} data-attendance-action="check_out">Check-out</button>`;
    }
    if (manage && day.status === 'not_started') {
      action += `<button class="detail-button" data-action="mark-absent" data-id="${d.id}" data-assignment-id="${a.assignment_id}" data-work-date="${day.work_date}">Tidak hadir</button>`;
    }
    const proof = (own || manage) ? [['check_in', day.check_in_at, day.has_check_in_photo, 'Masuk'], ['check_out', day.check_out_at, day.has_check_out_photo, 'Pulang']]
      .filter(([, stamp]) => stamp)
      .map(([kind, stamp, photo, text]) => `<small>${text} ${escapeHtml(attendanceTimeLabel(stamp))}${photo ? ` · <a href="/api/attendance/${a.assignment_id}/photo/${kind}?date=${day.work_date}" target="_blank" rel="noopener">foto</a>` : ''}</small>`).join('') : '';
    const flag = day.needs_action ? '<span class="badge badge-red">Perlu tindakan</span>' : '';
    const corrected = day.corrected ? `<small class="correction-note">Dikoreksi pengelola${day.note ? ` · ${escapeHtml(day.note)}` : ''}</small>` : '';
    const correction = renderCorrectionForm(d, a, day.work_date, day);
    return `<div class="day-row${day.work_date === today ? ' is-today' : ''}${day.needs_action ? ' needs-action' : ''}"><div class="day-row-main"><b>${escapeHtml(dayLabel(day.work_date))}</b>${proof}${corrected}</div>${flag}<span class="badge badge-${tone}">${label}</span><div class="assignment-actions">${action}</div>${correction}</div>`;
  }).join('');
  const editable = has('events.assign') && d.status === 'scheduled' && !['submitted', 'accepted'].includes(d.closing_report?.status);
  const mapped = new Set(a.days.map((x) => x.work_date));
  const locked = new Set(a.days.filter((x) => x.status !== 'not_started').map((x) => x.work_date));
  const editor = editable ? `<details class="day-editor"><summary>Atur hari kerja</summary><form data-days-form data-id="${d.id}" data-assignment-id="${a.assignment_id}"><div class="day-chip-row">${d.event_days.map((day) => `<label class="day-chip-option"><input type="checkbox" name="work_days" value="${day}" ${mapped.has(day) ? 'checked' : ''} ${locked.has(day) ? 'disabled' : ''}><span>${escapeHtml(dayLabel(day))}</span></label>`).join('')}</div><p class="inline-form-error" data-days-error role="alert" hidden></p><div class="day-editor-actions"><button class="button button-primary button-small" type="submit">Simpan hari</button></div><small class="muted">Hari yang sudah punya absensi tidak dapat dilepas.</small></form></details>` : '';
  return `<div class="assignment-days">${rows}${editor}</div>`;
}

async function saveAssignmentDays(form, confirmation) {
  const err = form.querySelector('[data-days-error]');
  err.hidden = true;
  const days = [...form.querySelectorAll('input[name="work_days"]')].filter((el) => el.checked || el.disabled && el.checked).map((el) => el.value);
  const locked = [...form.querySelectorAll('input[name="work_days"]:disabled')].map((el) => el.value);
  const all = [...new Set([...days, ...locked])];
  if (!all.length) { err.textContent = 'Pilih minimal satu hari kerja.'; err.hidden = false; return; }
  const button = form.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    await api(`/api/events/${form.dataset.id}/assignment`, { method: 'POST', body: JSON.stringify({ action: 'days', assignment_id: Number(form.dataset.assignmentId), days: all, ...(confirmation || {}) }) });
    toast('Hari kerja diperbarui.'); await refreshData();
  } catch (error) {
    const availability = error.details?.availability;
    if (availability?.conflicts?.length) {
      const lines = availability.conflicts.map((c) => `• ${c.title} (${(c.shared_days || []).map(dayLabel).join(', ')})`).join('\n');
      if (window.confirm(`Orang ini sudah punya event lain di hari yang sama:\n${lines}\n\nTetap jadwalkan?`)) {
        button.disabled = false;
        return saveAssignmentDays(form, { schedule_confirmed: true, schedule_ack_signature: availability.signature });
      }
    } else { err.textContent = error.message; err.hidden = false; }
  } finally { button.disabled = false; }
}

document.addEventListener('submit', (event) => {
  const form = event.target.closest('[data-days-form]');
  if (!form) return;
  event.preventDefault();
  saveAssignmentDays(form);
});

/* Date label for lists: "Sabtu, 3 Oktober 2026 – Minggu, 4 Oktober 2026 · 2 hari" for multi-day events. */
function wibDate(value) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Jakarta' }).format(new Date(value));
}

function eventSpanDays(startsAt, endsAt) {
  const start = new Date(startsAt), end = new Date(endsAt);
  if (Number.isNaN(start.valueOf()) || Number.isNaN(end.valueOf()) || end <= start) return 1;
  const first = wibDate(start), last = wibDate(new Date(end.valueOf() - 1));
  return Math.round((Date.parse(last) - Date.parse(first)) / 86400000) + 1;
}

/* Same rule as the server: longer than 24 hours and touching more than one WIB date. */
function isMultiDaySpan(startsAt, endsAt) {
  return (new Date(endsAt) - new Date(startsAt)) > 24 * 3600 * 1000 && eventSpanDays(startsAt, endsAt) > 1;
}

function eventRangeLabel(startsAt, endsAt) {
  if (!isMultiDaySpan(startsAt, endsAt)) return `${dateLong(startsAt)} · ${timePart(startsAt)}`;
  const lastDay = new Date(new Date(endsAt).valueOf() - 1);
  return `${dateLong(startsAt)} ${timePart(startsAt)} – ${dateLong(lastDay)} ${timePart(endsAt)} · ${eventSpanDays(startsAt, endsAt)} hari`;
}


/* Correction of a forgotten day. Only Event Coordinator / Head Operations hold attendance.correct,
   and nobody can correct their own attendance (the server enforces both). */
function canCorrect(d, a, workDate, record) {
  if (!has('attendance.correct') || a.user_id === state.data.user.id) return false;
  if (['cancelled', 'completed'].includes(d.status)) return false;
  if (workDate > wibToday()) return false;
  return ['not_started', 'checked_in', 'absent'].includes(record.status || record.attendance_status || 'not_started');
}

function renderCorrectionForm(d, a, workDate, record) {
  if (!canCorrect(d, a, workDate, record)) return '';
  const recordedIn = record.check_in_at ? new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Jakarta', dateStyle: 'short', timeStyle: 'short' }).format(new Date(record.check_in_at)).replace(' ', 'T') : '';
  const inValue = recordedIn || `${workDate}T09:00`;
  return `<details class="day-correction"><summary>Koreksi absensi</summary><form data-correction-form data-id="${d.id}" data-assignment-id="${a.assignment_id}" ${d.multi_day ? `data-work-date="${workDate}"` : ''}>
    <div class="correction-result"><label><input type="radio" name="result" value="present" checked> Hadir</label>${record.status === 'checked_in' ? '' : '<label><input type="radio" name="result" value="absent"> Tidak hadir</label>'}</div>
    <div class="correction-times" data-correction-times><label>Jam masuk${recordedIn ? ' (sudah tercatat)' : ''}<input class="field-input" type="datetime-local" name="check_in_at" value="${inValue}" ${recordedIn ? 'disabled' : ''} required></label>
    <label>Jam pulang<input class="field-input" type="datetime-local" name="check_out_at" value="${workDate}T17:00" required></label></div>
    <label class="field-label">Alasan koreksi (wajib)<textarea class="field-input" name="note" rows="2" maxlength="300" required placeholder="Contoh: lupa absen, hadir sesuai laporan PIC"></textarea></label>
    <p class="inline-form-error" data-correction-error role="alert" hidden></p>
    <div class="day-editor-actions"><button class="button button-primary button-small" type="submit">Simpan koreksi</button></div>
    <small class="muted">Tercatat di audit log dan diberi label "Dikoreksi pengelola". Absensi yang sudah lengkap dengan foto tidak dapat diubah.</small></form></details>`;
}

/* Single-day events: one record per assignment. */
function renderSingleDayCorrection(d, a) {
  const day = d.event_days && d.event_days[0];
  if (!day) return '';
  const flag = a.needs_action ? '<span class="badge badge-red">Perlu tindakan</span>' : '';
  const corrected = a.corrected_at ? `<small class="correction-note">Dikoreksi pengelola${a.correction_note ? ` · ${escapeHtml(a.correction_note)}` : ''}</small>` : '';
  const form = renderCorrectionForm(d, a, day, a);
  return flag || corrected || form ? `<div class="assignment-days single">${flag}${corrected}${form}</div>` : '';
}

document.addEventListener('change', (event) => {
  const form = event.target.closest && event.target.closest('[data-correction-form]');
  if (!form || event.target.name !== 'result') return;
  const absent = event.target.value === 'absent';
  form.querySelector('[data-correction-times]').hidden = absent;
  form.querySelectorAll('[data-correction-times] input').forEach((el) => { el.required = !absent; });
});

async function submitCorrection(form) {
  const err = form.querySelector('[data-correction-error]');
  err.hidden = true;
  const result = form.querySelector('input[name="result"]:checked').value;
  const toWib = (name) => { const el = form.querySelector(`input[name="${name}"]`); return el && el.value && !el.disabled ? `${el.value}:00+07:00` : undefined; };
  const body = { action: 'correct', assignment_id: Number(form.dataset.assignmentId), result, note: form.querySelector('textarea[name="note"]').value.trim(),
    ...(form.dataset.workDate ? { work_date: form.dataset.workDate } : {}),
    ...(result === 'present' ? { check_in_at: toWib('check_in_at'), check_out_at: toWib('check_out_at') } : {}) };
  const button = form.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    await api(`/api/events/${form.dataset.id}/attendance`, { method: 'POST', body: JSON.stringify(body) });
    toast('Absensi dikoreksi.'); await refreshData();
  } catch (error) { err.textContent = error.message; err.hidden = false; } finally { button.disabled = false; }
}

document.addEventListener('submit', (event) => {
  const form = event.target.closest && event.target.closest('[data-correction-form]');
  if (!form) return;
  event.preventDefault();
  submitCorrection(form);
});

function renderAttendanceBanner(d) {
  if (!d.attendance_needs_action || !has('attendance.manage')) return '';
  return `<div class="attendance-banner" role="status"><b>${d.attendance_needs_action} crew belum terselesaikan absensinya.</b> Koreksi jika mereka hadir, atau tandai tidak hadir.${d.multi_day ? ' Event multi-hari tidak dapat di-clear sebelum semuanya selesai.' : ''}</div>`;
}

/* Manual events: create an event by hand, no Google Calendar involved (for testing and one-off jobs). */
function renderManualEventPanel() {
  if (!has('events.assign')) return '';
  const pad = (n) => String(n).padStart(2, '0');
  const start = new Date(); start.setHours(9, 0, 0, 0);
  const end = new Date(start.valueOf() + 2 * 86400000); end.setHours(18, 0, 0, 0);
  const local = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return `<details class="panel manual-event-panel"><summary>+ Buat event manual <span>tanpa Google Calendar · cocok untuk uji coba mapping crew</span></summary>
    <form data-manual-event-form class="manual-event-form">
      <label class="field-label">Judul event<input class="field-input" name="title" required minlength="3" maxlength="120" placeholder="Contoh: Uji coba Expo 3 hari"></label>
      <label class="field-label">Kode project <small>(kosong = kode sementara OPS-…)</small><input class="field-input" name="project_code" maxlength="50" placeholder="Contoh: TEST-001"></label>
      <label class="field-label">Lokasi<input class="field-input" name="location" maxlength="200" placeholder="Opsional"></label>
      <label class="field-label">Mulai<input class="field-input" type="datetime-local" name="starts_at" value="${local(start)}" required></label>
      <label class="field-label">Selesai<input class="field-input" type="datetime-local" name="ends_at" value="${local(end)}" required></label>
      <label class="field-label manual-event-check"><input type="checkbox" name="is_full_day"> Full day <small>(kosongkan = otomatis: Full day jika lebih dari 24 jam)</small></label>
      <p class="inline-form-error" data-manual-event-error role="alert" hidden></p>
      <div class="manual-event-actions"><button class="button button-primary" type="submit">Buat event</button></div>
      <small class="muted">Event manual tidak diubah oleh sync Calendar. Hanya Administrator yang dapat menghapusnya, dan hanya jika belum masuk payroll.</small>
    </form></details>`;
}

document.addEventListener('submit', async (event) => {
  const form = event.target.closest && event.target.closest('[data-manual-event-form]');
  if (!form) return;
  event.preventDefault();
  const err = form.querySelector('[data-manual-event-error]'); err.hidden = true;
  const value = (name) => form.elements[name].value.trim();
  const wib = (name) => (value(name) ? `${value(name)}:00+07:00` : '');
  const body = { title: value('title'), project_code: value('project_code'), location: value('location'), starts_at: wib('starts_at'), ends_at: wib('ends_at'),
    ...(form.elements.is_full_day.checked ? { is_full_day: true } : {}) };
  const button = form.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    const made = await api('/api/events/manual', { method: 'POST', body: JSON.stringify(body) });
    toast(`Event manual dibuat (${made.project_code}).`);
    await refreshData();
    if (typeof openEvent === 'function') await openEvent(made.event_id, 'team');
  } catch (error) { err.textContent = error.message; err.hidden = false; } finally { button.disabled = false; }
});

document.addEventListener('click', async (event) => {
  const button = event.target.closest && event.target.closest('[data-action="delete-manual-event"]');
  if (!button) return;
  if (!window.confirm('Hapus event manual ini beserta penugasan dan absensinya? Tindakan ini tidak dapat dibatalkan.')) return;
  try {
    await api(`/api/events/${button.dataset.id}/delete-manual`, { method: 'POST', body: JSON.stringify({ confirm: true }) });
    state.drawer = null; $('#modal-root').innerHTML = '';
    toast('Event manual dihapus.'); await refreshData();
  } catch (error) { toast(error.message, 'error'); }
});
