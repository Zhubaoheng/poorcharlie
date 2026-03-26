"""Tests for PDF extraction and section splitting."""

from investagent.datasources.pdf_extract import (
    _split_by_headers,
    extract_sections,
)


# ---------------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------------

_SAMPLE_MD = """
## **CORPORATE INFORMATION**

Board members and stuff here.

## **CONSOLIDATED STATEMENT OF PROFIT OR LOSS AND OTHER COMPREHENSIVE INCOME**

|Item|2024 RMB'000|2023 RMB'000|
|---|---|---|
|Revenue|2,800,000|2,593,000|
|Cost of sales|(1,300,000)|(1,226,000)|

## **CONSOLIDATED STATEMENT OF FINANCIAL POSITION**

|Item|31/12/2024|31/12/2023|
|---|---|---|
|Total assets|15,000,000|14,000,000|

## **CONSOLIDATED STATEMENT OF CASH FLOWS**

|Item|2024|2023|
|---|---|---|
|Operating cash flow|900,000|850,000|

## 3. BASIS OF PREPARATION OF CONSOLIDATED FINANCIAL STATEMENTS AND MATERIAL ACCOUNTING POLICY INFORMATION

The consolidated financial statements have been prepared...

### Revenue from contracts with customers

Revenue is recognised when control transfers...

## 5. REVENUE AND SEGMENT INFORMATION

Segment A: 1,500,000
Segment B: 1,300,000

## BORROWINGS

No bank borrowings as at December 31, 2024.

## Principal risks and uncertainties

The company faces regulatory risk in the burial reform...

## CONNECTED TRANSACTION

Related party transactions with entity X...
"""


def test_split_by_headers():
    sections = _split_by_headers(_SAMPLE_MD)
    headers = [h for h, _ in sections]
    assert "CORPORATE INFORMATION" in headers
    assert "CONSOLIDATED STATEMENT OF PROFIT OR LOSS AND OTHER COMPREHENSIVE INCOME" in headers
    assert "BORROWINGS" in headers


def test_extract_sections_hk():
    sections = extract_sections(_SAMPLE_MD, "HK")
    assert "income_statement" in sections
    assert "balance_sheet" in sections
    assert "cash_flow" in sections
    assert "borrowings" in sections
    assert "risk_factors" in sections
    assert "related_party" in sections
    # Corporate information should NOT be extracted
    assert "corporate_information" not in sections


def test_extract_sections_income_statement_content():
    sections = extract_sections(_SAMPLE_MD, "HK")
    assert "Revenue" in sections["income_statement"]
    assert "2,800,000" in sections["income_statement"]


def test_extract_sections_a_share():
    md = """
## 合并利润表

|项目|2023年|2022年|
|---|---|---|
|营业收入|1,500,000|1,200,000|

## 合并资产负债表

|项目|2023年|2022年|
|---|---|---|
|总资产|5,000,000|4,500,000|

## 重要会计政策

收入确认：在商品控制权转移时确认...

## 前五名客户

第一大客户占比 15%
"""
    sections = extract_sections(md, "A_SHARE")
    assert "income_statement" in sections
    assert "balance_sheet" in sections
    assert "accounting_policies" in sections
    assert "concentration" in sections


def test_extract_sections_empty_markdown():
    sections = extract_sections("", "HK")
    assert sections == {}


def test_extract_sections_no_matches():
    md = "## Some Random Header\n\nNo financial data here."
    sections = extract_sections(md, "HK")
    assert sections == {}


def test_extract_sections_char_limit():
    # Create a section that exceeds the default limit
    huge = "## CONSOLIDATED STATEMENT OF PROFIT OR LOSS\n\n" + "x" * 20000
    sections = extract_sections(huge, "HK", max_total_chars=5000)
    assert "income_statement" in sections
    assert len(sections["income_statement"]) <= 15100  # per-section limit + truncation marker


def test_extract_sections_total_budget():
    sections = extract_sections(_SAMPLE_MD, "HK", max_total_chars=500)
    total = sum(len(v) for v in sections.values())
    # Should respect budget (with some slack for truncation markers)
    assert total <= 600
