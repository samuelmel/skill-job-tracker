"""Catalogo das 78 plataformas monitoradas (58 ATS + 20 job boards)."""

import re
from dataclasses import dataclass

from vaya_indexer.database.models import ImplementationStatus, PlatformCategory

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

# slug da plataforma -> nome do conector registrado em connectors/registry.py
IMPLEMENTED_CONNECTORS = {"greenhouse": "greenhouse"}


@dataclass(frozen=True)
class PlatformEntry:
    name: str
    slug: str
    category: PlatformCategory
    connector_type: str | None
    implementation_status: ImplementationStatus


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _entry(name: str, category: PlatformCategory) -> PlatformEntry:
    slug = slugify(name)
    connector = IMPLEMENTED_CONNECTORS.get(slug)
    status = ImplementationStatus.IMPLEMENTED if connector else ImplementationStatus.NOT_STARTED
    return PlatformEntry(name, slug, category, connector, status)


def build_catalog() -> list[PlatformEntry]:
    return [_entry(n, PlatformCategory.ATS) for n in ATS_NAMES] + [
        _entry(n, PlatformCategory.JOB_BOARD) for n in JOB_BOARD_NAMES
    ]
