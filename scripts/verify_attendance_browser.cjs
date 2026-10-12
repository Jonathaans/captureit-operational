/* End-to-end attendance through Waitress with Chromium's synthetic camera and location.
   Requires playwright + Chromium. Run: node scripts/verify_attendance_browser.cjs
   Optional CHROMIUM_PATH, PYTHON, QA_SCREENSHOTS (directory). Uses a temporary test DB. */
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
const path=require('node:path'),fs=require('node:fs');
const root=path.resolve(__dirname,'..');
const server=spawn(process.env.PYTHON||'python3',['tests/browser_attendance_server.py'],{cwd:root});
const ready=new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Test server did not start')),15000);
  server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/BROWSER_QA_URL=(\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
  server.stderr.on('data',chunk=>process.stderr.write(chunk));
  server.on('exit',code=>{clearTimeout(timer);reject(Error('Test server exited '+code));});
});
async function login(page,url,email,password){
  await page.goto(url);await page.locator('#login-email').fill(email);await page.locator('#login-password').fill(password);
  await page.locator('#login-form button').click();await page.locator('#app-shell').waitFor({state:'visible'});
}
async function navigate(page,name){
  if(await page.locator('#mobile-menu').isVisible())await page.locator('#mobile-menu').click();
  await page.locator(`#navigation [data-page="${name}"]`).click();
}
async function call(page,url,body){
  return await page.evaluate(({url,body})=>api(url,body?{method:'POST',body:JSON.stringify(body)}:{}),{url,body});
}
(async()=>{
  let browser;
  try{
    const base=await ready;
    browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined,
      args:['--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
    const adminContext=await browser.newContext();const admin=await adminContext.newPage();
    await login(admin,base,'admin@captureit.local','demo1234');
    const bootstrap=await call(admin,'/api/bootstrap');
    for(const [kind,viewport] of [['desktop',{width:1365,height:900}],['mobile',{width:390,height:844}]]){
      const context=await browser.newContext({viewport,isMobile:kind==='mobile',hasTouch:kind==='mobile',
        permissions:['camera','geolocation'],geolocation:{latitude:-6.2,longitude:106.8,accuracy:20}});
      const page=await context.newPage();const errors=[];page.on('pageerror',err=>errors.push(err.message));
      await login(page,base,`browser-${kind}@qa.local`,'BrowserQA!2026');
      const me=(await call(page,'/api/me')).user;
      const today=await page.evaluate(()=>localDateISO());
      await navigate(admin,'inhouse-attendance');
      const form=admin.locator('#inhouse-shift-form');await form.waitFor();
      await form.locator('[name=user_id]').selectOption(String(me.id));
      await form.locator('[name=shift_name]').fill('Malam QA');
      await form.locator('[name=start]').fill(today);await form.locator('[name=end]').fill(today);
      await form.locator('[name=work_start]').fill('22:00');await form.locator('[name=work_end]').fill('06:00');
      await form.locator('[type=submit]').click();
      await admin.getByText('Jadwal shift tersimpan.',{exact:true}).waitFor();
      await navigate(page,'inhouse-attendance');
      await page.locator('.inhouse-own-card').getByText(/Malam QA/).waitFor();
      assert.equal(await page.locator('#inhouse-shift-form').count(),0,'staff cannot schedule shifts');
      await page.locator('[data-action=inhouse-attendance]').click();
      await page.waitForFunction(()=>document.querySelector('#attendance-video')?.videoWidth>0);
      await page.locator('#capture-selfie').click();await page.locator('#submit-attendance-photo').waitFor({state:'visible'});
      await page.locator('#retake-selfie').click();assert.equal(await page.locator('#capture-selfie').isEnabled(),true);
      await page.locator('#capture-selfie').click();await page.locator('#submit-attendance-photo').waitFor({state:'visible'});
      await page.locator('#submit-attendance-photo').click();await page.locator('.camera-dialog').waitFor({state:'detached'});
      await page.locator('[data-attendance-action=check_out]').waitFor();
      assert.equal((await call(page,'/api/inhouse-attendance')).own_today.status,'checked_in');
      await page.locator('[data-attendance-action=check_out]').click();
      await page.waitForFunction(()=>document.querySelector('#attendance-video')?.videoWidth>0);
      await page.locator('#capture-selfie').click();await page.locator('#submit-attendance-photo').waitFor({state:'visible'});
      await page.locator('#submit-attendance-photo').click();await page.locator('.camera-dialog').waitFor({state:'detached'});
      assert.equal((await call(page,'/api/inhouse-attendance')).own_today.status,'checked_out');
      await page.locator('.inhouse-own-card').getByText('Absensi hari ini lengkap',{exact:true}).waitFor();
      assert.equal(await page.evaluate(()=>state.attendanceStream),null,'camera stops after sending');
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'attendance fits viewport');
      if(process.env.QA_SCREENSHOTS){fs.mkdirSync(process.env.QA_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.QA_SCREENSHOTS,`${kind}-attendance.png`),fullPage:true});await admin.screenshot({path:path.join(process.env.QA_SCREENSHOTS,'shift-manager.png'),fullPage:true});}
      // Real cards must open their own event/Design tab with touch/mouse and keyboard.
      const made=await call(admin,'/api/events/manual',{title:`Browser ${kind} event`,starts_at:`${today}T09:00`,ends_at:`${today}T18:00`});
      let task=(await call(admin,'/api/bootstrap')).design_tasks.find(t=>t.event_id===made.event_id);
      await call(admin,`/api/design/${task.id}/brief`,{brief_text:'QA brief',assignee_id:me.id,version:task.brief_version});
      await page.reload();await page.locator('#app-shell').waitFor({state:'visible'});await navigate(page,'design');
      const card=page.locator(`.kanban-card[data-id="${made.event_id}"]`);
      if(kind==='mobile')await card.tap();else await card.click();
      await page.locator('[aria-label="Detail event"]').waitFor();
      assert.equal(await page.evaluate(()=>state.drawer.id),made.event_id);assert.equal(await page.evaluate(()=>state.drawerTab),'design');
      await page.locator('[data-action=close-drawer]').click();await card.focus();await card.press('Enter');
      await page.locator('[aria-label="Detail event"]').waitFor();
      assert.deepEqual(errors,[]);console.log(`PASS ${kind}: shift form, check-in/out, retake, camera cleanup, event card, keyboard, layout`);
      await context.close();
    }
    // Camera permission failure remains recoverable through retry and native/device photo.
    const context=await browser.newContext({permissions:['geolocation'],geolocation:{latitude:-6.2,longitude:106.8,accuracy:20}});
    const page=await context.newPage();
    await page.addInitScript(()=>{navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('Denied','NotAllowedError');};});
    await login(page,base,'admin@captureit.local','demo1234');await navigate(page,'inhouse-attendance');
    await page.locator('[data-action=inhouse-attendance]').click();await page.getByText(/Izin kamera ditolak/).waitFor();
    const image=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=80;c.height=80;c.getContext('2d').fillRect(0,0,80,80);return c.toDataURL('image/png').split(',')[1];});
    await page.locator('#attendance-device-photo').setInputFiles({name:'qa.png',mimeType:'image/png',buffer:Buffer.from(image,'base64')});
    await page.locator('#submit-attendance-photo').waitFor({state:'visible'});
    await page.locator('#submit-attendance-photo').click();await page.locator('.camera-dialog').waitFor({state:'detached'});
    await page.locator('[data-attendance-action=check_out]').click();await page.getByText(/Izin kamera ditolak/).waitFor();
    await page.locator('[aria-label="Tutup kamera"]').click();await page.locator('.camera-dialog').waitFor({state:'detached'});
    assert.equal(await page.evaluate(()=>state.attendanceCapture),null);
    console.log('PASS device-photo fallback and cancel after camera permission error');
    await context.close();await adminContext.close();
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;server.kill('SIGTERM');});
