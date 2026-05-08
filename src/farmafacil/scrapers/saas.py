"""Farmacias SAAS drug search via their VTEX Intelligent Search API."""

from farmafacil.scrapers.vtex import VTEXScraper


class SAASScraper(VTEXScraper):
    """Search Farmacias SAAS product catalog via VTEX API.

    Farmacias SAAS (farmaciasaas.com) is a major Venezuelan pharmacy chain
    powered by the VTEX e-commerce platform.  Their Intelligent Search API
    is publicly accessible and requires no authentication.
    """

    base_url = "https://www.farmaciasaas.com"

    @property
    def pharmacy_name(self) -> str:
        return "Farmacias SAAS"
