"""RTL-1: one adapter per retailer, all behind this interface."""

from typing import Protocol

from pydantic import BaseModel


class Product(BaseModel):
    sku: str
    description: str
    size: str = ""
    price: float | None = None


class Store(BaseModel):
    store_id: str
    name: str
    address: str = ""


class CartResult(BaseModel):
    """Either a filled cart or a link a person opens to finish (RTL-4)."""

    added_skus: list[str]
    checkout_url: str | None = None


class RetailerAdapter(Protocol):
    name: str

    def get_store(self, postal_code: str) -> Store: ...

    def search_product(self, query: str, store: Store) -> list[Product]: ...

    def match_line(
        self, ingredient: str, qty: float, unit: str, store: Store
    ) -> Product | None: ...

    def add_to_cart(self, items: list[tuple[Product, int]]) -> CartResult: ...
