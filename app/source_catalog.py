"""Curated job-source catalog for configurable discovery.

The catalog is intentionally broad, but the worker still uses read-only search
and public feeds. Adding a site here does not grant bypass access or credentials.
"""

from __future__ import annotations

from typing import Any


KNOWN_JOB_SOURCES: list[dict[str, Any]] = [
    {"name": "Greenhouse", "domains": ["boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"], "kind": "ats", "source_url": "https://boards.greenhouse.io/"},
    {"name": "Lever", "domains": ["jobs.lever.co"], "kind": "ats", "source_url": "https://jobs.lever.co/"},
    {"name": "Ashby", "domains": ["jobs.ashbyhq.com"], "kind": "ats", "source_url": "https://jobs.ashbyhq.com/"},
    {"name": "Workable", "domains": ["jobs.workable.com", "apply.workable.com"], "kind": "ats", "source_url": "https://jobs.workable.com/"},
    {"name": "SmartRecruiters", "domains": ["careers.smartrecruiters.com"], "kind": "ats", "source_url": "https://careers.smartrecruiters.com/"},
    {"name": "Workday", "domains": ["myworkdayjobs.com"], "kind": "ats", "source_url": "https://www.myworkdayjobs.com/"},
    {"name": "iCIMS", "domains": ["icims.com"], "kind": "ats", "source_url": "https://www.icims.com/"},
    {"name": "Taleo", "domains": ["taleo.net"], "kind": "ats", "source_url": "https://taleo.net/"},
    {"name": "Breezy", "domains": ["breezy.hr"], "kind": "ats", "source_url": "https://breezy.hr/"},
    {"name": "JazzHR", "domains": ["applytojob.com", "jazz.co"], "kind": "ats", "source_url": "https://applytojob.com/"},
    {"name": "Recruitee", "domains": ["recruitee.com"], "kind": "ats", "source_url": "https://recruitee.com/"},
    {"name": "Teamtailor", "domains": ["teamtailor.com"], "kind": "ats", "source_url": "https://www.teamtailor.com/"},
    {"name": "Personio", "domains": ["jobs.personio.com"], "kind": "ats", "source_url": "https://jobs.personio.com/"},
    {"name": "LinkedIn Jobs", "domains": ["linkedin.com/jobs"], "kind": "board", "source_url": "https://www.linkedin.com/jobs/"},
    {"name": "Indeed", "domains": ["indeed.com", "indeed.ch"], "kind": "board", "source_url": "https://www.indeed.com/"},
    {"name": "Glassdoor", "domains": ["glassdoor.com"], "kind": "board", "source_url": "https://www.glassdoor.com/Job/"},
    {"name": "Wellfound", "domains": ["wellfound.com/jobs"], "kind": "board", "source_url": "https://wellfound.com/jobs"},
    {"name": "YC Work at a Startup", "domains": ["ycombinator.com/jobs"], "kind": "board", "source_url": "https://www.ycombinator.com/jobs"},
    {"name": "Otta / Welcome to the Jungle", "domains": ["otta.com", "welcometothejungle.com"], "kind": "board", "source_url": "https://www.welcometothejungle.com/en/jobs"},
    {"name": "The Muse", "domains": ["themuse.com/jobs"], "kind": "board", "source_url": "https://www.themuse.com/jobs"},
    {"name": "Dice", "domains": ["dice.com"], "kind": "board", "source_url": "https://www.dice.com/jobs"},
    {"name": "Monster", "domains": ["monster.com"], "kind": "board", "source_url": "https://www.monster.com/jobs"},
    {"name": "ZipRecruiter", "domains": ["ziprecruiter.com"], "kind": "board", "source_url": "https://www.ziprecruiter.com/jobs-search"},
    {"name": "Built In", "domains": ["builtin.com/jobs"], "kind": "board", "source_url": "https://builtin.com/jobs"},
    {"name": "RemoteOK", "domains": ["remoteok.com"], "kind": "remote", "source_url": "https://remoteok.com/"},
    {"name": "Remotive", "domains": ["remotive.com"], "kind": "remote", "source_url": "https://remotive.com/remote-jobs"},
    {"name": "We Work Remotely", "domains": ["weworkremotely.com"], "kind": "remote", "source_url": "https://weworkremotely.com/remote-jobs"},
    {"name": "Remote.co", "domains": ["remote.co/remote-jobs"], "kind": "remote", "source_url": "https://remote.co/remote-jobs/"},
    {"name": "Arbeitnow", "domains": ["arbeitnow.com"], "kind": "europe", "source_url": "https://www.arbeitnow.com/jobs"},
    {"name": "SwissDevJobs", "domains": ["swissdevjobs.ch"], "kind": "switzerland", "source_url": "https://swissdevjobs.ch/jobs/all"},
    {"name": "Jobs.ch", "domains": ["jobs.ch"], "kind": "switzerland", "source_url": "https://www.jobs.ch/en/vacancies/"},
    {"name": "Jobup.ch", "domains": ["jobup.ch"], "kind": "switzerland", "source_url": "https://www.jobup.ch/en/jobs/"},
    {"name": "ICTjobs.ch", "domains": ["ictjobs.ch"], "kind": "switzerland", "source_url": "https://www.ictjobs.ch/"},
    {"name": "EU-Startups Jobs", "domains": ["jobs.eu-startups.com"], "kind": "europe", "source_url": "https://jobs.eu-startups.com/"},
    {"name": "EuroTechJobs", "domains": ["eurotechjobs.com"], "kind": "europe", "source_url": "https://www.eurotechjobs.com/"},
    {"name": "Landing.Jobs", "domains": ["landing.jobs"], "kind": "europe", "source_url": "https://landing.jobs/jobs"},
]


def all_source_domains() -> list[str]:
    domains: list[str] = []
    for source in KNOWN_JOB_SOURCES:
        domains.extend(str(domain) for domain in source.get("domains", []))
    return domains


def all_source_urls() -> list[str]:
    urls: list[str] = []
    for source in KNOWN_JOB_SOURCES:
        url = source.get("source_url")
        if url:
            urls.append(str(url))
    return urls


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
