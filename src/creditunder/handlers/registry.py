from creditunder.domain.enums import ProductType
from creditunder.handlers.base import BaseProductHandler
from creditunder.handlers.personal_finance import PersonalFinanceHandler

_REGISTRY: dict[ProductType, BaseProductHandler] = {
    ProductType.PERSONAL_FINANCE: PersonalFinanceHandler(),
}


def get_handler(product_type: ProductType) -> BaseProductHandler:
    handler = _REGISTRY.get(product_type)
    if handler is None:
        raise ValueError(f"No handler registered for product type: {product_type}")
    return handler
