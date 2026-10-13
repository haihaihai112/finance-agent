/* Four controlled display fixtures + real HTTP loan copy / consent / reassessment. */
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs/promises");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const baseURL = process.env.FINANCE_BASE_URL || "http://127.0.0.1:8789";
const only = process.env.BROWSER_CASE || "all";
const screenshotDir = process.env.BROWSER_SCREENSHOT_DIR;

async function screenshot(page, name) {
  if (!screenshotDir) return;
  await fs.mkdir(screenshotDir, {recursive: true});
  await page.screenshot({path: path.join(screenshotDir, name + ".png"), fullPage: true});
}
async function settled(page) { await page.locator("#send-query").waitFor({state:"visible"}); await page.waitForFunction(() => !document.getElementById("send-query").disabled); }
async function restore(page, id) {
  await page.locator("#restore-run-id").evaluate(el => el.closest("details").open = true);
  await page.locator("#restore-run-id").fill(id);
  await page.locator("#restore-run").click();
  await settled(page);
  assert.equal(await page.locator("#run-id").textContent(), id);
}
async function fixtureCases(browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(baseURL);
  await page.locator("#create-session").click(); await settled(page);
  const fixtures = [
    ["potentially_covered", "可能符合条款，需机构审核", true],
    ["outside_coverage_dates", "出险日期不在保障期内", false],
    ["excluded_by_provided_clause", "所提供条款包含除外情形", true],
    ["requires_manual_clause_review", "条款需人工审核", true],
  ];
  for (const [verdict, label, within] of fixtures) {
    const id = "fixture-" + verdict;
    const endpoint = `/api/v1/runs/${id}`;
    await page.route("**" + endpoint, route => route.fulfill({json:{
      run_id:id, intent:"claim", status:"completed", state_version:2, form:{}, document_ids:[],
      result:{data_mode:"mock", conclusion:"受控显示夹具：" + verdict, citations:[], limitations:["此案例仅验证渲染"],
        facts:{structured_facts:{clause_check:{preliminary_result:verdict, incident_date:"2026-10-01", within_effective_dates:within}}}},
    }}));
    await restore(page, id);
    const values = await page.locator("#result-facts .summary-value").allTextContents();
    assert.deepEqual(values, [label, "2026-10-01", within ? "是" : "否"]);
    await screenshot(page, id);
    console.log("PASS controlled display fixture:", verdict);
    await page.unroute("**" + endpoint);
  }
  await context.close();
}
async function restoreFailureFixtures(browser) {
  // Controlled network failures: no claim that these are live OCR failures.
  for (const failPart of ["extraction", "metadata"]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const runs = {
      "fixture-A": {run_id:"fixture-A", intent:"loan", status:"waiting_user", state_version:1, form:{}, document_ids:["doc-A"]},
      "fixture-B": {run_id:"fixture-B", intent:"loan", status:"waiting_user", state_version:1, form:{}, document_ids:["doc-B"]},
    };
    const resumes = [];
    await page.route("**/api/v1/runs/fixture-*", route => route.fulfill({json:runs[route.request().url().split("/").at(-1)]}));
    await page.route("**/api/v1/documents/doc-*", route => {
      const id = route.request().url().split("/").at(-1);
      return route.fulfill(id === "doc-B" && failPart === "metadata" ? {status:503,json:{detail:"controlled metadata failure"}} : {json:{document_id:id,filename:id + ".pdf"}});
    });
    await page.route("**/api/v1/documents/doc-*/extraction", route => {
      const id = route.request().url().split("/").at(-2);
      return route.fulfill(id === "doc-B" && failPart === "extraction" ? {status:503,json:{detail:"controlled extraction failure"}} : {json:{text:"evidence for " + id,fields:[]}});
    });
    await page.route("**/api/v1/runs/fixture-*/resume", route => {
      const id = route.request().url().split("/").at(-2);
      resumes.push({id, body:route.request().postDataJSON()});
      return route.fulfill({json:runs[id]});
    });
    await page.goto(baseURL);
    await page.locator("#create-session").click(); await settled(page);
    await restore(page, "fixture-A");
    assert.match(await page.locator("#document-list").textContent(), /doc-A/);
    await page.locator("#restore-run-id").fill("fixture-B");
    await page.locator("#restore-run").click(); await settled(page);
    assert.match(await page.locator("#notice").textContent(), /controlled .* failure/);
    await page.locator("#message").fill("恢复失败后的补答");
    await page.locator("#send-query").click(); await settled(page);
    // Atomic recovery may retain A; it must never submit A's attachment to B.
    assert.deepEqual(resumes.map(item => [item.id, item.body.attachment_ids]), [["fixture-A", ["doc-A"]]]);
    assert.equal(await page.locator("#run-id").textContent(), "fixture-A");
    assert.match(await page.locator("#document-list").textContent(), /doc-A/);
    assert.doesNotMatch(await page.locator("#document-list").textContent(), /doc-B/);
    console.log("PASS controlled atomic restoration failure fixture:", failPart);
    await context.close();
  }
}
async function realLoan(browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const pageErrors = []; page.on("pageerror", error => pageErrors.push(error.message));
  await page.goto(baseURL);
  await page.locator("#create-session").click(); await settled(page);
  await page.locator("#create-demo").evaluate(el => el.closest("details").open = true);
  await page.locator("#create-demo").click(); await settled(page);
  await page.locator("#document-list li").nth(1).locator("input[type=checkbox]").check();
  await page.locator('[data-intent="loan"]').click();
  await page.locator("#message").fill("申请贷款，请提取演示材料");
  await page.locator("#send-query").click(); await settled(page);
  const firstId = await page.locator("#run-id").textContent();
  const identity = await page.evaluate(() => JSON.parse(sessionStorage.getItem("finance-workbench-session-v1")).session);
  const api = async (endpoint, body) => {
    const response = await context.request[body === undefined ? "get" : "post"](baseURL + endpoint, {
      headers:{Authorization:"Bearer " + identity.token}, ...(body === undefined ? {} : {data:body}),
    });
    assert.equal(response.status(), 200, await response.text()); return response.json();
  };
  const confirmFilled = async () => {
    for (const field of await page.locator("#business-fields .field").all()) {
      if ((await field.locator("input:not([type=checkbox]),select").inputValue()).trim()) await field.locator("input[type=checkbox]").check();
    }
  };
  await page.locator("#field-subject_ref").fill("browser-synthetic-subject");
  await page.locator("#field-requested_amount").fill("12000");
  await page.locator("#field-loan_term_months").fill("12");
  await page.locator("#field-monthly_income").fill("8000");
  await page.locator("#field-monthly_debt_payment").fill("1000");
  await page.locator("#field-employment_status").fill("自由职业");
  await confirmFilled();
  await page.locator("#confirm-resume").click(); await settled(page);
  assert.equal(await page.locator("#wait-reason").textContent(), "授权");
  await page.locator("#grant-consent").click(); await settled(page);
  await page.locator("#confirm-resume").click(); await settled(page);
  const historical = await api(`/api/v1/runs/${firstId}`);
  assert.equal(historical.status, "completed");
  assert.ok(historical.form.consent_ref);
  assert.ok(historical.document_ids.length);
  const originalExtraction = await api(`/api/v1/documents/${historical.document_ids[0]}/extraction`);
  await page.reload(); await settled(page);
  assert.equal(await page.locator("#run-id").textContent(), firstId);
  await page.locator("#copy-loan").click(); await settled(page);
  const copiedId = await page.locator("#run-id").textContent();
  assert.notEqual(copiedId, firstId);
  const copied = await api(`/api/v1/runs/${copiedId}`);
  assert.equal(copied.parent_run_id, firstId);
  assert.deepEqual(copied.document_ids, historical.document_ids);
  assert.deepEqual(copied.form.confirmed_fields, []);
  assert.equal(copied.form.consent_ref, null);
  assert.equal(await page.locator("#business-fields input[type=checkbox]:checked").count(), 0);
  assert.equal(await page.locator("#consent-status").textContent(), "尚未授权");
  assert.equal(await page.locator("#run-parent-id").textContent(), firstId);
  assert.ok(await page.locator("#document-list").textContent());
  assert.ok(await page.locator("#extractions .evidence-field").count());
  assert.deepEqual(await api(`/api/v1/documents/${copied.document_ids[0]}/extraction`), originalExtraction);
  await page.locator("#confirm-resume").click(); await settled(page);
  assert.match(await page.locator("#notice").textContent(), /核对并勾选/);
  assert.deepEqual(await api(`/api/v1/runs/${copiedId}`), copied);
  await page.locator("#field-requested_amount").fill("24000");
  await page.locator("#field-loan_term_months").fill("24");
  await confirmFilled();
  await page.locator("#confirm-resume").click(); await settled(page);
  const awaiting = await api(`/api/v1/runs/${copiedId}`);
  assert.equal(awaiting.wait_reason, "consent");
  assert.equal(awaiting.result, null);
  assert.equal(awaiting.form.requested_amount.amount, "24000");
  assert.equal(awaiting.form.loan_term_months, 24);
  await page.locator("#grant-consent").click(); await settled(page);
  await page.locator("#confirm-resume").click(); await settled(page);
  const finished = await api(`/api/v1/runs/${copiedId}`);
  assert.equal(finished.status, "completed");
  assert.notEqual(finished.form.consent_ref, historical.form.consent_ref);
  assert.equal(finished.result.facts.structured_facts.assessment.table_row.requested_amount, "24000");
  assert.deepEqual(await api(`/api/v1/runs/${firstId}`), historical);
  await screenshot(page, "loan-copy-reauthorized");
  await page.locator("#read-parent-run").click(); await settled(page);
  assert.equal(await page.locator("#run-id").textContent(), firstId);
  assert.equal(await page.locator("#conclusion").textContent(), historical.result.conclusion);
  assert.deepEqual(pageErrors, []);
  console.log("PASS real API loan copy:", {firstId, copiedId, changedAmount:"24000", changedTerm:24, historicalUnchanged:true});
  await context.close();
}
(async () => {
  const browser = await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL || "msedge", headless:process.env.BROWSER_HEADLESS !== "false"});
  try {
    if (["all", "fixtures"].includes(only)) await fixtureCases(browser);
    if (["all", "restore"].includes(only)) await restoreFailureFixtures(browser);
    if (["all", "loan"].includes(only)) await realLoan(browser);
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode=1;});
