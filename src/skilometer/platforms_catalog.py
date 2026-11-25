"""Catalogo das 78 plataformas monitoradas (58 ATS + 20 job boards)."""

import re
from dataclasses import dataclass

from skilometer.coverage.metadata import PlatformMetadata, load_platform_metadata
from skilometer.database.models import ImplementationStatus, PlatformCategory

ATS_NAMES = (
    "ADP, ApplicantPro, Ashby, Avature, BambooHR, BrassRing, Breezy HR, CareerPlug, Comeet, "
    "CSOD, Dayforce, Deel, Dover, Eightfold AI, FirstStage, Freshteam, Gem.com, GoHire, "
    "Greenhouse, HiBob, HireBridge, HireHive, Hireology, HiringThing, iCIMS, iSolvedHire, "
    "JazzHR, Jobvite, JOIN.com, Kula.ai, Lever.co, Manatal, Oracle Recruiting, PageUp, "
    "Paradox, Paycom, Paycor, Paylocity, Personio, Phenom People, Pinpoint, Polymer, Recooty, "
    "Recruitee, Rippling, Rival, SAP SuccessFactors, SmartRecruiters, talentReef, Taleo, "
    "Teamtailor, Trakstar, TriNet, UltiPro, WeRecruit, Workable, Workday, Zoho Recruit"
).split(", ")

JOB_BOARD_NAMES = (
    "Niceboard, WP Job Manager, JobKit, Jobboardly, Jobiqo, JBoard.io, JobBoardFire, "
    "SmartJobBoard, JobBoard.io, Wiley Madgex, Talenetic, JobMount, CareerSiteCloud, "
    "JobBoard.com, Artha, jobboardsoftware.com, jobBoardHQ, Nodex, eJobBoard, "
    "ejobsitesoftware.com"
).split(", ")


@dataclass(frozen=True)
class PlatformEntry:
    name: str
    slug: str
    category: PlatformCategory
    connector_type: str | None
    implementation_status: ImplementationStatus
    docs_url: str | None = None
    extraction_strategy: str | None = None
    notes: str | None = None


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _entry(
    name: str, category: PlatformCategory, metadata: dict[str, PlatformMetadata]
) -> PlatformEntry:
    slug = slugify(name)
    meta = metadata.get(slug)
    if meta is None:
        raise ValueError(f"platforms.json sem entrada para '{slug}'")
    # Sem conector, o status e o da pesquisa (NOT_STARTED|RESEARCHING|BLOCKED).
    status = (
        ImplementationStatus.IMPLEMENTED
        if meta.connector_type
        else ImplementationStatus(meta.research_status.value)
    )
    return PlatformEntry(
        name=name,
        slug=slug,
        category=category,
        connector_type=meta.connector_type,
        implementation_status=status,
        docs_url="\n".join(meta.docs_urls) or None,
        extraction_strategy=meta.extraction_strategy,
        notes="; ".join(meta.limitations) or None,
    )


def build_catalog() -> list[PlatformEntry]:
    metadata = load_platform_metadata()
    return [_entry(n, PlatformCategory.ATS, metadata) for n in ATS_NAMES] + [
        _entry(n, PlatformCategory.JOB_BOARD, metadata) for n in JOB_BOARD_NAMES
    ]
