"use strict";
const {reveal, click, fill}=require("./ui-controls.cjs");
const assert=require("node:assert/strict");
const fs=require("node:fs/promises");
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || "playwright");
const base=process.env.FINANCE_BASE_URL || "http://127.0.0.1:8791";
async function settled(page){await page.waitForFunction(()=>!document.getElementById("create-session").disabled);}
(async()=>{
 const browser=await chromium.launch({channel:"msedge",headless:true});
 try{
 const recoveryContext=await browser.newContext();const recoveryPage=await recoveryContext.newPage();
 await recoveryPage.goto(base);await click(recoveryPage,"#create-session");await settled(recoveryPage);
 let rejectedPosts=0,rejectedId;
 await recoveryPage.route("**/api/v1/queries",route=>{rejectedPosts++;rejectedId=route.request().postDataJSON().request_id;return route.abort("failed");});
 await fill(recoveryPage,"#message","你好");await click(recoveryPage,"#send-query");await settled(recoveryPage);
 await click(recoveryPage,"#locate-request");await settled(recoveryPage);
 assert.match(await recoveryPage.locator("#notice").textContent(),/404/);assert.equal(rejectedPosts,1);assert.ok(await recoveryPage.locator("#send-query").isDisabled());
 assert.equal(await recoveryPage.locator("#release-pending-request").count(),1,"404 pending recovery must provide explicit release");
 await fill(recoveryPage,"#request-id-input","arbitrary-404");await click(recoveryPage,"#locate-request");await settled(recoveryPage);assert.ok(await recoveryPage.locator("#release-pending-request").isDisabled());
 await fill(recoveryPage,"#request-id-input",rejectedId);assert.ok(await recoveryPage.locator("#release-pending-request").isDisabled());
 await recoveryPage.route(`**/api/v1/requests/${rejectedId}`,route=>route.fulfill({status:503,json:{detail:"controlled lookup failure"}}));await click(recoveryPage,"#locate-request");await settled(recoveryPage);assert.ok(await recoveryPage.locator("#release-pending-request").isDisabled());await recoveryPage.unroute(`**/api/v1/requests/${rejectedId}`);
 await click(recoveryPage,"#locate-request");await settled(recoveryPage);
 recoveryPage.once("dialog",dialog=>dialog.dismiss());await click(recoveryPage,"#release-pending-request");assert.ok(await recoveryPage.locator("#send-query").isDisabled());
 recoveryPage.once("dialog",dialog=>{assert.match(dialog.message(),/不会取消.*历史/);return dialog.accept();});await click(recoveryPage,"#release-pending-request");assert.ok(await recoveryPage.locator("#send-query").isEnabled());assert.equal(rejectedPosts,1);
 const abandoned=await recoveryPage.evaluate(()=>JSON.parse(sessionStorage.getItem("finance-workbench-abandoned-requests-v1")));assert.deepEqual(Object.keys(abandoned).sort(),["requests","session_id"]);assert.deepEqual(abandoned.requests,[{request_id:rejectedId,operation:"query"}]);
 await recoveryPage.reload();await settled(recoveryPage);assert.ok((await recoveryPage.locator("#abandoned-requests").textContent()).includes(rejectedId));await recoveryPage.locator("#abandoned-requests").locator("..").locator("summary").click();await click(recoveryPage,"#abandoned-requests button");assert.equal(await recoveryPage.locator("#request-id-input").inputValue(),rejectedId);
 await recoveryPage.unroute("**/api/v1/queries");await fill(recoveryPage,"#message","你好");await click(recoveryPage,"#send-query");await settled(recoveryPage);assert.notEqual(await recoveryPage.locator("#run-id").textContent(),"尚未发起");assert.equal(rejectedPosts,1);
 await click(recoveryPage,"#create-session");await settled(recoveryPage);assert.equal(await recoveryPage.evaluate(()=>sessionStorage.getItem("finance-workbench-abandoned-requests-v1")),null);
 console.log("PASS pre-server POST failure: real GET404, explicit acknowledged same-session release, retained identifiers after reload, no POST replay");await recoveryContext.close();
 for(const width of [1440,390]){
 const context=await browser.newContext({viewport:{width,height:900},acceptDownloads:true});const page=await context.newPage();
 await page.goto(base);await click(page,"#create-session");await settled(page);
 assert.equal(await page.locator("#history-refresh").count(),1,"history refresh must exist");
 let posts=0;let requestId;
 await page.route("**/api/v1/queries",async route=>{posts++;requestId=route.request().postDataJSON().request_id;await route.fetch();await route.abort("failed");});
 await fill(page,"#message","你好");await click(page,"#send-query");await settled(page);
 assert.equal(posts,1);assert.equal(await page.locator("#pending-request-id").textContent(),requestId);
 const pending=await page.evaluate(()=>JSON.parse(sessionStorage.getItem("finance-workbench-request-v1")));assert.deepEqual(Object.keys(pending).sort(),["operation","request_id","session_id"]);
 await page.reload();await settled(page);assert.equal(posts,1);
 await page.route(`**/api/v1/requests/${requestId}`,r=>r.abort("failed"));await click(page,"#locate-request");await settled(page);assert.match(await page.locator("#request-status").textContent(),/查询失败.*核对/);assert.equal(await page.locator("#pending-request-id").textContent(),requestId);assert.equal(posts,1);await page.unroute(`**/api/v1/requests/${requestId}`);
 await click(page,"#locate-request");await settled(page);
 assert.match(await page.locator("#request-status").textContent(),/请求已完成/);
 const id=await page.locator("#run-id").textContent();assert.notEqual(id,"尚未发起");
 await click(page,"#history-refresh");await settled(page);assert.ok((await page.locator("#history-list").textContent()).includes(id));
 await page.locator("#history-list button").first().click();await settled(page);assert.equal(await page.locator("#run-id").textContent(),id);
 await click(page,"#refresh-events");await settled(page);assert.ok((await page.locator("#event-list").textContent()).length>0);
 const fixture={run_id:"delivery-fixture",intent:"other",status:"completed",state_version:1,document_ids:[],form:{},result:{conclusion:"测试精度",data_mode:"mock",facts:{amount:"9007199254740993.01"},citations:[],limitations:["受控夹具"]}};
 await page.route("**/api/v1/runs/delivery-fixture",r=>r.fulfill({json:fixture}));
 await page.locator("#restore-run-id").evaluate(e=>e.closest("details").open=true);await fill(page,"#restore-run-id",fixture.run_id);await click(page,"#restore-run");await settled(page);
 await page.route("**/api/v1/runs/delivery-fixture/export?format=*",r=>{assert.ok(r.request().headers().authorization);assert.ok(!r.request().url().includes("token"));return r.fulfill({contentType:"application/json",headers:{"Content-Disposition":"attachment; filename=report.json"},body:JSON.stringify({result:fixture.result})});});
 const downloadPromise=page.waitForEvent("download");await click(page,"#export-json");const download=await downloadPromise;assert.ok((await fs.readFile(await download.path(),"utf8")).includes("9007199254740993.01"));await settled(page);
 await fill(page,"#request-id-input","unknown-request");await click(page,"#locate-request");await settled(page);assert.match(await page.locator("#notice").textContent(),/404/);assert.match(await page.locator("#request-status").textContent(),/查询失败.*核对/);assert.ok(!(await page.locator("#request-status").textContent()).includes("请求已完成"));
 await page.route("**/api/v1/requests/unknown-request",r=>r.fulfill({json:{request_id:"unknown-request",status:"processing",updated_at:"2000-01-01T00:00:00Z"}}));
 await click(page,"#locate-request");await settled(page);
 await page.route("**/api/v1/requests/unknown-request/interrupt",r=>r.fulfill({status:409,json:{detail:"请求状态已变化，请重新查询"}}));
 await click(page,"#interrupt-request");await settled(page);assert.match(await page.locator("#notice").textContent(),/409/);assert.equal(posts,1);
 assert.equal(await page.evaluate(()=>localStorage.length),0);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 // A failed cross-history attachment fetch must preserve the current edit.
 const failed={...fixture,run_id:"broken-history",document_ids:["broken-document"]};
 await page.route("**/api/v1/runs?*",r=>r.fulfill({json:Array.from({length:10},(_,i)=>({...failed,run_id:i ? `history-${i}` : failed.run_id}))}));
 await page.route("**/api/v1/runs/broken-history",r=>r.fulfill({json:failed}));
 await page.route("**/api/v1/documents/broken-document",r=>r.fulfill({status:503,json:{detail:"controlled attachment failure"}}));
 await click(page,"#history-refresh");await settled(page);await page.locator("#history-list button").first().click();await settled(page);assert.equal(await page.locator("#run-id").textContent(),fixture.run_id);
 await click(page,"#history-next");await settled(page);assert.equal(await page.locator("#history-page").textContent(),"第 2 页");await click(page,"#history-prev");await settled(page);
 await click(page,"#history-next");await settled(page);assert.equal(await page.locator("#history-page").textContent(),"第 2 页");
 await click(page,"#create-session");await settled(page);assert.equal(await page.evaluate(()=>sessionStorage.getItem("finance-workbench-request-v1")),null);assert.equal(await page.locator("#request-id-input").inputValue(),"");assert.equal(await page.locator("#history-page").textContent(),"第 1 页");
 console.log(`PASS delivery Edge ${width}px: lost response, reload, read-only recovery, history, events, authenticated exact download, unknown request, interrupt conflict`);await context.close();
 }
 const context=await browser.newContext();const page=await context.newPage();const errors=[];page.on("pageerror",e=>errors.push(e.message));await page.goto(base);await click(page,"#create-session");await settled(page);
 for(const [intent,message] of [["investment","我想投资"],["claim","我要申请保险理赔"]]){
   if(await page.locator("#new-business").isVisible())await click(page,"#new-business");
   await page.locator(`[data-intent="${intent}"]`).click();await fill(page,"#message",message);await click(page,"#send-query");await settled(page);
   assert.equal(await page.locator("#run-status").textContent(),"等待补充");assert.ok((await page.locator("#run-question").textContent()).length>0);
 }
 await click(page,"#new-business");await page.locator('[data-intent="policy_tax"]').click();await fill(page,"#message","请计算居民年度综合所得个人所得税");await click(page,"#send-query");await settled(page);
 const values={jurisdiction:"中国大陆",tax_year:"2026",income_type:"resident_comprehensive_annual",resident_status:"resident",annual_income:"120000",annual_deductions:"0"};
 for(const [field,value] of Object.entries(values)){const input=page.locator(`#field-${field}`);if(await input.evaluate(e=>e.tagName)==="SELECT")await input.selectOption(value);else await input.fill(value);await input.locator("..").locator('input[type="checkbox"]').check();}
 await click(page,"#confirm-resume");await settled(page);assert.equal(await page.locator("#run-status").textContent(),"已完成");assert.ok((await page.locator("#result-facts").textContent()).includes("3480.00"));
 for(const format of ["json","markdown"]){const promise=page.waitForEvent("download");await page.locator(`#export-${format}`).click();const d=await promise;assert.ok((await fs.readFile(await d.path(),"utf8")).includes("3480.00"));await settled(page);}
 assert.deepEqual(errors,[]);console.log("PASS real API investment/claim missing-information and confirmed annual tax 3480.00 with JSON/Markdown downloads");await context.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
