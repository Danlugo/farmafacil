"""Tests for concurrent scraper execution in search_drug()."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from farmafacil.models.schemas import DrugResult
from farmafacil.services.search import search_drug


def _make_result(name: str, pharmacy: str) -> DrugResult:
    """Create a minimal DrugResult for testing."""
    return DrugResult(
        drug_name=name,
        pharmacy_name=pharmacy,
        price_bs=Decimal("100"),
        available=True,
    )


def _make_scraper_mock(name: str, results: list[DrugResult] | Exception, delay: float = 0):
    """Create a mock scraper with optional delay to simulate network latency.

    Args:
        name: Pharmacy name for the mock scraper.
        results: List of DrugResult to return, or an Exception to raise.
        delay: Seconds to delay before returning (simulates network).

    Returns:
        Mock scraper object with pharmacy_name and search().
    """
    mock = AsyncMock()
    mock.pharmacy_name = name

    async def _search(query, city=None, max_results=10):
        if delay > 0:
            await asyncio.sleep(delay)
        if isinstance(results, Exception):
            raise results
        return results

    mock.search = _search
    return mock


class TestConcurrentExecution:
    """Verify scrapers run concurrently via asyncio.gather()."""

    @pytest.mark.asyncio
    async def test_all_scrapers_results_combined(self):
        """Results from all scrapers are combined into a single list."""
        scrapers = [
            _make_scraper_mock("PharmA", [_make_result("Drug1", "PharmA")]),
            _make_scraper_mock("PharmB", [_make_result("Drug2", "PharmB")]),
            _make_scraper_mock("PharmC", [_make_result("Drug3", "PharmC")]),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            response = await search_drug("test")

        assert response.total == 3
        names = {r.drug_name for r in response.results}
        assert names == {"Drug1", "Drug2", "Drug3"}

    @pytest.mark.asyncio
    async def test_scrapers_run_concurrently_not_sequentially(self):
        """Scrapers with delays run in parallel — total time is max, not sum.

        Three scrapers each take 0.3s. If sequential, total ≥ 0.9s.
        If concurrent, total ≈ 0.3s. We assert < 0.6s to be safe.
        """
        scrapers = [
            _make_scraper_mock("PharmA", [_make_result("Drug1", "PharmA")], delay=0.3),
            _make_scraper_mock("PharmB", [_make_result("Drug2", "PharmB")], delay=0.3),
            _make_scraper_mock("PharmC", [_make_result("Drug3", "PharmC")], delay=0.3),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            loop = asyncio.get_running_loop()
            start = loop.time()
            response = await search_drug("test")
            elapsed = loop.time() - start

        assert response.total == 3
        # If sequential: ≥ 0.9s. If concurrent: ≈ 0.3s.
        assert elapsed < 0.6, f"Scrapers appear sequential — took {elapsed:.2f}s (expected < 0.6s)"

    @pytest.mark.asyncio
    async def test_searched_pharmacies_lists_all_names(self):
        """searched_pharmacies includes all scraper names regardless of success."""
        scrapers = [
            _make_scraper_mock("PharmA", [_make_result("Drug1", "PharmA")]),
            _make_scraper_mock("PharmB", RuntimeError("network down")),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            response = await search_drug("test")

        assert "PharmA" in response.searched_pharmacies
        assert "PharmB" in response.searched_pharmacies


    @pytest.mark.asyncio
    async def test_scraper_returns_empty_list_combined_with_other_results(self):
        """A scraper returning zero results doesn't affect other scrapers' results."""
        scrapers = [
            _make_scraper_mock("PharmA", []),  # succeeds but finds nothing
            _make_scraper_mock("PharmB", [_make_result("Drug1", "PharmB")]),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            response = await search_drug("test")

        assert response.total == 1
        assert response.results[0].pharmacy_name == "PharmB"


class TestErrorIsolation:
    """Verify one scraper failure doesn't affect others."""

    @pytest.mark.asyncio
    async def test_one_scraper_fails_others_succeed(self):
        """A failing scraper doesn't prevent results from other scrapers."""
        scrapers = [
            _make_scraper_mock("PharmA", [_make_result("Drug1", "PharmA")]),
            _make_scraper_mock("PharmB", RuntimeError("connection refused")),
            _make_scraper_mock("PharmC", [_make_result("Drug3", "PharmC")]),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            response = await search_drug("test")

        assert response.total == 2
        names = {r.drug_name for r in response.results}
        assert names == {"Drug1", "Drug3"}

    @pytest.mark.asyncio
    async def test_all_scrapers_fail_returns_empty(self):
        """When all scrapers fail, returns empty results (not an exception)."""
        scrapers = [
            _make_scraper_mock("PharmA", TimeoutError("timeout")),
            _make_scraper_mock("PharmB", RuntimeError("503")),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            response = await search_drug("test")

        assert response.total == 0
        assert response.results == []

    @pytest.mark.asyncio
    async def test_timeout_error_isolated(self):
        """TimeoutError from one scraper doesn't cancel the others."""
        scrapers = [
            _make_scraper_mock("PharmA", TimeoutError("took too long")),
            _make_scraper_mock("PharmB", [
                _make_result("Drug1", "PharmB"),
                _make_result("Drug2", "PharmB"),
            ]),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            response = await search_drug("test")

        assert response.total == 2
        assert all(r.pharmacy_name == "PharmB" for r in response.results)


class TestCacheBypass:
    """Verify concurrent scrapers only run on cache miss."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_scrapers(self):
        """When cache returns results, scrapers are not called."""
        cached = [_make_result("CachedDrug", "Farmatodo")]
        scraper = _make_scraper_mock("PharmA", [_make_result("Live", "PharmA")])

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", [scraper]),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=cached)),
        ):
            response = await search_drug("test")

        assert response.total == 1
        assert response.results[0].drug_name == "CachedDrug"

    @pytest.mark.asyncio
    async def test_results_saved_after_successful_scrape(self):
        """After concurrent scraping, results are saved to cache."""
        scrapers = [
            _make_scraper_mock("PharmA", [_make_result("Drug1", "PharmA")]),
        ]
        save_mock = AsyncMock()

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", save_mock),
        ):
            await search_drug("losartan")

        save_mock.assert_called_once()
        args = save_mock.call_args
        assert args[0][0] == "losartan"  # query
        assert len(args[0][2]) == 1  # results list

    @pytest.mark.asyncio
    async def test_no_save_when_all_fail(self):
        """When all scrapers fail (empty results), nothing is saved to cache."""
        scrapers = [
            _make_scraper_mock("PharmA", RuntimeError("down")),
        ]
        save_mock = AsyncMock()

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", save_mock),
        ):
            await search_drug("test")

        save_mock.assert_not_called()


class TestFailedPharmaciesTracking:
    """Verify failed_pharmacies is populated for scraper exceptions."""

    @pytest.mark.asyncio
    async def test_failed_pharmacies_empty_when_all_succeed(self):
        """No exceptions → failed_pharmacies is empty."""
        scrapers = [
            _make_scraper_mock("PharmA", [_make_result("Drug1", "PharmA")]),
            _make_scraper_mock("PharmB", [_make_result("Drug2", "PharmB")]),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            response = await search_drug("test")

        assert response.failed_pharmacies == []

    @pytest.mark.asyncio
    async def test_failed_pharmacies_lists_only_failed(self):
        """Only scrapers that raised are listed in failed_pharmacies."""
        scrapers = [
            _make_scraper_mock("PharmA", [_make_result("Drug1", "PharmA")]),
            _make_scraper_mock("PharmB", RuntimeError("network down")),
            _make_scraper_mock("PharmC", [_make_result("Drug3", "PharmC")]),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            response = await search_drug("test")

        assert response.failed_pharmacies == ["PharmB"]
        assert response.total == 2

    @pytest.mark.asyncio
    async def test_failed_pharmacies_lists_all_when_all_fail(self):
        """When every scraper fails, failed_pharmacies matches searched_pharmacies."""
        scrapers = [
            _make_scraper_mock("PharmA", TimeoutError("timeout")),
            _make_scraper_mock("PharmB", RuntimeError("503")),
        ]

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", scrapers),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
            patch("farmafacil.services.search.save_search_results", new=AsyncMock()),
        ):
            response = await search_drug("test")

        assert set(response.failed_pharmacies) == {"PharmA", "PharmB"}
        assert response.total == 0

    @pytest.mark.asyncio
    async def test_failed_pharmacies_empty_on_cache_hit(self):
        """Cache hit path does not populate failed_pharmacies."""
        cached = [_make_result("CachedDrug", "Farmatodo")]
        scraper = _make_scraper_mock("PharmA", RuntimeError("should not run"))

        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", [scraper]),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=cached)),
        ):
            response = await search_drug("test")

        assert response.failed_pharmacies == []


class TestEmptyScraperList:
    """Edge case: no active scrapers configured."""

    @pytest.mark.asyncio
    async def test_no_scrapers_returns_empty(self):
        """With no active scrapers and no cache, returns empty results."""
        with (
            patch("farmafacil.services.search.ACTIVE_SCRAPERS", []),
            patch("farmafacil.services.search.get_cached_results", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.search.find_cached_products", new=AsyncMock(return_value=[])),
        ):
            response = await search_drug("test")

        assert response.total == 0
        assert response.results == []
        assert response.searched_pharmacies == []
