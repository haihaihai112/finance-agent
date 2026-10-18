"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {describeWorkflow} = require("../../web/workflow-view.js");

test("new conversations and ambiguous requests stay at describing needs", () => {
  assert.equal(describeWorkflow(null).step, 0);
  assert.equal(describeWorkflow({status:"waiting_user",wait_reason:"clarify_intent"}).step, 0);
});
test("loan upload offers a distinct self-reported path without pretending documents were checked", () => {
  const view=describeWorkflow({intent:"loan",status:"waiting_user",wait_reason:"upload_documents"});
  assert.equal(view.step,1);
  assert.equal(view.needsDocuments,true);
  assert.match(view.help,/自填/);
});
test("consent remains a separate stage within confirmation", () => {
  const view=describeWorkflow({intent:"loan",status:"waiting_user",wait_reason:"consent"});
  assert.equal(view.step,1);
  assert.match(view.title,/授权/);
  assert.equal(view.needsDocuments,false);
});
test("partial output stays visibly partial and a cancelled request does not claim a result", () => {
  const partial=describeWorkflow({status:"partial",result:{conclusion:"部分净值"}});
  assert.equal(partial.step,2);
  assert.match(partial.title,/部分/);
  const cancelled=describeWorkflow({status:"cancelled"});
  assert.equal(cancelled.hasResult,false);
  assert.match(cancelled.title,/取消/);
});
test("completed output and a pending unknown request have distinct navigation", () => {
  const view=describeWorkflow({status:"completed",result:{conclusion:"done"}});
  assert.equal(view.step,2);
  assert.equal(view.hasResult,true);
  const pending=describeWorkflow(null,{pending:true});
  assert.equal(pending.needsRecovery,true);
  assert.match(pending.help,/重复提交/);
});
