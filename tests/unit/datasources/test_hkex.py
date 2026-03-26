"""Tests for HKEX fetcher — all external calls mocked."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from investagent.datasources.base import FilingDocument
from investagent.datasources.hkex import (
    HKEXFetcher,
    _classify_filing,
    _normalize_stock_code,
    _parse_date,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_normalize_stock_code():
    assert _normalize_stock_code("700") == "00700"
    assert _normalize_stock_code("0700") == "00700"
    assert _normalize_stock_code("00700") == "00700"
    assert _normalize_stock_code("9988.HK") == "09988"
    assert _normalize_stock_code("1") == "00001"


def test_parse_date_formats():
    assert _parse_date("20/03/2024") == date(2024, 3, 20)
    assert _parse_date("2024-03-20") == date(2024, 3, 20)
    assert _parse_date("2024/03/20") == date(2024, 3, 20)


def test_parse_date_fallback():
    result = _parse_date("invalid")
    assert isinstance(result, date)


def test_classify_filing_annual():
    assert _classify_filing("Annual Report 2024") == "Annual Report"
    assert _classify_filing("ANNUAL REPORT 2023") == "Annual Report"


def test_classify_filing_interim():
    assert _classify_filing("INTERIM REPORT 2024") == "Interim Report"
    assert _classify_filing("Interim Report 2025") == "Interim Report"


def test_classify_filing_other():
    assert _classify_filing("MONTHLY RETURN") is None
    assert _classify_filing("List of Directors") is None


# ---------------------------------------------------------------------------
# HKEXFetcher.search_filings
# ---------------------------------------------------------------------------

@pytest.fixture
def fetcher():
    return HKEXFetcher()


def _make_mock_response(status: int = 200, body: bytes = b"") -> MagicMock:
    page = MagicMock()
    page.status = status
    page.body = body
    return page


_ANNUAL_HTML = b"""<html><body>
<table><tbody>
<tr><td>22/04/2025</td><td><a href="/listedco/2025/annual.pdf">Annual Report 2024</a></td></tr>
</tbody></table>
</body></html>"""

_INTERIM_HTML = b"""<html><body>
<table><tbody>
<tr><td>17/09/2024</td><td><a href="/listedco/2024/interim.pdf">INTERIM REPORT 2024</a></td></tr>
</tbody></table>
</body></html>"""


def _setup_hkex_mock(mock_session_cls, mock_load_ids, annual_html=_ANNUAL_HTML, interim_html=_INTERIM_HTML):
    mock_load_ids.return_value = {
        "01448": {"id": 98167, "name": "FU SHOU YUAN", "code": "01448"},
    }
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session_cls.return_value = mock_session

    mock_session.get.return_value = _make_mock_response(200, b"<html>home</html>")
    # Two POST calls: first for "Annual Report", then for "Interim Report"
    mock_session.post.side_effect = [
        _make_mock_response(200, annual_html),
        _make_mock_response(200, interim_html),
    ]
    return mock_session


@patch("investagent.datasources.hkex._load_stock_ids")
@patch("investagent.datasources.hkex.FetcherSession")
async def test_search_filings_annual(mock_session_cls, mock_load_ids, fetcher):
    _setup_hkex_mock(mock_session_cls, mock_load_ids)

    results = await fetcher.search_filings("1448.HK", filing_types=["Annual Report"])

    assert len(results) == 1
    assert results[0].filing_type == "Annual Report"
    assert results[0].market == "HK"
    assert results[0].filing_date == date(2025, 4, 22)
    assert "annual.pdf" in results[0].source_url


@patch("investagent.datasources.hkex._load_stock_ids")
@patch("investagent.datasources.hkex.FetcherSession")
async def test_search_filings_all_types(mock_session_cls, mock_load_ids, fetcher):
    _setup_hkex_mock(mock_session_cls, mock_load_ids)

    results = await fetcher.search_filings("1448.HK")
    assert len(results) == 2
    types = {r.filing_type for r in results}
    assert types == {"Annual Report", "Interim Report"}


@patch("investagent.datasources.hkex._load_stock_ids")
async def test_search_filings_unknown_stock(mock_load_ids, fetcher):
    mock_load_ids.return_value = {}
    results = await fetcher.search_filings("99999.HK")
    assert results == []


@patch("investagent.datasources.hkex._load_stock_ids")
@patch("investagent.datasources.hkex.FetcherSession")
async def test_search_filings_empty(mock_session_cls, mock_load_ids, fetcher):
    empty = b"<html>No results</html>"
    _setup_hkex_mock(mock_session_cls, mock_load_ids, annual_html=empty, interim_html=empty)

    results = await fetcher.search_filings("1448.HK")
    assert results == []


# ---------------------------------------------------------------------------
# HKEXFetcher.download_filing
# ---------------------------------------------------------------------------

@patch("investagent.datasources.hkex.Fetcher")
async def test_download_filing_pdf(mock_fetcher_cls, fetcher):
    mock_page = MagicMock()
    mock_page.status = 200
    mock_page.body = b"%PDF-1.4 fake content"
    mock_fetcher_cls.get.return_value = mock_page

    filing = FilingDocument(
        market="HK",
        ticker="1448.HK",
        company_name="FU SHOU YUAN",
        filing_type="Annual Report",
        fiscal_year="2024",
        fiscal_period="FY",
        filing_date=date(2025, 4, 22),
        source_url="https://www1.hkexnews.hk/listedco/report.pdf",
        content_type="pdf",
    )

    result = await fetcher.download_filing(filing)
    assert result.raw_content == b"%PDF-1.4 fake content"


@patch("investagent.datasources.hkex.Fetcher")
async def test_download_filing_http_error(mock_fetcher_cls, fetcher):
    mock_page = MagicMock()
    mock_page.status = 404
    mock_fetcher_cls.get.return_value = mock_page

    filing = FilingDocument(
        market="HK",
        ticker="1448.HK",
        company_name="FU SHOU YUAN",
        filing_type="Annual Report",
        fiscal_year="2024",
        fiscal_period="FY",
        filing_date=date(2025, 4, 22),
        source_url="https://www1.hkexnews.hk/listedco/report.pdf",
        content_type="pdf",
    )

    with pytest.raises(ValueError, match="status 404"):
        await fetcher.download_filing(filing)
