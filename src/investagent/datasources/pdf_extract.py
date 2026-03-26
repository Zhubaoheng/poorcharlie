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
_SECTION_DEFS_HK: list[tuple[str, list[str], int]] = [
    ("income_statement", [
        "Consolidated Statement of Profit or Loss",
        "Consolidated Income Statement",
        "Consolidated Statement of Comprehensive Income",
    ], 15000),
    ("balance_sheet", [
        "Consolidated Statement of Financial Position",
        "Consolidated Balance Sheet",
    ], 15000),
    ("cash_flow", [
        "Consolidated Statement of Cash Flows",
        "Consolidated Cash Flow Statement",
    ], 15000),
    ("changes_in_equity", [
        "Consolidated Statement of Changes in Equity",
    ], 10000),
    ("accounting_policies", [
        "Material Accounting Policy",
        "Significant Accounting Polic",
        "Summary of Material Accounting",
        "Basis of Preparation",
    ], 12000),
    ("segments", [
        "Segment Information",
        "Operating Segments",
        "Segment Reporting",
    ], 10000),
    ("borrowings", [
        "Borrowings",
        "Bank Loans",
        "Notes Payable",
        "Interest-bearing",
    ], 8000),
    ("risk_factors", [
        "Risk Factors",
        "Principal Risks",
        "Risk Management",
        "Financial Risk",
    ], 8000),
    ("related_party", [
        "Related Party",
        "Connected Transaction",
    ], 8000),
    ("concentration", [
        "Major Customers",
        "Concentration",
        "Five Largest",
    ], 6000),
    ("revenue_recognition", [
        "Revenue Recognition",
        "Revenue from Contracts",
    ], 6000),
]

_SECTION_DEFS_A_SHARE: list[tuple[str, list[str], int]] = [
    ("income_statement", [
        "合并利润表",
        "利润表",
    ], 15000),
    ("balance_sheet", [
        "合并资产负债表",
        "资产负债表",
    ], 15000),
    ("cash_flow", [
        "合并现金流量表",
        "现金流量表",
    ], 15000),
    ("accounting_policies", [
        "重要会计政策",
        "主要会计政策",
        "会计政策和会计估计",
    ], 12000),
    ("segments", [
        "分部信息",
        "分部报告",
        "主营业务分行业",
        "主营业务分产品",
    ], 10000),
    ("borrowings", [
        "短期借款",
        "长期借款",
        "应付债券",
        "有息负债",
    ], 8000),
    ("risk_factors", [
        "风险因素",
        "重大风险",
        "风险提示",
    ], 8000),
    ("related_party", [
        "关联方交易",
        "关联方关系",
    ], 8000),
    ("concentration", [
        "前五名客户",
        "前五名供应商",
        "主要客户",
    ], 6000),
    ("special_items", [
        "非经常性损益",
        "非经常损益",
    ], 6000),
]

_SECTION_DEFS_US_ADR: list[tuple[str, list[str], int]] = [
    ("income_statement", [
        "Consolidated Statements of Operations",
        "Consolidated Statements of Income",
        "Consolidated Income Statement",
    ], 15000),
    ("balance_sheet", [
        "Consolidated Balance Sheet",
        "Consolidated Statements of Financial Position",
    ], 15000),
    ("cash_flow", [
        "Consolidated Statements of Cash Flows",
        "Consolidated Cash Flow Statement",
    ], 15000),
    ("accounting_policies", [
        "Significant Accounting Polic",
        "Summary of Significant Accounting",
        "Critical Accounting Polic",
    ], 12000),
    ("segments", [
        "Segment Information",
        "Segment Reporting",
    ], 10000),
    ("risk_factors", [
        "Risk Factors",
        "Item 3",
    ], 8000),
    ("related_party", [
        "Related Party",
    ], 8000),
]

_MARKET_DEFS: dict[str, list[tuple[str, list[str], int]]] = {
    "HK": _SECTION_DEFS_HK,
    "A_SHARE": _SECTION_DEFS_A_SHARE,
    "US_ADR": _SECTION_DEFS_US_ADR,
}

# Maximum total characters across all extracted sections
_MAX_TOTAL_CHARS = 120_000


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

def _split_by_headers(markdown: str) -> list[tuple[str, str]]:
    """Split markdown into (header_text, body_text) pairs by ## headers."""
    # Match ## headers (with optional bold markers)
    pattern = re.compile(r"^(#{1,3})\s+\**(.+?)\**\s*$", re.MULTILINE)

    sections: list[tuple[str, str]] = []
    matches = list(pattern.finditer(markdown))

    for i, match in enumerate(matches):
        header = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        sections.append((header, body))

    return sections


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

    result: dict[str, str] = {}
    total_chars = 0

    for section_key, keywords, char_limit in defs:
        if total_chars >= max_total_chars:
            break

        # Find all matching header sections
        matched_parts: list[str] = []
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
