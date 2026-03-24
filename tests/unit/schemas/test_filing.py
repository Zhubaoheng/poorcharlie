"""Tests for investagent.schemas.filing."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from investagent.schemas.common import AgentMeta
from investagent.schemas.filing import (
    AccountingPolicyEntry,
    AcquisitionRecord,
    BalanceSheetRow,
    BuybackRecord,
    CashFlowRow,
    ConcentrationData,
    CovenantStatus,
    DebtInstrument,
    FilingMeta,
    FilingOutput,
    FootnoteExtract,
    IncomeStatementRow,
    RiskFactorEntry,
    SegmentRow,
    SpecialItem,
)


def _meta() -> AgentMeta:
    return AgentMeta(
        agent_name="filing",
        timestamp=datetime.now(tz=timezone.utc),
        model_used="test",
        token_usage=0,
    )


def _filing_meta() -> FilingMeta:
    return FilingMeta(
        market="A_SHARE",
        accounting_standard="CAS",
        fiscal_years_covered=["2020", "2021", "2022", "2023", "2024"],
        filing_types=["年报"],
        currency="CNY",
        reporting_language="zh-CN",
    )


class TestFilingMeta:
    def test_a_share_meta(self):
        m = _filing_meta()
        assert m.market == "A_SHARE"
        assert m.currency == "CNY"
        assert len(m.fiscal_years_covered) == 5

    def test_hk_meta(self):
        m = FilingMeta(
            market="HK",
            accounting_standard="IFRS",
            fiscal_years_covered=["2023", "2024"],
            filing_types=["年报", "中期报告"],
            currency="HKD",
            reporting_language="zh-HK",
        )
        assert m.accounting_standard == "IFRS"

    def test_us_adr_meta(self):
        m = FilingMeta(
            market="US_ADR",
            accounting_standard="US_GAAP",
            fiscal_years_covered=["2024"],
            filing_types=["20-F"],
            currency="USD",
            reporting_language="en",
        )
        assert m.market == "US_ADR"


class TestFinancialRows:
    def test_income_statement_all_none(self):
        row = IncomeStatementRow(fiscal_year="2024", fiscal_period="FY")
        assert row.revenue is None
        assert row.net_income_to_parent is None

    def test_income_statement_with_data(self):
        row = IncomeStatementRow(
            fiscal_year="2024",
            fiscal_period="FY",
            revenue=100_000_000,
            net_income=20_000_000,
            net_income_to_parent=18_000_000,
            eps_basic=1.5,
        )
        assert row.net_income_to_parent == 18_000_000

    def test_balance_sheet_minimal(self):
        row = BalanceSheetRow(fiscal_year="2024")
        assert row.goodwill is None

    def test_cash_flow_row(self):
        row = CashFlowRow(
            fiscal_year="2024",
            operating_cash_flow=50_000_000,
            capex=-10_000_000,
            free_cash_flow=40_000_000,
        )
        assert row.free_cash_flow == 40_000_000

    def test_segment_row_with_extra(self):
        row = SegmentRow(
            fiscal_year="2024",
            segment_name="消费电子",
            revenue=80_000_000,
            extra={"gross_margin_pct": 35.2},
        )
        assert row.extra["gross_margin_pct"] == 35.2

    def test_segment_row_without_extra(self):
        row = SegmentRow(
            fiscal_year="2024",
            segment_name="云服务",
            revenue=20_000_000,
        )
        assert row.extra is None


class TestAccountingPolicy:
    def test_unchanged_policy(self):
        entry = AccountingPolicyEntry(
            category="revenue_recognition",
            fiscal_year="2024",
            method="完工百分比法",
            raw_text="本公司采用完工百分比法确认收入...",
            changed_from_prior=False,
        )
        assert entry.change_description is None

    def test_changed_policy(self):
        entry = AccountingPolicyEntry(
            category="depreciation",
            fiscal_year="2024",
            method="年限平均法，5年→8年",
            raw_text="本公司自2024年起将固定资产折旧年限由5年调整为8年...",
            changed_from_prior=True,
            change_description="折旧年限由5年延长至8年，导致年折旧费用减少约2000万元",
        )
        assert entry.changed_from_prior is True


class TestDebtStructure:
    def test_debt_instrument(self):
        d = DebtInstrument(
            instrument_type="bond",
            principal=500_000_000,
            interest_rate=3.85,
            maturity_date="2027-06-15",
            covenants=["资产负债率不超过70%"],
            ranking="senior",
        )
        assert d.principal == 500_000_000

    def test_covenant_status(self):
        c = CovenantStatus(
            covenant_type="资产负债率",
            threshold="≤70%",
            current_value="58%",
            headroom="12%",
            in_compliance=True,
        )
        assert c.in_compliance is True


class TestSpecialItems:
    def test_government_subsidy(self):
        item = SpecialItem(
            fiscal_year="2024",
            description="政府产业扶持补贴",
            pre_tax_amount=15_000_000,
            classification="government_subsidy",
            recurrence="recurring",
        )
        assert item.classification == "government_subsidy"

    def test_impairment(self):
        item = SpecialItem(
            fiscal_year="2024",
            description="商誉减值",
            pre_tax_amount=-200_000_000,
            classification="impairment",
            recurrence="first_time",
        )
        assert item.pre_tax_amount < 0


class TestConcentration:
    def test_a_share_concentration(self):
        c = ConcentrationData(
            top_customer_pct=25.3,
            top5_customers_pct=48.7,
            customer_losses=["2023年失去客户A"],
            major_supplier_dependencies=["芯片供应商X"],
            top5_suppliers_pct=62.1,
            geographic_revenue_split={"华东": 45.0, "华南": 30.0, "海外": 25.0},
        )
        assert c.top5_suppliers_pct == 62.1


class TestCapitalAllocation:
    def test_buyback_record(self):
        r = BuybackRecord(
            fiscal_year="2024",
            amount_spent=100_000_000,
            shares_retired=5_000_000,
            avg_price_paid=20.0,
        )
        assert r.avg_price_paid == 20.0

    def test_acquisition_record(self):
        r = AcquisitionRecord(
            fiscal_year="2023",
            target="子公司B",
            purchase_price=300_000_000,
            goodwill_recognized=120_000_000,
        )
        assert r.impairment_charges is None


class TestFootnoteAndRisk:
    def test_footnote_extract(self):
        f = FootnoteExtract(
            topic="related_party",
            fiscal_year="2024",
            raw_text="本公司与控股股东之间的关联交易如下：销售商品12亿元...",
            structured_summary="控股股东关联销售12亿元",
        )
        assert "关联交易" in f.raw_text

    def test_risk_factor(self):
        r = RiskFactorEntry(
            category="regulatory",
            description="行业监管政策变化风险",
            raw_text="若未来监管部门出台更严格的行业准入政策...",
            materiality="high",
        )
        assert r.materiality == "high"


class TestFilingOutputFull:
    def test_minimal_filing_output(self):
        output = FilingOutput(
            meta=_meta(),
            filing_meta=_filing_meta(),
            income_statement=[],
            balance_sheet=[],
            cash_flow=[],
            segments=[],
            accounting_policies=[],
            debt_schedule=[],
            covenant_status=[],
            special_items=[],
            buyback_history=[],
            acquisition_history=[],
            dividend_per_share_history=[],
            footnote_extracts=[],
            risk_factors=[],
        )
        assert output.filing_meta.market == "A_SHARE"
        assert output.concentration is None

    def test_filing_output_with_data(self):
        output = FilingOutput(
            meta=_meta(),
            filing_meta=_filing_meta(),
            income_statement=[
                IncomeStatementRow(
                    fiscal_year="2024",
                    fiscal_period="FY",
                    revenue=1_000_000_000,
                    net_income_to_parent=150_000_000,
                ),
            ],
            balance_sheet=[
                BalanceSheetRow(fiscal_year="2024", total_assets=5_000_000_000),
            ],
            cash_flow=[
                CashFlowRow(fiscal_year="2024", operating_cash_flow=200_000_000),
            ],
            segments=[],
            accounting_policies=[
                AccountingPolicyEntry(
                    category="revenue_recognition",
                    fiscal_year="2024",
                    method="时点确认",
                    raw_text="本公司在将商品控制权转移给客户时确认收入...",
                    changed_from_prior=False,
                ),
            ],
            debt_schedule=[],
            covenant_status=[],
            special_items=[],
            concentration=ConcentrationData(
                top5_customers_pct=45.0,
                customer_losses=[],
                major_supplier_dependencies=[],
                geographic_revenue_split={"境内": 80.0, "境外": 20.0},
            ),
            buyback_history=[],
            acquisition_history=[],
            dividend_per_share_history=[],
            footnote_extracts=[],
            risk_factors=[],
        )
        assert len(output.income_statement) == 1
        assert output.income_statement[0].revenue == 1_000_000_000
        assert output.concentration.top5_customers_pct == 45.0

    def test_filing_output_missing_required_field(self):
        with pytest.raises(ValidationError):
            FilingOutput(
                meta=_meta(),
                # missing filing_meta and all required lists
            )
