#!/usr/bin/env python3
"""Read public employer ATS boards and prepare evidence-based review artifacts.

No account login, application submission, private API, browser automation, or
gross-to-net salary estimation is performed. Python 3.11+; standard library only.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

UTC = timezone.utc
PROVIDERS = {"greenhouse", "lever", "ashby"}
API_HOSTS = {"boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com"}
ROLE_WORDS = ("legal", "counsel", "lawyer", "attorney", "privacy", "compliance", "regulatory", "governance", "policy", "法务", "合规", "法律")
LANGUAGE_WORDS = ("korean", "english", "mandarin", "chinese", "bilingual", "multilingual", "한국어", "영어", "韩语", "英语", "韩文")
AI_WORDS = ("AI", "artificial intelligence", "machine learning", "generative AI", "LLM", "人工智能")
EXPERIENCE_RE = re.compile(
    r"(?P<minimum>\d+(?:\.\d+)?)\s*(?:[-–]\s*\d+(?:\.\d+)?)?\s*(?:\+|or more)?\s*"
    r"(?:years?|yrs?)[’']?\s*(?:of\s+)?(?:"
    r"(?:post[-\s]qualifi(?:cation|ed)|PQE)(?:\s+experience)?|"
    r"(?:(?:relevant|employment|corporate|commercial|privacy|data|protection|and|compliance|in[-\s]house|professional|legal|law|firm|regulatory|policy|related|work|litigation|transactional|IP|operations|technology|technical)\s+){0,8}experience|"
    r"practi[sc]ing\s+law|(?:as\s+an?\s+|in\s+)(?:lawyer|attorney|counsel|legal))", re.IGNORECASE
)


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain(value: Any) -> str:
    text = str(value or "")
    # Some public boards encode their HTML as entities within a JSON string.
    # A bounded second pass strips those tags instead of preserving tag markup.
    for _ in range(3):
        parser = TextParser()
        parser.feed(text)
        cleaned = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
        if cleaned == text:
            break
        text = cleaned
    return text


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def valid_public_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def words_in(text: str, words: tuple[str, ...] | list[str]) -> list[str]:
    # Boundaries prevent "AI" matching "training" or "policy" matching "policymaker".
    return [word for word in words if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.IGNORECASE)
            or (not word.isascii() and word.casefold() in text.casefold())]


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.hostname not in API_HOSTS:
            raise ValueError("Provider redirected outside supported HTTPS public API hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_json(url: str) -> Any:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in API_HOSTS:
        raise ValueError("Unsupported public API destination")
    request = Request(url, headers={"User-Agent": "ApplyAgentPublicJobDiscovery/1.0", "Accept": "application/json"}, method="GET")
    # System TLS certificate and hostname verification stay enabled.
    with build_opener(SafeRedirects()).open(request, timeout=30) as response:
        data = response.read(15_000_001)
    if len(data) > 15_000_000:
        raise ValueError("Provider response exceeds the 15 MB limit")
    return json.loads(data)


def source_endpoint(source: dict[str, Any]) -> str:
    provider, board = source.get("provider"), source.get("board", "")
    if provider not in PROVIDERS or not isinstance(board, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", board):
        raise ValueError("Source needs a supported provider and a plain board identifier")
    if provider == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    if provider == "lever":
        return f"https://api.lever.co/v0/postings/{board}?mode=json"
    return f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"


def normalize(source: dict[str, Any], payload: Any, fetched_at: str) -> list[dict[str, Any]]:
    provider = source["provider"]
    if provider == "lever":
        if not isinstance(payload, list):
            raise ValueError("Lever payload is not a postings list")
        items = payload
    else:
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError(f"{provider} payload has no jobs list")
        items = payload["jobs"]
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Provider returned a malformed job record")
        if provider == "ashby" and item.get("isListed") is False:
            continue
        identity = item.get("id")
        title = item.get("text") if provider == "lever" else item.get("title")
        url = item.get({"greenhouse": "absolute_url", "lever": "hostedUrl", "ashby": "jobUrl"}[provider])
        if identity is None or not str(title or "").strip() or not valid_public_url(url):
            raise ValueError("Provider job lacks a stable ID, title, or public HTTPS job URL")
        if provider == "greenhouse":
            location = plain((item.get("location") or {}).get("name"))
            description = plain(item.get("content"))
            workplace = "unknown"
            published = None  # Greenhouse updated_at is not the original posting date.
            updated = item.get("updated_at")
        elif provider == "lever":
            location = plain((item.get("categories") or {}).get("location"))
            sections = [item.get("description"), item.get("additional")]
            sections.extend(f"{entry.get('text', '')} {entry.get('content', '')}" for entry in item.get("lists", []) if isinstance(entry, dict))
            description = plain(" ".join(str(part or "") for part in sections))
            workplace = plain(item.get("workplaceType")) or "unknown"
            published = item.get("createdAt")
            updated = None
        else:
            locations = [item.get("location", "")]
            locations.extend(entry.get("location", "") for entry in item.get("secondaryLocations", []) if isinstance(entry, dict))
            location = plain("; ".join(str(part) for part in locations if part))
            description = plain(item.get("descriptionPlain") or item.get("descriptionHtml"))
            workplace = plain(item.get("workplaceType")) or "unknown"
            published, updated = item.get("publishedAt"), None
        job_id = f"{provider}:{source['board']}:{identity}"
        result.append({"job_id": job_id, "company": source.get("company", source["board"]), "title": plain(title),
                       "location": location or "unknown", "workplace_type": workplace,
                       "source_url": url, "source_api_url": source_endpoint(source), "source_ids": [job_id],
                       "published_at": published, "updated_at": updated, "fetched_at": fetched_at,
                       "description": description, "source_compensation": item.get("compensation")})
    return result


def deduplicate(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for job in jobs:
        keys = (job["job_id"], canonical_url(job["source_url"]))
        existing = next((by_key[key] for key in keys if key in by_key), None)
        if existing:
            existing["source_ids"] = sorted(set(existing["source_ids"] + job["source_ids"]))
            for key in keys:
                by_key[key] = existing
        else:
            result.append(job)
            for key in keys:
                by_key[key] = job
    return result


def experience_evidence(description: str) -> tuple[float | None, list[str]]:
    matches = []
    for match in EXPERIENCE_RE.finditer(description):
        prefix = description[max(0, match.start() - 18):match.start()].casefold()
        if re.search(r"(?:up to|less than|at most|fewer than)\s*$", prefix):
            continue
        matches.append(match)
    if not matches:
        return None, []
    return max(float(match.group("minimum")) for match in matches), [match.group(0) for match in matches]


def mainland_location(location: str) -> bool:
    # Mixed/ambiguous multi-country locations must not silently waive sponsorship.
    remaining = location.casefold()
    places = ["mainland china", "china", "shanghai", "shenzhen", "beijing", "guangzhou", "hangzhou", "上海", "深圳", "北京", "中国大陆"]
    found = bool(words_in(location, places))
    for place in places + ["remote", "hybrid", "on-site", "onsite"]:
        remaining = re.sub(r"(?<!\w)" + re.escape(place) + r"(?!\w)", "", remaining, flags=re.IGNORECASE)
    return found and not re.sub(r"[\W_]+", "", remaining)


def verified_value(facts: dict[str, Any], key: str, fetched_at: str, max_age_days: int) -> Any:
    fact = facts.get(key)
    if not isinstance(fact, dict) or not valid_public_url(fact.get("source_url")):
        return None
    try:
        checked = datetime.fromisoformat(str(fact.get("verified_at", "")).replace("Z", "+00:00"))
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if checked.tzinfo is None or not 0 <= (fetched - checked).total_seconds() <= max_age_days * 86400:
            return None
    except (ValueError, TypeError):
        return None
    return fact.get("value")


def numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def classify(job: dict[str, Any], criteria: dict[str, Any], verified_facts: dict[str, Any]) -> dict[str, Any]:
    title, description = job["title"], job["description"]
    role_matches = words_in(title, criteria.get("role_keywords", list(ROLE_WORDS)))
    excluded_title_matches = words_in(title, criteria.get("exclude_title_keywords", []))
    languages = words_in(f"{title} {description}", criteria.get("language_keywords", list(LANGUAGE_WORDS)))
    ai_matches = words_in(f"{title} {description}", list(AI_WORDS))
    preferred = criteria.get("preferred_locations", [])
    location_matches = [place for place in preferred if words_in(job["location"], [place])]
    pqe_min, pqe_evidence = experience_evidence(description)
    facts = verified_facts.get(job["job_id"], {})
    if not isinstance(facts, dict):
        facts = {}
    age = criteria.get("verified_evidence_max_age_days", 30)
    net = verified_value(facts, "monthly_net_cny", job["fetched_at"], age)
    bonus = verified_value(facts, "guaranteed_bonus_months", job["fetched_at"], age)
    qualified = verified_value(facts, "qualification_compatible", job["fetched_at"], age)
    permit = verified_value(facts, "work_permit_support", job["fetched_at"], age)
    outside_mainland = not mainland_location(job["location"])
    permit_required = bool(criteria.get("require_work_permit_outside_mainland_china", True)) and outside_mainland
    salary_floor = criteria.get("monthly_net_floor_cny")
    bonus_floor = criteria.get("guaranteed_bonus_months_floor")
    bonus_strict = criteria.get("bonus_must_exceed_floor", True)
    missing, failures, contextual_unknowns = [], [], []
    if not numeric(salary_floor):
        missing.append("salary criterion is not configured")
    elif not numeric(net):
        missing.append("verified monthly net compensation in CNY is unknown")
    elif net < salary_floor:
        failures.append("compensation below configured net monthly floor")
    if not numeric(bonus):
        contextual_unknowns.append("guaranteed bonus months are unknown; discretionary awards are not guaranteed")
    if criteria.get("bonus_is_requirement", False):
        if not numeric(bonus_floor):
            missing.append("required guaranteed bonus criterion is not configured")
        elif not numeric(bonus):
            missing.append("required guaranteed bonus months are unknown")
        elif bonus < bonus_floor or (bonus_strict and bonus == bonus_floor):
            failures.append("guaranteed bonus below configured requirement")
    if qualified is False:
        failures.append("professional qualification requirements are incompatible")
    elif qualified is not True:
        missing.append("professional qualification compatibility needs review")
    if permit_required:
        if permit is False:
            failures.append("required work permit support is unavailable")
        elif permit is not True:
            missing.append("work permit support outside mainland China is unknown")
    # A missing PQE statement is evidence to review, never evidence of no requirement.
    if pqe_min is None and qualified is not True:
        missing.append("minimum PQE or relevant experience is unstated or not parsed")
    pqe_max = criteria.get("maximum_required_experience_years", 4)
    if excluded_title_matches:
        status, reasons = "excluded_title", ["title matches configured exclusions: " + ", ".join(excluded_title_matches)]
    elif not role_matches:
        status, reasons = "excluded_role", ["no configured role keyword in title"]
    elif criteria.get("require_language_match", False) and not languages:
        status, reasons = "excluded_language", ["no configured language keyword found"]
    elif pqe_min is not None and pqe_min > pqe_max:
        status = "stretch_pqe" if criteria.get("over_experience_limit", "reject") == "stretch" else "excluded_pqe"
        reasons = [f"advertised experience lower bound {pqe_min:g} exceeds configured ceiling {pqe_max:g}; inspect whether required or preferred"]
    elif failures:
        status, reasons = "excluded_requirement", failures
    elif missing:
        status, reasons = "needs_verification", missing
    else:
        status, reasons = "ready_for_review", ["configured gates have dated verification; review the live vacancy before applying"]
    return {**job, "status": status, "reasons": reasons, "unknowns": missing, "contextual_unknowns": contextual_unknowns,
            "role_matches": role_matches, "language_matches": languages, "ai_matches": ai_matches,
            "excluded_title_matches": excluded_title_matches,
            "preferred_location_matches": location_matches, "minimum_experience_years": pqe_min,
            "experience_evidence": pqe_evidence,
            "monthly_net_cny": net if numeric(net) else "unknown",
            "guaranteed_bonus_months": bonus if numeric(bonus) else "unknown",
            "qualification_compatible": qualified if isinstance(qualified, bool) else "unknown",
            "work_permit_support": (permit if isinstance(permit, bool) else "unknown") if permit_required else "not_required_for_mainland_location",
            "verified_facts": facts,
            "score": len(role_matches) * 5 + len(languages) * 3 + len(ai_matches) * 2 + (10 if location_matches else 0) + (2 if "hybrid" in job["workplace_type"].casefold() else 0)}


def validate_config(config: Any) -> None:
    if not isinstance(config, dict) or not isinstance(config.get("sources"), list):
        raise ValueError("Config requires a sources list")
    if not isinstance(config.get("criteria", {}), dict) or not isinstance(config.get("verified_facts", {}), dict):
        raise ValueError("criteria and verified_facts must be objects")
    for source in config["sources"]:
        if not isinstance(source, dict):
            raise ValueError("Every source must be an object")
        source_endpoint(source)
    criteria = config.get("criteria", {})
    for key in ("role_keywords", "exclude_title_keywords", "language_keywords", "preferred_locations"):
        if key in criteria and (not isinstance(criteria[key], list) or any(not isinstance(value, str) or not value for value in criteria[key])):
            raise ValueError(f"{key} must be a list of nonempty strings")
    if not numeric(criteria.get("maximum_required_experience_years", 4)):
        raise ValueError("maximum_required_experience_years must be a nonnegative number")
    if criteria.get("over_experience_limit", "reject") not in {"reject", "stretch"}:
        raise ValueError("over_experience_limit must be reject or stretch")
    if not isinstance(criteria.get("verified_evidence_max_age_days", 30), int) or not 1 <= criteria.get("verified_evidence_max_age_days", 30) <= 365:
        raise ValueError("verified_evidence_max_age_days must be between 1 and 365")


def collect(config: dict[str, Any], fetcher=fetch_json, fetched_at: str | None = None) -> dict[str, Any]:
    validate_config(config)
    fetched_at = fetched_at or now_utc()
    jobs, sources = [], []
    for source in config["sources"]:
        if source.get("enabled", True) is not True:
            continue
        endpoint = source_endpoint(source)
        record = {"provider": source["provider"], "board": source["board"], "source_url": endpoint, "fetched_at": fetched_at}
        try:
            normalized = normalize(source, fetcher(endpoint), fetched_at)
            jobs.extend(normalized)
            record.update(status="success", jobs_fetched=len(normalized))
        except (URLError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            # Error types/statuses are enough to diagnose transport failures without dumping responses.
            status_code = getattr(exc, "code", None)
            record.update(status="error", jobs_fetched=0, error=f"{type(exc).__name__}" + (f" HTTP {status_code}" if status_code else ""))
        sources.append(record)
    unique_jobs = deduplicate(jobs)
    classified = [classify(job, config.get("criteria", {}), config.get("verified_facts", {})) for job in unique_jobs]
    order = {"ready_for_review": 0, "needs_verification": 1, "stretch_pqe": 2}
    classified.sort(key=lambda job: (order.get(job["status"], 3), -job["score"], job["company"].casefold(), job["title"].casefold(), job["job_id"]))
    counts: dict[str, int] = {}
    for job in classified:
        counts[job["status"]] = counts.get(job["status"], 0) + 1
    succeeded = sum(source["status"] == "success" for source in sources)
    failed = sum(source["status"] == "error" for source in sources)
    return {"fetched_at": fetched_at, "run_status": "failed" if not succeeded else ("partial" if failed else "success"),
            "successful_sources": succeeded, "failed_sources": failed, "raw_jobs": len(jobs),
            "duplicate_jobs": len(jobs) - len(unique_jobs), "status_counts": counts,
            "sources": sources, "jobs": classified}


def cell(value: Any) -> str:
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value if value is not None else "unknown")
    return "'" + text if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text


def markdown(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("[", "\\[").replace("]", "\\]")


def save_outputs(report: dict[str, Any], output_dir: Path, state_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    previous: dict[str, Any] = {}
    if state_path.exists():
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            raise ValueError("State file must be an object")
    for job in report["jobs"]:
        seen = previous.get(job["job_id"], {})
        job["is_new"] = not bool(seen)
        job["first_seen_at"] = seen.get("first_seen_at", report["fetched_at"])
        previous[job["job_id"]] = {"first_seen_at": job["first_seen_at"], "last_seen_at": report["fetched_at"]}
    if report["successful_sources"]:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(previous, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "jobs.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = ["job_id", "company", "title", "location", "workplace_type", "status", "reasons", "unknowns", "contextual_unknowns", "score", "is_new", "first_seen_at", "fetched_at", "source_url", "source_api_url", "published_at", "updated_at", "minimum_experience_years", "experience_evidence", "monthly_net_cny", "guaranteed_bonus_months", "work_permit_support", "qualification_compatible", "language_matches", "ai_matches", "source_ids"]
    with (output_dir / "jobs.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: cell(job.get(key)) for key in fields} for job in report["jobs"])
    lines = ["# Daily public job discovery", "", f"Fetched: {report['fetched_at']}", "",
             f"Run status: **{report['run_status']}**. Successful sources: {report['successful_sources']}; failed sources: {report['failed_sources']}.", "",
             "Open postings are discovery leads. Unknown required compensation, work permit support, or qualification compatibility prevents readiness. Bonus is contextual unless explicitly configured as mandatory. No applications were submitted.", "",
             f"Current unique postings: {len(report['jobs'])}; duplicates removed: {report['duplicate_jobs']}.", "",
             "## Source evidence", "", "| Board | Status | Jobs | Evidence |", "| --- | --- | ---: | --- |"]
    for source in report["sources"]:
        detail = source.get("error", source["status"])
        lines.append(f"| {markdown(source['provider'] + ':' + source['board'])} | {markdown(detail)} | {source['jobs_fetched']} | {source['source_url']} |")
    lines += ["", "## Review queue", "", "| Company / role | Location | Status | Unknowns / reasons | Source |", "| --- | --- | --- | --- | --- |"]
    queue = [job for job in report["jobs"] if not job["status"].startswith("excluded_")]
    for job in queue[:100]:
        lines.append(f"| {markdown(job['company'] + ' / ' + job['title'])} | {markdown(job['location'])} | {job['status']} | {markdown('; '.join(job['unknowns'] or job['reasons']))} | {job['source_url']} |")
    if not queue:
        lines += ["", "No review leads were produced from successfully fetched boards." if report["successful_sources"] else "Discovery failed: no board was fetched successfully. This is not a zero-match result."]
    if len(queue) > 100:
        lines += ["", "Showing the first 100 review leads; jobs.json and jobs.csv include all fetched postings."]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Private declarative JSON configuration path")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--state", type=Path, default=Path(".job-search-state/seen.json"))
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        report = collect(config)
        save_outputs(report, args.output, args.state)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(f"Configuration/output error ({type(exc).__name__}); check file access and documented schema. No configuration values were logged.", file=sys.stderr)
        return 2
    print(f"Discovery {report['run_status']}: {report['successful_sources']} source(s) succeeded, {report['failed_sources']} failed; {len(report['jobs'])} unique posting(s).")
    return 0 if report["successful_sources"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
