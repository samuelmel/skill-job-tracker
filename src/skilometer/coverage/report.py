"""Relatorio de cobertura (docs/coverage.md e docs/coverage.json) gerado a partir do banco."""

import json
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skilometer.coverage.metadata import PlatformMetadata, load_platform_metadata
from skilometer.database.models import ImplementationStatus, Platform, PlatformVerification

SAMPLE_SIZE = 5
STATUS_ORDER = tuple(ImplementationStatus)


class PlatformCoverage(BaseModel):
    name: str
    slug: str
    category: str
    status: str
    strategy: str | None
    connector_type: str | None
    instances_configured: int
    instances_verified: int
    last_verified_at: datetime | None
    last_attempt_at: datetime | None
    last_attempt_ok: bool | None
    last_error: str | None
    jobs: int | None
    sample_titles: list[str] = Field(default_factory=list)
    sample_urls: list[str] = Field(default_factory=list)
    limitations: str | None


class CoverageReport(BaseModel):
    generated_at: datetime
    total: int
    totals_by_status: dict[str, int]
    platforms: list[PlatformCoverage]


async def build_report(
    session: AsyncSession, metadata: dict[str, PlatformMetadata] | None = None
) -> CoverageReport:
    meta = metadata if metadata is not None else load_platform_metadata()
    platforms = (
        await session.scalars(select(Platform).order_by(Platform.category, Platform.name))
    ).all()
    verifications = (
        await session.scalars(
            select(PlatformVerification).order_by(PlatformVerification.verified_at.desc())
        )
    ).all()
    by_platform: dict[object, list[PlatformVerification]] = {}
    for v in verifications:  # ordem: mais recente primeiro
        by_platform.setdefault(v.platform_id, []).append(v)

    rows: list[PlatformCoverage] = []
    for p in platforms:
        history = by_platform.get(p.id, [])
        latest_per_source: dict[object, PlatformVerification] = {}
        for v in history:
            latest_per_source.setdefault(v.source_id, v)
        evidence = [v for v in latest_per_source.values() if v.success]
        per_source = [list(zip_longest(v.sample_job_titles, v.sample_urls)) for v in evidence]
        # intercala as instancias para a amostra nao vir toda da mesma empresa
        samples = [s for round_ in zip_longest(*per_source) for s in round_ if s is not None]
        last = history[0] if history else None
        item_meta = meta.get(p.slug)
        rows.append(
            PlatformCoverage(
                name=p.name,
                slug=p.slug,
                category=p.category.value,
                status=p.implementation_status.value,
                strategy=p.extraction_strategy,
                connector_type=p.connector_type,
                instances_configured=len(item_meta.example_instances) if item_meta else 0,
                instances_verified=len(evidence),
                last_verified_at=p.last_verified_at,
                last_attempt_at=last.verified_at if last else None,
                last_attempt_ok=last.success if last else None,
                last_error=last.error_message if last and not last.success else None,
                jobs=p.last_verified_job_count,
                sample_titles=[t for t, _ in samples[:SAMPLE_SIZE]],
                sample_urls=[u for _, u in samples[:SAMPLE_SIZE] if u],
                limitations=p.notes,
            )
        )
    totals = {s.value: 0 for s in STATUS_ORDER}
    for row in rows:
        totals[row.status] += 1
    return CoverageReport(
        generated_at=datetime.now(UTC),
        total=len(rows),
        totals_by_status=totals,
        platforms=rows,
    )


def _cell(value: str | None, limit: int = 90) -> str:
    text = " ".join((value or "").split()).replace("|", "\\|")
    return (text[: limit - 1] + "…") if len(text) > limit else (text or "-")


def render_markdown(report: CoverageReport) -> str:
    verified = report.totals_by_status.get(ImplementationStatus.VERIFIED.value, 0)
    lines = [
        "# Cobertura das plataformas",
        "",
        f"Gerado em {report.generated_at:%Y-%m-%d %H:%M} UTC por `skilometer coverage-report`. "
        "So conta como **VERIFIED** a plataforma com execucao real bem-sucedida "
        "(run SUCCEEDED com vagas); fixtures sinteticas e testes mockados nao contam.",
        "",
        f"**{verified} de {report.total} plataformas com evidencia real.**",
        "",
        "## Totais por status",
        "",
        "| Status | Plataformas |",
        "|---|---|",
        *(f"| {s.value} | {report.totals_by_status.get(s.value, 0)} |" for s in STATUS_ORDER),
        "",
        "## O que falta",
        "",
    ]
    pending = [r for r in report.platforms if r.status != ImplementationStatus.VERIFIED.value]
    for status in STATUS_ORDER:
        names = [r.name for r in pending if r.status == status.value]
        if names:
            lines.append(f"- **{status.value}** ({len(names)}): {', '.join(names)}")
    if not pending:
        lines.append("- Nada: todas as plataformas estao VERIFIED.")
    lines += [
        "",
        "## Matriz",
        "",
        "| Plataforma | Categoria | Status | Estrategia | Instancias (ok/config.) | "
        "Ultima verificacao | Vagas | Amostra (evidencia) | Limitacoes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report.platforms:
        if r.last_verified_at:
            last = f"{r.last_verified_at:%Y-%m-%d %H:%M} UTC"
        else:
            last = "nunca"
        if r.last_attempt_ok is False:
            last += f" (ultima tentativa falhou: {_cell(r.last_error, 60)})"
        sample = "; ".join(_cell(t, 60) for t in r.sample_titles) or "-"
        lines.append(
            f"| {_cell(r.name)} | {r.category} | {r.status} | {_cell(r.strategy, 70)} | "
            f"{r.instances_verified}/{r.instances_configured} | {last} | "
            f"{r.jobs if r.jobs is not None else '-'} | {sample} | {_cell(r.limitations, 70)} |"
        )
    return "\n".join(lines) + "\n"


def write_report(report: CoverageReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "coverage.md"
    json_path = output_dir / "coverage.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    payload: dict[str, Any] = report.model_dump(mode="json")
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return md_path, json_path
