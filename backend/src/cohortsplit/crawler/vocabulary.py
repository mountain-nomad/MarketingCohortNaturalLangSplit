"""Template vocabulary shared by metadata collection and use-case generation.

Generic role names, not a customer schema (see rulings R4/R5).
"""

PURCHASE_TABLES = ("orders", "order", "purchases", "purchase", "transactions", "transaction")
PRODUCT_TABLES = ("products", "product")
CATEGORY_TABLES = ("categories", "category")
CART_TABLES = ("carts", "cart", "baskets", "basket")

# The only tables whose smallest integer key may be read to fill example use cases.
KEY_EXAMPLE_TABLES: frozenset[str] = frozenset((*PRODUCT_TABLES, *CATEGORY_TABLES))
