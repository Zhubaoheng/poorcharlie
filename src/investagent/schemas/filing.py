"""Filing Structuring Skill output schema.

Structured extraction from financial filings across A-shares, HK, and US-listed
Chinese ADRs. Preserves raw text for critical sections (accounting policies,
footnotes, risk factors) while normalizing financial tables into strong types.
"""

from __future__ import annotations

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


# ---------------------------------------------------------------------------
# Filing metadata
# ---------------------------------------------------------------------------

class FilingMeta(BaseModel, frozen=True):
    market: str              # "A_SHARE" | "HK" | "US_ADR"
    accounting_standard: str  # "CAS" | "IFRS" | "US_GAAP" | "HKFRS"
    fiscal_years_covered: list[str]
    filing_types: list[str]  # A股: ["年报","半年报"] / 港股: ["年报","中期报告"] / 中概: ["20-F","6-K"]
    currency: str            # "CNY" | "HKD" | "USD" — 财报报告货币
    reporting_language: str  # "zh-CN" | "zh-HK" | "en"
    market_currency: str | None = None  # 股价货币 (可能与 currency 不同, e.g., HKD vs CNY)


# ---------------------------------------------------------------------------
# Financial statement rows — one row per fiscal year/period
# ---------------------------------------------------------------------------

class IncomeStatementRow(BaseModel, frozen=True):
    fiscal_year: str
    fiscal_period: str  # "FY" | "H1" | "Q1" | "Q2" | "Q3" | "Q4"
    revenue: float | None = None
    cost_of_revenue: float | None = None
    gross_profit: float | None = None
    rd_expense: float | None = None
    sga_expense: float | None = None
    depreciation_amortization: float | None = None
    operating_income: float | None = None
    interest_expense: float | None = None
    tax_provision: float | None = None
    net_income: float | None = None
    net_income_to_parent: float | None = None  # 归母净利润
    eps_basic: float | None = None
    eps_diluted: float | None = None
    shares_basic: float | None = None
    shares_diluted: float | None = None


class BalanceSheetRow(BaseModel, frozen=True):
    fiscal_year: str
    fiscal_period: str = "FY"  # "FY" | "H1" | "Q1" etc.
    cash_and_equivalents: float | None = None
    short_term_investments: float | None = None
    accounts_receivable: float | None = None
    inventory: float | None = None
    total_current_assets: float | None = None
    ppe_net: float | None = None
    goodwill: float | None = None
    intangible_assets: float | None = None
    total_assets: float | None = None
    accounts_payable: float | None = None
    short_term_debt: float | None = None
    total_current_liabilities: float | None = None
    long_term_debt: float | None = None
    total_liabilities: float | None = None
    shareholders_equity: float | None = None
    minority_interest: float | None = None


class CashFlowRow(BaseModel, frozen=True):
    fiscal_year: str
    fiscal_period: str = "FY"  # "FY" | "H1" | "Q1" etc.
    operating_cash_flow: float | None = None
    capex: float | None = None
    free_cash_flow: float | None = None
    dividends_paid: float | None = None
    buyback_amount: float | None = None
    debt_issued: float | None = None
    debt_repaid: float | None = None
    acquisitions: float | None = None


class SegmentRow(BaseModel, frozen=True):
    fiscal_year: str
    segment_name: str
    revenue: float | None = None
    operating_income: float | None = None
    assets: float | None = None
    extra: dict[str, float | str | None] | None = None  # 公司特有指标


# ---------------------------------------------------------------------------
# Accounting policies — raw text preserved per year per category
# ---------------------------------------------------------------------------

class AccountingPolicyEntry(BaseModel, frozen=True):
    category: str  # "revenue_recognition" | "depreciation" | "inventory" | ...
    fiscal_year: str
    method: str
    raw_text: str
    changed_from_prior: bool
    change_description: str | None = None


# ---------------------------------------------------------------------------
# Debt structure
# ---------------------------------------------------------------------------

class DebtInstrument(BaseModel, frozen=True):
    instrument_type: str  # "bank_loan" | "bond" | "convertible" | "credit_facility"
    principal: float | None = None
    interest_rate: float | str | None = None  # float or descriptive string like "2.4%-6.2%"
    maturity_date: str | None = None
    covenants: list[str] = []
    ranking: str | None = None  # "senior" | "subordinated" | "secured"


class CovenantStatus(BaseModel, frozen=True):
    covenant_type: str
    threshold: str
    current_value: str
    headroom: str
    in_compliance: bool


# ---------------------------------------------------------------------------
# Special items / 非经常性损益
# ---------------------------------------------------------------------------

class SpecialItem(BaseModel, frozen=True):
    fiscal_year: str
    description: str
    pre_tax_amount: float
    classification: str  # "restructuring" | "litigation" | "impairment" | "asset_disposal" | "government_subsidy"
    recurrence: str  # "first_time" | "recurring" | "multi_year"


# ---------------------------------------------------------------------------
# Concentration data
# ---------------------------------------------------------------------------

class ConcentrationData(BaseModel, frozen=True):
    top_customer_pct: float | None = None
    top5_customers_pct: float | None = None
    customer_losses: list[str]
    major_supplier_dependencies: list[str]
    top5_suppliers_pct: float | None = None
    geographic_revenue_split: dict[str, float]


# ---------------------------------------------------------------------------
# Capital allocation history
# ---------------------------------------------------------------------------

class BuybackRecord(BaseModel, frozen=True):
    fiscal_year: str
    amount_spent: float | None = None
    shares_retired: float | None = None
    avg_price_paid: float | None = None


class AcquisitionRecord(BaseModel, frozen=True):
    fiscal_year: str
    target: str | None = None
    purchase_price: float | None = None
    goodwill_recognized: float | None = None
    impairment_charges: float | None = None


# ---------------------------------------------------------------------------
# Footnote extracts — raw text preserved
# ---------------------------------------------------------------------------

class FootnoteExtract(BaseModel, frozen=True):
    topic: str  # "debt" | "leases" | "litigation" | "related_party" | "contingencies" | "pledged_assets"
    fiscal_year: str
    raw_text: str
    structured_summary: str


# ---------------------------------------------------------------------------
# Risk factors
# ---------------------------------------------------------------------------

class RiskFactorEntry(BaseModel, frozen=True):
    category: str  # "regulatory" | "competition" | "technology" | "legal" | "policy"
    description: str
    raw_text: str
    materiality: str  # "high" | "medium" | "low"


# ---------------------------------------------------------------------------
# Top-level filing output
# ---------------------------------------------------------------------------

class FilingOutput(BaseAgentOutput):
    # 元数据
    filing_meta: FilingMeta

    # 强类型财务表格
    income_statement: list[IncomeStatementRow]
    balance_sheet: list[BalanceSheetRow]
    cash_flow: list[CashFlowRow]
    segments: list[SegmentRow]

    # 会计政策（保留原文）
    accounting_policies: list[AccountingPolicyEntry]

    # 债务结构
    debt_schedule: list[DebtInstrument]
    covenant_status: list[CovenantStatus]

    # 非经常性损益
    special_items: list[SpecialItem]

    # 集中度
    concentration: ConcentrationData | None = None

    # 资本配置
    buyback_history: list[BuybackRecord]
    acquisition_history: list[AcquisitionRecord]
    dividend_per_share_history: list[dict[str, float | str | None]]

    # 关键脚注原文
    footnote_extracts: list[FootnoteExtract]

    # 风险因素
    risk_factors: list[RiskFactorEntry]
