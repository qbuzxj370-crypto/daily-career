# CLAUDE2.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Always read `docs/ref.md` first

**`docs/ref.md` is the running record of this project and must be read at the start of every session,
before touching any code.** This file (`CLAUDE.md`) says what the rules are; `docs/career-plan.md` says
what is being built and why; **`docs/ref.md` says how far it actually got, what has really been run
against live APIs versus only against fixtures, and which implementation decisions deviate from the plan
and why.** Without it you will re-derive decisions that were already made — or worse, revert one.

**When you finish work, update `docs/ref.md`** — its §2 status table, §4 decisions list, and §7 next
steps — in the same change that made them stale. A stale ref.md is worse than none, because the next
session trusts it.

## Status: P2–P5 implemented, P1 still open

`docs/career-plan.md` is the spec. P2 (collect/normalize/signals), P3 (LLM judgement), P4 (Notion publish +
dedup), and P5 (Slack + schedule) are implemented and covered by `tests/` (95 tests, offline). `--mock`
runs the whole collector→signals→evaluate→render path with no keys.

**P1 is not done and cannot be done without the user's API keys.** The `FIELDS` tables in
`src/sources/saramin.py` and `src/sources/worknet.py` are *doc-derived assumptions* — written defensively
(multiple candidate paths per field, XML+JSON both parsed) precisely because they are unverified. They are
not a substitute for the probe. Run `--probe` against both real APIs, fix `FIELDS` against the dump in
`probe/`, and replace the fixtures in `tests/test_sources.py` with the real payloads. Do not treat a
green `tests/test_sources.py` as evidence that the mapping is correct — it only proves the parser handles
the *assumed* shape.

Sibling project `../daily-recall` is the working reference implementation of the same pipeline shape. Read
its `CLAUDE.md` before designing anything here; the point of this project is to reuse that structure and
spend the new effort on collection and judgement only.

## What this is

`career-scout` (주간 구직 스카우트) is a **personal job-filtering tool**, not a job aggregator. Every Friday
morning it pulls infra/ops-track postings from official Korean job APIs, judges each one against the user's
hard criteria, writes them to a Notion DB, and pushes a weekly digest to Slack.

**The problem is not finding postings — it is the hours spent eyeballing dozens per week to reject them.**
In this track the same job title covers wildly different work: one "시스템 엔지니어" posting is 24/365 관제
상주 shift work, another sits next to a 구축 team. So the primary product is the **verdict**, not the list.
The four criteria in `docs/career-plan.md` §판정 기준 are the reason the tool exists — blurring them for
implementation convenience defeats it:

| Signal | Direction |
|---|---|
| 관제 전담 (24/365 monitoring, shift work) | 🔴 위험 |
| 소기업 / 오너 1인 (lone IT staff, no senior) | 🔴 위험 |
| 구축 조직 동거 (co-located with a build/SI org) | 🟢 가점 |
| 주간 중심 (daytime only, no shifts) | 🟢 가점 |

Verdict is 3-valued: `적합` / `보통` / `위험`.

## Commands (`src/pipeline.py`)

```bash
python -m src.pipeline --probe saramin --role cloud   # dump one raw API response (P1: pin field mapping)
python -m src.pipeline --mock                         # offline path check, no API keys needed
python -m src.pipeline --dry-run                      # real collect + judge, no Notion write
python -m src.pipeline --dry-run --no-llm             # collect + regex signals only (zero LLM cost)
python -m src.pipeline --publish                      # real publish (week-idempotent)
python -m src.pipeline --purge                        # archive pages older than 3 ISO weeks
python -m src.pipeline --init-db --parent-page <ID>   # one-time: create the Notion DB
python -m pytest tests -q                             # rules + orchestration invariants
```

`--no-prescreen` skips the title prescreen (everything collected gets judged and published);
`--no-purge` skips the retention sweep `--publish` runs first; `--keep-weeks N` overrides how
many ISO weeks survive that sweep.

`--role <slug>` narrows to one role, `--week 2026-W33` pins the ISO week. Follow daily-recall's
convention: `--mock` is the offline smoke test and runs the full collector→signals→evaluate→render path
from `_mock_jobs()` with no key set (and never calls the LLM). `--dry-run --no-llm` is specific to this
project and exists so regex rule changes can be iterated for free.

Secrets: `.env` locally, GitHub Actions Secrets in CI —
`SARAMIN_ACCESS_KEY`, `GEMINI_API_KEY`, `NOTION_API_KEY`, `NOTION_DB_ID` (required),
`SLACK_WEBHOOK_URL` (optional). **워크넷 needs no key** — it is scraped, not called. `GEMINI_API_KEY` and `NOTION_API_KEY` can be the same values daily-recall
uses; `NOTION_DB_ID` must be a **separate DB**. Never commit `.env` or a webhook URL — the URL itself is
the secret. Schedule is `.github/workflows/weekly.yml`, cron `0 22 * * 4` UTC = **Fri 07:00 KST**.

## Pipeline architecture

`src/pipeline.py` orchestrates 9 stages, each a separate module:

0. **purge** (`state.py`) — archive pages older than `PURGE_KEEP_WEEKS` ISO weeks. Runs first, and a
   failure here never blocks the publish.
1. **idempotency** (`state.py`) — already collected this ISO week? → exit.
2. **collector** (`collector.py` + `sources/`) — call each source per role keyword, normalize to `JobPosting`,
   drop intra-run duplicates.
3. **dedup** (`state.py`) — fetch **all** existing `SourceKey`s from Notion in one paginated sweep, filter in memory.
4. **signals** (`signals.py` + `config/rules.py`) — regex extraction of risk/bonus signals. *Code decides here.*
5. **prescreen** (`prescreen.py`) — company + title only, ~60 per call. What it drops is **never judged and
   never written to Notion**.
6. **evaluator** (`evaluator.py`) — Gemini JSON mode, batched, signals injected as prompt hints.
7. **notion_pub** (`notion_pub.py`) — one posting = one page.
8. **slack_pub** (`slack_pub.py`) — weekly digest.

Build the publish flow dependency-injected like `../daily-recall/src/pipeline.py:41` (state, collect_fn,
evaluate_fn, publisher, slack_fn passed in) so orchestration is verifiable without live APIs.

## Key architectural facts

- **Rules first, LLM second — and the rules win.** Unambiguous signals (`3교대`, `24*365`, `관제`) are
  confirmed by regex in code, then injected into the LLM prompt as hints. If the LLM disagrees, **code
  force-overwrites `verdict` back to `위험`** — the same pattern as `../daily-recall/src/generator.py:146`,
  where context values overwrite model output. The model is a summarizer here, not the authority.
  *Mitigation rule:* if `관제` co-occurs with a `구축 조직 동거` signal, do **not** force 위험 — leave it to
  the LLM, so genuine ops+build hybrid roles are not thrown away. Shift-schedule signals (`3교대`, `24*365`)
  are forced with no mitigation.

- **Two independent idempotency layers. Do not collapse them.** `CollectedWeek` (ISO week, e.g. `2026-W33`)
  stops a second run in the same week from doing any work at all. `SourceKey` (`"{source}:{공고ID}"`) stops
  a posting that was already seen *in an earlier week* from being created again. Removing either one
  produces duplicate pages the moment the other is bypassed.

- **`SourceKey` must be a Notion property, never body text** — properties are queryable, page bodies are not.
  Same reason daily-recall stores `Question` as a property.

- **Fetch SourceKeys in bulk, once per run.** One query per posting means dozens of round trips. Reuse the
  pagination loop at `../daily-recall/src/state.py:75`.

- **Notion DB is the single source of truth.** No local state file; the CI runner is ephemeral. Dedup and
  week-idempotency are both derived from Notion queries.

- **`Status` is a human column.** The pipeline sets it to `신규` at creation and **must never update an
  existing page.** The user hand-manages 검토중/지원/보류/탈락 there; a "sync" or "refresh existing pages"
  feature would silently erase their triage work. Publishing is create-only.
  The one write that touches an existing page is the retention sweep (`state.purge_before`), and it is
  bounded by the same concern: **it archives only pages still at `신규`.** Age is a necessary condition
  for cleanup, never a sufficient one — a posting the user already applied to does not get deleted for
  being old. Week comparison is lexicographic on `YYYY-Www`, which is chronological because of the
  zero-padding; `test_week_strings_compare_chronologically` pins that.

- **The title prescreen is the only stage that makes a posting disappear.** Anything it drops is never
  judged and never reaches Notion, so two rules hold it in place (`tests/test_prescreen.py`):
  rule-forced 위험 is dropped by *code* without asking the model, and **LLM failure fails open** — a
  broken batch passes through to full judgement rather than vanishing. Losing a week of job leads to a
  429 is much worse than paying for a few extra judgements. Its prompt gets company + title only; if
  `raw_text` leaks in, the stage has no reason to exist.

- **Notion 2025-09 API uses data sources.** Pages are created against a `data_source_id`, not a database id
  — reuse `resolve_data_source_id()` at `../daily-recall/src/state.py:51` and the "missing select option"
  400 absorber at `../daily-recall/src/state.py:38`.

- **사람인 = official API. 워크넷 = HTML scrape. The split is deliberate and robots.txt decides it.**
  The original "official APIs only" rule was overturned on 2026-08-10 for 워크넷 only, because
  **고용24 OPEN-API is 기업회원 전용** — its own intro page says so, and a real call with a valid key
  returns `개인회원은 사용할 수 없는 OPEN-API입니다`. data.go.kr delegates that dataset back to 고용24,
  so no API path exists for a personal account. Before scraping anything, **check robots.txt**:
  work24.go.kr is `Allow: /` (so `/wk/a/b/1200/...`, which is also in its sitemap, is fair game) while
  saramin.co.kr has `Disallow: /zf_user/recruit/` — so 사람인 must stay on its API and **must never be
  scraped**. `/cm/f/c/0100/selectUnifySearchPost.do` (work24 통합검색) is explicitly disallowed too.

- **LLM calls are batched, and the free tier's real ceiling is daily, not per-minute.** Measured
  2026-08-10 from the 429 body: **20 requests per day per model** (`gemini-2.5-flash` and
  `gemini-2.5-flash-lite` each get their own 20). Waiting does not clear it. Judge in batches of
  `EVAL_BATCH_SIZE` (10) returning an array; on batch failure, retry that batch as individual calls,
  then fall back to `MODEL_FALLBACK`. **Exception: on 429/RESOURCE_EXHAUSTED, skip the individual
  retries** — they are certain to fail and would burn 10 more of the day's 20 requests
  (`evaluator._is_quota_exhausted`). This budget is why the prescreen exists: 144 postings cost 15
  requests to judge outright, versus 3 to prescreen plus a few for the survivors.

- **Slack is a secondary notification.** Its exceptions are swallowed; a Slack failure never invalidates a
  successful Notion publish. On total collect/publish failure, write a `Kind=error` page and re-raise so the
  scheduler registers it.

- **Risky postings appear as a count only in Slack — never their titles.** Not cosmetics: the tool's purpose
  is to *save the time spent scanning*, and listing rejects in the digest re-creates exactly the scanning it
  removes. Titles for 위험 stay in Notion for anyone who wants to audit the judgement.

- **Controlled markdown subset.** `md_to_notion.py` is copied verbatim from daily-recall and parses only that
  project's fixed subset (H1–H3, paragraph, `**bold**`, `` `code` ``, fenced blocks, `-` bullets, `---`, `>`).
  **No markdown tables** — the converter breaks on them, so the evaluator prompt must forbid them too.

- **slug is the canonical key.** `config/roles.py` owns `slug → (display name, keywords, priority)`.
  `JobPosting.role` stores the slug; display names appear only in Notion `Role` and in prompts. Narrow scope
  by adjusting priority/weights, never by deleting a slug from the canonical list — deleting invalidates the
  Notion select options and previously published pages.

## P1 gates the source mappings

`--probe`, dumping raw responses to `probe/`, is what pins the field mappings. Do not derive them from
docs alone; docs diverge from actual responses often enough that anything built on documentation is
likely to be rewritten. `run_probe()` prints the extracted `JobPosting` and a list of **empty fields**.

**워크넷 is done** (2026-08-10) — mapping verified against the live page, and `tests/test_sources.py`
now carries two real result rows as its fixture. **사람인 is not** — its `FIELDS` table is still a
doc-derived assumption and its fixture is invented. A green `test_sources.py` proves the 워크넷 mapping
and only the 사람인 *parser shape*.

Two 워크넷 details that cost hours to find and will not be re-derivable from the page source:
**`searchMode=Y` is mandatory** — without it the keyword is silently ignored and you get the unfiltered
latest list (which looks like a working search until you read the titles). And the per-row anchor is the
compare-checkbox `value`, which packs `공고번호|정보구분|회사명|공고제목` in one attribute. Endpoints stay
env-overridable (`SARAMIN_API_URL`, `WORKNET_SEARCH_URL`) since work24 has already moved once.

⚠️ **Known limitation, do not paper over it.** Neither source gives company headcount or the posting
body. 사람인's search API returns metadata only; 워크넷's list rows carry 급여·경력·학력·근무형태·지역
but no 사원수 and no 본문. So "소기업 · 오너 1인" is caught only when the wording says it outright
(`1인 전산`, `대표 직속`), and text-based signals like `3교대` only fire if they appear in the fields
that are listed. (워크넷 does surface 근무형태 — `주5일`, `08:30 ~ 19:30` — which is why "주간 중심"
fires reliably there and rarely on 사람인.) `Verdict.company_size` therefore carries `판단 불가`, rendered in the page
body as "공고에 정보 없음 — 직접 확인 필요". Do not guess confidently, and do not quietly drop the criterion.

## Tests

`python -m pytest tests -q` — offline, no keys, also run in CI before publishing.

- `test_rules.py` — the user's four criteria as regex behaviour, plus "rules beat the LLM" (forced 위험
  survives an LLM `적합`, and the 관제+구축 mitigation does not force).
- `test_pipeline.py` — the two idempotency layers separately, partial-publish handling, Slack never
  listing 위험 titles, batch→individual→rule fallback.
- `test_notion.py` — `SourceKey` is a property not body text, `Status` is create-only `신규`, unknown
  signal names are dropped, page body has no markdown table, every written property exists in the schema.
- `test_state.py` — bulk `SourceKey` sweep paginates (not one query per posting), `week_exists` stops
  after one row.
- `test_sources.py` — 워크넷 fixture is a real response and pins that mapping plus the three filter
  params; the **사람인 fixture is still an assumption** (see P1).
- `test_prescreen.py` — rule-forced 위험 is dropped without an LLM call, LLM failure keeps the whole
  batch, the prompt carries no `raw_text`, and a screened-out job is never published.
- `test_state.py` — also covers the retention sweep: only weeks before the cutoff, never a page whose
  `Status` the user changed, never a page whose week is unreadable.

## Reuse from daily-recall (do not rewrite)

| Take | From | Changes |
|---|---|---|
| `md_to_notion.py` entire file | `../daily-recall/src/md_to_notion.py` | none |
| `NotionState._query()` pagination | `../daily-recall/src/state.py:75` | none |
| `resolve_data_source_id()` | `../daily-recall/src/state.py:51` | none |
| `_is_missing_select_option()` | `../daily-recall/src/state.py:38` | none |
| `init_db()` / `_schema_properties()` | `../daily-recall/src/notion_pub.py:53` | swap the property schema |
| `slack_pub._post()` | `../daily-recall/src/slack_pub.py:14` | none |
| `_publish_flow` injection shape | `../daily-recall/src/pipeline.py:41` | swap the stages |

## Tuning constants

`config/settings.py`: `MODEL` / `MODEL_FALLBACK` (`gemini-2.5-flash` / `gemini-2.5-flash-lite`, env
overridable), `EVAL_BATCH_SIZE=10`, `PRESCREEN_BATCH_SIZE=60`, `MAX_JOBS_PER_ROLE=30`, `SLACK_TOP_N=5`,
`ROLE_PRIORITY` (`cloud`, `public_it` first), `SEND_SLACK = bool(SLACK_WEBHOOK_URL)`,
`PRESCREEN` (`CS_PRESCREEN=0` disables), `PURGE_KEEP_WEEKS=3` (`CS_PURGE_KEEP_WEEKS`).
`config/roles.py` owns the 5 role slugs; `config/rules.py` owns the `RISK` / `POSITIVE` regex sets.

**워크넷 search filters** (`WORKNET_CAREER_TYPES=N,Z`, `WORKNET_ACADEMIC_GBN=00,04`,
`WORKNET_REG_DAYS=7`) narrow the search server-side before anything else runs. The codes and their
measured effect are tabulated in `docs/ref.md` §7(1); three details bite if you touch them: the
server reads `careerTypes` (plural) and ignores the checkbox's own `careerType`; `regDateStdt`/
`regDateEndt` must be `YYYYMMDD` or the result list comes back **empty**; and `termSearchGbn=W-1`
is a UI button state the server ignores, so the dates must be computed. The defaults include
학력무관/경력무관 on purpose — most 워크넷 postings are registered that way, and filtering to
`04`/`N` alone drops the majority of postings the user actually qualifies for (614 → 72).

Regex rules in `config/rules.py` are the one part of this project that **must keep real tests** — they are
pure functions over strings, they encode the user's actual criteria, and a silently broken pattern turns a
위험 posting into a 적합 one. Patterns are graded `hard` (force 위험 unconditionally) vs `soft`
(mitigable via `MITIGATED_BY`); changing a pattern's grade changes the user's criteria, so add a test with
it. See `tests/test_rules.py`.
