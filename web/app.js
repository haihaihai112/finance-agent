/* No build step. All external content is rendered as text; financial numbers stay strings. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const STORAGE_KEY = "finance-workbench-session-v1";
  const TITLES = {investment:"投资咨询",claim:"理赔申请",loan:"贷款申请",policy_tax:"政策 / 税务",other:"其他"};
  const DESCRIPTIONS = {
    auto:"由服务端根据自然语言识别五类业务；涉及多类业务时会先追问处理顺序。自动识别是入口模式。",
    investment:"先确认投资画像，再查询公开基金日频净值。请输入六位基金 / ETF 基金代码；当前不提供实时行情。",
    claim:"先上传保单和出险材料，确认提取字段后查询当前用户的历史保单与条款版本。",
    loan:"支持多份材料；核对收入、负债和贷款要求后，另行授权本次模拟征信与风控评估。",
    policy_tax:"确认地域和税年。年度试算使用居民综合所得收入额；扣除合计不包含 60000 元基本减除费用。",
    other:"此入口用于其他问题；超出金融业务范围时系统会明确说明。"
  };
  // [field, label, type, help, select options]. Never prefill financial values.
  const SCHEMAS = {
    investment:[
      ["jurisdiction","地域","text","首版支持中国大陆"],
      ["investable_amount","可投资金额","money","元 / CNY"],
      ["horizon_months","投资期限（月）","integer","至少 1 个月"],
      ["risk_tolerance","风险承受程度","select","请选择并确认",[["low","低 / 保守"],["medium","中 / 稳健"],["high","高 / 积极"]]],
      ["liquidity_need","流动资金需求","money","不能超过可投资金额"],
      ["liabilities","负债合计","money","无负债须明确填写 0"],
      ["existing_assets","现有资产","list","逗号或换行分隔；无资产填写“无”"],
      ["instrument_ids","基金 / ETF 基金代码","list","六位代码，逗号或换行分隔"]
    ],
    claim:[
      ["policy_id","保单号","text","请与保单原文核对"],
      ["policy_version","条款版本","text","请与历史保单的版本核对"],
      ["insurer","保险机构","text","可选；填写后须确认"],
      ["insurance_type","险种","text","可选；填写后须确认"],
      ["premium","保费","money","可选；元 / CNY"],
      ["insured_amount","保险金额","money","可选；元 / CNY"],
      ["incident_type","出险类型","text","依据材料填写"],
      ["incident_date","出险日期","date","年-月-日"]
    ],
    loan:[
      ["subject_ref","申请人参考编号","text","仅作为当前模拟主体编号"],
      ["requested_amount","申请贷款金额","money","元 / CNY"],
      ["loan_term_months","贷款期限（月）","integer","至少 1 个月"],
      ["monthly_income","月收入","money","月度金额，元 / CNY"],
      ["monthly_debt_payment","月偿债支出","money","无支出须明确填写 0"],
      ["employment_status","就业状态","text","例如：在职；如实填写"],
      ["employment_months","就业时长（月）","integer","可选，至少 0 个月"],
      ["assets_total","资产合计","money","可选；元 / CNY"],
      ["liabilities_total","负债合计","money","可选；元 / CNY"]
    ],
    policy_tax:[
      ["jurisdiction","地域","text","首版支持中国大陆"],
      ["tax_year","税年","integer","例如：2026；适用范围由政策校验"],
      ["income_type","所得类型","select","年度试算使用居民综合所得收入额",[["resident_comprehensive_annual","居民综合所得（年度）"]]],
      ["resident_status","纳税居民身份","select","依据实际身份选择",[["resident","居民个人"],["non_resident","非居民个人（首版不支持试算）"]]],
      ["annual_income","年度综合所得收入额","money","全年收入额；不是仅填单月工资，也不是扣除后的应纳税所得额"],
      ["annual_deductions","年度扣除合计","money","不含 60000 元基本减除费用；无其他扣除填 0"]
    ],other:[]
  };
  const LABELS = Object.fromEntries(Object.values(SCHEMAS).flat().map((row) => [row[0],row[1]]));
  const WAIT_LABELS = {clarify_intent:"意图澄清",confirm_form:"表单确认",intent_clarification:"意图澄清",missing_information:"信息缺失",missing_fields:"信息缺失",upload_documents:"上传单据",document_upload:"上传单据",field_confirmation:"表单确认",form_confirmation:"表单确认",consent:"授权",authorization:"授权"};
  const STATUS_LABELS = {waiting_user:"等待补充",waiting:"等待补充",needs_input:"等待补充",completed:"已完成",success:"已完成",partial:"部分结果",failed:"失败",cancelled:"已取消",unavailable:"不可用",running:"运行中",received:"已接收",routed:"已路由",validating:"校验中",formatting:"整理结果中"};
  const state = {session:null,run:null,intent:null,entryMode:"auto",documents:[],extractions:new Map(),consentRef:null,busy:false,conflict:false,controls:new Map()};
  const canResume = () => Boolean(state.run && ["waiting_user","waiting","needs_input"].includes(state.run.status));
  const canCopyLoan = () => state.intent==="loan" && ["completed","partial","failed","cancelled"].includes(state.run?.status);
  const textValue = (value) => value == null ? "—" : typeof value === "string" ? value : JSON.stringify(value,null,2);
  const fieldName = (name) => String(name).split(".").at(-1);
  const labelFor = (name) => LABELS[fieldName(name)] || ({document_refs:"上传材料",confirmed_fields:"字段确认",consent_ref:"征信授权"}[fieldName(name)]) || String(name);
  function node(tag,text,className) {
    const el=document.createElement(tag);
    if(text !== undefined) el.textContent=String(text);
    if(className) el.className=className;
    return el;
  }
  function notice(message,kind="info") {
    $("notice").textContent=message;
    $("notice").className=`notice ${kind}`;
    $("notice").hidden=!message;
  }
  function persist() {
    try {
      sessionStorage.setItem(STORAGE_KEY,JSON.stringify({session:state.session,run_id:state.run?.run_id || null,entryMode:state.entryMode}));
    } catch { notice("浏览器未允许 sessionStorage；刷新后请手动恢复会话。","warning"); }
  }
  function selectedDocuments() { return state.documents.filter((item) => item.selected).map((item) => item.document_id); }
  function syncControls() {
    document.querySelectorAll("button").forEach((button) => { button.disabled=state.busy; });
    document.querySelectorAll(".intent").forEach((button) => {button.disabled=state.busy || canResume();});
    const active=Boolean(state.session?.token);
    ["upload-button","create-demo","send-query","restore-run"].forEach((id) => {$(id).disabled=state.busy || !active;});
    $("refresh-run").disabled=state.busy || !active || !state.run;
    $("copy-loan").hidden=!canCopyLoan();
    $("copy-loan").disabled=state.busy || !active || !canCopyLoan();
    $("read-parent-run").hidden=!state.run?.parent_run_id;
    $("read-parent-run").disabled=state.busy || !active;
    $("confirm-resume").disabled=state.busy || !active || !canResume() || state.conflict;
    $("grant-consent").disabled=state.busy || !active || !canResume() || Boolean(state.consentRef) || state.conflict;
    $("revoke-consent").disabled=state.busy || !state.consentRef;
    $("send-query").textContent=state.busy ? "请求处理中…" : canResume() ? "发送补答并继续" : "发起业务";
    $("new-business").hidden=!state.run;
    $("attachment-summary").textContent=selectedDocuments().length ? `附加 ${selectedDocuments().length} 份资料` : "未附加资料";
    $("session-id").textContent=state.session?.session_id || "尚未建立";
    $("user-id").textContent=state.session?.user_id || "—";
    $("restore-session-id").value=state.session?.session_id || "";
    $("consent-status").textContent=state.consentRef ? "已明确授权本次模拟评估" : "尚未授权";
    $("revoke-consent").hidden=!state.consentRef;
  }
  class ApiError extends Error { constructor(message,status,detail) {super(message);this.status=status;this.detail=detail;} }
  async function api(path,{method="GET",body,privateRequest=true}={}) {
    const headers={};
    if(privateRequest) {
      if(!state.session?.token) throw new Error("请先建立或恢复会话。");
      headers.Authorization=`Bearer ${state.session.token}`;
    }
    if(body !== undefined && !(body instanceof FormData)) headers["Content-Type"]="application/json";
    // A mutation is sent exactly once. Unknown network outcomes require reading the run.
    const controller=new AbortController();
    const timeout=setTimeout(() => controller.abort(),90000);
    let response;
    try {response=await fetch(path,{method,headers,body:body===undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),signal:controller.signal});}
    catch(error) {throw new Error(error.name==="AbortError" ? "请求超时；服务端可能已处理。请读取当前运行确认结果，不会自动重复请求。" : "网络连接失败；服务端处理状态未知。请刷新运行后再操作，不会自动重试。");}
    finally {clearTimeout(timeout);}
    const raw=await response.text();
    let data;
    try {data=raw ? JSON.parse(raw) : {};} catch {throw new ApiError(`服务端返回非 JSON 响应（HTTP ${response.status}）。`,response.status,raw.slice(0,500));}
    if(!response.ok) {
      const detail=data.detail || data.error || data;
      const error=new ApiError(`HTTP ${response.status}：${textValue(detail)}`,response.status,detail);
      error.requestPath=path;throw error;
    }
    return data;
  }
  async function action(task) {
    if(state.busy) return;
    state.busy=true;notice("");syncControls();
    try {await task();}
    catch(error) {
      if(error.status===409 && error.requestPath?.endsWith("/resume")) {
        state.conflict=true;
        notice("运行版本冲突，本次提交未被接受。请点击“刷新当前运行”，重新核对最新字段，再确认提交。\n"+error.message,"warning");
      } else if(error.status===401 || error.status===403) {
        notice("会话凭据无效、已过期或当前资料不属于此会话。请恢复正确会话。\n"+error.message,"error");
      } else notice(error.message || String(error),"error");
    } finally {state.busy=false;syncControls();}
  }
  function appendChat(role,message) {
    const log=$("chat-log");
    if(log.querySelector(".empty")) log.replaceChildren();
    const item=node("p",undefined,`chat-message ${role}`);
    item.append(node("span",role==="user" ? "你" : "工作流", "chat-role"),node("span",message));
    log.append(item);log.scrollTop=log.scrollHeight;
  }
  function selectEntryMode(mode) {
    if(mode!=="auto" && !Object.hasOwn(TITLES,mode)) return;
    state.entryMode=mode;
    document.querySelectorAll(".intent").forEach((button) => {
      button.classList.toggle("active",button.dataset.intent===mode);
      button.setAttribute("aria-pressed",String(button.dataset.intent===mode));
    });
    $("intent-description").textContent=DESCRIPTIONS[mode];persist();
  }
  function clearRun() {
    state.run=null;state.intent=null;state.consentRef=null;state.conflict=false;state.controls.clear();
    ["form-panel","result-panel","consent-panel"].forEach((id)=>{$(id).hidden=true;});
    $("run-id").textContent="尚未发起";
    $("run-intent").textContent="待识别";
    ["run-version","run-status","wait-reason","run-parent-id"].forEach((id)=>{$(id).textContent="—";});
    $("run-lineage").hidden=true;
    persist();syncControls();
  }
  function clearSession() {
    state.session=null;state.documents=[];state.extractions.clear();clearRun();
    $("restore-token").value="";$("chat-log").replaceChildren(node("p","建立会话后，可发起一条业务流程。","empty"));
    renderDocuments();renderExtractions();persist();syncControls();
  }
  function addDocuments(documents,{selected=true}={}) {
    for(const item of documents) {
      if(!item?.document_id) continue;
      const existing=state.documents.find((doc)=>doc.document_id===item.document_id);
      if(existing) Object.assign(existing,item);
      else state.documents.push({...item,selected});
    }
    renderDocuments();
  }
  function renderDocuments() {
    const list=$("document-list");list.replaceChildren();
    if(!state.documents.length) list.append(node("li","暂无资料","empty"));
    for(const doc of state.documents) {
      const li=node("li");li.append(node("div",doc.filename || doc.original_filename || doc.document_id,"document-name"));
      li.append(node("div",doc.document_id,"help"));
      if(doc.data_mode) li.append(node("span",doc.data_mode,`pill ${doc.data_mode}`));
      const actions=node("div",undefined,"document-actions");
      const label=node("label"),checkbox=node("input");checkbox.type="checkbox";checkbox.checked=doc.selected;
      checkbox.addEventListener("change",()=>{doc.selected=checkbox.checked;syncControls();});label.append(checkbox,node("span","附加到业务"));
      const button=node("button","查看提取");button.type="button";button.addEventListener("click",()=>action(()=>loadExtraction(doc.document_id)));
      actions.append(label,button);li.append(actions);list.append(li);
    }
    syncControls();
  }
  async function loadExtraction(id) {
    const extraction=await api(`/api/v1/documents/${encodeURIComponent(id)}/extract`,{method:"POST"});
    state.extractions.set(id,extraction);renderExtractions();
    return extraction;
  }
  async function restoreRunDocuments(response) {
    const ids=response.document_ids || [];
    // Restore server-owned references after refresh. File content never goes into sessionStorage.
    const documents=await Promise.all(ids.map(id=>api(`/api/v1/documents/${encodeURIComponent(id)}`)));
    const extractions=await Promise.all(ids.map(id=>api(`/api/v1/documents/${encodeURIComponent(id)}/extraction`)));
    state.documents=documents.map(doc=>({...doc,selected:true}));
    state.extractions=new Map(ids.map((id,index)=>[id,extractions[index]]));
    renderDocuments();renderExtractions();
  }
  function renderExtractions() {
    $("extraction-panel").hidden=!state.extractions.size;
    const container=$("extractions");container.replaceChildren();
    for(const [id,extraction] of state.extractions) {
      const block=node("section",undefined,"extraction-document");
      const doc=state.documents.find((item)=>item.document_id===id);
      block.append(node("h3",doc?.filename || id));
      for(const warning of extraction.warnings || []) block.append(node("p",warning,"help"));
      if(!extraction.fields?.length) block.append(node("p","未提取出可预填字段。请查看原文并在表单中手动填写。","help"));
      for(const field of extraction.fields || []) {
        const row=node("div",undefined,"evidence-field");
        row.append(node("strong",labelFor(field.name)),node("div",textValue(field.value),"evidence-value"),node("div",field.quote || "未提供原文片段","evidence-quote"));
        const confidence=typeof field.confidence==="number" ? `${Math.round(field.confidence*100)}%` : "未提供";
        row.append(node("div",`来源：${field.source || "—"} · 文档：${field.document_id || id} · 页码：${field.page_number || "—"} · 置信度：${confidence} · 服务端确认：${field.confirmed ? "是" : "否"} · 坐标：${field.bounding_box ? textValue(field.bounding_box) : "未提供"}`,"evidence-meta"));
        row.append(node("p","确认请在业务表单中操作。原始证据保持不变。","help"));block.append(row);
      }
      const details=node("details"),summary=node("summary","查看解析原文");details.append(summary,node("pre",extraction.text || "无可显示原文","evidence-quote"));block.append(details);container.append(block);
    }
  }
  function unwrapForm(response) {
    const form=response.form || {};
    if(Object.hasOwn(response,"intent")) {
      const intent=Object.hasOwn(TITLES,response.intent) ? response.intent : null;
      const nested={investment:"investment_profile",claim:"claim_form",loan:"credit_form",policy_tax:"tax_profile"}[intent];
      return {values:nested && form[nested] ? form[nested] : form,intent};
    }
    for(const [key,intent] of [["investment_profile","investment"],["claim_form","claim"],["credit_form","loan"],["tax_profile","policy_tax"]]) {
      if(form[key] && typeof form[key]==="object") return {values:form[key],intent};
    }
    if("requested_amount" in form || "monthly_income" in form) return {values:form,intent:"loan"};
    if("policy_id" in form || "incident_type" in form) return {values:form,intent:"claim"};
    if("investable_amount" in form || "instrument_ids" in form) return {values:form,intent:"investment"};
    if("tax_year" in form || "annual_income" in form) return {values:form,intent:"policy_tax"};
    return {values:form,intent:state.intent};
  }
  function inputString(value,type) {
    if(value==null) return "";
    if(type==="money") return value?.amount==null ? "" : String(value.amount);
    if(type==="list") return Array.isArray(value) ? value.length ? value.join(", ") : "无" : String(value);
    return String(value);
  }
  function createField(spec,value,pending,confirmed) {
    const [name,label,type,help,options]=spec;
    const wrapper=node("div",undefined,`field${pending ? " missing" : ""}`);
    const id=`field-${name}`,labelNode=node("label",label);labelNode.htmlFor=id;wrapper.append(labelNode);
    const input=node(type==="select" ? "select" : "input");input.id=id;input.name=name;
    if(type==="select") {
      const empty=node("option","请选择");empty.value="";input.append(empty);
      for(const [value,title] of options) {const option=node("option",title);option.value=value;input.append(option);}
      const existing=inputString(value,type);
      if(existing && !options.some(([candidate])=>candidate===existing)) {const option=node("option",existing);option.value=existing;input.append(option);}
    } else {
      input.type=type==="date" ? "date" : "text";
      if(type==="money" || type==="integer") input.inputMode=type==="money" ? "decimal" : "numeric";
      input.autocomplete="off";
      if(type==="text") input.maxLength=["insurer","insurance_type","incident_type"].includes(name) ? 256 : 128;
    }
    input.value=inputString(value,type);input.setAttribute("aria-describedby",`${id}-help`);
    wrapper.append(input);
    const helpNode=node("div",help,"field-unit");helpNode.id=`${id}-help`;wrapper.append(helpNode);
    if(type==="money" && value?.currency && value.currency!=="CNY") wrapper.append(node("div",`提取币种 ${value.currency} 尚未确认；提交勾选后会确认上述金额为元 / CNY。请先核对。`,"pending"));
    const confirmLabel=node("label",undefined,"confirm-label"),checkbox=node("input");checkbox.type="checkbox";
    // Only persisted, server-confirmed values can display checked. New/OCR values never default to checked.
    checkbox.checked=confirmed;
    confirmLabel.append(checkbox,node("span",type==="money" ? "我已核对金额及元 / CNY 单位" : "我已核对并确认此字段"));wrapper.append(confirmLabel);
    input.addEventListener("input",()=>{checkbox.checked=false;confirmationSummary();});
    input.addEventListener("change",()=>{checkbox.checked=false;confirmationSummary();});
    checkbox.addEventListener("change",confirmationSummary);
    state.controls.set(name,{input,checkbox,type,label});return wrapper;
  }
  function confirmationSummary() {
    const filled=[...state.controls.values()].filter((field)=>field.input.value.trim());
    $("confirmation-summary").textContent=`已填写 ${filled.length} 项，已确认 ${filled.filter((field)=>field.checkbox.checked).length} 项。`;
  }
  function readConfirmedFields() {
    const values={};
    for(const [name,field] of state.controls) {
      const value=field.input.value.trim();
      if(!value) {if(field.checkbox.checked) throw new Error(`“${field.label}”为空，不能确认。`);continue;}
      if(!field.checkbox.checked) throw new Error(`请先核对并勾选“${field.label}”，或清空此可选字段。`);
      if(field.type==="money") {
        if(!/^\d+(?:\.\d{1,2})?$/.test(value)) throw new Error(`“${field.label}”请填写非负金额，最多两位小数，不含千位分隔符。`);
        values[name]={amount:value,currency:"CNY"};
      } else if(field.type==="integer") {
        if(!/^\d+$/.test(value)) throw new Error(`“${field.label}”请填写非负整数。`);
        values[name]=value;
      } else if(field.type==="list") {
        values[name]=value==="无" ? [] : value.split(/[,，\n]+/).map((item)=>item.trim()).filter(Boolean);
        if(name==="instrument_ids" && (!values[name].length || values[name].some((item)=>!/^\d{6}$/.test(item)))) throw new Error("基金 / ETF 代码请填写六位数字，并用逗号分隔。");
      } else values[name]=value;
    }
    return values;
  }
  function renderConflicts(conflicts) {
    const container=$("conflicts");container.replaceChildren();
    const entries=Array.isArray(conflicts) ? conflicts.map((entry,i)=>[String(i+1),entry]) : Object.entries(conflicts || {});
    container.hidden=!entries.length;
    if(!entries.length) return;
    container.append(node("strong","存在材料 / 用户输入冲突，请核对后显式填写正确值："));
    for(const [key,candidates] of entries) {
      container.append(node("h3",labelFor(key)));
      for(const candidate of Array.isArray(candidates) ? candidates : [candidates]) {
        container.append(node("p",typeof candidate==="object" ? `值：${textValue(candidate.value)}\n来源：${candidate.source || "—"}；文档：${candidate.document_id || "—"}；原文：${candidate.quote || "—"}` : textValue(candidate),"evidence-quote"));
      }
    }
  }
  function renderForm(response) {
    const {values,intent}=unwrapForm(response);state.intent=intent;
    const schema=SCHEMAS[intent] || [];
    const pending=(response.pending_fields || []).map(fieldName);
    const panel=$("form-panel");panel.hidden=Boolean(response.result);
    $("form-title").textContent=intent ? `${TITLES[intent]} · 字段确认` : "澄清业务意图";
    $("run-question").textContent=response.question || "请补答或核对表单字段后继续。";
    $("pending-fields").textContent=pending.length ? `待补充 / 确认：${response.pending_fields.map(labelFor).join("、")}` : "请核对所有已填写字段。";
    $("pending-fields").hidden=!pending.length && !schema.length;
    $("form-instructions").hidden=!schema.length;
    renderConflicts(response.conflicts);
    const fields=$("business-fields");fields.replaceChildren();state.controls.clear();
    for(const spec of schema) fields.append(createField(spec,values[spec[0]],pending.includes(spec[0]),(values.confirmed_fields || []).includes(spec[0])));
    $("consent-panel").hidden=intent!=="loan";
    state.consentRef=values.consent_ref || null;
    $("raw-response").textContent=JSON.stringify(response,null,2);
    $("business-form").hidden=!schema.length;
    confirmationSummary();
  }
  // Shift a decimal string two places for percentage display; no float conversion or recalculation.
  function percentage(value) {
    if(value==null) return "无法计算";
    const match=String(value).match(/^(-?)(\d+)(?:\.(\d+))?$/);
    if(!match) return String(value);
    const fraction=(match[3] || "").padEnd(2,"0");
    const whole=(match[2]+fraction.slice(0,2)).replace(/^0+(?=\d)/,"");
    const decimals=fraction.slice(2).replace(/0+$/,"");
    return `${match[1]}${whole}${decimals ? "."+decimals : ""}%`;
  }
  function moneyText(value,currency="CNY") {
    return value==null ? "—" : `${String(value)} ${currency==="CNY" ? "元" : currency}`;
  }
  function metricCard(label,value) {
    const card=node("div",undefined,"summary-card");
    card.append(node("span",label,"summary-label"),node("strong",value,"summary-value"));return card;
  }
  function summaryTable(headers,rows) {
    const table=node("table",undefined,"facts-table summary-table"),head=node("thead"),headRow=node("tr"),body=node("tbody");
    for(const title of headers) headRow.append(node("th",title));head.append(headRow);
    for(const values of rows) {const row=node("tr");for(const value of values)row.append(node("td",value));body.append(row);}
    table.append(head,body);return table;
  }
  function renderFacts(facts) {
    const target=$("result-facts");target.replaceChildren();
    const calculations=facts?.calculations || {};
    const structured=facts?.structured_facts || {};
    let shown=false;
    if(state.intent==="loan") {
      const assessment=calculations.result ? calculations : structured.assessment || {};
      if(Object.hasOwn(assessment,"result")) {
        const category={demo_pass:"通过模拟规则筛查",manual_review:"需人工复核（模拟规则）"}[assessment.result] || assessment.result;
        const grid=node("div",undefined,"summary-grid");
        grid.append(metricCard("模拟分类",category),metricCard("仅本金月摊",moneyText(assessment.principal_only_monthly_payment,assessment.table_row?.currency || "CNY")),metricCard("偿债比例",percentage(assessment.debt_service_ratio)));
        target.append(grid,node("p","仅本金月摊不含利息或费用；偿债比例包含原有月偿债支出。分类仅用于演示，不代表审批。","help"));shown=true;
      }
    } else if(state.intent==="policy_tax" && Object.hasOwn(calculations,"annual_tax")) {
      const grid=node("div",undefined,"summary-grid");
      grid.append(metricCard("年度税额",moneyText(calculations.annual_tax,calculations.currency || "CNY")),metricCard("应纳税所得额",moneyText(calculations.taxable_income,calculations.currency || "CNY")),metricCard("适用税率",percentage(calculations.rate)));
      target.append(grid,node("p","年度税额未扣减已预缴税额，不能直接作为补税或退税金额。","help"));shown=true;
    } else if(state.intent==="investment") {
      if(Array.isArray(calculations.allocation) && calculations.allocation.length) {
        target.append(summaryTable(["配置类别","金额","比例"],calculations.allocation.map(item=>[item.category,moneyText(item.amount,item.currency),percentage(item.weight)])));shown=true;
      }
      const metrics=calculations.historical_metrics || structured.historical_metrics || {};
      if(Object.keys(metrics).length) {
        target.append(node("h3","基金历史净值指标"));
        target.append(summaryTable(["基金代码 / 区间","净值变化","观测最大回撤"],Object.entries(metrics).map(([code,item])=>[`${code}\n${item.first_date} 至 ${item.last_date}\n${item.observations} 个观测值`,percentage(item.nav_change),percentage(item.observed_max_drawdown)])));
        target.append(node("p",calculations.metric_basis || "仅根据区间内单位净值计算；不是总收益率或收益预测。","help"));shown=true;
      }
    } else if(state.intent==="claim" && structured.clause_check) {
      const check=structured.clause_check,grid=node("div",undefined,"summary-grid");
      const verdict={potentially_covered:"可能符合条款，需机构审核",outside_coverage_dates:"出险日期不在保障期内",excluded_by_provided_clause:"所提供条款包含除外情形",requires_manual_clause_review:"条款需人工审核"}[check.preliminary_result] || check.preliminary_result || "未提供";
      grid.append(metricCard("条款初筛",verdict),metricCard("出险日期",check.incident_date || "—"),metricCard("是否在保单有效期内",check.within_effective_dates===true ? "是" : check.within_effective_dates===false ? "否" : "未提供"));target.append(grid);shown=true;
    }
    if(!shown) target.append(node("p",Object.keys(facts || {}).length ? "本次未提供可展示的摘要计算字段，可展开原始事实核验。" : "本次无结构化计算结果。","help"));
    if(Object.keys(facts || {}).length) {
      const details=node("details",undefined,"result-raw-facts");
      details.append(node("summary","展开原始结构化事实（核验）"),node("pre",JSON.stringify(facts,null,2)));
      target.append(details);
    }
  }
  function safeURL(value) {
    try {const url=new URL(value);return ["https:","http:"].includes(url.protocol) ? url.href : null;} catch {return null;}
  }
  function renderResult(result) {
    $("result-panel").hidden=!result;if(!result)return;
    const mode=result.data_mode;
    $("data-mode").textContent=({real:"real · 真实来源",redacted:"redacted · 脱敏来源",mock:"mock · 模拟数据"}[mode]) || `${mode || "未标记"}`;
    $("data-mode").className=`pill ${["real","redacted","mock"].includes(mode) ? mode : "neutral"}`;
    $("conclusion").textContent=result.conclusion || "无结论";renderFacts(result.facts);
    const citations=$("citations");citations.replaceChildren();
    if(!result.citations?.length)citations.append(node("p","本次没有可核验引用，请结合结果限制评估。","help"));
    for(const ref of result.citations || []) {
      const row=node("article",undefined,"citation"),title=node("div",undefined,"citation-title");
      const uri=ref.source_uri ? safeURL(ref.source_uri) : null;
      if(uri) {const link=node("a",ref.source_title || ref.source_id);link.href=uri;link.target="_blank";link.rel="noopener noreferrer";title.append(link);} else title.textContent=ref.source_title || ref.source_id || "来源";
      row.append(title,node("p",ref.quote || "未提供原文片段"));
      row.append(node("div",`来源 ID：${ref.source_id || "—"} · 页码：${ref.page_number || "—"} · 文档：${ref.document_id || "—"}\n发布：${ref.published_at || "—"} · 生效：${ref.effective_from || "—"} 至 ${ref.effective_to || "—"}\n检索：${ref.retrieved_at || "—"}`,"evidence-meta"));citations.append(row);
    }
    $("limitations").replaceChildren(...(result.limitations || []).map((value)=>node("li",value)));
    $("next-action").textContent=result.next_action ? `下一步：${result.next_action}` : "";
  }
  function applyRun(response,{chat=true}={}) {
    if(!response?.run_id) throw new Error("服务端未返回运行 ID，请检查接口契约。");
    state.run=response;state.conflict=false;
    $("run-id").textContent=response.run_id;$("restore-run-id").value=response.run_id;
    $("run-version").textContent=String(response.state_version);
    $("run-status").textContent=STATUS_LABELS[response.status] || response.status;
    $("wait-reason").textContent=WAIT_LABELS[response.wait_reason] || response.wait_reason || "—";
    $("run-parent-id").textContent=response.parent_run_id || "—";
    $("run-lineage").hidden=!response.parent_run_id;
    renderForm(response);renderResult(response.result);
    $("run-intent").textContent=TITLES[state.intent] || "待澄清";
    if(chat && (response.question || response.result?.conclusion)) appendChat("assistant",response.question || response.result.conclusion);
    persist();syncControls();
  }
  async function refreshRun(id=state.run?.run_id) {
    if(!id) throw new Error("请输入运行 ID。");
    const response=await api(`/api/v1/runs/${encodeURIComponent(id)}`);
    if(state.run?.run_id!==id) state.consentRef=null;
    applyRun(response,{chat:false});await restoreRunDocuments(response);
    notice("已读取最新运行版本和资料；请核对后继续。");
  }
  async function resume({message,confirmedFields={}}) {
    if(!state.run)throw new Error("请先发起业务。");
    if(state.conflict)throw new Error("请先刷新运行版本。");
    const response=await api(`/api/v1/runs/${encodeURIComponent(state.run.run_id)}/resume`,{method:"POST",body:{request_id:crypto.randomUUID(),state_version:state.run.state_version,message,confirmed_fields:confirmedFields,attachment_ids:selectedDocuments(),consent_ref:state.consentRef}});
    applyRun(response);
  }
  async function checkHealth() {
    try {
      const health=await api("/health",{privateRequest:false});
      $("health-dot").className="status-dot ready";
      const mode=health.model_mode || health.llm_mode || (health.model_configured===false ? "rule_fallback" : health.model_configured===true ? "deepseek_configured" : null);
      const modeLabel=mode && /rule|fallback|unconfigured/i.test(mode) ? "规则模式（DeepSeek 未配置或不可用）" : mode==="deepseek_configured" ? "DeepSeek 已配置" : mode;
      $("health-label").textContent=`服务可响应 · ${modeLabel || health.stage || health.status}`;
      const target=$("service-details");target.replaceChildren();
      for(const [key,value] of Object.entries(health)) target.append(node("div",`${key}：${textValue(value)}`));
      if(mode && /rule|fallback|unconfigured/i.test(mode)) target.append(node("p","DeepSeek 未配置或不可用，当前由规则处理。"));
    } catch(error) {
      $("health-dot").className="status-dot error";$("health-label").textContent="服务连接失败";
      $("service-details").textContent=error.message;
    }
  }
  $("create-session").addEventListener("click",()=>action(async()=>{
    const session=await api("/api/v1/sessions",{method:"POST",privateRequest:false});
    if(!session.session_id || !session.token)throw new Error("会话接口未返回完整凭据。");
    clearSession();state.session=session;persist();syncControls();notice("新会话已建立。请上传材料或描述问题。");
  }));
  $("restore-session").addEventListener("click",()=>action(async()=>{
    const sessionId=$("restore-session-id").value.trim(),token=$("restore-token").value.trim();
    if(!sessionId || !token) throw new Error("恢复会话需要会话 ID 和 Token。");
    clearSession();state.session={session_id:sessionId,token};persist();syncControls();notice("已使用输入的凭据；私有请求会由服务端校验身份。");
  }));
  $("forget-session").addEventListener("click",()=>{if(state.busy)return;clearSession();notice("已清除此标签页的会话凭据。服务端资料未删除。");});
  $("upload-form").addEventListener("submit",(event)=>{event.preventDefault();action(async()=>{
    const files=Array.from($("files").files);if(!files.length)throw new Error("请选择要上传的文件。");
    let success=0;const issues=[];
    for(const file of files) {
      const form=new FormData();form.append("file",file);
      try {
        const metadata=await api("/api/v1/documents",{method:"POST",body:form});
        addDocuments([{...metadata,filename:metadata.filename || file.name}]);success++;
        try {await loadExtraction(metadata.document_id);} catch(error) {issues.push(`${file.name} 已上传，但提取失败：${error.message}`);}
      } catch(error) {issues.push(`${file.name} 上传失败：${error.message}`);}
    }
    $("files").value="";notice(`已上传 ${success} / ${files.length} 份材料。${issues.length ? "\n"+issues.join("\n") : "请核对提取证据，再在表单中确认。"}`,issues.length ? "warning" : "info");
  });});
  $("create-demo").addEventListener("click",()=>action(async()=>{
    const data=await api("/api/v1/demo",{method:"POST"});
    addDocuments(data.documents || [],{selected:false});
    $("demo-result").textContent=`${data.note || "已创建合成演示样本"}\n${textValue(data.policies || [])}`;
    notice("演示样本已创建。请显式勾选要附加的资料，查看提取，再发起业务；未自动提交或授权。");
  }));
  document.querySelectorAll(".intent").forEach((button)=>button.addEventListener("click",()=>{
    if(canResume()) {notice("当前运行正在等待补充。要切换业务，请点击“结束当前编辑，发起另一条业务”。","warning");return;}
    selectEntryMode(button.dataset.intent);
  }));
  $("query-form").addEventListener("submit",(event)=>{event.preventDefault();action(async()=>{
    const message=$("message").value.trim();if(!message)throw new Error("请填写业务说明或补答。");
    appendChat("user",message);
    if(canResume()) {
      // Form-bearing waits must use the explicit confirmation action. A chat reply cannot bypass it.
      await resume({message});
    } else {
      state.consentRef=null;
      const body={session_id:state.session.session_id,request_id:crypto.randomUUID(),message,attachment_ids:selectedDocuments()};
      if(state.entryMode!=="auto") body.intent_hint=state.entryMode;
      const response=await api("/api/v1/queries",{method:"POST",body});
      applyRun(response);
    }
    $("message").value="";
  });});
  $("business-form").addEventListener("submit",(event)=>{event.preventDefault();action(async()=>{
    const fields=readConfirmedFields();
    if(!Object.keys(fields).length)throw new Error("请至少填写并确认一个业务字段，或在聊天区补答。");
    const message=$("message").value.trim() || "已逐项核对并确认提交的表单字段，请按当前流程继续。";
    await resume({message,confirmedFields:fields});$("message").value="";
  });});
  $("grant-consent").addEventListener("click",()=>action(async()=>{
    if(!state.run || state.intent!=="loan")throw new Error("仅贷款运行可授予此授权。");
    const data=await api(`/api/v1/runs/${encodeURIComponent(state.run.run_id)}/consent`,{method:"POST",body:{granted:true}});
    if(!data.granted || !data.consent_ref)throw new Error("服务端未确认授权。");
    state.consentRef=data.consent_ref;notice("已明确授权本次模拟评估。请点击“提交已确认字段并继续”执行评估；授权按钮本身不会执行业务。");
  }));
  $("revoke-consent").addEventListener("click",()=>action(async()=>{
    const ref=state.consentRef;if(!ref)throw new Error("当前无有效授权。");
    await api(`/api/v1/runs/${encodeURIComponent(state.run.run_id)}/consent`,{method:"POST",body:{granted:false}});state.consentRef=null;
    notice("已撤销授权。已有结果保留为历史记录；后续模拟评估需要重新授权。");
  }));
  $("refresh-run").addEventListener("click",()=>action(()=>refreshRun()));
  $("copy-loan").addEventListener("click",()=>action(async()=>{
    if(!canCopyLoan())throw new Error("仅已结束的贷款运行可复制为新申请。");
    const response=await api(`/api/v1/runs/${encodeURIComponent(state.run.run_id)}/copy`,{method:"POST",body:{request_id:crypto.randomUUID()}});
    state.consentRef=null;state.controls.clear();
    selectEntryMode("loan");applyRun(response);await restoreRunDocuments(response);
    notice("已复制为新申请，原运行和结果保留为历史记录。请重新逐项核对字段，并另行授权本次模拟评估。");
  }));
  $("read-parent-run").addEventListener("click",()=>action(()=>refreshRun(state.run?.parent_run_id)));
  $("restore-run").addEventListener("click",()=>action(()=>refreshRun($("restore-run-id").value.trim())));
  $("new-business").addEventListener("click",()=>{if(state.busy)return;clearRun();notice("已退出当前编辑；可发起新业务。旧运行可凭运行 ID 恢复。");});
  $("health-refresh").addEventListener("click",()=>action(checkHealth));
  try {
    const saved=JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
    if(saved?.session?.session_id && saved?.session?.token) {
      state.session=saved.session;state.entryMode=Object.hasOwn(TITLES,saved.entryMode) ? saved.entryMode : "auto";
      if(saved.run_id) $("restore-run-id").value=saved.run_id;
    }
  } catch {notice("会话缓存无法读取，请重新建立会话。","warning");}
  selectEntryMode(state.entryMode);syncControls();checkHealth();
  // Refresh restores an existing run by read only. It never resends a financial query.
  if(state.session && $("restore-run-id").value) action(async()=>{
    await refreshRun($("restore-run-id").value);
    notice("已恢复会话和运行。此前上传的资料由服务端保留；浏览器未缓存文件原文。请核对字段后继续。");
  });
})();
