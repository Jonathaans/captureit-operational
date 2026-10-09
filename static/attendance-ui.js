/* In-house office hours: late / early-leave labels, schedule settings (Configure), event-date rules for crew. */

function punctualityInfo(row, schedule) {
  const lateGrace = Number(schedule?.late_grace_minutes || 0), earlyGrace = Number(schedule?.early_grace_minutes || 0);
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
      <small class="muted">Berlaku untuk absensi berikutnya. Data yang sudah tercatat menyimpan jadwal saat absen, jadi riwayat tidak berubah. Toleransi 0 berarti terlambat dihitung mulai menit pertama setelah jam masuk.</small>
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
