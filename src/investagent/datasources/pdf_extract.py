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
]

_SECTION_DEFS_A_SHARE: list[tuple[str, list[str], int]] = [
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
]

_SECTION_DEFS_US_ADR: list[tuple[str, list[str], int]] = [
    ("income_statement", [
        "Consolidated Statements of Operations",
        "Consolidated Statements of Income",
        "Consolidated Income Statement",
        "CONSOLIDATED INCOME STATEMENT",
        "CONSOLI DATED INCOME",  # EDGAR OCR artifact
    ], 30000),
    ("balance_sheet", [
        "Consolidated Balance Sheet",
        "Consolidated Statements of Financial Position",
        "CONSOLIDATED BALANCE SHEET",
    ], 30000),
    ("cash_flow", [
        "Consolidated Statements of Cash Flows",
        "Consolidated Cash Flow Statement",
        "CONSOLIDATED STA TEMENTS OF CASH FLOWS",  # EDGAR OCR artifact
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
    ("risk_factors", [
        "Risk Factors",
        "Item 3",
    ], 16000),
    ("related_party", [
        "Related Party",
    ], 16000),
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

    Supports both markdown headers (## ...) and plain-text ALL-CAPS headers
    (common in SEC EDGAR filings).
    """
    # Try markdown headers first
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

    # Fallback: ALL-CAPS lines as section headers (EDGAR plain text)
    # Match lines that are mostly uppercase, at least 20 chars, standalone
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
            # Only keep sections with meaningful body (>50 chars)
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
