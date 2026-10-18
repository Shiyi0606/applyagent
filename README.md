# ApplyAgent public job discovery

A small Python companion for a human-reviewed application workflow. It reads public employer ATS boards, ranks legal, privacy, compliance, regulatory, policy, and AI-governance leads, and saves source evidence. It does not log in to LinkedIn, scrape account pages, contact anyone, or submit applications.

Python 3.11 or newer is the only prerequisite. There are no third-party dependencies or paid APIs. Repository files contain no applicant resume, name, contact details, or private search thresholds.

## Run locally

Copy `config/job-search.example.json` to a private path **outside this checkout**, edit the criteria and official board identifiers, and run:

```sh
python -m unittest discover -s tests -v
python scripts/job_search.py --config /absolute/private/path/job-search.json --output output --state .job-search-state/seen.json
```

The example leaves compensation thresholds unset. Unset required thresholds prevent any role from becoming `ready_for_review`. It includes Anthropic/Greenhouse and OpenAI/Ashby board identifiers as starting examples, not an assertion that a vacancy is currently available or that these endpoints are reachable from your environment. Confirm board identifiers by following each employer's official careers page. Replace or extend the sources as needed, including NGOs that use a supported provider. Boards hosted on other systems need a separate supported adapter or manual review; this does not search every company or NGO worldwide.

Supported public, unauthenticated GET endpoints:

| Provider | Configuration | Endpoint |
| --- | --- | --- |
| Greenhouse | `provider: greenhouse`, `board: identifier` | `https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true` |
| Lever | `provider: lever`, `board: identifier` | `https://api.lever.co/v0/postings/{board}?mode=json` |
| Ashby | `provider: ashby`, `board: identifier` | `https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true` |

TLS verification stays enabled. Redirects outside the supported public API hosts are rejected. No credentials are sent. Failed or malformed boards appear as source errors; a run with **zero successful boards exits 2**, even if it produced an empty report. A partially successful run exits 0 and is explicitly marked `partial`. A well-formed empty board is a successful fetch with no current postings.

## Criteria and verification

`criteria` is declarative JSON. `role_keywords` match the job title; language and AI terms add ranking signals. Optional `exclude_title_keywords` removes titles containing whole terms or phrases, case-insensitively; for example, `["engineer", "data scientist", "general counsel"]` avoids technical and executive false matches while keeping `Senior Legal Counsel` unless another gate excludes it. `engineer` does not match `engineering`. A language keyword in an advertisement does not assert an applicant's proficiency. `preferred_locations` boosts matching locations while preserving worldwide leads. Hybrid postings receive a small preference. No preferred location is a visa entitlement.

`maximum_required_experience_years` applies to parsed experience statements, including ranges such as `5–7 years of legal experience`. With the default ceiling of 4, a stated lower bound of 4 is retained and 5 is excluded. Set `over_experience_limit` to `stretch` to keep higher-experience roles in a separate stretch queue. Extraction is conservative text matching and can capture preferred experience too; inspect the recorded excerpt and full advertisement to distinguish a preference from a requirement. Local bar, professional language, citizenship, and other requirements still require human verification.

Salary eligibility requires a dated, evidenced `monthly_net_cny` fact at or above `monthly_net_floor_cny`. The script **does not convert gross pay to net pay** or convert currencies. If pay is denominated in another currency, confirm actual after-tax monthly pay and an evidenced CNY equivalent outside the script before recording that fact. Unknown pay remains unknown. `guaranteed_bonus_months` is reported separately and is contextual by default; discretionary or estimated bonuses must never be recorded as guaranteed. Only enable `bonus_is_requirement` when a mandatory bonus floor is intended. `bonus_must_exceed_floor` determines a strict `>` versus `>=` comparison.

Outside an unambiguous mainland-China location, `require_work_permit_outside_mainland_china: true` requires explicit work-permit support. Remote, mixed-country, and unknown locations do not waive that check. Every review-ready lead also requires `qualification_compatible: true`, reflecting a human review of the applicant's actual qualifications against that specific advertisement. Unknown compensation, required work permit support, or qualification compatibility prevents readiness.

Verified facts are keyed by the normalized `job_id` found in `jobs.json`. Each fact requires an HTTPS evidence URL and a timezone-aware `verified_at` timestamp. Evidence expires after `verified_evidence_max_age_days` (30 by default). The following is a schema illustration with deliberately unset values; replace placeholders only after verification:

```json
{
  "verified_facts": {
    "greenhouse:CONFIRMED_BOARD:JOB_ID": {
      "monthly_net_cny": {"value": null, "source_url": "https://employer.example/careers/role", "verified_at": "REPLACE_WITH_ACTUAL_ISO_TIMESTAMP"},
      "qualification_compatible": {"value": null, "source_url": "https://employer.example/careers/role", "verified_at": "REPLACE_WITH_ACTUAL_ISO_TIMESTAMP"},
      "work_permit_support": {"value": null, "source_url": "https://employer.example/careers/role", "verified_at": "REPLACE_WITH_ACTUAL_ISO_TIMESTAMP"}
    }
  }
}
```

Facts are human assertions, not independently authenticated by this program. Use direct employer, official compensation, tax, immigration, or recruiter evidence that actually supports the asserted fact. Do not record personal contact details, resume text, passwords, account cookies, or tokens in this configuration.

## Outputs

- `jobs.json`: all normalized current postings, original description text and compensation evidence, source/API URLs, fetched timestamps, deduplicated identifiers, parsed experience excerpts, verified facts, explicit unknowns, status, and first-seen state.
- `jobs.csv`: spreadsheet-friendly review fields; formula-like leading text is escaped.
- `report.md`: source health and the first 100 non-excluded review leads, with links and missing information.
- `.job-search-state/seen.json`: posting identifiers and first/last-seen timestamps for local repeat runs. Previously seen postings remain in the registry; it is not a list of still-open vacancies.

`ready_for_review` means the configured gates have dated verification; it still requires checking that the live vacancy remains open and that the specific application is accurate. `needs_verification` records missing facts. `stretch_pqe` is separate from eligible leads. `excluded_*` statuses explain why a role was filtered. No status submits an application. Repeated IDs or canonical job URLs are combined; different URLs for the same vacancy can still require manual deduplication.

## Daily GitHub Actions schedule

The included workflow is a **draft until pushed to the repository's default branch and enabled**. Nothing running in this checkout schedules a persistent task. It runs at **09:00 Asia/Shanghai (01:00 UTC)**, subject to GitHub scheduler delays, and also supports manual dispatch.

1. Save a private search JSON object as repository Actions secret `JOB_SEARCH_CONFIG_JSON`. Keep resume/contact/account data out of it. The workflow writes it to a restricted temporary file and never logs its value.
2. Set repository Actions variable `JOB_SEARCH_ENABLED` to the exact string `true` when ready. Missing or `false` leaves both scheduled and manually dispatched jobs gated off.
3. Download each run's report artifact from the Actions page. Artifacts expire after 14 days. A cache carries seen-posting IDs between runs; if that cache is unavailable, newness starts again.
4. Pause by setting `JOB_SEARCH_ENABLED` to `false`, or disable the workflow in GitHub Actions.

The workflow has only `contents: read`, runs no external messaging, and uploads no private configuration. It creates no ongoing chat notifications and cannot know when a job has been accepted; pause it when the search should stop. This checkout is not published or enabled automatically.

## LinkedIn and application preparation

Use LinkedIn's own public search and saved-job features manually alongside official employer sources. Example public navigation links are [legal counsel](https://www.linkedin.com/jobs/search/?keywords=legal%20counsel), [AI governance](https://www.linkedin.com/jobs/search/?keywords=AI%20governance), and [Korean legal](https://www.linkedin.com/jobs/search/?keywords=Korean%20legal). These links are not scraped or fetched by this workflow. Confirm every application on the employer's official site.

For ApplyPilot or other application tools, prepare truthful tailored documents and verify all eligibility requirements before submission. Account access, CAPTCHAs, and application-specific declarations remain interactive; this companion has no submission integration and uses no fingerprint or CAPTCHA bypasses.
