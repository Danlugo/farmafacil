"""Base scraper interface for pharmacy websites."""

import abc
import logging

from farmafacil.models.schemas import DrugResult

logger = logging.getLogger(__name__)


class BaseScraper(abc.ABC):
    """Abstract base class for all pharmacy scrapers."""

    @property
    @abc.abstractmethod
    def pharmacy_name(self) -> str:
        """Human-readable pharmacy name."""
        ...

    @property
    def is_delivery_only(self) -> bool:
        """Whether this pharmacy is delivery-only (no physical stores).

        Delivery-only pharmacies show a 🛵 Delivery label and product URL
        instead of 📍 nearest-store info in search results.
        """
        return False

    @abc.abstractmethod
    async def search(
        self, query: str, city: str | None = None, max_results: int = 10
    ) -> list[DrugResult]:
        """Search for a drug by name and return results.

        Args:
            query: Drug name or partial name to search for.
            city: Optional city for localized pricing/stock.
            max_results: Maximum results to return.

        Returns:
            List of DrugResult with availability and pricing info.
        """
        ...

    def normalize_drug_name(self, name: str) -> str:
        """Normalize a drug name for consistent matching."""
        return name.strip().lower()
