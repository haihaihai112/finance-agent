/* Shared, read-only presentation of the existing server workflow. */
(function (root) {
  "use strict";
  function describeWorkflow(run, {pending=false, busy=false}={}) {
    const hasResult=Boolean(run?.result);
    const view={step:0,hasResult,needsDocuments:false,needsRecovery:pending,
      title:"先描述你的需求",help:"无需准备全部信息，可以先说说你想解决的问题。"};
    if(pending) return {...view,step:hasResult ? 2 : run && run.wait_reason!=="clarify_intent" ? 1 : 0,title:busy ? "正在处理" : "先确认上次请求的结果",
      help:"请等待当前请求；如遇连接异常，先查看请求状态，避免重复提交。"};
    if(!run) return view;
    if(run.wait_reason==="clarify_intent") return {...view,title:"先确认办理哪项业务",help:"请在对话中补充你想优先处理的问题。"};
    if(run.status==="cancelled") return {...view,title:"本次办理已取消",help:"历史记录仍保留。可以复制后重新确认，或开始新对话。"};
    if(hasResult) return {...view,step:2,title:run.status==="partial" ? "已有部分结果" : "查看结果与依据",
      help:run.status==="partial" ? "请留意缺失数据与使用限制；补充后可复制为新业务。" : "请结合来源与限制阅读。需要调整时，可以复制后重新确认。"};
    if(run.status==="failed") return {...view,title:"本次办理未完成",help:"请查看错误提示；确认处理状态后再开始新业务。"};
    if(run.wait_reason==="consent") return {...view,step:1,title:"单独确认本次授权",help:"字段确认不等于授权。授权后才能调用模拟征信与风控服务。"};
    if(run.wait_reason==="upload_documents") return {...view,step:1,needsDocuments:true,title:"准备并核对材料",
      help:run.intent==="loan" ? "上传收入、负债材料；没有材料时可显式选择自填试算，结果会标注未经材料核验。" : "上传保单或出险单据，核对提取内容后再查询历史保单。"};
    return {...view,step:1,title:busy ? "正在处理已确认信息" : "补充并确认关键信息",help:"请核对金额、币种与资料来源。填写后逐项勾选确认，系统不会替你确认财务信息。"};
  }
  const api = {describeWorkflow};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FinanceWorkflowView = api;
})(typeof globalThis === "object" ? globalThis : this);
