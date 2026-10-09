"""Data-driven example use cases (FR-2, AC-22, AC-23).

Deterministic templates, no LLM. Each template declares the data it needs; the
generator checks the discovered schema and keeps the template, rewrites it to
the closest supported use case, or drops it, recording why. Every generated use
case is ``pending_review`` and never used until a human confirms it (BR-7).

Table/column roles are recognised by template vocabulary (e.g. a table named
``orders`` or ``purchases``), never by a hardcoded customer schema. Values come
only from the policy-filtered data profile (samples, key examples) or from schema
metadata (CHECK-constraint value lists).
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from cohortsplit.cohort_spec.draft import (
    Aggregate,
    AttributeCondition,
    AttributeTimeWindowCondition,
    Condition,
    DraftCohortSpec,
    EntityRef,
    Join,
    RelatedCondition,
    TimeWindow,
    ValueFilter,
)
from cohortsplit.crawler.catalog import ColumnDoc, TableDoc, WarehouseCatalog
from cohortsplit.crawler.entities import USER_TABLE_NAMES, detect_user_table

UseCaseStatus = Literal["pending_review", "confirmed", "rejected", "needs_rereview"]


@dataclass(frozen=True)
class GeneratedUseCase:
    key: str
    template_key: str
    nl_request: str
    spec: DraftCohortSpec
    rewritten_from: str | None = None
    generation_note: str | None = None
    status: UseCaseStatus = "pending_review"


@dataclass(frozen=True)
class DroppedTemplate:
    template_key: str
    nl_template: str
    reason: str


@dataclass(frozen=True)
class UseCaseGeneration:
    use_cases: tuple[GeneratedUseCase, ...]
    dropped: tuple[DroppedTemplate, ...]


# --- template vocabulary -----------------------------------------------------------------

PURCHASE_TABLES = ("orders", "order", "purchases", "purchase", "transactions", "transaction")
PRODUCT_TABLES = ("products", "product")
CATEGORY_TABLES = ("categories", "category")
CART_TABLES = ("carts", "cart", "baskets", "basket")
LIKE_WORDS = frozenset(
    {"like", "likes", "favorite", "favorites", "favourite", "favourites", "wishlist", "wishlists"}
    | {"reaction", "reactions"}
)
VIEW_WORDS = frozenset({"view", "views", "pageview", "pageviews", "impression", "impressions"})
STATUS_COLUMNS = ("status", "order_status", "cart_status", "state")
ORDER_TIME_COLUMNS = ("ordered_at", "order_date", "purchased_at", "placed_at", "created_at")
SIGNUP_COLUMNS = ("registered_at", "signed_up_at", "signup_at", "joined_at", "created_at")
MONEY_COLUMNS = ("grand_total", "total_amount", "order_total", "total", "amount", "subtotal")
LABEL_COLUMNS = ("name", "title", "label", "slug")
REFERRAL_WORDS = frozenset({"referral", "referrer", "referred", "campaign", "utm", "promo"})
LINE_WORDS = frozenset({"item", "items", "line", "lines"})
MAX_HOPS = 4


class _UnsupportedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _words(name: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", name.lower()) if w}


Hop = tuple[str, str, str]  # (neighbour table, from column, to column), qualified


class _Context:
    def __init__(self, catalog: WarehouseCatalog, user: TableDoc) -> None:
        self.catalog = catalog
        self.user = user
        self.tables = {t.qualified_name: t for t in catalog.tables}
        self.edges: dict[str, list[Hop]] = {name: [] for name in self.tables}
        for table in catalog.tables:
            for fk in table.foreign_keys:
                if len(fk.columns) != 1 or fk.referred_table not in self.tables:
                    continue
                child = f"{table.qualified_name}.{fk.columns[0]}"
                parent = f"{fk.referred_table}.{fk.referred_columns[0]}"
                self.edges[table.qualified_name].append((fk.referred_table, child, parent))
                self.edges[fk.referred_table].append((table.qualified_name, parent, child))
        for hops in self.edges.values():
            hops.sort()

    # -- lookup ----------------------------------------------------------------------------

    @property
    def entity(self) -> EntityRef:
        return EntityRef(table=self.user.qualified_name, key=self.user.primary_key[0])

    def find_table(self, names: Iterable[str]) -> TableDoc | None:
        for name in names:
            for table in self.catalog.tables:
                if table.name.lower() == name and table.qualified_name != self.user.qualified_name:
                    return table
        return None

    def tables_with_words(self, words: frozenset[str]) -> list[TableDoc]:
        return [t for t in self.catalog.tables if _words(t.name) & words]

    def neighbours(self, table: TableDoc) -> list[TableDoc]:
        return [self.tables[n] for n, _, _ in self.edges[table.qualified_name]]

    def values(self, table: TableDoc, column: ColumnDoc) -> tuple[str, ...]:
        """Sampled values (most frequent first), then remaining CHECK-constraint values."""
        sampled = self.catalog.sample_values(table.qualified_name, column.name)
        return tuple(dict.fromkeys((*sampled, *(column.allowed_values or ()))))

    def key_example(self, table: TableDoc) -> str:
        profile = self.catalog.profile(table.qualified_name)
        if profile is None or not profile.key_examples or len(table.primary_key) != 1:
            raise _UnsupportedError(
                f"no policy-permitted example value for {table.qualified_name} "
                "(sampling disabled, key not sampleable, or table empty)"
            )
        return profile.key_examples[0]

    # -- paths -----------------------------------------------------------------------------

    def path(
        self, start: TableDoc, goal: TableDoc, *, prefer: frozenset[str] = frozenset()
    ) -> tuple[Join, ...] | None:
        """Shortest FK path; ties prefer intermediate tables named with ``prefer`` words."""
        best: tuple[tuple[int, int, tuple[str, ...]], tuple[Hop, ...]] | None = None
        stack: list[tuple[str, tuple[Hop, ...]]] = [(start.qualified_name, ())]
        avoid = {self.user.qualified_name} - {start.qualified_name, goal.qualified_name}
        while stack:
            current, hops = stack.pop()
            if current == goal.qualified_name and hops:
                inner = [h[0] for h in hops[:-1]]
                score = sum(1 for name in inner if _words(name.split(".")[1]) & prefer)
                key = (len(hops), -score, tuple(h[0] for h in hops))
                if best is None or key < best[0]:
                    best = (key, hops)
                continue
            if len(hops) >= MAX_HOPS:
                continue
            visited = {start.qualified_name, *(h[0] for h in hops)}
            for hop in self.edges[current]:
                if hop[0] not in visited and hop[0] not in avoid:
                    stack.append((hop[0], (*hops, hop)))
        if best is None:
            return None
        return tuple(Join(from_column=h[1], to_column=h[2]) for h in best[1])

    def direct_path(self, table: TableDoc) -> tuple[Join, ...] | None:
        path = self.path(self.user, table)
        return path if path is not None and len(path) == 1 else None

    def purchase_table(self) -> tuple[TableDoc, tuple[Join, ...]]:
        for name in PURCHASE_TABLES:
            table = self.find_table([name])
            path = self.direct_path(table) if table else None
            if table is not None and path is not None:
                return table, path
        raise _UnsupportedError(f"no purchase/order table linked to {self.user.qualified_name}")

    def required(self, names: tuple[str, ...], what: str) -> TableDoc:
        table = self.find_table(names)
        if table is None:
            raise _UnsupportedError(f"no {what} table (expected one named {'/'.join(names[:2])})")
        return table

    def path_from_purchase(self, goal: TableDoc) -> tuple[TableDoc, tuple[Join, ...]]:
        purchase, to_purchase = self.purchase_table()
        rest = self.path(purchase, goal, prefer=LINE_WORDS)
        if rest is None:
            raise _UnsupportedError(
                f"no relationship path from {purchase.qualified_name} to {goal.qualified_name}"
            )
        return purchase, (*to_purchase, *rest)


def _column(table: TableDoc, names: Iterable[str], categories: set[str]) -> ColumnDoc | None:
    for name in names:
        column = table.column(name)
        if column is not None and column.type_category in categories:
            return column
    return None


def _q(table: TableDoc, column: ColumnDoc | str) -> str:
    name = column if isinstance(column, str) else column.name
    return f"{table.qualified_name}.{name}"


Resolution = tuple[str, Condition, str | None]

# --- templates ---------------------------------------------------------------------------


def _product_via(ctx: _Context, words: frozenset[str], what: str) -> Resolution:
    product = ctx.required(PRODUCT_TABLES, "product")
    for event in ctx.tables_with_words(words):
        to_event = ctx.direct_path(event)
        to_product = ctx.path(event, product)
        if to_event is not None and to_product is not None and len(to_product) <= 2:
            value = ctx.key_example(product)
            condition = RelatedCondition(
                path=(*to_event, *to_product),
                filters=(
                    ValueFilter(
                        column=_q(product, product.primary_key[0]), operator="=", value=value
                    ),
                ),
            )
            return value, condition, None
    raise _UnsupportedError(
        f"no {what} data: no {what} table linked to both "
        f"{ctx.user.qualified_name} and {product.qualified_name}"
    )


def _liked_product(ctx: _Context) -> Resolution:
    value, condition, note = _product_via(ctx, LIKE_WORDS, "likes")
    return f"Users who liked product {value}", condition, note


def _viewed_product(ctx: _Context) -> Resolution:
    value, condition, note = _product_via(ctx, VIEW_WORDS, "product-view")
    return f"Users who viewed product {value}", condition, note


def _purchased_product(ctx: _Context) -> Resolution:
    product = ctx.required(PRODUCT_TABLES, "product")
    _, path = ctx.path_from_purchase(product)
    value = ctx.key_example(product)
    condition = RelatedCondition(
        path=path,
        filters=(
            ValueFilter(column=_q(product, product.primary_key[0]), operator="=", value=value),
        ),
    )
    note = "'Bought' counts any order here; define purchase statuses in business context."
    return f"Users who bought product {value}", condition, note


def _purchased_in_category(ctx: _Context) -> Resolution:
    category = ctx.required(CATEGORY_TABLES, "category")
    _, path = ctx.path_from_purchase(category)
    label = _column(category, LABEL_COLUMNS, {"string"})
    label_values = ctx.catalog.sample_values(category.qualified_name, label.name) if label else ()
    if label is not None and label_values:
        column, value = _q(category, label), label_values[0]
    else:
        column, value = _q(category, category.primary_key[0]), ctx.key_example(category)
    condition = RelatedCondition(
        path=path, filters=(ValueFilter(column=column, operator="=", value=value),)
    )
    return f"Users who bought anything from category {value}", condition, None


def _status_column(ctx: _Context, table: TableDoc) -> tuple[ColumnDoc, tuple[str, ...]]:
    column = _column(table, STATUS_COLUMNS, {"string", "enum"})
    values = ctx.values(table, column) if column else ()
    if column is None or not values:
        raise _UnsupportedError(f"no status column with known values on {table.qualified_name}")
    return column, values


def _orders_with_status(ctx: _Context) -> Resolution:
    orders, path = ctx.purchase_table()
    status, values = _status_column(ctx, orders)
    when = _column(orders, ORDER_TIME_COLUMNS, {"datetime"})
    if when is None:
        raise _UnsupportedError(f"no order timestamp column on {orders.qualified_name}")
    value = values[0]
    condition = RelatedCondition(
        path=path,
        filters=(ValueFilter(column=_q(orders, status), operator="=", value=value),),
        time_window=TimeWindow(column=_q(orders, when), last_days=90),
        aggregate=Aggregate(function="count", operator=">=", value=2),
    )
    nl = f"Users who placed at least 2 orders with status {value} in the last 90 days"
    return nl, condition, None


def _cart_with_status(ctx: _Context) -> Resolution:
    cart = ctx.required(CART_TABLES, "cart")
    path = ctx.direct_path(cart)
    if path is None:
        raise _UnsupportedError(f"{cart.qualified_name} is not linked to {ctx.user.qualified_name}")
    status, values = _status_column(ctx, cart)
    value = "abandoned" if "abandoned" in values else values[0]
    condition = RelatedCondition(
        path=path, filters=(ValueFilter(column=_q(cart, status), operator="=", value=value),)
    )
    sampled = ctx.catalog.sample_values(cart.qualified_name, status.name)
    note = (
        None
        if value in sampled
        else "Value taken from the column's CHECK constraint; no sampled rows have it yet."
    )
    return f"Users who have a cart with status {value}", condition, note


def _attribute_or_related(
    ctx: _Context, matches: Callable[[ColumnDoc], bool]
) -> tuple[ColumnDoc, TableDoc, str, Condition] | None:
    """A matching column with sample values on the user table, else on a directly linked one."""
    candidates = [ctx.user, *sorted(ctx.neighbours(ctx.user), key=lambda t: t.qualified_name)]
    for table in candidates:
        for column in table.columns:
            values = ctx.catalog.sample_values(table.qualified_name, column.name)
            if not matches(column) or not values:
                continue
            value_filter = ValueFilter(column=_q(table, column), operator="=", value=values[0])
            if table is ctx.user:
                return column, table, values[0], AttributeCondition(filter=value_filter)
            path = ctx.direct_path(table)
            if path is not None:
                return (
                    column,
                    table,
                    values[0],
                    RelatedCondition(path=path, filters=(value_filter,)),
                )
    return None


def _from_country(ctx: _Context) -> Resolution:
    found = _attribute_or_related(ctx, lambda c: "country" in c.name.lower())
    if found is None:
        raise _UnsupportedError(
            f"no country column with policy-permitted sample values linked to "
            f"{ctx.user.qualified_name}"
        )
    _, _, value, condition = found
    return f"Users from country {value}", condition, None


def _referral_campaign(ctx: _Context) -> Resolution:
    found = _attribute_or_related(
        ctx, lambda c: c.type_category == "string" and bool(_words(c.name) & REFERRAL_WORDS)
    )
    if found is None:
        raise _UnsupportedError(
            "no referral or campaign data: no column named like referral/campaign/utm/promo "
            f"with sample values linked to {ctx.user.qualified_name}"
        )
    _, _, value, condition = found
    return f"Users who joined through referral campaign {value}", condition, None


def _used_discount(ctx: _Context) -> Resolution:
    orders, path = ctx.purchase_table()
    candidates = [orders, *sorted(ctx.neighbours(orders), key=lambda t: t.qualified_name)]
    for table in candidates:
        column = next(
            (
                c
                for c in table.columns
                if "discount" in c.name.lower() and c.type_category == "numeric"
            ),
            None,
        )
        if column is None:
            continue
        full_path = path if table is orders else ctx.path(ctx.user, table)
        if full_path is None or table.qualified_name == ctx.user.qualified_name:
            continue
        condition = RelatedCondition(
            path=full_path, filters=(ValueFilter(column=_q(table, column), operator=">", value=0),)
        )
        return "Users who placed an order with a discount", condition, None
    raise _UnsupportedError(f"no numeric discount column on {orders.qualified_name} or its lines")


def _spent_more_than(ctx: _Context) -> Resolution:
    orders, path = ctx.purchase_table()
    money = _column(orders, MONEY_COLUMNS, {"numeric"})
    if money is None:
        raise _UnsupportedError(f"no order total/amount column on {orders.qualified_name}")
    condition = RelatedCondition(
        path=path,
        aggregate=Aggregate(function="sum", column=_q(orders, money), operator=">", value=500),
    )
    note = "Which orders count as spending (e.g. statuses) belongs in business context."
    return "Users who spent more than 500 in total", condition, note


def _registered_recently(ctx: _Context) -> Resolution:
    signup = _column(ctx.user, SIGNUP_COLUMNS, {"datetime"})
    if signup is None:
        raise _UnsupportedError(f"no registration timestamp column on {ctx.user.qualified_name}")
    condition = AttributeTimeWindowCondition(
        window=TimeWindow(column=_q(ctx.user, signup), last_days=30)
    )
    return "Users who registered in the last 30 days", condition, None


@dataclass(frozen=True)
class _Template:
    key: str
    nl_template: str
    resolve: Callable[[_Context], Resolution]
    fallback: "_Template | None" = None


_BOUGHT_PRODUCT = _Template("purchased_product", "Users who bought product X", _purchased_product)
_USED_DISCOUNT = _Template(
    "used_discount", "Users who placed an order with a discount", _used_discount
)

TEMPLATES: tuple[_Template, ...] = (
    _Template("liked_product", "Users who liked product X", _liked_product, _BOUGHT_PRODUCT),
    _Template("viewed_product", "Users who viewed product X", _viewed_product),
    _Template(
        "purchased_in_category",
        "Users who bought anything from category X",
        _purchased_in_category,
    ),
    _Template(
        "orders_with_status",
        "Users who placed at least 2 orders with status X in the last 90 days",
        _orders_with_status,
    ),
    _Template("cart_with_status", "Users who have a cart with status X", _cart_with_status),
    _Template("from_country", "Users from country X", _from_country),
    _Template(
        "referral_campaign",
        "Users who joined through referral campaign X",
        _referral_campaign,
        _USED_DISCOUNT,
    ),
    _Template("spent_more_than", "Users who spent more than 500 in total", _spent_more_than),
    _Template(
        "registered_recently", "Users who registered in the last 30 days", _registered_recently
    ),
)


def _build(ctx: _Context, template: _Template) -> GeneratedUseCase | DroppedTemplate:
    try:
        nl, condition, note = template.resolve(ctx)
        spec = DraftCohortSpec(entity=ctx.entity, where=condition)
        return GeneratedUseCase(
            key=template.key,
            template_key=template.key,
            nl_request=nl,
            spec=spec,
            generation_note=note,
        )
    except _UnsupportedError as unsupported:
        fallback = template.fallback
        if fallback is None:
            return DroppedTemplate(template.key, template.nl_template, unsupported.reason)
        try:
            nl, condition, note = fallback.resolve(ctx)
        except _UnsupportedError as also:
            return DroppedTemplate(
                template.key,
                template.nl_template,
                f"{unsupported.reason}; closest supported use case "
                f"'{fallback.nl_template}' is not supported either: {also.reason}",
            )
        generation_note = (
            f"Rewritten from '{template.nl_template}': {unsupported.reason}. "
            f"Closest supported use case: '{fallback.nl_template}'."
        )
        if note:
            generation_note = f"{generation_note} {note}"
        return GeneratedUseCase(
            key=template.key,
            template_key=template.key,
            nl_request=nl,
            spec=DraftCohortSpec(entity=ctx.entity, where=condition),
            rewritten_from=template.nl_template,
            generation_note=generation_note,
        )


def generate_use_cases(
    catalog: WarehouseCatalog, *, user_table: str | None = None
) -> UseCaseGeneration:
    """Deterministic: the same catalog always yields the same use cases, in template order."""
    user = detect_user_table(catalog.tables, user_table)
    if user is None:
        reason = (
            f"user entity table {user_table} not found in the crawled schemas"
            if user_table
            else "no user entity table found (expected a table named "
            f"{'/'.join(USER_TABLE_NAMES[:4])}; set COHORTSPLIT_CRAWLER_USER_TABLE)"
        )
        return UseCaseGeneration(
            use_cases=(),
            dropped=tuple(DroppedTemplate(t.key, t.nl_template, reason) for t in TEMPLATES),
        )
    ctx = _Context(catalog, user)
    built = [_build(ctx, template) for template in TEMPLATES]
    return UseCaseGeneration(
        use_cases=tuple(b for b in built if isinstance(b, GeneratedUseCase)),
        dropped=tuple(b for b in built if isinstance(b, DroppedTemplate)),
    )
