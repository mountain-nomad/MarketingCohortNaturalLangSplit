"""The LLM-facing semantic context snapshot (BR-7, AC-24).

:func:`assemble_snapshot` is the single place that decides what may influence cohort
interpretation. It admits only:

* **confirmed** use cases (pending, rejected and ``needs_rereview`` never);
* human business context whose references resolve in the latest crawl (stale entries are
  listed by key, never silently used);
* raw schema metadata from the generated ``table_schema`` docs (tables, columns, types,
  keys, CHECK-constraint values, row counts);
* sample values that the **current** sampling policy still permits (sampling switch,
  denylist, export grants). A value sampled before a column was export-granted or
  denylisted is not exposed. The same holds for literals the crawler copied into a
  *generated* use case (a sampled value or a key example): if today's policy forbids
  that column's values, the use case is **withheld** (listed by id, never sent).

Generation notes, review notes and free-text docs are never part of it. The semantic
version is computed by :func:`effective_version` from exactly the content the snapshot
exposes (policy-filtered samples, non-withheld confirmed use cases), so the two always
agree and a policy change that alters the LLM context also changes the version.
"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from cohortsplit.crawler.sampling import SamplingPolicy
from cohortsplit.semantic.context import Definition
from cohortsplit.semantic.inventory import SchemaInventory, check_definition
from cohortsplit.semantic.version import (
    DocContent,
    EntryContent,
    SemanticVersion,
    UseCaseContent,
    compute_semantic_version,
)
from cohortsplit.warehouse.models import ColumnInfo, TableKind, TableRef, TypeCategory

TABLE_SCHEMA = "table_schema"
DATA_PROFILE = "data_profile"
CONFIRMED = "confirmed"
GENERATED = "generated"
# Filter operators whose literal is a concrete data value (sampled value or key example
# when generated); thresholds such as ``> 0`` are template constants, not data.
LITERAL_OPERATORS = frozenset({"=", "!=", "in", "not_in"})

_DEFINITION: TypeAdapter[Definition] = TypeAdapter(Definition)


@dataclass(frozen=True)
class UseCaseRow:
    id: int
    status: str
    nl_request: str
    spec: Mapping[str, Any]
    spec_version: str
    # Fail closed: a row of unknown origin is checked like a generated one.
    origin: str = GENERATED


@dataclass(frozen=True)
class PromptUseCase:
    id: int
    nl_request: str
    spec: Mapping[str, Any]
    spec_version: str


@dataclass(frozen=True)
class PromptBusinessContext:
    key: str
    kind: str
    synonyms: tuple[str, ...]
    description: str
    definition: Mapping[str, Any]


@dataclass(frozen=True)
class PromptColumn:
    name: str
    data_type: str
    nullable: bool
    comment: str | None
    allowed_values: tuple[str, ...] | None
    sample_values: tuple[str, ...]


@dataclass(frozen=True)
class PromptForeignKey:
    columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]


@dataclass(frozen=True)
class PromptTable:
    qualified_name: str
    kind: str
    comment: str | None
    columns: tuple[PromptColumn, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[PromptForeignKey, ...]
    estimated_row_count: int | None


@dataclass(frozen=True)
class SemanticContextSnapshot:
    semantic_version: str
    canonical_user_id: str | None
    business_context: tuple[PromptBusinessContext, ...]
    stale_business_context: tuple[str, ...]
    confirmed_use_cases: tuple[PromptUseCase, ...]
    tables: tuple[PromptTable, ...]
    # Confirmed generated use cases left out because today's policy forbids a literal.
    withheld_use_cases: tuple[int, ...] = ()


def parse_definition(raw: Mapping[str, Any]) -> Definition | None:
    """A stored definition, or None if it no longer validates (treated as stale)."""
    try:
        return _DEFINITION.validate_python(dict(raw))
    except ValidationError:
        return None


def entry_is_resolvable(entry: EntryContent, inventory: SchemaInventory) -> bool:
    definition = parse_definition(entry.definition)
    return definition is not None and not check_definition(definition, inventory)


def _table_ref(table_doc: Mapping[str, Any]) -> TableRef:
    return TableRef(
        schema=str(table_doc["schema_name"]),
        name=str(table_doc["name"]),
        kind=cast(TableKind, table_doc.get("kind", "table")),
    )


def _column_info(doc: Mapping[str, Any]) -> ColumnInfo:
    return ColumnInfo(
        name=str(doc["name"]),
        data_type=str(doc.get("data_type", "")),
        type_category=cast(TypeCategory, doc.get("type_category", "other")),
        nullable=bool(doc.get("nullable", True)),
        ordinal=0,
    )


def permitted_samples(
    table_doc: Mapping[str, Any], profile: Mapping[str, Any] | None, policy: SamplingPolicy
) -> dict[str, tuple[str, ...]]:
    """Stored sample values of ``table_doc``'s columns that ``policy`` still permits."""
    if profile is None:
        return {}
    ref = _table_ref(table_doc)
    columns = {str(c["name"]): c for c in table_doc.get("columns") or ()}
    allowed: dict[str, tuple[str, ...]] = {}
    for column in profile.get("columns") or ():
        name = str(column.get("name"))
        values = column.get("values")
        doc = columns.get(name)
        if not column.get("sampled") or not values or doc is None:
            continue
        if policy.decide(ref, _column_info(doc)).allowed:
            allowed[name] = tuple(str(v) for v in values)
    return allowed


def _key_examples_permitted(table_doc: Mapping[str, Any], policy: SamplingPolicy) -> bool:
    primary_key = tuple(table_doc.get("primary_key") or ())
    columns = {str(c["name"]): c for c in table_doc.get("columns") or ()}
    if len(primary_key) != 1 or primary_key[0] not in columns:
        return False
    return policy.decide_key_example(
        _table_ref(table_doc), _column_info(columns[primary_key[0]])
    ).allowed


def _tables_by_name(docs: Iterable[DocContent]) -> dict[str, Mapping[str, Any]]:
    return {
        f"{d.content['schema_name']}.{d.content['name']}": d.content
        for d in docs
        if d.kind == TABLE_SCHEMA
    }


def policy_filtered_docs(docs: Iterable[DocContent], policy: SamplingPolicy) -> list[DocContent]:
    """``docs`` with every stored sample and key example today's policy forbids removed.

    Identity when the policy still permits everything stored, so a re-check under the
    crawl-time policy does not change the version.
    """
    docs = list(docs)
    tables = _tables_by_name(docs)
    result: list[DocContent] = []
    for doc in docs:
        table = tables.get(str(doc.content.get("table", "")))
        if doc.kind != DATA_PROFILE or table is None:
            result.append(doc)
            continue
        permitted = permitted_samples(table, doc.content, policy)
        content = dict(doc.content)
        content["columns"] = [
            column
            if not column.get("sampled")
            or not column.get("values")
            or column.get("name") in permitted
            else {
                **column,
                "sampled": False,
                "values": None,
                "reason": "withheld by current policy",
            }
            for column in doc.content.get("columns") or ()
        ]
        if content.get("key_examples") and not _key_examples_permitted(table, policy):
            content["key_examples"] = []
        result.append(DocContent(doc.doc_key, doc.kind, content))
    return result


def _literal_filters(node: Any) -> Iterator[Mapping[str, Any]]:
    """Every ``{column, operator, value}`` filter in a raw spec (aggregates excluded)."""
    if isinstance(node, Mapping):
        if "function" not in node and {"column", "operator", "value"} <= node.keys():
            yield node
        for child in node.values():
            yield from _literal_filters(child)
    elif isinstance(node, list | tuple):
        for child in node:
            yield from _literal_filters(child)


def _literal_permitted(
    column: str,
    values: Iterable[Any],
    tables: Mapping[str, Mapping[str, Any]],
    policy: SamplingPolicy,
) -> bool:
    table_name, _, name = column.rpartition(".")
    table = tables.get(table_name)
    docs = {str(c["name"]): c for c in (table or {}).get("columns") or ()}
    if table is None or name not in docs:
        # Column gone: not a policy question. The crawl that removed it flags confirmed
        # use cases for re-review (AC-26), and the reviewer must still see what to fix.
        return True
    allowed_values = {str(v) for v in docs[name].get("allowed_values") or ()}
    if all(str(v) in allowed_values for v in values):
        return True  # CHECK-constraint values are schema metadata, not samples
    if policy.decide(_table_ref(table), _column_info(docs[name])).allowed:
        return True
    return tuple(table.get("primary_key") or ()) == (name,) and _key_examples_permitted(
        table, policy
    )


def withheld_reason(
    use_case: UseCaseRow, tables: Mapping[str, Mapping[str, Any]], policy: SamplingPolicy
) -> str | None:
    """Why a generated use case's literals may not be shown or sent today, or None.

    Human-authored use cases carry the reviewer's own literals and are never withheld.
    """
    if use_case.origin != GENERATED:
        return None
    for literal in _literal_filters(use_case.spec):
        if literal.get("operator") not in LITERAL_OPERATORS:
            continue
        value = literal.get("value")
        values = value if isinstance(value, list | tuple) else [value]
        column = str(literal.get("column"))
        if not _literal_permitted(column, values, tables, policy):
            return (
                f"Its example value comes from {column}, whose values the current sampling "
                "policy no longer permits (export grant, denylist or sampling switch). "
                "Rewrite it or re-run the crawler."
            )
    return None


def withheld_reasons(
    use_cases: Iterable[UseCaseRow], docs: Iterable[DocContent], policy: SamplingPolicy
) -> dict[int, str]:
    tables = _tables_by_name(docs)
    reasons = {u.id: withheld_reason(u, tables, policy) for u in use_cases}
    return {i: r for i, r in reasons.items() if r is not None}


def effective_version(
    *,
    entries: Iterable[EntryContent],
    use_cases: Iterable[UseCaseRow],
    generated_docs: Iterable[DocContent],
    policy: SamplingPolicy,
) -> SemanticVersion:
    """The semantic version of exactly what :func:`assemble_snapshot` exposes."""
    docs = list(generated_docs)
    confirmed = [u for u in use_cases if u.status == CONFIRMED]
    withheld = withheld_reasons(confirmed, docs, policy)
    return compute_semantic_version(
        entries,
        [
            UseCaseContent(u.nl_request, u.spec, u.spec_version)
            for u in confirmed
            if u.id not in withheld
        ],
        policy_filtered_docs(docs, policy),
    )


def _prompt_table(
    content: Mapping[str, Any], profile: Mapping[str, Any] | None, policy: SamplingPolicy
) -> PromptTable:
    samples = permitted_samples(content, profile, policy)
    columns = tuple(
        PromptColumn(
            name=str(c["name"]),
            data_type=str(c.get("data_type", "")),
            nullable=bool(c.get("nullable", True)),
            comment=c.get("comment"),
            allowed_values=(
                tuple(str(v) for v in c["allowed_values"])
                if c.get("allowed_values") is not None
                else None
            ),
            sample_values=samples.get(str(c["name"]), ()),
        )
        for c in content.get("columns") or ()
    )
    foreign_keys = tuple(
        PromptForeignKey(
            columns=tuple(fk.get("columns") or ()),
            referred_table=str(fk.get("referred_table")),
            referred_columns=tuple(fk.get("referred_columns") or ()),
        )
        for fk in content.get("foreign_keys") or ()
    )
    row_count = profile.get("estimated_row_count") if profile is not None else None
    return PromptTable(
        qualified_name=f"{content['schema_name']}.{content['name']}",
        kind=str(content.get("kind", "table")),
        comment=content.get("comment"),
        columns=columns,
        primary_key=tuple(content.get("primary_key") or ()),
        foreign_keys=foreign_keys,
        estimated_row_count=row_count if isinstance(row_count, int) else None,
    )


def assemble_snapshot(
    *,
    entries: Iterable[EntryContent],
    use_cases: Iterable[UseCaseRow],
    generated_docs: Iterable[DocContent],
    policy: SamplingPolicy,
) -> SemanticContextSnapshot:
    ordered_entries = sorted(entries, key=lambda e: e.key)
    docs = list(generated_docs)
    rows = list(use_cases)
    confirmed = sorted((u for u in rows if u.status == CONFIRMED), key=lambda u: u.id)
    withheld = withheld_reasons(confirmed, docs, policy)
    visible = [u for u in confirmed if u.id not in withheld]

    schemas = {d.doc_key: d.content for d in docs if d.kind == TABLE_SCHEMA}
    profiles = {d.doc_key: d.content for d in docs if d.kind == DATA_PROFILE}
    inventory = SchemaInventory.from_table_docs(schemas.values())

    usable: list[PromptBusinessContext] = []
    stale: list[str] = []
    canonical: str | None = None
    for entry in ordered_entries:
        if not entry_is_resolvable(entry, inventory):
            stale.append(entry.key)
            continue
        usable.append(
            PromptBusinessContext(
                key=entry.key,
                kind=entry.kind,
                synonyms=tuple(entry.synonyms),
                description=entry.description,
                definition=entry.definition,
            )
        )
        if entry.kind == "canonical_user_id":
            canonical = str(entry.definition["column"])

    tables = tuple(
        sorted(
            (_prompt_table(content, profiles.get(key), policy) for key, content in schemas.items()),
            key=lambda t: t.qualified_name,
        )
    )
    version = effective_version(
        entries=ordered_entries, use_cases=rows, generated_docs=docs, policy=policy
    )
    return SemanticContextSnapshot(
        semantic_version=version.version,
        canonical_user_id=canonical,
        business_context=tuple(usable),
        stale_business_context=tuple(stale),
        confirmed_use_cases=tuple(
            PromptUseCase(
                id=u.id, nl_request=u.nl_request, spec=u.spec, spec_version=u.spec_version
            )
            for u in visible
        ),
        tables=tables,
        withheld_use_cases=tuple(sorted(withheld)),
    )
