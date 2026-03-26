"""HKEX data source for Hong Kong listed stocks.

Uses ``scrapling`` to search and download filings from hkexnews.hk.

The search uses the TitleSearchPanel form — a plain HTML POST to
titlesearch.xhtml that returns a full results page. A session is needed
to acquire cookies from the initial page load.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime
from typing import Any

from scrapling.fetchers import Fetcher, FetcherSession

from investagent.datasources.base import FilingDocument, FilingFetcher

logger = logging.getLogger(__name__)

# URLs
_ACTIVE_STOCK_URL = "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_e.json"
_SEARCH_URL = "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en"

# Filing type keywords used to classify results
_ANNUAL_KEYWORDS = ("ANNUAL REPORT", "年度报告", "年报")
_INTERIM_KEYWORDS = ("INTERIM REPORT", "中期报告", "中期报告")

_PERIOD_MAP: dict[str, str] = {
    "Annual Report": "FY",
    "Interim Report": "H1",
}

# Stock ID cache (static data, shared across instances)
_stock_id_cache: dict[str, dict[str, Any]] = {}


def _normalize_stock_code(ticker: str) -> str:
    """Normalize HK stock code to 5-digit format (e.g., '700' -> '00700')."""
    digits = re.sub(r"[^\d]", "", ticker.split(".")[0])
    return digits.zfill(5)


def _parse_date(date_str: str) -> date:
    """Parse date strings commonly seen on HKEX."""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%d %b %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return date.today()


def _load_stock_ids() -> dict[str, dict[str, Any]]:
    """Load the HKEX active stock list and build a code -> info mapping."""
    global _stock_id_cache
    if _stock_id_cache:
        return _stock_id_cache

    page = Fetcher.get(_ACTIVE_STOCK_URL, stealthy_headers=True)
    if page.status != 200:
        logger.warning("Failed to load HKEX stock list: status %d", page.status)
        return {}

    try:
        data = json.loads(page.body)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse HKEX stock list JSON")
        return {}

    for stock in data:
        code = stock.get("c", "")
        _stock_id_cache[code] = {
            "id": stock.get("i"),
            "name": stock.get("n", ""),
            "code": code,
        }
    return _stock_id_cache


def _classify_filing(title: str) -> str | None:
    """Classify a filing title as Annual Report, Interim Report, or None."""
    upper = title.upper()
    for kw in _ANNUAL_KEYWORDS:
        if kw in upper:
            return "Annual Report"
    for kw in _INTERIM_KEYWORDS:
        if kw in upper:
            return "Interim Report"
    return None


def _search_filings_sync(
    stock_code: str,
    stock_id: int,
    stock_name: str,
    ticker: str,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[FilingDocument]:
    """Search HKEX filings via TitleSearchPanel POST.

    Uses title keyword filtering ("Annual Report" / "Interim Report")
    to avoid the 100-result limit being consumed by non-report filings.
    """
    from_date = f"{start_year}0101" if start_year else ""
    to_date = f"{end_year}1231" if end_year else ""

    all_body_parts: list[str] = []

    with FetcherSession(impersonate="chrome") as session:
        session.get(_SEARCH_URL, stealthy_headers=True)

        for title_kw in ("Annual Report", "Interim Report"):
            resp = session.post(
                _SEARCH_URL,
                data={
                    "lang": "EN",
                    "category": "0",
                    "market": "SEHK",
                    "searchType": "0",
                    "documentType": "",
                    "t1code": "",
                    "t2Gcode": "",
                    "t2code": "",
                    "stockId": str(stock_id),
                    "from": from_date,
                    "to": to_date,
                    "title": title_kw,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": _SEARCH_URL,
                },
            )

            if resp.status != 200:
                logger.warning("HKEX search returned status %d for %s", resp.status, title_kw)
                continue

            part = resp.body.decode("utf-8", errors="replace") if isinstance(resp.body, bytes) else str(resp.body)
            if part:
                all_body_parts.append(part)

    body = "\n".join(all_body_parts)
    if not body:
        return []

    # Parse results: extract PDF/HTM links and corresponding dates
    links = re.findall(
        r'href="([^"]*(?:\.pdf|\.htm)[^"]*)"[^>]*>([^<]*)',
        body, re.IGNORECASE,
    )
    dates = re.findall(r"(\d{2}/\d{2}/\d{4})", body)

    results: list[FilingDocument] = []
    for idx, (href, title_raw) in enumerate(links):
        title = re.sub(r"&#x[0-9a-f]+;", "", title_raw).strip()
        if not title:
            continue

        filing_type = _classify_filing(title)
        if filing_type is None:
            continue

        # Build full URL
        if href.startswith("//"):
            source_url = f"https:{href}"
        elif href.startswith("/"):
            source_url = f"https://www1.hkexnews.hk{href}"
        elif not href.startswith("http"):
            source_url = f"https://www1.hkexnews.hk/{href}"
        else:
            source_url = href

        content_type = "pdf" if ".pdf" in href.lower() else "html"
        fd = _parse_date(dates[idx]) if idx < len(dates) else date.today()

        results.append(
            FilingDocument(
                market="HK",
                ticker=ticker,
                company_name=stock_name or ticker,
                filing_type=filing_type,
                fiscal_year=str(fd.year),
                fiscal_period=_PERIOD_MAP.get(filing_type, "FY"),
                filing_date=fd,
                source_url=source_url,
                content_type=content_type,
                metadata={
                    "stock_code": stock_code,
                    "title": title,
                },
            )
        )

    return results


class HKEXFetcher(FilingFetcher):
    """Fetch annual and interim reports from HKEX."""

    @property
    def market(self) -> str:
        return "HK"

    def _search_sync(
        self,
        ticker: str,
        filing_types: list[str] | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[FilingDocument]:
        stock_code = _normalize_stock_code(ticker)

        stock_ids = _load_stock_ids()
        stock_info = stock_ids.get(stock_code)
        if not stock_info:
            logger.warning("Stock %s not found in HKEX active list", stock_code)
            return []

        results = _search_filings_sync(
            stock_code=stock_code,
            stock_id=stock_info["id"],
            stock_name=stock_info["name"],
            ticker=ticker,
            start_year=start_year,
            end_year=end_year,
        )

        # Filter by filing type if specified
        if filing_types:
            type_map = {
                "年报": "Annual Report",
                "中期报告": "Interim Report",
                "Annual Report": "Annual Report",
                "Interim Report": "Interim Report",
            }
            eng_types = {type_map.get(ft, ft) for ft in filing_types}
            results = [r for r in results if r.filing_type in eng_types]

        return results

    def _download_sync(self, filing: FilingDocument) -> FilingDocument:
        try:
            page = Fetcher.get(filing.source_url, stealthy_headers=True)

            if page.status != 200:
                raise ValueError(
                    f"Download failed with status {page.status}: {filing.source_url}"
                )

            raw = page.body if isinstance(page.body, bytes) else page.body.encode("utf-8")

            return FilingDocument(
                market=filing.market,
                ticker=filing.ticker,
                company_name=filing.company_name,
                filing_type=filing.filing_type,
                fiscal_year=filing.fiscal_year,
                fiscal_period=filing.fiscal_period,
                filing_date=filing.filing_date,
                source_url=filing.source_url,
                content_type=filing.content_type,
                raw_content=raw,
                text_content=None,
                metadata=filing.metadata,
            )
        except Exception as e:
            logger.error("Failed to download %s: %s", filing.source_url, e)
            raise

    # ------------------------------------------------------------------
    # Async interface
    # ------------------------------------------------------------------

    async def search_filings(
        self,
        ticker: str,
        filing_types: list[str] | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[FilingDocument]:
        return await asyncio.to_thread(
            self._search_sync, ticker, filing_types, start_year, end_year,
        )

    async def download_filing(self, filing: FilingDocument) -> FilingDocument:
        return await asyncio.to_thread(self._download_sync, filing)
