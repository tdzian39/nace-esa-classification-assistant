# CLAUDE.md

Guidance for Claude Code working in this repository.

# NACE/ESA classification assistant — Raiffeisenbank CZ Finance/MIS

## Context

**Tool 1 only** (since 22 Sept 2026). MO treasury sets up foreign securities issuers in CTS
and must pick a 2-digit NACE code and an elementary ESA 2010 sector code. Input: ISIN and/or
issuer name and/or activity description. Output: issuer name, description, suggested NACE +
CTS ID, suggested ESA + CTS ID, top 3 candidates each, with evidence.

Deploys to **Vercel**. Tool 2 (RES/OR lookup) moved to `jaeksrampota/res-or-lookup` in PR #5,
taking DWS and ARES with it — nothing here reads them. **The plan, every decision and every
open question live in `docs/ROADMAP.md`: read it after this file and keep both in step.**

Sources, in order: **GLEIF** (`api.gleif.org`) + **OpenFIGI** (`api.openfigi.com`) for an
issuer given by ISIN; **web search** for activity descriptions of foreign issuers only.
Never scrape `apl.czso.cz` or `or.justice.cz`.

## Codebooks (xlsx; from a private Vercel Blob store in deployment, roadmap D3)

| file | columns | notes |
|---|---|---|
| `CTS_BA0036_NEW.xlsx` | ID (CTS ID), VALUE (ESA), DESCRIPTION | real headers are `.ID`, `Hodnota`, `Popis`. 110 rows, one non-leaf placeholder `0000000` |
| `BA0036_2024_jen_validni.xlsx` | Kód, Název, Popis | 109 leaves; **only these are valid in CTS** — reject parent codes. Three `pritomnost v ...` columns ignored |
| `CTS_OKEC_NACE2.xlsx` | ID, VALUE (first 2 chars), DESCRIPTION | sheet `Sheet1`; 87 divisions, CTS IDs 455–542 |
| `NACE_STAT.xlsx` | NACE, Zkrtext, Text | sheet `KLAS80143_CS`, 969 rows; also `uroven`/`chodnota`, ignored. Several rows per division, max 56 (oddíl 46) |

- **BA0036 keys on a 7-digit CNB code** (`1221300` = Banky pod zahraniční kontrolou), not the
  5-digit ESA form `S.12213`. Registers report the ESA form (`12203`), which does **not** exist
  in CTS: CTS splits S.1220x into banks (1221x) / credit unions (1222x) / other deposit-takers
  (1224x), and S.125 likewise. A register's sector is therefore **not** a lexical lookup into
  BA0036 — still open (roadmap Q7).
- **CTS is on CZ-NACE 2025 (Rev. 2.1)**: 87 divisions, no 45, CTS ID 496 missing where 45 sat.
  Rev. 2 codes need mapping.
- All four load with 0 errors/warnings as `cb-3b12e64837840ca0`; 87 divisions match 1:1.
- Prompt budget (~4 chars/token): 87 short labels ≈ 895 tokens; every label of every division
  ≈ 10,400; a 12-division shortlist's full texts ≈ 1,440. Hence the two-stage classifier —
  narrow on short labels, decide on full texts — not one prompt holding the codebook.

## Layout (everything under `/app`)

```
core/identifiers  ico.py (mod-11; batch reader only), isin.py
core/sources      base.py, gleif.py, openfigi.py, identity.py (ISIN -> issuer), web.py
core/codebooks    loaders, versioning, consistency; blob.py (private Vercel Blob)
core/classify     candidates.py (pre-filter), hints.py, llm.py, proposal.py, golden.py
core/export       columns.py (the row), xlsx.py     core/batch reader.py (E6 reuses)
core/probe.py     the /probe register checks
api/  GET / · POST /suggest · POST /api/suggest · GET /suggest.xlsx · /health · /probe
ui/   suggest.html + prototype/suggest.html      tests/golden  cases.json, identity.json
config/settings.py · .env.example · vercel.json · .python-version · pyproject.toml
```

Stack: Python 3.12, FastAPI, openpyxl, pydantic. UI server-rendered (Jinja2 + htmx), no JS
framework. No pandas; numpy is a dev extra only (tests feed numpy scalars to the normalisers).

## Hard rules

- Every row carries source (GLEIF/OPENFIGI/WEB), retrieval timestamp, codebook version.
- Keep full NACE codes in data; truncate to 2 digits only at the CTS-ID mapping step.
- Never write to CTS. Log every lookup (identifier, timestamp, user) — never its content.
- Prompts carry only public facts: issuer name, web description, register facts, codebook
  labels.
- LLM output is a selection from a supplied 10–15 candidate list, with code, one-sentence
  justification and confidence; any code not in the list is rejected. Return top 3. Model and
  provider configurable; default to the cheapest and measure against `/tests/golden` first.
- Serverless: nothing writable but `/tmp`, no long-lived state, lazy startup, no reverse proxy
  in front (a client-settable header is not an identity).
- **The repository is PUBLIC** (D2): never commit codebooks, `.env`, audit logs or real
  lookups.
- The LLM stays off until an approved endpoint exists (E9). Keep its tests green.

## Commands (from `app/`)

```bash
../.venv/Scripts/python.exe -m pip install -e ".[dev]"
../.venv/Scripts/python.exe -m core.classify "popis cinnosti"      # shortlist (--verbose, --estimate)
../.venv/Scripts/python.exe -m core.classify --golden [--model]    # recall + top-1 (--model costs money)
../.venv/Scripts/python.exe -m core.classify --golden-capture      # re-record register answers (network)
../.venv/Scripts/python.exe -m core.classify --usage               # limits and today's spend
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .
../.venv/Scripts/python.exe -m core.codebooks [--no-strict --json --dir PATH]
```

The venv is Anaconda 3.13.9 (no 3.12 on this machine) but `requires-python >= 3.12`, so stay
3.12-compatible. Bare `python` is the Windows Store stub. Quote paths — they contain spaces.
Codebook CLI exit codes: 0 consistent, 1 errors, 2 missing file/wrong columns.

Test module basenames must be unique (default import mode), hence `test_codebooks_*.py`.
Without the real xlsx in `app/data/codebooks/` 16 tests skip (real-file smoke 2,
`test_classify_real_recall.py` 5, `test_classify_cost.py` 9); other codebook tests build
synthetic fixtures via `tests/codebooks/conftest.py`.

## Current state

**Deterministic mode is a result, not a failure — never present it as one.** With no
`LLM_API_KEY` the tool narrows 87 NACE divisions and 56 ESA sectors to ~12 candidates each,
resolves every CTS ID up front, shows the shortlist on the page and in the xlsx
(`NACE_candidates`, `ESA_candidates`), and says it did not choose, and why.

Figures are **provisional** (no case is `verified_by`-confirmed) and must not be quoted as
accuracy. Next steps are `docs/ROADMAP.md` §0: E2 (login + audit, needs D4) once MO is let in,
and the FIRDS LEI fallback. No Vercel Pro; nothing can be checked in CTS (Jakub, 23 Sept 2026).

## Architecture notes

- **Settings** (`config/settings.py`): pydantic-settings reading env / `app/.env`. Relative
  paths resolve against `APP_ROOT`, never the cwd. `get_settings()` is `lru_cache`d; tests
  build `Settings(...)` directly.
- **Codebooks**: `xlsx.read_table()` (openpyxl, header found within 20 rows, accent-insensitive
  aliases) → `loaders.load_codebooks()` → frozen `CodebookSet` → `consistency.check_consistency()`
  returning `Finding`s with stable codes (`E_*`/`W_*`/`I_*`). `load_and_check()` is the startup
  entry point and raises on any error. `_build_*` take plain rows so another source can replace
  xlsx. `versioning.build_version()` derives `cb-<16hex>[+label]` from file hashes.
- **CTS-ID emission points: exactly two** — `cts_id_for_esa()` (valid leaf only; parents raise)
  and `cts_id_for_nace()`, the only caller of `nace_to_division()`, the single place NACE is
  truncated. Keep it that way. ESA compares on a digits-only key; CTS IDs are opaque text with
  leading zeros preserved.
- **Identifiers**: pure functions. `normalize_*` raise errors carrying a Literal `reason`;
  `is_valid_*` / `try_normalize_*` never raise. NaN blanks are the caller's problem.
- **Sources fail soft**: returning `None` means the register lacks the issuer; raising
  `SourceUnavailableError` means it could not be asked. **Never collapse the two** — an outage
  must not be reported as "not found".
- **Issuer identity** (`gleif.py`, `openfigi.py`, `identity.py`): an ISIN is resolved before the
  web is searched. GLEIF `/lei-records?filter[isin]=` gives legal name, country, ELF legal form,
  entity category (GENERAL / FUND / BRANCH / RESIDENT_GOVERNMENT_ENTITY /
  INTERNATIONAL_ORGANIZATION / SOLE_PROPRIETOR) and status; `/direct-parent`, `/ultimate-parent`
  (404 = none, then `/direct-parent-reporting-exception`, e.g. `NO_KNOWN_PERSON`). OpenFIGI
  `POST /v3/mapping` gives the instrument. `identify()` never raises for a source failure.
  * `fact_sheet()` is Czech prose whose parentheses carry the **English** words the hint table
    reacts to, so a GLEIF category reaches the shortlist with no new mechanism. It is appended
    to the description the pre-filter scores and the model reads.
  * The legal name is the web search query (an ISIN never was one); what MO typed still wins as
    the displayed name.
  * **Facts are stated, never decided**: "the ultimate parent sits abroad" is a fact; whether
    that is "pod zahraniční kontrolou" is the classifier's call (Q7).
  * Rate limits: GLEIF 60/min (throttle 1.0 s), OpenFIGI keyless 25/min (throttle 2.5 s). 429
    and 5xx retry, other 4xx do not. Up to 4 GLEIF requests per ISIN
    (`GLEIF_FETCH_PARENTS=false` makes it 1) and 1 OpenFIGI.
  * GLEIF has no ISIN mapping for some funds (iShares Core MSCI World) that OpenFIGI names —
    hence both registers, in that order.
  * `source` names the registers that answered, even when the answer was "nothing", then `WEB`.
    Egress needed: `api.gleif.org`, `api.openfigi.com` (443).
- **Web evidence** (`web.py`): the scraping ban is **enforced** by `BLOCKED_HOSTS`/`is_blocked()`,
  checked when filtering hits and again inside the fetcher. A typed description is authoritative
  and skips the web. Every thin result (no provider, search down, 404, PDF, all blocked) returns
  evidence with no description, which must make the classifier **abstain rather than guess from
  the name**. The provider is a Protocol — which search API a bank may call is procurement.
- **Candidate pre-filter** (`candidates.py`): narrows to ~12 per codebook, each already carrying
  its CTS ID, so a returned code cannot be one CTS does not know. It optimises **recall**: a code
  the filter omits is one the model can never return. Mechanisms: the reviewable keyword table
  (`hints.py`, cs+en) and IDF-weighted overlap (`text.py`), plus English division titles
  (`nace_en.py`, scored only — never shown, never in a prompt).
- **Register rules outrank keywords** (`hints.REGISTER_RULES`): GLEIF
  `RESIDENT_GOVERNMENT_ENTITY` → NACE 84, `INTERNATIONAL_ORGANIZATION` → 99, matched on the
  bracketed code so only the register can fire them. "European Investment Bank" says bank; the
  register says what it is. OpenFIGI `Govt` is deliberately **not** a rule (Kommuninvest, a bank,
  issues Govt bonds). Four S.125 families have no `Popis` and are reached by keyword only — keep
  their entries when editing the table.
- **ESA is a grid, not a list**: BA0036 is *entity family* × *control type*, **derived** from the
  codebook names by stripping the control suffix, so a codebook update reshapes it.
  `build_families()` takes one residency block at a time. The filter offers all control variants
  of a family, because the two axes are settled by different evidence.
- **The residual-sector rule**: `BASELINE_ESA_FAMILIES` reserves slots for "Nefinanční podniky"
  **before** ranking. Appending afterwards is not enough — an industrial issuer whose description
  says only "bonds" and "finance" loses every slot to financial families. Measured: 20% of ESA
  recall. Do not turn it into a plain fallback.
- **Navrhovaný kód** (`proposal.py`): the model's first pick; with no model answer, the
  shortlist's first candidate **only if a rule put it there** (score ≥ 5; lexical stops at 1.0)
  **and no rule for another code or ESA family ties with it** — the captive trap, where "bank"
  and "captive" tie and the alphabet would pick the bank. A rule's proposal has no confidence,
  names its rules and lists tied variants. `answered` still means the model answered.
- **The classifier** (`llm.py`, `prompts.py`): the JSON schema pins `code` to an enum of exactly
  the shortlisted codes; `_accept()` re-checks and builds the Suggestion **from the candidate**,
  so CTS ID and label come from the codebook and cannot be invented. Every failure path — no
  candidates, no description, no key, model down, malformed answer, model declined — is an
  **abstention with a readable reason, never a guess and never a lost row**. MO can research an
  issuer; nobody catches a confident wrong code in CTS.
- **Prompt versioning**: bump `PROMPT_VERSION` when wording or schema changes, or yesterday's
  answers keep being served — it is in the cache key.
- **Cache key**: kind + normalized name + description + codebook version + model + prompt
  version. Wider than "cache by normalized name" on purpose: caching on the name alone would
  serve CTS IDs from a previous codebook.
- **Gotcha**: `SqliteCache` defines `__len__`, so an EMPTY cache is falsy and
  `cache or NullCache()` silently disabled caching until it had something in it — which it never
  would. Use explicit `is None` for anything that might define `__len__`.
- **Provider** (`provider.py`): OpenAI Chat Completions over plain httpx, no SDK, so
  `llm_base_url` points at any compatible endpoint. Usage read under both
  `prompt_tokens`/`completion_tokens` and `input_tokens`/`output_tokens`. 429 and 5xx retry;
  4xx does not (a defect in the request). Default `gpt-5.6-luna`, a reasoning model, so
  `LLM_REASONING_EFFORT` (default `none`, empty = not sent) goes out and `temperature` only
  with no effort or `none`. Connections wait at most 5 s.
- **The lookup deadline**: Vercel ends the function at 60 s and the registers alone can take
  ~46 s, so a call that could end after `LOOKUP_DEADLINE_SECONDS` (50) is **not started** — the
  reason says so and the rules' proposal stands. Both of a lookup's two calls must fit, so
  raising `LLM_TIMEOUT_SECONDS`/`LLM_MAX_ATTEMPTS` **disables the model** rather than making it
  patient; startup warns. A cached answer is still served; the null provider gets
  `call_seconds=0`, keeping "no model configured".
- **Blank means unset for `LLM_API_KEY`**: a blank key used to send `Authorization: Bearer `
  on every call. `--golden --model` refuses to start (exit 3) without a callable model.
- **Cost controls, measured not guessed** (`--estimate` reports without calling):
  * Definitions trimmed to `MAX_DEFINITION_CHARS` (1100) — oddíl 46's 56 sub-activities would
    cost more than the rest of the prompt. ~4,300 → ~3,400 tokens per issuer.
  * The trim is by **character budget in codebook order**, not top-N by relevance: a count-based
    cap dropped "Činnosti účelových finančních společností" — the one line putting a captive
    vehicle in division 64 — because English scores zero against Czech, so "best six" silently
    meant "first six". Guarded by `test_classify_cost.py`.
  * Shortlist size is **not** a useful lever: 12→8 saves ~9%, and ESA recall falls to 80% at 6
    because a shorter list loses a whole family of three control variants.
  * The cache is the real saving: a repeated issuer costs nothing.
- **Golden set** (`tests/golden/`): a case counts only when `verified_by` is set; verified and
  provisional are scored separately and **no accuracy may be quoted from provisional cases**.
  All are provisional: 10 fictional traps plus 36 real issuers built from public sources (Q8).
  ESA codes come from CNB BA0036 v044. A real case is scored like the pipeline — description
  **plus** the register fact sheet, replayed from `identity.json`, where an unrecorded request
  is an error, never a silent miss.
- **Pipeline** (`core/suggest.py`): input → evidence → shortlist → classifier → suggestions, in
  one place so API, UI and CLI behave identically. It never raises for ordinary failures: a
  malformed ISIN is a note, a missing description or exhausted budget is an abstention. A
  reviewer always gets a row with the reason.
- **Output row** (`export/columns.py`): the single definition, used by the xlsx and by the JSON
  `row`. Neither builds its own — keep it that way. An abstention leaves the code columns empty
  and puts the reason in `notes`. `SUGGESTION_TEXT_COLUMNS` are written as Excel text so leading
  zeros survive; timestamps are UTC written naive, hence the `(UTC)` headers.
- **Batch** (`batch/reader.py`): unused until E6. Two rules that look like edge cases but are the
  point: a malformed IČO is looked up **as given** rather than falling back to the name column (a
  wrong IČO must surface, not silently return another company), and with no recognised header,
  row 1 is data.
- **Audit**: `request_user()` reads `WEB_USER_HEADER` (default `X-Remote-User`) because in a
  server the OS account is the *service* account. **The proxy must strip any client-supplied
  copy.** `core/audit.py` logs identifier, timestamp, user, sources and outcome — **never
  retrieved content**, so the log can ship without carrying client data.
- **API/UI**: codebooks load **once per process** (lifespan or first lookup, under a lock) and an
  inconsistent or missing set never serves a suggestion — those answer 503 with the reason, the
  page keeps the input, `/health` turns 503, retried after 30 s. It must not raise: on Vercel a
  raising lifespan kills the instance, `/health` included.
  * `CODEBOOK_SOURCE=blob` downloads the four files first (plain GET, atomic writes to `/tmp`).
  * `/probe`: one harmless request per register, secrets shown only as present/absent, never
    called by `/health`. It found that Wikimedia refuses a User-Agent with no contact.
  * Vercel config is `vercel.json` + `[tool.vercel]`/`[tool.uv]` + `.python-version` +
    `.vercelignore`, pinned by `tests/test_vercel_config.py`. No `api/index.py`, no rewrites (the
    FastAPI preset breaks routing), no `requirements.txt`.
  * **No server-side session**: the download re-runs the request — free because the classifier
    caches, and a shared link keeps meaning.
  * The look is the RB team gateway's, **re-implemented** in our CSS: that repo has no licence,
    so nothing is copied and no logo files are committed. Theme toggle in `localStorage`
    (`nace-esa-theme`), shared with `/probe`.
  * htmx's `integrity` must be the hash cdnjs publishes — a wrong one blocks the script silently
    and the page degrades to plain form posts. Keep the `htmx:beforeSwap` handler or 4xx/5xx
    answers (the 503 refusal) show nothing. State labels appear in the template only where
    rendered (CSS comments ship with the page); `tests/test_templates.py` pins them.
  * `ui/prototype/suggest.html` is the design reference; its CSS, theme script, top bar and
    masthead are verbatim copies — change both together.
- **Docker** (`app/Dockerfile`): codebooks and `.env` are mounted, never baked in. Mount a volume
  at `/app/data/cache` or the answer cache and usage ledger are lost on every restart — which
  also makes the daily budget unenforceable, and it fails closed.
- **Not yet built**: `classify/rules.py` (E4's full rule table over register facts; until then
  those facts reach the pre-filter as words in the fact sheet). `tests/fixtures` holds only a
  README. The step 5 RES → CTS mapping left with Tool 2.
