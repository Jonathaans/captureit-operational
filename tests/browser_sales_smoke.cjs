/* Real browser smoke against tests/browser_sales_server.py; disposable data only.
   BROWSER_QA_URL=http://127.0.0.1:PORT node tests/browser_sales_smoke.cjs
   Optional: CHROMIUM_PATH, QA_SCREENSHOT_DIR, NODE_PATH for local tooling. */
const {chromium,devices}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.BROWSER_QA_URL;
if(!base){if(process.env.NODE_TEST_CONTEXT)process.exit(0);throw new Error('Start tests/browser_sales_server.py and set BROWSER_QA_URL');}
const out=process.env.QA_SCREENSHOT_DIR || '/tmp/captureit-browser-qa';fs.mkdirSync(out,{recursive:true});
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {}),args:['--no-sandbox','--disable-dev-shm-usage']});
  const errors=[];
  async function pageFor(options={}){
    const context=await browser.newContext(options),page=await context.newPage();page.on('pageerror',e=>{errors.push(e.message);console.error('BROWSER ERROR',e.message);});page.setDefaultTimeout(10000);
    await page.goto(base);await page.locator('#login-email').fill('admin@captureit.local');await page.locator('#login-password').fill('demo1234');await page.locator('#login-form button[type=submit]').click();await page.locator('[data-page="sales-board"]').first().waitFor();return page;
  }
  async function nav(p,id){const link=p.locator(`.nav-link[data-page="${id}"]`);const mobile=await p.locator('#mobile-menu').isVisible();if(mobile && !(await p.locator('#sidebar').evaluate(n=>n.classList.contains('open'))))await p.locator('#mobile-menu').click();await link.click();if(mobile)await p.waitForFunction(()=>document.querySelector('#sidebar').getBoundingClientRect().right<=0);await p.evaluate(()=>window.scrollTo(0,0));}
  async function saved(p,route,action){const response=p.waitForResponse(r=>r.url().endsWith(route) && r.request().method()==='POST');await action();const r=await response;assert.equal(r.status(),200,await r.text());return r.json();}
  async function noOverflow(p){const layout=await p.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,nodes:[...document.querySelectorAll('.page-head,.page-actions,.sales-pipeline-select,.main-area,.page-content,.kanban-scroll,.sales-drawer')].map(e=>({class:e.className,rect:e.getBoundingClientRect().toJSON()}))}));assert(layout.scroll<=layout.width+1,'Page has horizontal overflow: '+JSON.stringify(layout));}
  let p;
  try {
    p=await pageFor({viewport:{width:1440,height:1000}});
    await nav(p,'sales-board');
    p.once('dialog',d=>d.accept('QA Enterprise '+Date.now()));await saved(p,'/api/sales/pipelines',()=>p.locator('[data-action="new-sales-pipeline"]').click());
    await p.waitForFunction(()=>!document.querySelector('[data-action="new-sales-pipeline"]').disabled);await p.locator('[data-action="open-sales-lead"]').first().click();let form=p.locator('#sales-lead-form');
    await form.locator('[name="client_name"]').fill('QA Browser Client');await form.locator('[name="event_name"]').fill('QA Browser Event');await form.locator('[name="event_date"]').fill('2041-07-01');await form.locator('[name="venue"]').fill('Jakarta');
    const lead=await saved(p,'/api/sales/leads',()=>form.locator('button[type=submit]').click());
    await p.locator('[data-action="edit-sales-document"][data-kind="quote"]').waitFor();
    for(const kind of ['quote','proforma','invoice','spk']){
      await p.locator(`[data-action="edit-sales-document"][data-kind="${kind}"]`).click();form=p.locator('#sales-document-form');await form.locator('[name="status"]').selectOption('sent');
      if(kind==='quote'){await form.locator('[name="quantity"]').fill('2');await form.locator('[name="days"]').fill('3');await form.locator('[name="unit_price_rupiah"]').fill('100000');await form.locator('[name="discount_rupiah"]').fill('10000');await form.locator('[name="tax_rupiah"]').fill('5000');}
      const doc=await saved(p,'/api/sales/documents',()=>form.locator('button[type=submit]').click());assert.equal(doc.amount_rupiah,595000);
      await p.locator('#sales-lead-form').waitFor();
    }
    form=p.locator('#spk-event-form');await form.locator('[name="starts_at"]').fill('2041-07-01T12:00');await form.locator('[name="ends_at"]').fill('2041-07-01T18:00');
    const spk=await form.locator('[name="spk_id"]').inputValue();await saved(p,`/api/sales/spk/${spk}/event`,()=>form.locator('button[type=submit]').click());
    await p.locator('#sales-lead-form').waitFor();const printHref=await p.locator('.sales-doc-row a').first().getAttribute('href');
    const print=await p.context().newPage();await print.goto(base+printHref);assert((await print.locator('main').innerText()).includes('595.000'));await print.screenshot({path:path.join(out,'sales-document.png'),fullPage:true});await print.close();
    await p.keyboard.press('Escape');await nav(p,'inventory');await p.locator('[data-action="toggle-inventory-item-form"]').click();form=p.locator('#inventory-item-form');
    const sku='QA-CAMERA-'+Date.now();await form.locator('[name="sku"]').fill(sku);await form.locator('[name="name"]').fill('QA Camera');await form.locator('[name="kind"]').selectOption('asset');await form.locator('[name="initial_quantity"]').fill('2');
    const item=await saved(p,'/api/inventory/items',()=>form.locator('button[type=submit]').click());
    const labels=await p.context().newPage();await labels.goto(`${base}/api/inventory/items/${item.id}/labels`);await labels.locator('[data-qr] img').first().waitFor();
    const qr=path.join(out,'asset-qr.png');await labels.locator('.qr-label').first().screenshot({path:qr});await labels.close();
    await nav(p,'sales-board');await p.locator(`[data-action="open-sales-lead"][data-id="${lead.id}"]`).click();await p.locator('[data-action="open-delivery-order"]').click();
    form=p.locator('#delivery-order-form');await form.locator('[name="spk_document_id"]').selectOption(spk);await form.locator(`[data-delivery-item="${item.id}"]`).fill('1');await form.locator('[name="release"]').check();
    const order=await saved(p,'/api/delivery-orders',()=>form.locator('button[type=submit]').click());assert.equal(order.status,'released');
    await p.locator(`[data-action="scan-delivery-order"][data-id="${order.id}"]`).click();
    await p.locator('[data-action="inventory-camera"]').click();await p.waitForFunction(()=>typeof Html5Qrcode!=='undefined');
    await p.locator('#inventory-scan-photo').setInputFiles(qr);await p.waitForFunction(()=>!document.querySelector('#inventory-camera-dialog').open);
    form=p.locator('#inventory-scan-form');assert.equal(await form.locator('[name="scan_code"]').inputValue(),sku+'-0001');
    await saved(p,'/api/inventory/scan',()=>form.locator('button[type=submit]').click());
    await p.waitForFunction(id=>state.data.delivery_orders.orders.find(o=>o.id===id)?.status==='out',order.id);
    form=p.locator('#inventory-scan-form');await form.locator('[name="direction"]').selectOption('return');await form.locator('[name="scan_code"]').fill(sku+'-0001');await saved(p,'/api/inventory/scan',()=>form.locator('button[type=submit]').click());
    await p.waitForFunction(id=>state.data.delivery_orders.orders.find(o=>o.id===id)?.status==='returned',order.id);await noOverflow(p);await p.screenshot({path:path.join(out,'inventory-desktop.png'),fullPage:true});
    await nav(p,'crm-queue');await p.locator('.crm-event').filter({hasText:'QA Calendar manual'}).click();form=p.locator('#crm-schedule-form');assert.equal(await form.locator('[name="start_time"]').inputValue(),'');await form.locator('[name="start_time"]').fill('10:00');await form.locator('[name="end_time"]').fill('18:00');
    const intake=await form.locator('[name="id"]').inputValue();await saved(p,`/api/calendar-queue/${intake}/promote`,()=>form.locator('button[type=submit]').click());
    await p.locator('#event-schedule-form').waitFor();assert((await p.locator('#event-schedule-form [name="starts_at"]').inputValue()).endsWith('T10:00'));
    await p.locator('[data-action="close-drawer"]').click();await p.screenshot({path:path.join(out,'crm-desktop.png'),fullPage:true});await noOverflow(p);
    await nav(p,'design');await p.locator('.kanban-card').first().click();await p.locator('.detail-drawer').waitFor();assert((await p.locator('.detail-drawer h2').innerText()).length>0);await p.locator('[data-action="close-drawer"]').click();
    await nav(p,'sales-board');const card=p.locator('.sales-kanban-card').filter({hasText:'QA Browser Client'}),target=p.locator('.sales-stage-column').nth(3);await card.dragTo(target);await p.waitForFunction(id=>state.data.sales.leads.find(l=>l.id===id)?.stage_code==='invoice',lead.id);
    await p.screenshot({path:path.join(out,'sales-desktop.png'),fullPage:true});await noOverflow(p);
    console.log('PASS desktop: multi-pipeline, linked docs, SPK event, SJ, QR decode, out/return, manual CRM hours, design detail, lead drag');
    const mobile=await pageFor({...devices['iPhone 13'],defaultBrowserType:undefined});
    for(const pageId of ['sales-board','sales-dashboard','inventory','delivery-orders','crm-queue']){await nav(mobile,pageId);await noOverflow(mobile);await mobile.screenshot({path:path.join(out,pageId+'-mobile.png'),fullPage:true});}
    p=mobile;await nav(mobile,'sales-board');await mobile.locator('.sales-kanban-card').filter({hasText:'QA Browser Client'}).click();await mobile.locator('#sales-lead-form').waitFor();await noOverflow(mobile);await mobile.screenshot({path:path.join(out,'lead-mobile.png'),fullPage:true});await mobile.locator('.drawer-close').click();
    const touchHandle=mobile.locator(`.sales-kanban-card[data-id="${lead.id}"] .sales-drag-handle`);await touchHandle.scrollIntoViewIfNeeded();
    await mobile.locator('.kanban-scroll').evaluate((scroll,id)=>{const column=scroll.querySelector(`[data-id="${id}"]`).closest('.sales-stage-column');scroll.scrollLeft+=column.getBoundingClientRect().left-scroll.getBoundingClientRect().left;},lead.id);
    const touchBox=await touchHandle.boundingBox(),scrollBox=await mobile.locator('.kanban-scroll').boundingBox(),cdp=await mobile.context().newCDPSession(mobile);
    const touchX=touchBox.x+touchBox.width/2,touchY=touchBox.y+touchBox.height/2;
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:touchX,y:touchY}]});
    for(let step=1;step<=18;step++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:scrollBox.x+scrollBox.width-12+step%2,y:touchY}]});await mobile.evaluate(()=>new Promise(requestAnimationFrame));}
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await mobile.waitForFunction(id=>state.data.sales.leads.find(l=>l.id===id)?.stage_code==='spk',lead.id);await noOverflow(mobile);
    assert.deepEqual(errors,[]);console.log('PASS mobile: five pages, lead detail, touch drag across stages, no horizontal page overflow or JavaScript errors');
  }catch(error){console.error('Page errors:',errors);if(p)await p.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});throw error;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
