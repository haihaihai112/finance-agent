"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs/promises");
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || "playwright");
const base=process.env.FINANCE_BASE_URL || "http://127.0.0.1:8791";
async function settled(page){await page.waitForFunction(()=>!document.getElementById("create-session").disabled);}
(async()=>{
 const browser=await chromium.launch({channel:"msedge",headless:true});
 try{for(const width of [1440,390]){
 const context=await browser.newContext({viewport:{width,height:900},acceptDownloads:true});const page=await context.newPage();
 await page.goto(base);await page.locator("#create-session").click();await settled(page);
 assert.equal(await page.locator("#history-refresh").count(),1,"history refresh must exist");
 let posts=0;let requestId;
 await page.route("**/api/v1/queries",async route=>{posts++;requestId=route.request().postDataJSON().request_id;await route.fetch();await route.abort("failed");});
 await page.locator("#message").fill("你好");await page.locator("#send-query").click();await settled(page);
 assert.equal(posts,1);assert.equal(await page.locator("#pending-request-id").textContent(),requestId);
 const pending=await page.evaluate(()=>JSON.parse(sessionStorage.getItem("finance-workbench-request-v1")));assert.deepEqual(Object.keys(pending).sort(),["operation","request_id","session_id"]);
 await page.reload();await settled(page);assert.equal(posts,1);
 await page.route(`**/api/v1/requests/${requestId}`,r=>r.abort("failed"));await page.locator("#locate-request").click();await settled(page);assert.match(await page.locator("#request-status").textContent(),/查询失败.*核对/);assert.equal(await page.locator("#pending-request-id").textContent(),requestId);assert.equal(posts,1);await page.unroute(`**/api/v1/requests/${requestId}`);
 await page.locator("#locate-request").click();await settled(page);
 assert.match(await page.locator("#request-status").textContent(),/请求已完成/);
 const id=await page.locator("#run-id").textContent();assert.notEqual(id,"尚未发起");
 await page.locator("#history-refresh").click();await settled(page);assert.ok((await page.locator("#history-list").textContent()).includes(id));
 await page.locator("#history-list button").first().click();await settled(page);assert.equal(await page.locator("#run-id").textContent(),id);
 await page.locator("#refresh-events").click();await settled(page);assert.ok((await page.locator("#event-list").textContent()).length>0);
 const fixture={run_id:"delivery-fixture",intent:"other",status:"completed",state_version:1,document_ids:[],form:{},result:{conclusion:"测试精度",data_mode:"mock",facts:{amount:"9007199254740993.01"},citations:[],limitations:["受控夹具"]}};
 await page.route("**/api/v1/runs/delivery-fixture",r=>r.fulfill({json:fixture}));
 await page.locator("#restore-run-id").evaluate(e=>e.closest("details").open=true);await page.locator("#restore-run-id").fill(fixture.run_id);await page.locator("#restore-run").click();await settled(page);
 await page.route("**/api/v1/runs/delivery-fixture/export?format=*",r=>{assert.ok(r.request().headers().authorization);assert.ok(!r.request().url().includes("token"));return r.fulfill({contentType:"application/json",headers:{"Content-Disposition":"attachment; filename=report.json"},body:JSON.stringify({result:fixture.result})});});
 const downloadPromise=page.waitForEvent("download");await page.locator("#export-json").click();const download=await downloadPromise;assert.ok((await fs.readFile(await download.path(),"utf8")).includes("9007199254740993.01"));await settled(page);
 await page.locator("#request-id-input").fill("unknown-request");await page.locator("#locate-request").click();await settled(page);assert.match(await page.locator("#notice").textContent(),/404/);assert.match(await page.locator("#request-status").textContent(),/查询失败.*核对/);assert.ok(!(await page.locator("#request-status").textContent()).includes("请求已完成"));
 await page.route("**/api/v1/requests/unknown-request",r=>r.fulfill({json:{request_id:"unknown-request",status:"processing",updated_at:"2000-01-01T00:00:00Z"}}));
 await page.locator("#locate-request").click();await settled(page);
 await page.route("**/api/v1/requests/unknown-request/interrupt",r=>r.fulfill({status:409,json:{detail:"请求状态已变化，请重新查询"}}));
 await page.locator("#interrupt-request").click();await settled(page);assert.match(await page.locator("#notice").textContent(),/409/);assert.equal(posts,1);
 assert.equal(await page.evaluate(()=>localStorage.length),0);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 // A failed cross-history attachment fetch must preserve the current edit.
 const failed={...fixture,run_id:"broken-history",document_ids:["broken-document"]};
 await page.route("**/api/v1/runs?*",r=>r.fulfill({json:Array.from({length:10},(_,i)=>({...failed,run_id:i ? `history-${i}` : failed.run_id}))}));
 await page.route("**/api/v1/runs/broken-history",r=>r.fulfill({json:failed}));
 await page.route("**/api/v1/documents/broken-document",r=>r.fulfill({status:503,json:{detail:"controlled attachment failure"}}));
 await page.locator("#history-refresh").click();await settled(page);await page.locator("#history-list button").first().click();await settled(page);assert.equal(await page.locator("#run-id").textContent(),fixture.run_id);
 await page.locator("#history-next").click();await settled(page);assert.equal(await page.locator("#history-page").textContent(),"第 2 页");await page.locator("#history-prev").click();await settled(page);
 await page.locator("#history-next").click();await settled(page);assert.equal(await page.locator("#history-page").textContent(),"第 2 页");
 await page.locator("#create-session").click();await settled(page);assert.equal(await page.evaluate(()=>sessionStorage.getItem("finance-workbench-request-v1")),null);assert.equal(await page.locator("#request-id-input").inputValue(),"");assert.equal(await page.locator("#history-page").textContent(),"第 1 页");
 console.log(`PASS delivery Edge ${width}px: lost response, reload, read-only recovery, history, events, authenticated exact download, unknown request, interrupt conflict`);await context.close();
 }
 const context=await browser.newContext();const page=await context.newPage();const errors=[];page.on("pageerror",e=>errors.push(e.message));await page.goto(base);await page.locator("#create-session").click();await settled(page);
 for(const [intent,message] of [["investment","我想投资"],["claim","我要申请保险理赔"]]){
   if(await page.locator("#new-business").isVisible())await page.locator("#new-business").click();
   await page.locator(`[data-intent="${intent}"]`).click();await page.locator("#message").fill(message);await page.locator("#send-query").click();await settled(page);
   assert.equal(await page.locator("#run-status").textContent(),"等待补充");assert.ok((await page.locator("#run-question").textContent()).length>0);
 }
 await page.locator("#new-business").click();await page.locator('[data-intent="policy_tax"]').click();await page.locator("#message").fill("请计算居民年度综合所得个人所得税");await page.locator("#send-query").click();await settled(page);
 const values={jurisdiction:"中国大陆",tax_year:"2026",income_type:"resident_comprehensive_annual",resident_status:"resident",annual_income:"120000",annual_deductions:"0"};
 for(const [field,value] of Object.entries(values)){const input=page.locator(`#field-${field}`);if(await input.evaluate(e=>e.tagName)==="SELECT")await input.selectOption(value);else await input.fill(value);await input.locator("..").locator('input[type="checkbox"]').check();}
 await page.locator("#confirm-resume").click();await settled(page);assert.equal(await page.locator("#run-status").textContent(),"已完成");assert.ok((await page.locator("#result-facts").textContent()).includes("3480.00"));
 for(const format of ["json","markdown"]){const promise=page.waitForEvent("download");await page.locator(`#export-${format}`).click();const d=await promise;assert.ok((await fs.readFile(await d.path(),"utf8")).includes("3480.00"));await settled(page);}
 assert.deepEqual(errors,[]);console.log("PASS real API investment/claim missing-information and confirmed annual tax 3480.00 with JSON/Markdown downloads");await context.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
