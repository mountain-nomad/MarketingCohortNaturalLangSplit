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
  denylisted is not exposed.

Generation notes, review notes and free-text docs are never part of it. The semantic
version is computed from exactly the content read, so the two always agree.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from cohortsplit.crawler.sampling import SamplingPolicy
from cohortsplit.semantic.context import Definition
from cohortsplit.semantic.inventory import SchemaInventory, check_definition
from cohortsplit.semantic.version import (
    DocContent,
    EntryContent,
    UseCaseContent,
    compute_semantic_version,
)
from cohortsplit.warehouse.models import ColumnInfo, TableKind, TableRef, TypeCategory

TABLE_SCHEMA = "table_schema"
DATA_PROFILE = "data_profile"
CONFIRMED = "confirmed"

_DEFINITION: TypeAdapter[Definition] = TypeAdapter(Definition)


@dataclass(frozen=True)
class UseCaseRow:
    id: int
    status: str
    nl_request: str
    spec: Mapping[str, Any]
    spec_version: str


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


def parse_definition(raw: Mapping[str, Any]) -> Definition | None:
    """A stored definition, or None if it no longer validates (treated as stale)."""
    try:
        return _DEFINITION.validate_python(dict(raw))
    except ValidationError:
        return None


def entry_is_resolvable(entry: EntryContent, inventory: SchemaInventory) -> bool:
    definition = parse_definition(entry.definition)
    return definition is not None and not check_definition(definition, inventory)


def permitted_samples(
    table_doc: Mapping[str, Any], profile: Mapping[str, Any] | None, policy: SamplingPolicy
) -> dict[str, tuple[str, ...]]:
    """Stored sample values of ``table_doc``'s columns that ``policy`` still permits."""
    if profile is None:
        return {}
    ref = TableRef(
        schema=str(table_doc["schema_name"]),
        name=str(table_doc["name"]),
        kind=cast(TableKind, table_doc.get("kind", "table")),
    )
    columns = {str(c["name"]): c for c in table_doc.get("columns") or ()}
    allowed: dict[str, tuple[str, ...]] = {}
    for column in profile.get("columns") or ():
        name = str(column.get("name"))
        values = column.get("values")
        doc = columns.get(name)
        if not column.get("sampled") or not values or doc is None:
            continue
        info = ColumnInfo(
            name=name,
            data_type=str(doc.get("data_type", "")),
            type_category=cast(TypeCategory, doc.get("type_category", "other")),
            nullable=bool(doc.get("nullable", True)),
            ordinal=0,
        )
        if policy.decide(ref, info).allowed:
            allowed[name] = tuple(str(v) for v in values)
    return allowed


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
    confirmed = sorted((u for u in use_cases if u.status == CONFIRMED), key=lambda u: u.id)

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
    version = compute_semantic_version(
        ordered_entries,
        [UseCaseContent(u.nl_request, u.spec, u.spec_version) for u in confirmed],
        docs,
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
            for u in confirmed
        ),
        tables=tables,
    )
