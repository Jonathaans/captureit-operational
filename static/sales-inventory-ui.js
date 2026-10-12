function salesBundle() { return state.data?.sales || { pipelines: [], stages: [], leads: [], documents: [], sales_people: [], dashboard: {} }; }
function inventoryBundle() { return state.data?.inventory || { items: [], movements: [], locations: [], low_stock: [] }; }
function deliveryBundle() { return state.data?.delivery_orders || { orders: [], items: [], events: [], spk_documents: [] }; }

function salesStagesFor(pipelineId) {
  return salesBundle().stages.filter((stage) => Number(stage.pipeline_id) === Number(pipelineId));
}

function selectedSalesPipeline() {
  const data = salesBundle();
  const pipelines = data.pipelines || [];
  if (!pipelines.length) return null;
  if (!state.salesPipelineId || !pipelines.some((row) => String(row.id) === String(state.salesPipelineId))) {
    state.salesPipelineId = String((pipelines.find((row) => row.is_default) || pipelines[0]).id);
  }
  return pipelines.find((row) => String(row.id) === String(state.salesPipelineId)) || pipelines[0];
}

function salesLeadDocuments(leadId) {
  return (salesBundle().documents || []).filter((doc) => Number(doc.lead_id) === Number(leadId));
}

function canManageSalesLead(lead) {
  return has("sales.manage_all") || (has("sales.manage_own") && (!lead || Number(lead.owner_id) === Number(state.data.user.id)));
}

function renderSalesBoardPage() {
  const data = salesBundle();
  const pipeline = selectedSalesPipeline();
  if (!pipeline) return `<div class="empty-state">Pipeline sales belum tersedia.</div>`;
  const stages = salesStagesFor(pipeline.id);
  const leads = (data.leads || []).filter((lead) => Number(lead.pipeline_id) === Number(pipeline.id));
  const pipelineSelect = `<label class="sales-pipeline-select">Pipeline<select id="sales-pipeline-select" class="filter-select">${data.pipelines.map((row) => `<option value="${row.id}" ${Number(row.id) === Number(pipeline.id) ? "selected" : ""}>${escapeHtml(row.name)}</option>`).join("")}</select></label>`;
  const actions = `${pipelineSelect}${has("sales.pipeline.manage") ? `<button class="button button-light" data-action="new-sales-pipeline">+ Pipeline</button>` : ""}${has("sales.manage_own") || has("sales.manage_all") ? `<button class="button button-primary" data-action="open-sales-lead">+ Lead</button>` : ""}`;
  const columns = stages.map((stage) => {
    const cards = leads.filter((lead) => Number(lead.stage_id) === Number(stage.id));
    return `<section class="sales-stage-column" data-sales-stage="${stage.id}">
      <div class="kanban-head"><b>${escapeHtml(stage.name)}</b><span class="kanban-count">${cards.length}</span></div>
      ${cards.map((lead) => `<article class="sales-kanban-card" draggable="${canManageSalesLead(lead)}" data-sales-lead-id="${lead.id}" data-action="open-sales-lead" data-id="${lead.id}" role="button" tabindex="0">
        <span class="kanban-card-code">${escapeHtml(lead.owner_name || "Sales")}</span>
        <h4>${escapeHtml(lead.client_name)}</h4>
        <p>${escapeHtml(lead.event_name)}${lead.event_date ? ` · ${escapeHtml(lead.event_date)}` : ""}</p>
        <div class="sales-card-value">${idr(lead.estimated_value_rupiah)}</div>
        <div class="kanban-card-foot"><span>${lead.document_count || 0} dokumen${lead.spk_number ? ` · SPK ${escapeHtml(lead.spk_number)}` : ""}</span><span class="detail-button button-small">Detail</span></div>
      </article>`).join("")}${cards.length ? "" : `<div class="empty-state sales-empty-column">Belum ada kartu</div>`}
    </section>`;
  }).join("");
  return `${pageHead("SALES", "Sales pipeline", "Leads bisa digeser sampai SPK. Sales biasa hanya melihat lead miliknya sendiri.", actions)}
    <div class="sales-summary-strip">
      <div><b>${data.dashboard?.open_leads || 0}</b><span>Lead aktif</span></div>
      <div><b>${idr(data.dashboard?.estimated_value || 0)}</b><span>Estimasi aktif</span></div>
      <div><b>${data.dashboard?.won_leads || 0}</b><span>Sudah SPK</span></div>
    </div>
    <div class="kanban-scroll"><div class="kanban sales-kanban">${columns}</div></div>
    <div class="drag-note">Tarik kartu untuk pindah tahap, atau buka detail untuk tambah Quote, Proforma, Invoice, dan SPK.</div>`;
}

function renderSalesDashboardPage() {
  const data = salesBundle();
  const rows = data.dashboard?.by_stage || [];
  const ownerRows = (data.leads || []).reduce((acc, lead) => {
    const key = lead.owner_name || "Tanpa owner";
    acc[key] ||= { name: key, total: 0, value: 0, won: 0 };
    acc[key].total += 1;
    acc[key].value += Number(lead.estimated_value_rupiah || 0);
    if (lead.status === "won") acc[key].won += 1;
    return acc;
  }, {});
  return `${pageHead("ADMIN SALES", "Dashboard sales", "Ringkasan pipeline seluruh sales dan nilai estimasi per tahap.", `<span class="badge badge-purple">${data.leads.length} lead</span>`)}
    <div class="sales-summary-strip sales-admin-strip">
      <div><b>${data.dashboard?.open_leads || 0}</b><span>Lead aktif</span></div>
      <div><b>${idr(data.dashboard?.estimated_value || 0)}</b><span>Estimasi aktif</span></div>
      <div><b>${idr(data.dashboard?.won_value || 0)}</b><span>Nilai SPK</span></div>
    </div>
    <section class="panel table-panel"><div class="panel-head"><div><h3>Per tahap pipeline</h3><p>Jumlah dan estimasi nilai semua pipeline.</p></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>TAHAP</th><th>JUMLAH</th><th>NILAI</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.stage_name)}</td><td>${row.total}</td><td>${idr(row.value_rupiah)}</td></tr>`).join("") || `<tr><td colspan="3"><div class="empty-state">Belum ada lead.</div></td></tr>`}</tbody></table></div></section>
    <section class="panel table-panel"><div class="panel-head"><div><h3>Per sales</h3><p>Monitoring owner lead.</p></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>SALES</th><th>LEAD</th><th>SPK</th><th>NILAI ESTIMASI</th></tr></thead><tbody>${Object.values(ownerRows).map((row) => `<tr><td>${escapeHtml(row.name)}</td><td>${row.total}</td><td>${row.won}</td><td>${idr(row.value)}</td></tr>`).join("") || `<tr><td colspan="4"><div class="empty-state">Belum ada data sales.</div></td></tr>`}</tbody></table></div></section>`;
}

function openSalesLeadDialog(id = null) {
  const data = salesBundle();
  const lead = id ? data.leads.find((row) => Number(row.id) === Number(id)) : null;
  const pipeline = lead ? data.pipelines.find((row) => Number(row.id) === Number(lead.pipeline_id)) : selectedSalesPipeline();
  const stages = salesStagesFor(pipeline?.id);
  const docs = lead ? salesLeadDocuments(lead.id) : [];
  const ownerControl = has("sales.manage_all") ? `<label class="field-label">Sales owner<select class="field-input" name="owner_id">${(data.sales_people || []).map((person) => `<option value="${person.id}" ${Number(person.id) === Number(lead?.owner_id || state.data.user.id) ? "selected" : ""}>${escapeHtml(person.full_name)}</option>`).join("")}</select></label>` : "";
  $("#modal-root").innerHTML = `<div class="drawer-backdrop" data-action="backdrop-close"><aside class="drawer sales-drawer">
    <div class="drawer-head"><div><h2>${lead ? "Detail lead" : "Lead baru"}</h2><p>${lead ? escapeHtml(lead.client_name) : "Tambah lead ke pipeline sales."}</p></div><button class="drawer-close" data-action="backdrop-close" aria-label="Tutup">x</button></div>
    <div class="drawer-body">
      <form id="sales-lead-form" class="sales-grid-form">
        <input type="hidden" name="id" value="${lead?.id || ""}">
        <input type="hidden" name="pipeline_id" value="${pipeline?.id || ""}">
        <label class="field-label">Tahap<select class="field-input" name="stage_id">${stages.map((stage) => `<option value="${stage.id}" ${Number(stage.id) === Number(lead?.stage_id || stages[0]?.id) ? "selected" : ""}>${escapeHtml(stage.name)}</option>`).join("")}</select></label>
        ${ownerControl}
        <label class="field-label">Nama client<input class="field-input" name="client_name" maxlength="160" value="${escapeHtml(lead?.client_name || "")}" required></label>
        <label class="field-label">Contact person<input class="field-input" name="contact_name" maxlength="160" value="${escapeHtml(lead?.contact_name || "")}"></label>
        <label class="field-label">No. HP / WA<input class="field-input" name="contact_phone" maxlength="60" value="${escapeHtml(lead?.contact_phone || "")}"></label>
        <label class="field-label">Email<input class="field-input" name="contact_email" maxlength="160" value="${escapeHtml(lead?.contact_email || "")}"></label>
        <label class="field-label">Nama event<input class="field-input" name="event_name" maxlength="180" value="${escapeHtml(lead?.event_name || "")}" required></label>
        <label class="field-label">Tanggal event<input class="field-input" name="event_date" type="date" value="${escapeHtml(lead?.event_date || "")}"></label>
        <label class="field-label">Venue<input class="field-input" name="venue" maxlength="220" value="${escapeHtml(lead?.venue || "")}"></label>
        <label class="field-label">Source<input class="field-input" name="source" maxlength="80" value="${escapeHtml(lead?.source || "")}"></label>
        <label class="field-label">Estimasi nilai<input class="field-input" name="estimated_value_rupiah" type="number" min="0" step="1000" value="${lead?.estimated_value_rupiah || 0}"></label>
        <label class="field-label sales-full">Catatan<textarea class="field-input" name="notes" maxlength="2000">${escapeHtml(lead?.notes || "")}</textarea></label>
        <div class="sales-full drawer-actions"><button class="button button-light" type="button" data-action="backdrop-close">Batal</button><button class="button button-primary" type="submit">Simpan lead</button></div>
      </form>
      ${lead ? `<section class="sales-doc-panel"><div class="panel-head"><div><h3>Dokumen sales</h3><p>Quote, Proforma Invoice, Invoice, dan SPK.</p></div></div>
        <div class="sales-doc-list">${docs.map((doc) => `<div class="sales-doc-row"><b>${escapeHtml(doc.kind.toUpperCase())} · ${escapeHtml(doc.document_no)}</b><span>${idr(doc.amount_rupiah)} · ${escapeHtml(doc.status)}</span>${doc.kind === "spk" ? `<button class="button button-light button-small" data-action="open-delivery-order" data-spk-id="${doc.id}" data-lead-id="${doc.lead_id}">Buat Surat Jalan</button>` : ""}</div>`).join("") || `<div class="empty-state">Belum ada dokumen.</div>`}</div>
        <form id="sales-document-form" class="sales-doc-form">
          <input type="hidden" name="lead_id" value="${lead.id}">
          <label class="field-label">Jenis<select class="field-input" name="kind"><option value="quote">Quote</option><option value="proforma">Proforma Invoice</option><option value="invoice">Invoice</option><option value="spk">Surat Perintah Kerja</option></select></label>
          <label class="field-label">Nomor<input class="field-input" name="document_no" maxlength="80" required></label>
          <label class="field-label">Nominal<input class="field-input" name="amount_rupiah" type="number" min="0" step="1000" value="${lead.estimated_value_rupiah || 0}"></label>
          <label class="field-label">Status<select class="field-input" name="status"><option value="draft">Draft</option><option value="sent">Terkirim</option><option value="approved">Approved</option><option value="paid">Paid</option><option value="cancelled">Cancelled</option></select></label>
          <label class="field-label">Tanggal<input class="field-input" name="issued_at" type="date" value="${localDateISO()}"></label>
          <label class="field-label sales-full">Catatan<input class="field-input" name="notes" maxlength="1200"></label>
          <button class="button button-primary button-small" type="submit">Tambah dokumen</button>
        </form>
      </section>` : ""}
    </div>
  </aside></div>`;
}

function renderInventoryPage() {
  const data = inventoryBundle();
  const delivery = deliveryBundle();
  const low = data.low_stock || [];
  const itemRows = (data.items || []).map((item) => `<tr><td><div class="table-event"><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.sku)}${item.barcode ? ` · ${escapeHtml(item.barcode)}` : ""}</small></div></td><td>${escapeHtml(item.category || "-")}</td><td>${item.kind === "asset" ? "Asset" : "Inventory"}</td><td><b>${item.stock_on_hand}</b> ${escapeHtml(item.unit)}</td><td>${item.min_quantity}</td></tr>`).join("");
  const movements = (data.movements || []).map((row) => `<tr><td>${escapeHtml(row.created_at)}</td><td>${escapeHtml(row.item_name)}<small class="shift-history-label">${escapeHtml(row.sku)}</small></td><td>${escapeHtml(row.direction)}</td><td>${row.quantity}</td><td>${escapeHtml(row.delivery_number || row.project_code || "-")}</td><td>${escapeHtml(row.actor_name || "-")}</td></tr>`).join("");
  return `${pageHead("INVENTORY", "Inventory & asset", "Scan barang keluar/masuk dan pantau stok alat untuk event.", has("inventory.manage") ? `<button class="button button-primary" data-action="toggle-inventory-item-form">+ Item</button>` : "")}
    <div class="sales-summary-strip">
      <div><b>${data.items.length}</b><span>Item aktif</span></div>
      <div><b>${low.length}</b><span>Stok rendah</span></div>
      <div><b>${data.movements.length}</b><span>Scan terbaru</span></div>
    </div>
    <section class="panel inventory-scan-panel"><div class="panel-head"><div><h3>Scan keluar / masuk</h3><p>Gunakan barcode scanner atau ketik SKU/barcode.</p></div></div>
      <form id="inventory-scan-form" class="inventory-scan-form">
        <label class="field-label">Kode barang<input class="field-input" name="scan_code" placeholder="SKU atau barcode" autocomplete="off" required autofocus></label>
        <label class="field-label">Arah<select class="field-input" name="direction"><option value="out">Keluar</option><option value="return">Kembali</option><option value="in">Masuk</option><option value="adjustment_in">Adjustment +</option><option value="adjustment_out">Adjustment -</option></select></label>
        <label class="field-label">Qty<input class="field-input" name="quantity" type="number" min="1" step="1" value="1" required></label>
        <label class="field-label">Surat Jalan<select class="field-input" name="delivery_order_id"><option value="">Tanpa SJ</option>${(delivery.orders || []).map((order) => `<option value="${order.id}">${escapeHtml(order.number)} · ${escapeHtml(order.event_title || order.event_name || "")}</option>`).join("")}</select></label>
        <label class="field-label">Catatan<input class="field-input" name="notes" maxlength="800"></label>
        <button class="button button-primary" type="submit">Simpan scan</button>
      </form>
    </section>
    ${has("inventory.manage") ? `<section id="inventory-item-form-panel" class="panel inventory-item-panel" hidden><div class="panel-head"><div><h3>Master item</h3><p>Tambah inventory atau asset yang bisa discan.</p></div></div><form id="inventory-item-form" class="inventory-item-form"><label class="field-label">SKU<input class="field-input" name="sku" required></label><label class="field-label">Barcode<input class="field-input" name="barcode"></label><label class="field-label">Nama item<input class="field-input" name="name" required></label><label class="field-label">Kategori<input class="field-input" name="category"></label><label class="field-label">Jenis<select class="field-input" name="kind"><option value="inventory">Inventory</option><option value="asset">Asset</option></select></label><label class="field-label">Satuan<input class="field-input" name="unit" value="pcs" required></label><label class="field-label">Stok awal<input class="field-input" name="initial_quantity" type="number" min="0" value="0"></label><label class="field-label">Min stok<input class="field-input" name="min_quantity" type="number" min="0" value="0"></label><button class="button button-primary" type="submit">Simpan item</button></form></section>` : ""}
    <section class="panel table-panel"><div class="panel-head"><div><h3>Daftar inventory</h3><p>${data.items.length} item aktif.</p></div>${low.length ? badge(`${low.length} stok rendah`, "gold") : badge("Stok aman", "green")}</div><div class="table-wrap"><table class="data-table"><thead><tr><th>ITEM</th><th>KATEGORI</th><th>JENIS</th><th>STOK</th><th>MIN</th></tr></thead><tbody>${itemRows || `<tr><td colspan="5"><div class="empty-state">Belum ada item inventory.</div></td></tr>`}</tbody></table></div></section>
    <section class="panel table-panel"><div class="panel-head"><div><h3>Riwayat scan</h3><p>80 transaksi terakhir.</p></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>WAKTU</th><th>ITEM</th><th>ARAH</th><th>QTY</th><th>LINK</th><th>PETUGAS</th></tr></thead><tbody>${movements || `<tr><td colspan="6"><div class="empty-state">Belum ada scan.</div></td></tr>`}</tbody></table></div></section>`;
}

function renderDeliveryOrdersPage() {
  const data = deliveryBundle();
  const inventory = inventoryBundle();
  const orderItems = (orderId) => (data.items || []).filter((item) => Number(item.delivery_order_id) === Number(orderId));
  const rows = (data.orders || []).map((order) => `<article class="delivery-card">
    <div><b>${escapeHtml(order.number)}</b><small>${escapeHtml(order.project_code || order.client_name || "Tanpa event")} · ${escapeHtml(order.event_title || order.event_name || "")}</small></div>
    <span>${badge(order.status, order.status === "returned" ? "green" : order.status === "draft" ? "gray" : "gold")}</span>
    <div class="delivery-items">${orderItems(order.id).map((item) => `<span>${escapeHtml(item.item_name)}: ${item.planned_quantity} ${escapeHtml(item.unit)} · keluar ${item.out_quantity || 0} · kembali ${item.returned_quantity || 0}</span>`).join("") || "Belum ada item."}</div>
  </article>`).join("");
  const itemInputs = (inventory.items || []).map((item) => `<label class="delivery-item-pick"><span>${escapeHtml(item.name)}<small>${escapeHtml(item.sku)} · stok ${item.stock_on_hand}</small></span><input type="number" min="0" step="1" data-delivery-item="${item.id}" value="0"></label>`).join("");
  return `${pageHead("SURAT JALAN", "Surat jalan event", "Surat jalan bisa dibuat dari event atau SPK, lalu dipakai saat scan barang keluar/masuk.", `<span class="badge badge-blue">${data.orders.length} surat jalan</span>`)}
    <section class="panel delivery-form-panel"><div class="panel-head"><div><h3>Buat / rilis surat jalan</h3><p>Pilih event atau SPK, lalu isi daftar alat yang akan dibawa.</p></div></div>
      <form id="delivery-order-form" class="delivery-order-form">
        <label class="field-label">Event<select class="field-input" name="event_id"><option value="">Pilih event</option>${(data.events || []).map((event) => `<option value="${event.id}">${escapeHtml(event.project_code)} · ${escapeHtml(event.title)}</option>`).join("")}</select></label>
        <label class="field-label">SPK<select class="field-input" name="spk_document_id"><option value="">Opsional</option>${(data.spk_documents || []).map((doc) => `<option value="${doc.id}" data-lead-id="${doc.lead_id}">${escapeHtml(doc.document_no)} · ${escapeHtml(doc.client_name)}</option>`).join("")}</select></label>
        <label class="field-label">Penerima<input class="field-input" name="recipient_name" maxlength="160"></label>
        <label class="field-label">Tujuan<input class="field-input" name="destination" maxlength="240"></label>
        <label class="field-label sales-full">Catatan<input class="field-input" name="notes" maxlength="1200"></label>
        <div class="delivery-item-grid sales-full">${itemInputs || `<div class="empty-state">Isi master inventory dahulu.</div>`}</div>
        <label class="field-label release-check"><input type="checkbox" name="release" value="1"> Rilis surat jalan sekarang</label>
        <button class="button button-primary" type="submit">Simpan surat jalan</button>
      </form>
    </section>
    <section class="delivery-list">${rows || `<div class="empty-state">Belum ada surat jalan.</div>`}</section>`;
}

async function handleSalesInventoryAction(action, element) {
  if (action === "open-sales-lead") { openSalesLeadDialog(element.dataset.id || null); return true; }
  if (action === "new-sales-pipeline") {
    const name = window.prompt("Nama pipeline baru:", "");
    if (name === null) return true;
    await api("/api/sales/pipelines", { method: "POST", body: JSON.stringify({ name }) });
    toast("Pipeline baru dibuat."); await refreshData(); return true;
  }
  if (action === "toggle-inventory-item-form") {
    const panel = $("#inventory-item-form-panel");
    if (panel) panel.hidden = !panel.hidden;
    return true;
  }
  if (action === "open-delivery-order") {
    state.page = "delivery-orders";
    state.prefillDeliverySpkId = element.dataset.spkId || "";
    $("#modal-root").innerHTML = "";
    renderShell();
    renderPage();
    setTimeout(() => {
      const select = $('[name="spk_document_id"]');
      if (select && state.prefillDeliverySpkId) select.value = state.prefillDeliverySpkId;
    }, 0);
    return true;
  }
  return false;
}

async function submitSalesLead(form) {
  const data = new FormData(form);
  const payload = Object.fromEntries(data.entries());
  payload.id = payload.id ? Number(payload.id) : undefined;
  payload.pipeline_id = Number(payload.pipeline_id);
  payload.stage_id = Number(payload.stage_id);
  if (payload.owner_id) payload.owner_id = Number(payload.owner_id);
  payload.estimated_value_rupiah = Number(payload.estimated_value_rupiah || 0);
  await api("/api/sales/leads", { method: "POST", body: JSON.stringify(payload) });
  toast("Lead sales tersimpan.");
  $("#modal-root").innerHTML = "";
  await refreshData();
}

async function submitSalesDocument(form) {
  const data = new FormData(form);
  const payload = Object.fromEntries(data.entries());
  payload.lead_id = Number(payload.lead_id);
  payload.amount_rupiah = Number(payload.amount_rupiah || 0);
  await api("/api/sales/documents", { method: "POST", body: JSON.stringify(payload) });
  toast("Dokumen sales tersimpan.");
  $("#modal-root").innerHTML = "";
  await refreshData();
}

async function submitInventoryItem(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.initial_quantity = Number(payload.initial_quantity || 0);
  payload.min_quantity = Number(payload.min_quantity || 0);
  await api("/api/inventory/items", { method: "POST", body: JSON.stringify(payload) });
  toast("Item inventory tersimpan.");
  await refreshData();
}

async function submitInventoryScan(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.quantity = Number(payload.quantity || 1);
  payload.delivery_order_id = payload.delivery_order_id ? Number(payload.delivery_order_id) : undefined;
  await api("/api/inventory/scan", { method: "POST", body: JSON.stringify(payload) });
  toast("Scan inventory tersimpan.");
  form.reset();
  form.querySelector('[name="quantity"]').value = 1;
  await refreshData();
}

async function submitDeliveryOrder(form) {
  const data = new FormData(form);
  const spkSelect = form.querySelector('[name="spk_document_id"]');
  const selected = spkSelect?.selectedOptions?.[0];
  const payload = Object.fromEntries(data.entries());
  payload.event_id = payload.event_id ? Number(payload.event_id) : undefined;
  payload.spk_document_id = payload.spk_document_id ? Number(payload.spk_document_id) : undefined;
  payload.lead_id = selected?.dataset?.leadId ? Number(selected.dataset.leadId) : undefined;
  payload.release = data.get("release") === "1";
  payload.items = $$("[data-delivery-item]", form).map((input) => ({
    item_id: Number(input.dataset.deliveryItem),
    planned_quantity: Number(input.value || 0),
  })).filter((item) => item.planned_quantity > 0);
  await api("/api/delivery-orders", { method: "POST", body: JSON.stringify(payload) });
  toast(payload.release ? "Surat jalan dirilis." : "Draft surat jalan tersimpan.");
  await refreshData();
}

document.addEventListener("submit", async (event) => {
  const handlers = {
    "sales-lead-form": submitSalesLead,
    "sales-document-form": submitSalesDocument,
    "inventory-item-form": submitInventoryItem,
    "inventory-scan-form": submitInventoryScan,
    "delivery-order-form": submitDeliveryOrder,
  };
  const handler = handlers[event.target.id];
  if (!handler) return;
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  if (button) button.disabled = true;
  try { await handler(event.target); }
  catch (error) { toast(error.message || "Data belum dapat disimpan.", "error"); }
  finally { if (button) button.disabled = false; }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "sales-pipeline-select") {
    state.salesPipelineId = event.target.value;
    renderPage();
  }
});

document.addEventListener("dragstart", (event) => {
  const card = event.target.closest(".sales-kanban-card");
  if (!card) return;
  state.salesDragId = Number(card.dataset.salesLeadId);
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", String(state.salesDragId));
});

document.addEventListener("dragover", (event) => {
  const column = event.target.closest(".sales-stage-column");
  if (column && state.salesDragId) { event.preventDefault(); column.classList.add("drag-over"); }
});

document.addEventListener("dragleave", (event) => {
  const column = event.target.closest(".sales-stage-column");
  if (column && !column.contains(event.relatedTarget)) column.classList.remove("drag-over");
});

document.addEventListener("drop", async (event) => {
  const column = event.target.closest(".sales-stage-column");
  if (!column || !state.salesDragId) return;
  event.preventDefault();
  column.classList.remove("drag-over");
  try {
    await api(`/api/sales/leads/${state.salesDragId}/stage`, { method: "POST", body: JSON.stringify({ stage_id: Number(column.dataset.salesStage) }) });
    toast("Tahap lead diperbarui.");
    await refreshData();
  } catch (error) { toast(error.message || "Lead belum dapat dipindahkan.", "error"); }
  state.salesDragId = null;
});

document.addEventListener("dragend", () => {
  state.salesDragId = null;
  $$(".sales-stage-column.drag-over").forEach((column) => column.classList.remove("drag-over"));
});

