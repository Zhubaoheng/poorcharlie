"""PDF text extraction and section splitting for financial filings.

Uses pymupdf4llm to convert PDF bytes into markdown, then extracts
relevant sections (financial statements, accounting policies, etc.)
by keyword matching on markdown headers.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section keywords by market
# ---------------------------------------------------------------------------

# Each entry: (section_key, [keywords], char_limit)
# MD&A is the most valuable qualitative section — give it the biggest budget.
_SECTION_DEFS_HK: list[tuple[str, list[str], int]] = [
    ("mda", [
        "Management Discussion and Analysis",
        "Management Discussion & Analysis",
        "MANAGEMENT DISCUSSION",
        "Business Review and Outlook",
        "Business Review",
        "Chairman's Statement",
        "Financial Review",
        "Operating and Financial Review",
    ], 60000),
    ("income_statement", [
        "Consolidated Statement of Profit or Loss",
        "Consolidated Income Statement",
        "Consolidated Statement of Comprehensive Income",
    ], 30000),
    ("balance_sheet", [
        "Consolidated Statement of Financial Position",
        "Consolidated Balance Sheet",
    ], 30000),
    ("cash_flow", [
        "Consolidated Statement of Cash Flows",
        "Consolidated Cash Flow Statement",
    ], 30000),
    ("changes_in_equity", [
        "Consolidated Statement of Changes in Equity",
    ], 20000),
    ("accounting_policies", [
        "Material Accounting Policy",
        "Significant Accounting Polic",
        "Summary of Material Accounting",
        "Basis of Preparation",
    ], 24000),
    ("segments", [
        "Segment Information",
        "Operating Segments",
        "Segment Reporting",
    ], 20000),
    ("borrowings", [
        "Borrowings",
        "Bank Loans",
        "Notes Payable",
        "Interest-bearing",
    ], 16000),
    ("risk_factors", [
        "Risk Factors",
        "Principal Risks",
        "Risk Management",
        "Financial Risk",
    ], 16000),
    ("related_party", [
        "Related Party",
        "Connected Transaction",
    ], 16000),
    ("concentration", [
        "Major Customers",
        "Concentration",
        "Five Largest",
    ], 12000),
    ("revenue_recognition", [
        "Revenue Recognition",
        "Revenue from Contracts",
    ], 12000),
    ("remuneration", [
        "Remuneration Committee",
        "Directors' Remuneration",
        "Remuneration of Senior Management",
        "Employee and Remuneration",
        "Share Schemes",
        "Share Option Scheme",
        "Share Award Scheme",
    ], 30000),
    ("directors_interests", [
        "Directors' and Chief Executive's Interests",
        "Directors' Interests in Shares",
        "Substantial Shareholders",
        "Interest in Shares",
        "Key management compensation",
        "Benefits and interests of directors",
    ], 16000),
    ("corporate_structure", [
        "Weighted Voting Rights",
        "Contractual Arrangements",
        "VIE Structure",
    ], 20000),
    ("non_ifrs", [
        "Non-IFRS",
        "Adjusted Net Profit",
        "Adjusted EBITDA",
    ], 8000),
    ("audit", [
        "Key Audit Matters",
        "Independent Auditor",
        "Auditor's Remuneration",
    ], 8000),
    ("liquidity", [
        "Liquidity and Financial Resources",
        "Capital Expenditures",
        "Investments Held",
        "Gearing",
    ], 8000),
    ("governance_risks", [
        "Geopolitical risk",
        "AI technology risk",
        "Competition risk",
        "Compliance risk",
        "Material Litigation",
        "Contingencies",
        "Events after the Reporting Period",
    ], 12000),
    ("supply_chain", [
        "Supply Chain Management",
        "Responsible Minerals",
        "Supplier",
    ], 10000),
    # Financial statement notes — split by topic
    ("notes_tax", [
        "Income tax expense",
        "Deferred income tax",
        "Preferential EIT",
        "Super Deduction",
    ], 12000),
    ("notes_goodwill_intangibles", [
        "Intangible assets",
        "Impairment test for goodwill",
        "Goodwill",
    ], 16000),
    ("notes_inventory", [
        "Inventories",
        "Inventory provision",
    ], 8000),
    ("notes_ppe", [
        "Property, plant and equipment",
    ], 10000),
    ("notes_investments", [
        "Investments accounted for using the equity method",
        "Major subsidiaries",
        "controlled structured entities",
    ], 16000),
    ("notes_share_based", [
        "Share-based payments",
        "Share options granted",
        "RSUs granted",
        "Employee fund",
    ], 12000),
    ("notes_contingencies", [
        "Contingencies",
        "Contingent liabilities",
    ], 8000),
    ("notes_borrowings", [
        "Borrowings",
    ], 8000),
    ("notes_earnings_per_share", [
        "Basic",
        "Diluted",
        "Earnings per share",
    ], 6000),
    ("five_year_summary", [
        "Five-Year Financial Summary",
        "FIVE-YEAR FINANCIAL SUMMARY",
        "Financial Summary",
    ], 10000),
]

_SECTION_DEFS_A_SHARE: list[tuple[str, list[str], int]] = [
    ("mda", [
        "经营情况讨论与分析",
        "管理层讨论与分析",
        "董事会报告",
        "业务回顾",
        "经营情况",
        "财务回顾",
        "主要业务分析",
    ], 60000),
    ("income_statement", [
        "合并利润表",
        "利润表",
    ], 30000),
    ("balance_sheet", [
        "合并资产负债表",
        "资产负债表",
    ], 30000),
    ("cash_flow", [
        "合并现金流量表",
        "现金流量表",
    ], 30000),
    ("accounting_policies", [
        "重要会计政策",
        "主要会计政策",
        "会计政策和会计估计",
    ], 24000),
    ("segments", [
        "分部信息",
        "分部报告",
        "主营业务分行业",
        "主营业务分产品",
    ], 20000),
    ("borrowings", [
        "短期借款",
        "长期借款",
        "应付债券",
        "有息负债",
    ], 16000),
    ("risk_factors", [
        "风险因素",
        "重大风险",
        "风险提示",
    ], 16000),
    ("related_party", [
        "关联方交易",
        "关联方关系",
    ], 16000),
    ("concentration", [
        "前五名客户",
        "前五名供应商",
        "主要客户",
    ], 12000),
    ("special_items", [
        "非经常性损益",
        "非经常损益",
    ], 12000),
    ("remuneration", [
        "董事及高级管理人员薪酬",
        "薪酬委员会",
        "管理层薪酬",
        "股权激励",
        "限制性股票",
        "员工薪酬",
    ], 20000),
    ("directors_interests", [
        "董事及监事持股",
        "主要股东",
        "持股变动",
        "控股股东",
        "实际控制人",
    ], 12000),
    ("non_gaap", [
        "非国际财务报告准则",
        "经调整",
        "扣非",
    ], 8000),
    ("audit", [
        "关键审计事项",
        "审计报告",
        "核数师",
    ], 8000),
    ("liquidity", [
        "流动资金",
        "资本支出",
        "融资活动",
        "资产负债率",
    ], 8000),
    ("governance_risks", [
        "重大诉讼",
        "或有事项",
        "报告期后事项",
        "期后事项",
    ], 8000),
    ("notes_tax", [
        "所得税费用",
        "递延所得税",
        "税收优惠",
    ], 12000),
    ("notes_goodwill_intangibles", [
        "商誉",
        "无形资产",
        "减值测试",
    ], 16000),
    ("notes_inventory", [
        "存货",
        "存货跌价",
    ], 8000),
    ("notes_investments", [
        "长期股权投资",
        "合营企业",
        "联营企业",
    ], 12000),
    ("notes_contingencies", [
        "或有事项",
        "承诺事项",
    ], 8000),
    ("five_year_summary", [
        "主要财务指标",
        "最近五年",
        "财务摘要",
    ], 10000),
]

_SECTION_DEFS_US_ADR: list[tuple[str, list[str], int]] = [
    # 20-F ITEM-based matching (handles OCR artifacts like "FINAN CIAL")
    ("mda", [
        "ITEM 5",        # Operating and Financial Review
        "ITEM5",
        "Operating and Financial Review",
        "Management Discussion",
        "OPERATING AND FINAN",
    ], 60000),
    ("risk_factors", [
        "ITEM 3",        # Key Information (includes Risk Factors)
        "ITEM3",
        "Risk Factors",
        "KEY INFORMATION",
    ], 30000),
    ("business_overview", [
        "ITEM 4",        # Information on the Company
        "ITEM4",
        "INFORM ATION ON THE COMPANY",
    ], 30000),
    ("remuneration", [
        "ITEM 6",        # Directors, Senior Management, Employees
        "ITEM6",
        "DIRECTORS, SENIOR MANAGEMENT",
        "Executive Compensation",
        "Compensation Discussion",
    ], 30000),
    ("directors_interests", [
        "ITEM 7",        # Major Shareholders
        "ITEM7",
        "MAJ OR SHAREHOLDERS",
        "Principal Shareholders",
    ], 16000),
    ("governance_risks", [
        "ITEM 8",        # Financial Information (legal proceedings, dividends)
        "ITEM8",
        "F INANCIAL INFORMATION",
        "Legal Proceedings",
    ], 16000),
    ("corporate_structure", [
        "ITEM 10",       # Additional Information (charter, VIE, taxation)
        "ITEM10",
        "A DDITIONAL INFORMATION",
        "VIE Structure",
        "Variable Interest",
    ], 30000),
    ("audit", [
        "ITEM 15",       # Controls and Procedures
        "ITEM15",
        "CONTROLS AND PROCEDURES",
        "ITEM 16A",      # Audit Committee Expert
        "ITEM 16C",      # Principal Accountant Fees
        "PRINCIPAL ACCOUNTANT",
    ], 12000),
    # Financial statements (inside ITEM 18 / after INDEX TO FINANCIAL STATEMENTS)
    ("income_statement", [
        "Consolidated Statements of Operations",
        "Consolidated Statements of Income",
        "Consolidated Income Statement",
        "CONSOLIDATED INCOME STATEMENT",
        "CONSOLI DATED INCOME",
    ], 30000),
    ("balance_sheet", [
        "Consolidated Balance Sheet",
        "Consolidated Statements of Financial Position",
        "CONSOLIDATED BALANCE SHEET",
        "CONSOLIDATED B ALANCE",
    ], 30000),
    ("cash_flow", [
        "Consolidated Statements of Cash Flows",
        "Consolidated Cash Flow Statement",
        "CONSOLIDATED STA TEMENTS OF CASH FLOWS",
        "CONSOLIDATED STATEMENTS OF CASH FLOWS",
    ], 30000),
    ("accounting_policies", [
        "Significant Accounting Polic",
        "Summary of Significant Accounting",
        "Critical Accounting Polic",
        "SIGNIFICANT ACCOUNTING POLIC",
    ], 24000),
    ("segments", [
        "Segment Information",
        "Segment Reporting",
        "SEGMENT INFORMATION",
    ], 20000),
    ("related_party", [
        "Related Party",
    ], 16000),
    ("notes_tax", [
        "Income tax",
        "Deferred tax",
    ], 12000),
    ("notes_goodwill_intangibles", [
        "Goodwill",
        "Intangible assets",
        "Impairment",
    ], 16000),
    ("notes_investments", [
        "Equity method",
        "Variable interest",
        "Subsidiaries",
    ], 16000),
    ("notes_share_based", [
        "Share-based compensation",
        "Stock option",
        "Restricted share",
    ], 12000),
]

_MARKET_DEFS: dict[str, list[tuple[str, list[str], int]]] = {
    "HK": _SECTION_DEFS_HK,
    "A_SHARE": _SECTION_DEFS_A_SHARE,
    "US_ADR": _SECTION_DEFS_US_ADR,
}

# Maximum total characters across all extracted sections
_MAX_TOTAL_CHARS = 200_000


# ---------------------------------------------------------------------------
# PDF → Markdown
# ---------------------------------------------------------------------------

def extract_pdf_markdown(raw_content: bytes) -> str:
    """Convert PDF bytes to markdown text using pymupdf4llm.

    Returns empty string if extraction fails.
    """
    try:
        import pymupdf
        import pymupdf4llm

        doc = pymupdf.open(stream=raw_content, filetype="pdf")
        md = pymupdf4llm.to_markdown(doc)
        doc.close()
        return md
    except Exception:
        logger.warning("PDF markdown extraction failed", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------------

def _split_by_headers(text: str) -> list[tuple[str, str]]:
    """Split text into (header_text, body_text) pairs.

    Strategy (in priority order):
    1. Markdown headers (## ...)  — PDF via pymupdf4llm
    2. ITEM X. pattern           — SEC EDGAR 20-F/10-K format
    3. ALL-CAPS lines            — Other plain text formats
    """
    # 1. Markdown headers
    md_pattern = re.compile(r"^(#{1,3})\s+\**(.+?)\**\s*$", re.MULTILINE)
    md_matches = list(md_pattern.finditer(text))

    if md_matches:
        sections: list[tuple[str, str]] = []
        for i, match in enumerate(md_matches):
            header = match.group(2).strip()
            start = match.end()
            end = md_matches[i + 1].start() if i + 1 < len(md_matches) else len(text)
            body = text[start:end].strip()
            sections.append((header, body))
        return sections

    # 2. SEC EDGAR "ITEM X." format — handles OCR artifacts like "ITEM 5. OPERATING AND FINAN CIAL"
    item_pattern = re.compile(
        r"^(ITEM\s*\d+[A-Z]?\.?\s+[A-Z][A-Z\s\(\)\-\,\.\/\'\:\;]+)$",
        re.MULTILINE,
    )
    item_matches = list(item_pattern.finditer(text))

    if len(item_matches) >= 5:  # Looks like a real 20-F with Items
        sections = []
        for i, match in enumerate(item_matches):
            header = re.sub(r"\s+", " ", match.group(1).strip())
            start = match.end()
            end = item_matches[i + 1].start() if i + 1 < len(item_matches) else len(text)
            body = text[start:end].strip()
            if len(body) > 50:
                sections.append((header, body))

        # For large ITEM bodies (>30K chars, e.g. ITEM 18/19 with financial statements),
        # also split by sub-headers matching financial statement patterns
        _SUB_PATTERNS = [
            ("Consolidated Income Statement", "income_statement"),
            ("Consolidated Statements of Operations", "income_statement"),
            ("Consolidated Balance Sheet", "balance_sheet"),
            ("Consolidated Statements of Cash Flows", "cash_flow"),
            ("Consolidated Statement of Comprehensive Income", "comprehensive_income"),
            ("Notes to Consolidated Financial Statements", "notes_to_fs"),
        ]
        extra_sections: list[tuple[str, str]] = []
        for header, body in sections:
            if len(body) < 30000:
                continue
            for sub_kw, sub_key in _SUB_PATTERNS:
                # Find the keyword in the body
                idx = body.lower().find(sub_kw.lower())
                if idx == -1:
                    continue
                # Extract from keyword position to next keyword or 15K chars
                sub_start = idx
                sub_end = len(body)
                for other_kw, _ in _SUB_PATTERNS:
                    if other_kw.lower() == sub_kw.lower():
                        continue
                    other_idx = body.lower().find(other_kw.lower(), sub_start + len(sub_kw))
                    if other_idx != -1 and other_idx < sub_end:
                        sub_end = other_idx
                chunk = body[sub_start:sub_end].strip()
                if len(chunk) > 100:
                    extra_sections.append((sub_kw, chunk[:30000]))

        sections.extend(extra_sections)
        return sections

    # 3. ALL-CAPS lines as section headers
    caps_pattern = re.compile(
        r"^([A-Z][A-Z\s\(\)\-\,\.\/]{15,})$", re.MULTILINE,
    )
    caps_matches = list(caps_pattern.finditer(text))

    if caps_matches:
        sections = []
        for i, match in enumerate(caps_matches):
            header = match.group(1).strip()
            start = match.end()
            end = caps_matches[i + 1].start() if i + 1 < len(caps_matches) else len(text)
            body = text[start:end].strip()
            if len(body) > 50:
                sections.append((header, body))
        return sections

    return []


def extract_sections(
    markdown: str,
    market: str,
    max_total_chars: int = _MAX_TOTAL_CHARS,
) -> dict[str, str]:
    """Extract relevant sections from markdown by keyword matching.

    Args:
        markdown: Full document as markdown text.
        market: "A_SHARE", "HK", or "US_ADR".
        max_total_chars: Maximum total characters across all sections.

    Returns:
        Dict mapping section keys to extracted text content.
    """
    defs = _MARKET_DEFS.get(market, _SECTION_DEFS_HK)
    header_sections = _split_by_headers(markdown)

    if not header_sections:
        return {}

    # Stop keywords: when we hit these headers, MD&A range capture ends
    _MDA_STOP_KEYWORDS = {
        "CONSOLIDATED", "INDEPENDENT AUDITOR", "FINANCIAL STATEMENTS",
        "CORPORATE GOVERNANCE", "DIRECTORS' REPORT", "ESG",
        "SUSTAINABILITY", "合并利润", "合并资产", "合并现金",
        "审计报告", "独立核数师", "公司治理",
    }

    result: dict[str, str] = {}
    total_chars = 0

    for section_key, keywords, char_limit in defs:
        if total_chars >= max_total_chars:
            break

        matched_parts: list[str] = []

        if section_key == "mda":
            # MD&A range capture: grab from first match through all
            # subsequent sub-headers until a major non-MD&A section
            capturing = False
            for header, body in header_sections:
                header_upper = header.upper()

                if not capturing:
                    # Start capturing when we hit an MD&A keyword
                    for kw in keywords:
                        if kw.upper() in header_upper:
                            capturing = True
                            break

                if capturing:
                    # Stop if we hit a non-MD&A major section
                    if any(stop in header_upper for stop in _MDA_STOP_KEYWORDS):
                        break
                    matched_parts.append(f"### {header}\n\n{body}")
        else:
            # Normal matching: find all headers containing any keyword
            for header, body in header_sections:
                header_upper = header.upper()
                for kw in keywords:
                    if kw.upper() in header_upper:
                        matched_parts.append(f"### {header}\n\n{body}")
                        break

        if not matched_parts:
            continue

        combined = "\n\n".join(matched_parts)

        # Apply per-section char limit
        if len(combined) > char_limit:
            combined = combined[:char_limit] + "\n\n... (截断)"

        # Apply global budget
        remaining = max_total_chars - total_chars
        if len(combined) > remaining:
            combined = combined[:remaining] + "\n\n... (总量截断)"

        result[section_key] = combined
        total_chars += len(combined)

    return result
