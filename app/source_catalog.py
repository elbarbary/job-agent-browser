"""Curated job-source catalog for configurable discovery.

The catalog is intentionally broad, but the worker still uses read-only search
and public feeds. Adding a site here does not grant bypass access or credentials.
"""

from __future__ import annotations

from typing import Any


KNOWN_JOB_SOURCES: list[dict[str, Any]] = [
    {"name": "Greenhouse", "domains": ["boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"], "kind": "ats"},
    {"name": "Lever", "domains": ["jobs.lever.co"], "kind": "ats"},
    {"name": "Ashby", "domains": ["jobs.ashbyhq.com"], "kind": "ats"},
    {"name": "Workable", "domains": ["jobs.workable.com", "apply.workable.com"], "kind": "ats"},
    {"name": "SmartRecruiters", "domains": ["careers.smartrecruiters.com"], "kind": "ats"},
    {"name": "Workday", "domains": ["myworkdayjobs.com"], "kind": "ats"},
    {"name": "iCIMS", "domains": ["icims.com"], "kind": "ats"},
    {"name": "Taleo", "domains": ["taleo.net"], "kind": "ats"},
    {"name": "Breezy", "domains": ["breezy.hr"], "kind": "ats"},
    {"name": "JazzHR", "domains": ["applytojob.com", "jazz.co"], "kind": "ats"},
    {"name": "Recruitee", "domains": ["recruitee.com"], "kind": "ats"},
    {"name": "Teamtailor", "domains": ["teamtailor.com"], "kind": "ats"},
    {"name": "Personio", "domains": ["jobs.personio.com"], "kind": "ats"},
    {"name": "LinkedIn Jobs", "domains": ["linkedin.com/jobs"], "kind": "board"},
    {"name": "Indeed", "domains": ["indeed.com", "indeed.ch"], "kind": "board"},
    {"name": "Glassdoor", "domains": ["glassdoor.com"], "kind": "board"},
    {"name": "Wellfound", "domains": ["wellfound.com/jobs"], "kind": "board"},
    {"name": "YC Work at a Startup", "domains": ["ycombinator.com/jobs"], "kind": "board"},
    {"name": "Otta / Welcome to the Jungle", "domains": ["otta.com", "welcometothejungle.com"], "kind": "board"},
    {"name": "The Muse", "domains": ["themuse.com/jobs"], "kind": "board"},
    {"name": "Dice", "domains": ["dice.com"], "kind": "board"},
    {"name": "Monster", "domains": ["monster.com"], "kind": "board"},
    {"name": "ZipRecruiter", "domains": ["ziprecruiter.com"], "kind": "board"},
    {"name": "Built In", "domains": ["builtin.com/jobs"], "kind": "board"},
    {"name": "RemoteOK", "domains": ["remoteok.com"], "kind": "remote"},
    {"name": "Remotive", "domains": ["remotive.com"], "kind": "remote"},
    {"name": "We Work Remotely", "domains": ["weworkremotely.com"], "kind": "remote"},
    {"name": "Remote.co", "domains": ["remote.co/remote-jobs"], "kind": "remote"},
    {"name": "Arbeitnow", "domains": ["arbeitnow.com"], "kind": "europe"},
    {"name": "SwissDevJobs", "domains": ["swissdevjobs.ch"], "kind": "switzerland"},
    {"name": "Jobs.ch", "domains": ["jobs.ch"], "kind": "switzerland"},
    {"name": "Jobup.ch", "domains": ["jobup.ch"], "kind": "switzerland"},
    {"name": "ICTjobs.ch", "domains": ["ictjobs.ch"], "kind": "switzerland"},
    {"name": "EU-Startups Jobs", "domains": ["jobs.eu-startups.com"], "kind": "europe"},
    {"name": "EuroTechJobs", "domains": ["eurotechjobs.com"], "kind": "europe"},
    {"name": "Landing.Jobs", "domains": ["landing.jobs"], "kind": "europe"},
]


def all_source_domains() -> list[str]:
    domains: list[str] = []
    for source in KNOWN_JOB_SOURCES:
        domains.extend(str(domain) for domain in source.get("domains", []))
    return domains


def source_names() -> list[str]:
    return [str(source["name"]) for source in KNOWN_JOB_SOURCES]


def domains_for_names(names: list[str] | None) -> list[str]:
    if not names:
        return all_source_domains()
    wanted = {name.casefold() for name in names}
    domains: list[str] = []
    for source in KNOWN_JOB_SOURCES:
        if str(source["name"]).casefold() in wanted:
            domains.extend(str(domain) for domain in source.get("domains", []))
    return domains or all_source_domains()
