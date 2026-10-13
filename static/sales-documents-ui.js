const salesDocNames = {quote: 'Quote', proforma: 'Proforma Invoice', invoice: 'Invoice', spk: 'Surat Perintah Kerja'};
const nextSalesDoc = {quote: 'proforma', proforma: 'invoice', invoice: 'spk'};
function salesInput(label, name, value = '', type = 'text', extra = '') {
  return `<label class="field-label">${label}<input class="field-input" name="${name}" type="${type}" value="${escapeHtml(String(value ?? ''))}" ${extra}></label>`;
}
function salesDrawer(title, content, backLead = null) {
  state.drawer = null;
  $('#modal-root').innerHTML = `<div class="drawer-backdrop" data-action="backdrop-close"><aside class="drawer sales-drawer" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}"><div class="drawer-head"><h2>${escapeHtml(title)}</h2><button class="drawer-close" data-action="${backLead ? 'open-sales-lead' : 'backdrop-close'}" ${backLead ? `data-id="${backLead}"` : ''} aria-label="Tutup">&times;</button></div><div class="drawer-body">${content}</div></aside></div>`;
}
function renderSalesDocumentsPanel(lead, docs) {
  const manage = canManageSalesLead(lead), publishedSpk = docs.find(d => d.kind === 'spk' && !['draft', 'cancelled'].includes(d.status));
  return `<section class="sales-doc-panel"><div class="panel-head"><h3>Dokumen sales</h3>${manage ? `<button class="button button-primary button-small" data-action="edit-sales-document" data-lead-id="${lead.id}" data-kind="quote">+ Quote</button>` : ''}</div><div class="sales-doc-list">${docs.map(doc => `<article class="sales-doc-row"><div><b>${escapeHtml(salesDocNames[doc.kind])} ${escapeHtml(doc.document_no)}</b><span>${idr(doc.amount_rupiah)} | ${escapeHtml(doc.status)}${doc.source_number ? ` | dari ${escapeHtml(doc.source_number)}` : ''}</span></div><div class="sales-row-actions"><a class="button button-light button-small" href="/api/sales/documents/${doc.id}/print" target="_blank" rel="noopener">Cetak</a>
    ${manage && doc.status === 'draft' ? `<button class="button button-light button-small" data-action="edit-sales-document" data-id="${doc.id}">Edit / terbitkan</button>` : ''}
    ${manage && !['draft', 'cancelled'].includes(doc.status) && nextSalesDoc[doc.kind] ? `<button class="button button-light button-small" data-action="edit-sales-document" data-source-id="${doc.id}" data-kind="${nextSalesDoc[doc.kind]}" data-lead-id="${lead.id}">Buat ${salesDocNames[nextSalesDoc[doc.kind]]}</button>` : ''}
    ${manage && ['sent', 'approved'].includes(doc.status) ? `${doc.status === 'sent' || ['invoice','proforma'].includes(doc.kind) ? `<button class="button button-light button-small" data-action="sales-document-status" data-id="${doc.id}" data-status="${['invoice','proforma'].includes(doc.kind) ? 'paid' : 'approved'}">${['invoice','proforma'].includes(doc.kind) ? 'Tandai lunas' : 'Setujui'}</button>` : ''}<button class="button button-light button-small" data-action="sales-document-status" data-id="${doc.id}" data-status="cancelled">Batalkan</button>` : ''}
    ${doc.kind === 'spk' && !['draft','cancelled'].includes(doc.status) && has('delivery_orders.manage') ? `<button class="button button-primary button-small" data-action="open-delivery-order" data-spk-id="${doc.id}">Buat Surat Jalan</button>` : ''}</div></article>`).join('') || '<div class="empty-state">Belum ada dokumen.</div>'}</div>
    ${publishedSpk && manage && !lead.event_id ? `<form id="spk-event-form" class="sales-grid-form"><input type="hidden" name="spk_id" value="${publishedSpk.id}">${salesInput('Mulai event (WIB)','starts_at','','datetime-local','required')}${salesInput('Selesai event (WIB)','ends_at','','datetime-local','required')}<button class="button button-light" type="submit">Jadwalkan event dari SPK</button></form>` : ''}${lead.event_id ? `<p>Terhubung ke event #${lead.event_id}</p>` : ''}</section>`;
}
function documentItemRow(item = {}) {
  return `<div class="sales-document-item">${salesInput('Deskripsi','description',item.description,'text','maxlength="500" required')}${salesInput('Qty','quantity',item.quantity || 1,'number','min="1" max="10000" required')}${salesInput('Hari','days',item.days || 1,'number','min="1" max="3660" required')}${salesInput('Harga satuan','unit_price_rupiah',item.unit_price_rupiah || 0,'number','min="0" step="1" required')}<button type="button" class="button button-light" data-action="remove-document-item" aria-label="Hapus item">&times;</button></div>`;
}
function openSalesDocument(element) {
  const data = salesBundle(), doc = data.documents.find(d => d.id === Number(element.dataset.id));
  const source = data.documents.find(d => d.id === Number(element.dataset.sourceId || doc?.source_document_id));
  const lead = data.leads.find(l => l.id === Number(doc?.lead_id || element.dataset.leadId));
  if (!lead || !canManageSalesLead(lead) || (doc && doc.status !== 'draft')) return toast('Dokumen tidak dapat diedit.', 'error');
  const kind = doc?.kind || element.dataset.kind || 'quote';
  const items = doc?.items?.length ? doc.items : source?.items?.length ? source.items : [{description:lead.event_name,unit_price_rupiah:source?.amount_rupiah || lead.estimated_value_rupiah}];
  salesDrawer(`${doc ? 'Edit' : 'Buat'} ${salesDocNames[kind]}`, `<form id="sales-document-form" class="sales-grid-form"><input type="hidden" name="id" value="${doc?.id || ''}"><input type="hidden" name="lead_id" value="${lead.id}"><input type="hidden" name="kind" value="${kind}"><input type="hidden" name="source_document_id" value="${source?.id || ''}">
    ${salesInput('Nomor dokumen','document_no',doc?.document_no,'text','maxlength="80" placeholder="Otomatis saat disimpan"')}<label class="field-label">Status<select name="status" class="field-input"><option value="draft">Draft</option><option value="sent">Terbit / terkirim</option><option value="approved">Disetujui</option></select></label>
    ${salesInput('Tanggal','issued_at',doc?.issued_at || localDateISO(),'date','required')}${salesInput('Jatuh tempo','due_at',doc?.due_at,'date')}${salesInput('Ditagihkan kepada','bill_to',doc?.bill_to || source?.bill_to || lead.client_name,'text','maxlength="1000"')}<p>Dokumen sumber: ${escapeHtml(source?.document_no || 'Lead ' + lead.client_name)}</p>
    <div id="sales-document-items" class="sales-full">${items.map(documentItemRow).join('')}</div><div class="sales-full"><button class="button button-light" type="button" data-action="add-document-item">+ Item</button></div>
    ${salesInput('Diskon (Rp)','discount_rupiah',doc?.discount_rupiah ?? source?.discount_rupiah ?? 0,'number','min="0" step="1"')}${salesInput('Pajak (Rp)','tax_rupiah',doc?.tax_rupiah ?? source?.tax_rupiah ?? 0,'number','min="0" step="1"')}<b class="sales-full" id="sales-document-total"></b><label class="field-label sales-full">Ketentuan<textarea class="field-input" name="terms" maxlength="4000">${escapeHtml(doc?.terms || source?.terms || '')}</textarea></label><label class="field-label sales-full">Catatan<textarea class="field-input" name="notes" maxlength="1200">${escapeHtml(doc?.notes || '')}</textarea></label><div class="sales-full drawer-actions"><button class="button button-light" type="button" data-action="open-sales-lead" data-id="${lead.id}">Kembali</button><button class="button button-primary" type="submit">Simpan dokumen</button></div></form>`, lead.id);
  updateDocumentTotal();
}
function updateDocumentTotal() {
  const form = $('#sales-document-form'); if (!form) return;
  const subtotal = $$('.sales-document-item',form).reduce((sum,row) => sum + Number(row.querySelector('[name="quantity"]').value) * Number(row.querySelector('[name="days"]').value) * Number(row.querySelector('[name="unit_price_rupiah"]').value),0);
  $('#sales-document-total').textContent = `Total: ${idr(subtotal - Number(form.elements.discount_rupiah.value) + Number(form.elements.tax_rupiah.value))}`;
}
async function handleSalesDocumentAction(action, element) {
  if (action === 'edit-sales-document') { openSalesDocument(element); return true; }
  if (action === 'add-document-item') { $('#sales-document-items').insertAdjacentHTML('beforeend',documentItemRow()); updateDocumentTotal(); return true; }
  if (action === 'remove-document-item') { element.closest('.sales-document-item').remove(); updateDocumentTotal(); return true; }
  if (action === 'sales-document-status') {
    const doc = salesBundle().documents.find(d => d.id === Number(element.dataset.id));
    if (!doc || !window.confirm(`Ubah ${doc.document_no} menjadi ${element.dataset.status}?`)) return true;
    await api('/api/sales/documents',{method:'POST',body:JSON.stringify({id:doc.id,lead_id:doc.lead_id,kind:doc.kind,status:element.dataset.status})}); await refreshData(); openSalesLeadDialog(doc.lead_id); return true;
  }
  return false;
}
document.addEventListener('input',event => { if(event.target.closest('#sales-document-form')) updateDocumentTotal(); });
document.addEventListener('submit',async event => {
  const form=event.target; if(form.id!=='spk-event-form') return; event.preventDefault();
  if(form.dataset.busy==='1') return; form.dataset.busy='1'; const button=form.querySelector('button[type=submit]'); button.disabled=true;
  try { const p=Object.fromEntries(new FormData(form)),doc=salesBundle().documents.find(d=>d.id===Number(p.spk_id));
    await api(`/api/sales/spk/${p.spk_id}/event`,{method:'POST',body:JSON.stringify(p)});await refreshData();openSalesLeadDialog(doc.lead_id);toast('Event operasional dibuat dari SPK.');
  } catch(error) { toast(error.message,'error'); } finally { form.dataset.busy='0';button.disabled=false; }
});
