"""CLI: seed-platforms, add-source, load-catalog, fetch, fetch-all, list-jobs, verify,
coverage-report.

Migrations: use `alembic upgrade head`.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from collections.abc import Coroutine, Sequence
from pathlib import Path
from typing import Any

from vaya_indexer.core.config import get_settings
from vaya_indexer.core.logging import configure_logging
from vaya_indexer.coverage.report import build_report, write_report
from vaya_indexer.coverage.verification import PlatformResult, VerificationService
from vaya_indexer.database.models import FetchRunStatus
from vaya_indexer.database.repositories.jobs import JobRepository
from vaya_indexer.database.repositories.platforms import PlatformRepository
from vaya_indexer.database.session import create_engine, create_session_factory
from vaya_indexer.extraction.browser import BrowserFetcher
from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.ingestion.fetch_all import fetch_all, list_active_targets
from vaya_indexer.ingestion.service import IngestionService, register_source
from vaya_indexer.lake.raw import RawLakeWriter
from vaya_indexer.platforms_catalog import build_catalog
from vaya_indexer.source_catalog import DEFAULT_CATALOG_PATH, load_catalog, read_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vaya", description="Job Board Indexer")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed-platforms", help="Carrega/atualiza o catalogo das 78 plataformas")

    add = sub.add_parser("add-source", help="Cadastra uma fonte de vagas")
    add.add_argument("--platform", required=True, help="slug da plataforma, ex.: greenhouse")
    add.add_argument("--company", help="nome da empresa dona da fonte")
    add.add_argument("--config", required=True, help='JSON, ex.: \'{"board_token": "acme"}\'')
    add.add_argument("--source-url", help="URL publica da fonte (informativo)")

    fetch = sub.add_parser("fetch", help="Executa a ingestao de uma fonte")
    fetch.add_argument("--source-id", required=True, type=uuid.UUID)

    catalog = sub.add_parser(
        "load-catalog", help="Carrega catalog/sources.json no banco (idempotente)"
    )
    catalog.add_argument("--path", type=Path, default=DEFAULT_CATALOG_PATH)

    fetch_all_cmd = sub.add_parser(
        "fetch-all", help="Ingere todas as sources ativas gravando o raw no lake"
    )
    fetch_all_cmd.add_argument("--platform", help="so esta plataforma (slug)")
    fetch_all_cmd.add_argument("--limit", type=int, help="no maximo N sources")
    fetch_all_cmd.add_argument("--concurrency", type=int, help="sources em paralelo (global)")
    fetch_all_cmd.add_argument("--lake-dir", help="raiz do lake (padrao: LAKE_DIR ou ./data)")
    fetch_all_cmd.add_argument(
        "--summary-json", type=Path, help="grava as contagens da coleta neste JSON"
    )

    listing = sub.add_parser("list-jobs", help="Lista vagas recentes")
    listing.add_argument("--limit", type=int, default=20)

    verify = sub.add_parser(
        "verify", help="Roda o conector contra instancias reais e grava evidencia de cobertura"
    )
    target = verify.add_mutually_exclusive_group(required=True)
    target.add_argument("--platform", help="slug da plataforma, ex.: greenhouse")
    target.add_argument("--all", action="store_true", help="todas com conector")
    verify.add_argument("--concurrency", type=int, default=3, help="instancias em paralelo")

    lake = sub.add_parser("lake", help="Pipeline analitico offline (bronze, dbt, dashboard)")
    lake_sub = lake.add_subparsers(dest="lake_command", required=True)
    for name, text in (
        ("bronze", "raw JSONL -> Parquet normalizado (idempotente)"),
        ("dbt", "dbt build (silver + gold) sobre o bronze"),
        ("dashboard", "gera os HTMLs a partir do gold"),
        ("monthly", "bronze + dbt build + dashboard"),
    ):
        cmd = lake_sub.add_parser(name, help=text)
        cmd.add_argument("--lake-dir", help="raiz do lake (padrao: LAKE_DIR ou ./data)")
        if name in ("bronze", "monthly"):
            cmd.add_argument("--catalog", type=Path, help="catalogo de fontes (config offline)")
            cmd.add_argument(
                "--reprocess", action="store_true", help="refaz o bronze de todas as runs"
            )
        if name in ("dashboard", "monthly"):
            cmd.add_argument("--month", help="mes YYYY-MM do index (padrao: ultimo com dados)")

    report = sub.add_parser("coverage-report", help="Gera docs/coverage.md e docs/coverage.json")
    report.add_argument("--output-dir", type=Path, default=Path("docs"))
    return parser


async def _seed_platforms() -> int:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session:
            entries = build_catalog()
            await PlatformRepository(session).upsert_catalog(entries)
            await session.commit()
        print(f"{len(entries)} plataformas sincronizadas")
    finally:
        await engine.dispose()
    return 0


async def _add_source(args: argparse.Namespace) -> int:
    config = json.loads(args.config)
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session:
            source = await register_source(
                session,
                platform_slug=args.platform,
                company_name=args.company,
                config=config,
                source_url=args.source_url,
            )
            await session.commit()
            print(f"source_id={source.id} external_id={source.external_id}")
    finally:
        await engine.dispose()
    return 0


async def _fetch(source_id: uuid.UUID) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    browser = BrowserFetcher(user_agent=settings.user_agent, timeout=settings.http_timeout)
    try:
        async with HttpClient(
            user_agent=settings.user_agent,
            timeout=settings.http_timeout,
            max_retries=settings.http_max_retries,
            per_domain_concurrency=settings.per_domain_concurrency,
            min_interval=settings.per_domain_min_interval,
        ) as http:
            service = IngestionService(
                create_session_factory(engine), http, settings.policy(), browser
            )
            summary = await service.run_source(source_id)
        print(summary.model_dump_json(indent=2))
        return 0 if summary.status.value != "FAILED" else 1
    finally:
        await browser.aclose()
        await engine.dispose()


async def _load_catalog(path: Path) -> int:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session:
            result = await load_catalog(session, read_catalog(path))
        print(
            f"catalogo: {result.total} entradas, {result.created} criadas, "
            f"{result.existing} ja existiam, {len(result.errors)} com erro"
        )
        for error in result.errors:
            print(f"  ERRO {error}")
        return 1 if result.errors else 0
    finally:
        await engine.dispose()


async def _fetch_all(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    browser = BrowserFetcher(user_agent=settings.user_agent, timeout=settings.http_timeout)
    try:
        factory = create_session_factory(engine)
        targets = await list_active_targets(factory, platform=args.platform, limit=args.limit)
        async with HttpClient(
            user_agent=settings.user_agent,
            timeout=settings.http_timeout,
            max_retries=settings.http_max_retries,
            per_domain_concurrency=settings.per_domain_concurrency,
            min_interval=settings.per_domain_min_interval,
        ) as http:
            service = IngestionService(
                factory,
                http,
                settings.policy(),
                browser,
                raw_writer=RawLakeWriter(args.lake_dir or settings.lake_dir),
            )
            report = await fetch_all(
                service.run_source,
                targets,
                concurrency=args.concurrency or settings.fetch_concurrency,
            )
        for outcome in report.outcomes:
            t = outcome.target
            note = outcome.error or ""
            if outcome.raw_error:
                note = f"{note} RAW FALHOU: {outcome.raw_error}".strip()
            print(
                f"{outcome.status.value:9} {t.platform}/{t.external_id} "
                f"jobs={outcome.jobs_found} {note}".rstrip()
            )
        print(
            f"{len(report.outcomes)} sources: "
            f"{report.count(FetchRunStatus.SUCCEEDED)} SUCCEEDED, "
            f"{report.count(FetchRunStatus.PARTIAL)} PARTIAL, "
            f"{report.count(FetchRunStatus.FAILED)} FAILED; "
            f"{report.jobs_found} vagas; {report.raw_failures} falhas de raw"
        )
        if args.summary_json:
            args.summary_json.write_text(json.dumps(report.summary()), encoding="utf-8")
        return report.exit_code
    finally:
        await browser.aclose()
        await engine.dispose()


async def _list_jobs(limit: int) -> int:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session:
            for job, company in await JobRepository(session).list_recent(limit):
                print(f"{job.title} | {company} | {job.location or '-'} | {job.status.value}")
    finally:
        await engine.dispose()
    return 0


def _print_verification(result: PlatformResult) -> None:
    if result.skipped_reason:
        print(f"{result.slug}: ignorada ({result.skipped_reason})")
        return
    print(f"{result.slug}: {result.status_before.value} -> {result.status_after.value}")
    for inst in result.instances:
        mark = "ok" if inst.success else f"FALHOU ({inst.error})"
        print(f"  - {inst.company}: {inst.jobs_found} vagas, {mark}")
        for title in inst.sample_titles:
            print(f"      * {title}")


async def _verify(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    browser = BrowserFetcher(user_agent=settings.user_agent, timeout=settings.http_timeout)
    try:
        async with HttpClient(
            user_agent=settings.user_agent,
            timeout=settings.http_timeout,
            max_retries=settings.http_max_retries,
            per_domain_concurrency=settings.per_domain_concurrency,
            min_interval=settings.per_domain_min_interval,
        ) as http:
            factory = create_session_factory(engine)
            ingestion = IngestionService(factory, http, settings.policy(), browser)
            service = VerificationService(factory, ingestion)
            if args.all:
                results = await service.verify_all(concurrency=args.concurrency)
            else:
                results = [
                    await service.verify_platform(args.platform, concurrency=args.concurrency)
                ]
        for result in results:
            _print_verification(result)
        attempted = [r for r in results if not r.skipped_reason]
        print(f"{sum(r.verified for r in attempted)}/{len(attempted)} plataformas verificadas")
        return 0 if all(r.verified for r in attempted) else 1
    finally:
        await browser.aclose()
        await engine.dispose()


async def _coverage_report(output_dir: Path) -> int:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session:
            report = await build_report(session)
        md_path, json_path = write_report(report, output_dir)
        print(f"{md_path} e {json_path} gerados ({report.totals_by_status})")
    finally:
        await engine.dispose()
    return 0


def _lake(args: argparse.Namespace) -> int:
    from vaya_indexer.lake.bronze import build_bronze
    from vaya_indexer.lake.dashboard import write_reports
    from vaya_indexer.lake.monthly import MONTHLY_DBT_ARGS, latest_month_with_data, run_monthly
    from vaya_indexer.lake.transform import run_dbt

    lake_dir = Path(args.lake_dir or os.environ.get("LAKE_DIR") or "data")
    match args.lake_command:
        case "bronze":
            res = build_bronze(lake_dir, args.catalog, args.reprocess)
            print(
                f"bronze: {len(res.processed)} runs novas, {len(res.skipped)} ja processadas, "
                f"{len(res.bad)} rejeitadas, {res.rows} linhas, "
                f"{res.normalize_errors} com normalize_error"
            )
            for bad in res.bad:
                print(f"  REJEITADA {bad.path}: {bad.reason}")
            for warning in res.warnings:
                print(f"  AVISO {warning}")
            return 1 if res.bad else 0
        case "dbt":
            print(run_dbt(lake_dir, extra_args=MONTHLY_DBT_ARGS))
            return 0
        case "dashboard":
            month = args.month or latest_month_with_data(lake_dir / "warehouse.duckdb")
            for path in write_reports(lake_dir, month):
                print(path)
            return 0
        case _:
            monthly = run_monthly(lake_dir, args.month, args.catalog, reprocess=args.reprocess)
            print(
                f"mes {monthly.month}: {len(monthly.bronze.processed)} runs novas no bronze, "
                f"{len(monthly.bronze.bad)} rejeitadas"
            )
            for bad in monthly.bronze.bad:
                print(f"  REJEITADA {bad.path}: {bad.reason}")
            for path in monthly.reports:
                print(path)
            return 0


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    # psycopg async nao funciona com o ProactorEventLoop (padrao no Windows).
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "lake":  # offline: nao exige DATABASE_URL
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
        return _lake(args)
    configure_logging(get_settings().log_level)
    match args.command:
        case "seed-platforms":
            return run_async(_seed_platforms())
        case "add-source":
            return run_async(_add_source(args))
        case "load-catalog":
            return run_async(_load_catalog(args.path))
        case "fetch-all":
            return run_async(_fetch_all(args))
        case "fetch":
            return run_async(_fetch(args.source_id))
        case "list-jobs":
            return run_async(_list_jobs(args.limit))
        case "verify":
            return run_async(_verify(args))
        case "coverage-report":
            return run_async(_coverage_report(args.output_dir))
    return 2


if __name__ == "__main__":
    sys.exit(main())
