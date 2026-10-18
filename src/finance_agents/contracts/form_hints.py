"""Public form instructions; examples are syntax samples, never user defaults."""

LABELS = {
    "jurisdiction": "地域",
    "tax_year": "税年",
    "income_type": "所得类型",
    "resident_status": "居民身份",
    "annual_income": "年度综合所得收入额",
    "annual_deductions": "年度扣除合计",
    "income": "收入",
    "deductions": "扣除",
    "investable_amount": "可投资金额",
    "horizon_months": "投资期限（月）",
    "risk_tolerance": "风险承受程度",
    "liquidity_need": "流动资金需求",
    "liabilities": "负债合计",
    "existing_assets": "现有资产",
    "instrument_ids": "基金代码",
    "policy_id": "保单号",
    "policy_version": "条款版本",
    "insurer": "保险机构",
    "insurance_type": "险种",
    "premium": "保费",
    "insured_amount": "保险金额",
    "incident_type": "出险类型",
    "incident_date": "出险日期",
    "intake_mode": "资料来源",
    "subject_ref": "申请人参考编号",
    "requested_amount": "申请贷款金额",
    "loan_term_months": "贷款期限（月）",
    "monthly_income": "月收入",
    "monthly_debt_payment": "月偿债支出",
    "employment_status": "就业状态",
    "employment_months": "就业时长（月）",
    "assets_total": "资产合计",
    "liabilities_total": "负债合计",
}
MONEY_FIELDS = {
    "annual_income",
    "annual_deductions",
    "income",
    "deductions",
    "investable_amount",
    "liquidity_need",
    "liabilities",
    "premium",
    "insured_amount",
    "requested_amount",
    "monthly_income",
    "monthly_debt_payment",
    "assets_total",
    "liabilities_total",
}
CHOICES = {
    "intake_mode": ["documents", "self_reported"],
    "income_type": ["resident_comprehensive_annual"],
    "resident_status": ["resident"],
    "risk_tolerance": ["low", "medium", "high"],
}
EXAMPLES = {
    "jurisdiction": "中国大陆",
    "tax_year": 2026,
    "horizon_months": 24,
    "loan_term_months": 12,
    "employment_months": 0,
    "existing_assets": [],
    "instrument_ids": ["510300"],
    "incident_date": "2026-10-01",
    "policy_id": "DEMO-POLICY-001",
    "policy_version": "demo-v1",
    "subject_ref": "synthetic-applicant",
    "employment_status": "employed",
    "incident_type": "hospitalization",
}
HELP = {
    "annual_income": "人民币全年综合所得收入额；不是单月工资或扣除后所得。",
    "annual_deductions": "60000元基本减除费用之外的全年扣除合计；没有须明确填0。",
    "income_type": "首版支持居民年度综合所得；不支持年终奖单独计税。",
    "instrument_ids": "六位基金代码数组；示例仅展示格式，不构成推荐；须有已入库净值。",
    "existing_assets": "字符串数组；无资产请明确提交空数组[]。",
    "intake_mode": "自填试算未经材料核验；须显式选择、确认字段，并另行授权模拟评估。",
    "policy_id": "上传材料后须查询当前用户历史保单；演示样本需先导入，示例不是你的真实保单。",
}


def form_hints(model):
    hints = {}
    for name in model.model_fields:
        if name not in LABELS:
            continue
        kind = "money" if name in MONEY_FIELDS else "text"
        example = EXAMPLES.get(name, "按实际填写")
        help_text = HELP.get(name, "请核对后确认；示例不会自动填入。")
        if kind == "money":
            example = {"amount": "120000.00", "currency": "CNY"}
            help_text += " 金额以元为单位填写，最多两位小数。"
        elif name in CHOICES:
            kind, example = "select", CHOICES[name][0]
        elif isinstance(example, list):
            kind = "list"
        elif isinstance(example, int):
            kind = "integer"
        elif name == "incident_date":
            kind = "date"
        hints[name] = {"label": LABELS[name], "type": kind, "help": help_text, "example": example}
        if name in CHOICES:
            hints[name]["choices"] = CHOICES[name]
    return hints
