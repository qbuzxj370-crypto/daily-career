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
dedup), and P5 (Slack + schedule) are implemented and covered by `tests/` (69 tests, offline). `--mock`
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
python -m src.pipeline --init-db --parent-page <ID>   # one-time: create the Notion DB
python -m pytest tests -q                             # rules + orchestration invariants
```

`--role <slug>` narrows to one role, `--week 2026-W33` pins the ISO week. Follow daily-recall's
convention: `--mock` is the offline smoke test and runs the full collector→signals→evaluate→render path
from `_mock_jobs()` with no key set (and never calls the LLM). `--dry-run --no-llm` is specific to this
project and exists so regex rule changes can be iterated for free.

Secrets: `.env` locally, GitHub Actions Secrets in CI —
`SARAMIN_ACCESS_KEY`, `DATA_GO_KR_KEY`, `GEMINI_API_KEY`, `NOTION_API_KEY`, `NOTION_DB_ID` (required),
`SLACK_WEBHOOK_URL` (optional). `GEMINI_API_KEY` and `NOTION_API_KEY` can be the same values daily-recall
uses; `NOTION_DB_ID` must be a **separate DB**. Never commit `.env` or a webhook URL — the URL itself is
the secret. Schedule is `.github/workflows/weekly.yml`, cron `0 22 * * 4` UTC = **Fri 07:00 KST**.

## Pipeline architecture

`src/pipeline.py` orchestrates 7 stages, each a separate module:

1. **idempotency** (`state.py`) — already collected this ISO week? → exit.
2. **collector** (`collector.py` + `sources/`) — call each source per role keyword, normalize to `JobPosting`,
   drop intra-run duplicates.
3. **dedup** (`state.py`) — fetch **all** existing `SourceKey`s from Notion in one paginated sweep, filter in memory.
4. **signals** (`signals.py` + `config/rules.py`) — regex extraction of risk/bonus signals. *Code decides here.*
5. **evaluator** (`evaluator.py`) — Gemini JSON mode, batched, signals injected as prompt hints.
6. **notion_pub** (`notion_pub.py`) — one posting = one page.
7. **slack_pub** (`slack_pub.py`) — weekly digest.

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

- **Notion 2025-09 API uses data sources.** Pages are created against a `data_source_id`, not a database id
  — reuse `resolve_data_source_id()` at `../daily-recall/src/state.py:51` and the "missing select option"
  400 absorber at `../daily-recall/src/state.py:38`.

- **Official APIs only. No scraping.** 사람인 오픈 API and 공공데이터포털(고용24) both return JSON with a free
  key. This is a deliberate constraint, not a temporary shortcut — do not add an HTML scraper as a
  "fallback" when an API field is missing.

- **LLM calls are batched.** ~40 postings individually would hit the free-tier RPM limit. Judge in batches of
  `EVAL_BATCH_SIZE` (10) returning an array; on batch failure, retry that batch as individual calls, then
  fall back to `MODEL_FALLBACK`.

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

`--probe` against both real APIs, dumping raw responses to `probe/`, is what pins the field mappings.
Do not derive them from API docs alone; docs diverge from actual responses often enough that anything
built on documentation is likely to be rewritten. `run_probe()` prints the extracted `JobPosting` and a
list of **empty fields** — an empty field means that source's `FIELDS` candidate paths are wrong.

Both clients keep the endpoint and parameters env-overridable (`SARAMIN_API_URL`, `WORKNET_API_URL`,
`WORKNET_AUTH_PARAM`, `WORKNET_EXTRA_PARAMS`) because 워크넷 in particular differs between the
work.go.kr endpoint (`authKey`, XML) and the data.go.kr passthrough (`serviceKey`).

⚠️ **Known limitation, do not paper over it.** 사람인's search API returns metadata only — no company
headcount and no posting body. So "소기업 · 오너 1인" is caught only when the wording says it outright
(`1인 전산`, `대표 직속`), and text-based signals like `3교대` only fire if they appear in the title,
industry, or keyword fields. `Verdict.company_size` therefore carries `판단 불가`, rendered in the page
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
- `test_sources.py` — parser shape only. **Its fixtures are assumptions, not real responses** (see P1).

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
overridable), `EVAL_BATCH_SIZE=10`, `MAX_JOBS_PER_ROLE=30`, `SLACK_TOP_N=5`, `ROLE_PRIORITY`
(`cloud`, `public_it` first), `SEND_SLACK = bool(SLACK_WEBHOOK_URL)`.
`config/roles.py` owns the 5 role slugs; `config/rules.py` owns the `RISK` / `POSITIVE` regex sets.

Regex rules in `config/rules.py` are the one part of this project that **must keep real tests** — they are
pure functions over strings, they encode the user's actual criteria, and a silently broken pattern turns a
위험 posting into a 적합 one. Patterns are graded `hard` (force 위험 unconditionally) vs `soft`
(mitigable via `MITIGATED_BY`); changing a pattern's grade changes the user's criteria, so add a test with
it. See `tests/test_rules.py`.
