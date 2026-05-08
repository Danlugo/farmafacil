"""Locatel drug search via their VTEX Intelligent Search API."""

from farmafacil.scrapers.vtex import VTEXScraper


class LocatelScraper(VTEXScraper):
    """Search Locatel product catalog via VTEX API.

    Locatel (locatel.com.ve) is one of Venezuela's largest pharmacy/retail
    chains, powered by the VTEX e-commerce platform.  Their Intelligent Search
    API is publicly accessible and requires no authentication.
    """

    base_url = "https://www.locatel.com.ve"

    @property
    def pharmacy_name(self) -> str:
        return "Locatel"
