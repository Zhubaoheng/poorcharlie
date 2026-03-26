"""巨潮资讯网 (cninfo.com.cn) data source for A-share filings.

Uses ``scrapling`` with TLS fingerprint impersonation and session-based
cookie management to access cninfo's semi-public API endpoints.

Key insight: cninfo requires a valid session cookie from the homepage before
its AJAX APIs will return data. We use FetcherSession to maintain cookies
across requests.
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

# cninfo API endpoints
_HOME_URL = "http://www.cninfo.com.cn/"
_SEARCH_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_STATIC_BASE = "https://static.cninfo.com.cn/"
_COMPANY_SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"

# Headers required for cninfo AJAX endpoints
_AJAX_HEADERS: dict[str, str] = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "http://www.cninfo.com.cn/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# Filing category codes on cninfo
_CATEGORY_MAP: dict[str, str] = {
    "年报": "category_ndbg_szsh;",
    "半年报": "category_bndbg_szsh;",
    "一季报": "category_yjdbg_szsh;",
    "三季报": "category_sjdbg_szsh;",
}

_PERIOD_MAP: dict[str, str] = {
    "年报": "FY",
    "半年报": "H1",
    "一季报": "Q1",
    "三季报": "Q3",
}


def _detect_column(ticker: str) -> str:
    """Detect exchange column from ticker prefix.

    - 6xxxxx / 9xxxxx -> SSE (Shanghai)
    - 0xxxxx / 3xxxxx / 2xxxxx -> SZSE (Shenzhen)
    - 4xxxxx / 8xxxxx -> BSE (Beijing)
    """
    code = re.sub(r"[^\d]", "", ticker.split(".")[0])
    if code.startswith(("6", "9")):
        return "sse"
    elif code.startswith(("0", "3", "2")):
        return "szse"
    elif code.startswith(("4", "8")):
        return "bse"
    return "szse"


class CninfoFetcher(FilingFetcher):
    """Fetch annual, semi-annual, and quarterly reports from cninfo."""

    def __init__(self) -> None:
        self._org_id_cache: dict[str, tuple[str, str]] = {}

    @property
    def market(self) -> str:
        return "A_SHARE"

    def _lookup_org_id(
        self, session: FetcherSession, ticker: str,
    ) -> tuple[str, str]:
        """Look up cninfo orgId for a stock code within a session."""
        code = re.sub(r"[^\d]", "", ticker.split(".")[0])

        page = session.post(
            _COMPANY_SEARCH_URL,
            data={"keyWord": code, "maxSecNum": 10, "maxListNum": 5},
            headers=_AJAX_HEADERS,
        )

        if page.status != 200:
            raise ValueError(f"cninfo company search failed with status {page.status}")

        body = page.body if isinstance(page.body, bytes) else str(page.body).encode()
        if not body:
            raise ValueError(f"cninfo returned empty response for ticker {ticker}")

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            raise ValueError(f"cninfo returned invalid JSON for ticker {ticker}")

        if isinstance(data, list) and data:
            for item in data:
                if item.get("code") == code:
                    return code, item["orgId"]
            return data[0].get("code", code), data[0].get("orgId", "")

        raise ValueError(f"No results found on cninfo for ticker {ticker}")

    def _get_org_id(
        self, session: FetcherSession, ticker: str,
    ) -> tuple[str, str]:
        """Get (stock_code, orgId) with caching."""
        if ticker not in self._org_id_cache:
            self._org_id_cache[ticker] = self._lookup_org_id(session, ticker)
        return self._org_id_cache[ticker]

    def _search_sync(
        self,
        ticker: str,
        filing_types: list[str] | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[FilingDocument]:
        with FetcherSession(impersonate="chrome") as session:
            # Warm up session — visit homepage to get cookies
            session.get(_HOME_URL, stealthy_headers=True)

            code, org_id = self._get_org_id(session, ticker)
            column = _detect_column(ticker)

            if filing_types is None:
                filing_types = ["年报", "半年报"]

            # Build category filter
            categories = "".join(
                _CATEGORY_MAP.get(ft, "") for ft in filing_types
            )

            # Date range
            se_date = ""
            if start_year and end_year:
                se_date = f"{start_year}-01-01~{end_year}-12-31"
            elif start_year:
                se_date = f"{start_year}-01-01~"
            elif end_year:
                se_date = f"~{end_year}-12-31"

            results: list[FilingDocument] = []
            page_num = 1
            max_pages = 5

            while page_num <= max_pages:
                try:
                    resp = session.post(
                        _SEARCH_URL,
                        data={
                            "stock": f"{code},{org_id}",
                            "tabName": "fulltext",
                            "column": column,
                            "category": categories,
                            "pageNum": str(page_num),
                            "pageSize": "30",
                            "seDate": se_date,
                            "sortName": "",
                            "sortType": "",
                            "isHLtitle": "true",
                        },
                        headers=_AJAX_HEADERS,
                    )

                    if resp.status != 200:
                        logger.warning("cninfo search returned status %d", resp.status)
                        break

                    body = resp.body if isinstance(resp.body, bytes) else str(resp.body).encode()
                    if not body:
                        logger.warning("cninfo returned empty body on page %d", page_num)
                        break

                    try:
                        data = json.loads(body)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("cninfo returned invalid JSON on page %d", page_num)
                        break

                    announcements = data.get("announcements", [])
                    if not announcements:
                        break

                    for ann in announcements:
                        adjunct_url = ann.get("adjunctUrl", "")
                        title = ann.get("announcementTitle", "").replace("<em>", "").replace("</em>", "")
                        ann_date = ann.get("announcementTime")

                        if not adjunct_url:
                            continue

                        # Parse date (cninfo uses millisecond timestamp)
                        if isinstance(ann_date, (int, float)):
                            fd = datetime.fromtimestamp(ann_date / 1000).date()
                        elif isinstance(ann_date, str):
                            fd = date.fromisoformat(ann_date[:10])
                        else:
                            fd = date.today()

                        # Determine filing type from title
                        # "半年度报告" contains "年报" so check "半年" first
                        if "半年" in title:
                            detected_type = "半年报"
                        elif "三季" in title or "第三季" in title:
                            detected_type = "三季报"
                        elif "一季" in title or "第一季" in title:
                            detected_type = "一季报"
                        elif "年报" in title or "年度报告" in title:
                            detected_type = "年报"
                        else:
                            detected_type = "年报"

                        # Extract fiscal year from title (e.g., "2024年年度报告" → "2024")
                        import re as _re
                        fy_match = _re.search(r"(\d{4})\s*年", title)
                        fiscal_year = fy_match.group(1) if fy_match else str(fd.year)

                        source_url = f"{_STATIC_BASE}{adjunct_url}"

                        results.append(
                            FilingDocument(
                                market="A_SHARE",
                                ticker=ticker,
                                company_name=ann.get("secName", ticker),
                                filing_type=detected_type,
                                fiscal_year=fiscal_year,
                                fiscal_period=_PERIOD_MAP.get(detected_type, "FY"),
                                filing_date=fd,
                                source_url=source_url,
                                content_type="pdf",
                                metadata={
                                    "org_id": org_id,
                                    "announcement_id": str(ann.get("announcementId", "")),
                                    "title": title,
                                },
                            )
                        )

                    # Check if there are more pages
                    total = data.get("totalAnnouncement", 0)
                    if page_num * 30 >= total:
                        break
                    page_num += 1

                except Exception:
                    logger.warning(
                        "Failed to search cninfo page %d for %s",
                        page_num, ticker, exc_info=True,
                    )
                    break

        return results

    def _download_sync(self, filing: FilingDocument) -> FilingDocument:
        try:
            resp = Fetcher.get(filing.source_url, stealthy_headers=True)

            if resp.status != 200:
                raise ValueError(
                    f"Download failed with status {resp.status}: {filing.source_url}"
                )

            raw = resp.body if isinstance(resp.body, bytes) else resp.body.encode("utf-8")

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
                text_content=None,  # PDF — needs separate extraction
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
