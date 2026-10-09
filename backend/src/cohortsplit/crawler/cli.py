"""``cohortsplit crawl`` subcommand.

Exit codes: 0 success, 1 crawl/appdb failure (previous content intact),
2 configuration error. Output never contains credentials.
"""

import argparse
import json
import logging
import sys
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from cohortsplit.audit.service import Actor, AuditService
from cohortsplit.auth.clock import utcnow
from cohortsplit.auth.crawler_integration import AuditCrawlHook, RoleExportGrants
from cohortsplit.config import ConfigError, load_settings
from cohortsplit.crawler.service import CrawlFailedError, CrawlReport, run_crawl
from cohortsplit.crawler.settings import load_crawler_settings
from cohortsplit.crawler.store import CrawlStore
from cohortsplit.db import create_appdb_engine, describe_location
from cohortsplit.semantic.crawl import CrawlInProgressError, exclusive_crawl, record_crawl_version
from cohortsplit.warehouse.errors import WarehouseNotConfiguredError
from cohortsplit.warehouse.factory import create_warehouse_adapter


def register(subparsers: "argparse._SubParsersAction[Any]") -> None:
    parser = subparsers.add_parser(
        "crawl",
        help="crawl the configured warehouse and store generated docs and example use cases",
        description=(
            "Crawl the configured warehouse (read-only) and replace generated schema docs, "
            "data profiles and pending example use cases. Human-authored content and "
            "reviewed use cases are preserved."
        ),
    )
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.set_defaults(handler=run)


def _configure_logging(level: str) -> None:
    logger = logging.getLogger("cohortsplit")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)


def _as_json(report: CrawlReport) -> dict[str, Any]:
    return {
        "status": "succeeded",
        "run_id": report.run_id,
        "location": report.location,
        "tables": report.table_count,
        "columns": report.column_count,
        "sampled_columns": list(report.sampled_columns),
        "use_cases": [
            {
                "key": u.key,
                "nl_request": u.nl_request,
                "status": u.status,
                "rewritten_from": u.rewritten_from,
                "generation_note": u.generation_note,
            }
            for u in report.use_cases
        ],
        "dropped": [
            {"template_key": d.template_key, "nl_template": d.nl_template, "reason": d.reason}
            for d in report.dropped
        ],
        "inserted_use_cases": report.inserted_use_cases,
        "preserved_keys": list(report.preserved_keys),
        "flagged": [
            {"id": f.id, "nl_request": f.nl_request, "missing": list(f.missing)}
            for f in report.flagged
        ],
        "content_hash": report.content_hash,
    }


def _print_human(report: CrawlReport) -> None:
    rewritten = sum(1 for u in report.use_cases if u.rewritten_from is not None)
    lines = [
        f"Crawled {report.location} (run {report.run_id}): {report.table_count} tables, "
        f"{report.column_count} columns, {len(report.sampled_columns)} sampled columns.",
        f"Example use cases: {len(report.use_cases)} generated, all pending review "
        f"({rewritten} rewritten, {len(report.dropped)} dropped); "
        f"{report.inserted_use_cases} stored, {len(report.preserved_keys)} reviewed kept.",
    ]
    for u in report.use_cases:
        suffix = f'  (rewritten from "{u.rewritten_from}")' if u.rewritten_from else ""
        lines.append(f"  - [{u.key}] {u.nl_request}{suffix}")
    for d in report.dropped:
        lines.append(f"  x [{d.template_key}] dropped: {d.reason}")
    lines.append(f"Flagged for re-review: {len(report.flagged)}")
    for f in report.flagged:
        lines.append(f"  ! #{f.id} {f.nl_request}: missing {', '.join(f.missing)}")
    lines.append(f"Content hash: {report.content_hash}")
    print("\n".join(lines))


def run(args: argparse.Namespace) -> int:
    try:
        settings = load_settings()
        crawler_settings = load_crawler_settings()
        adapter = create_warehouse_adapter(settings)
    except (ConfigError, WarehouseNotConfiguredError) as exc:
        print(f"crawl: configuration error: {exc}", file=sys.stderr)
        return 2
    _configure_logging(settings.log_level)

    engine = create_appdb_engine(settings)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    try:
        # Same lock as the API: one crawl at a time across the CLI and every app worker.
        with exclusive_crawl(engine):
            report = run_crawl(
                adapter,
                CrawlStore(engine),
                settings=crawler_settings,
                # Export-granted columns are never sampled (ruling R2); runs are audited.
                export_grants=RoleExportGrants(session_factory),
                audit=AuditCrawlHook(AuditService(session_factory), Actor.cli()),
                triggered_by="cli",
            )
            # The crawl may have changed the semantic version (generated docs, flags).
            try:
                record_crawl_version(
                    session_factory, report.run_id, actor_user_id=None, now=utcnow()
                )
            except SQLAlchemyError:
                # The crawl itself is committed; the version is still computed from content,
                # only this history row is missing. Say so instead of claiming failure.
                print(
                    "warning: crawl succeeded but its semantic version could not be recorded "
                    "in the history.",
                    file=sys.stderr,
                )
    except CrawlInProgressError:
        print(
            "crawl refused: another crawl is running (API or CLI). Nothing was changed; "
            "try again when it has finished.",
            file=sys.stderr,
        )
        return 1
    except CrawlFailedError as exc:
        print(f"crawl failed: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print(
            f"crawl failed: Application database is unreachable at {describe_location(engine.url)} "
            "or not migrated. Check COHORTSPLIT_APPDB_* and run `alembic upgrade head`. "
            "Nothing was changed.",
            file=sys.stderr,
        )
        return 1
    finally:
        engine.dispose()

    if args.json:
        print(json.dumps(_as_json(report), indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0
