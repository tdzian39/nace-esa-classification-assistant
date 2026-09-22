# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Project: NACE/ESA classification assistant for Raiffeisenbank CZ Finance/MIS

## Context

**Scope since 22 Sept 2026: Tool 1 only.** Tool 2 (RES/OR lookup) is built elsewhere
(`jaeksrampota/res-or-lookup`); its code here is parked and will be removed (roadmap E0).
**Deployment target: Vercel** (not CodeNOW). **The LLM stays off** until an approved endpoint
exists. The plan, every decision and every open question live in `docs/ROADMAP.md` - read it
after this file and keep both in step.

Originally two internal tools sharing one codebase. Users are bank employees (Middle Office
treasury).

Tool 1 – ESA/NACE suggester for foreign issuers. MO sets up foreign securities
issuers in CTS and must pick a 2-digit NACE code and an elementary ESA 2010 sector
code from CTS codebooks. Input: ISIN and/or issuer name and/or activity description.
Output: issuer name, activity description, suggested NACE + CTS ID, suggested ESA +
CTS ID, top 3 candidates each, with evidence.

Tool 2 (PARKED, out of scope here) – RES/OR lookup for client corrections. Reporting reviews monthly rows flagged
by the OKEČ-vs-NACE check. For a list of Czech companies (name or IČO) return one
row per company with RES fields (name, IČO, main NACE Rev.2, other NACE Rev.2, main
NACE Rev.2.1 / "CZ NACE 2025", other NACE Rev.2.1, ESA 2010 sector, founding date)
and OR fields (obchodní firma, IČO, předmět podnikání, předmět činnosti, datum vzniku
a zápisu) side by side, exported as xlsx. Bonus: single-lookup UI.

## Data sources, in priority order

1. DWS (bank data warehouse), read-only. Assume it contains: RES snapshot, OR
   snapshot, CTS codebooks OKEC_NACE2 and BA0036, CTS issuer and instrument master.
   Connection details come from environment variables; never hardcode. Until real
   table names are confirmed, put all SQL behind a single `dws.py` adapter with
   clearly named functions and TODO markers on table/column names.
2. Public ARES REST API (ares.gov.cz) as fallback for IČOs missing in DWS. Mark
   such rows as source="ARES_LIVE".
3. GLEIF (api.gleif.org) and OpenFIGI (api.openfigi.com) for a foreign issuer given by
   ISIN: the issuer's LEI record (legal name, country, legal form, entity category,
   direct/ultimate parent) and the instrument (market name, security type, market sector).
   Public, keyless, fail-soft; rows are stamped GLEIF / OPENFIGI. Added 2026-09-22.
4. Web search for activity descriptions of foreign issuers only.
Do not scrape apl.czso.cz or or.justice.cz.

## Codebooks (xlsx, bootstrap only; DWS tables are the runtime source)

- CTS_BA0036_NEW.xlsx: ID (CTS ID), VALUE (ESA code), DESCRIPTION.
  REAL FILE (received 2026-09-22): headers are `.ID`, `Popis`, `Hodnota` - i.e. ID/.ID,
  DESCRIPTION=Popis, VALUE=Hodnota. 110 rows, no duplicates, one non-leaf placeholder
  `0000000`.
- BA0036_2024_jen_validni.xlsx: Kód (ESA code), Název, Popis. Only these leaf codes
  are valid in CTS. Reject parent codes.
  REAL FILE: 109 rows, plus three extra columns (`pritomnost v ...`) that are ignored.
- IMPORTANT, from the real files: both BA0036 files key on a **7-digit CNB code**
  (`1221300` = Banky pod zahraniční kontrolou), NOT the 5-digit ESA form `S.12213` the
  spec implied. RES/ARES report the ESA form instead (`12203` for Raiffeisenbank), and
  `12203`/`1220300` do **not** exist in the CTS codebook: CTS splits S.1220x further into
  banks (1221x) / credit unions (1222x) / other deposit-takers (1224x), and S.125 likewise.
  So the step 5 mapping "RES ESA sector -> BA0036 ID" is **not** a lexical lookup for those
  sectors and needs a decision before it is built.
- CTS_OKEC_NACE2.xlsx: ID (CTS ID), VALUE (first 2 chars of NACE), DESCRIPTION.
  REAL FILE (received 2026-09-22): matches the spec exactly. Sheet `Sheet1` (a blank
  `Sheet2` follows it), 87 divisions, CTS IDs 455-542.
- NACE_STAT.xlsx: NACE (2-digit), Zkrtext, Text. Multiple rows per 2-digit code.
  REAL FILE: sheet `KLAS80143_CS`, 969 rows, 5 columns - `uroven` (2=division,
  3=group, 4=class) and `chodnota` (the code at that level) in addition to the three
  named above; the two extra columns are currently ignored. Median 9 rows per division,
  max 56 (oddil 46 Velkoobchod).
- ALL FOUR real codebooks load with 0 errors / 0 warnings, version `cb-3b12e64837840ca0`:
  110 CTS ESA entries, 109 valid ESA leaves, 87 NACE divisions in CTS and 87 in
  NACE_STAT - a perfect 1:1 match with no unlabelled or unmapped division.
- Prompt budget measured on the real file (~4 chars/token): all 87 SHORT division labels
  ~895 tokens; every label of every division ~10,400; the full labels of a 12-division
  shortlist ~1,440. That is the evidence for the two-stage classifier - narrow on short
  labels, decide on full texts - rather than one prompt holding the whole codebook.

## Repository layout (everything lives under /app)

/app
  /core
    /identifiers      ico.py (normalize to 8 digits, mod-11 check), isin.py (format check)
    /sources          base.py (common interface), dws.py, ares.py, web.py,
                      gleif.py + openfigi.py + identity.py (ISIN -> issuer)
    /codebooks        loaders, versioning, startup consistency check
    /classify         rules.py (deterministic RES→CTS ID mapping), llm.py, candidates.py (pre-filter)
    /export           xlsx.py (one row per subject, columns prefixed RES_/OR_/CTS_)
  /api                FastAPI: POST /batch (xlsx in → xlsx out), GET /lookup?ico=…, POST /suggest
  /ui                 minimal: single lookup page, batch upload/download page
  /tests
    /fixtures         verified IČO → expected output
    /golden           verified issuer name/description → expected NACE/ESA
  /config             settings via pydantic-settings, .env.example
  README.md
  pyproject.toml

Stack: Python 3.12, FastAPI, pandas, openpyxl, pydantic. Keep the UI server-rendered
(Jinja2 + htmx) unless told otherwise. No JS framework.

## Build order (historical - superseded by `docs/ROADMAP.md` on 22 Sept 2026)

1. Codebook loaders + tests. Startup check: every emitted CTS ID must exist in the
   loaded codebook.
2. IČO normalization + DWS adapter + ARES fallback. Tool 2 end to end via CLI.
3. Batch xlsx in/out. Tolerate messy input: mixed name/IČO column, whitespace, IČO
   stored as number with lost leading zeros.
4. Single-lookup API + UI.
5. Deterministic classifier: RES ESA sector → BA0036 ID, RES 2-digit NACE → OKEC_NACE2 ID.
6. LLM classifier for foreign issuers (see rules below).

Stop after each step, run tests, summarize what exists, wait for go-ahead.

## Hard rules

- Every output row carries: source (DWS/ARES_LIVE/WEB), snapshot or retrieval
  timestamp, codebook version.
- Keep full NACE codes in data; truncate to 2 digits only at the CTS-ID mapping step.
- Return both NACE revisions from RES plus an explicit `nace_mismatch` boolean.
- NACE is a list (main + others), never a single field.
- Never write to CTS or DWS. Read-only credentials.
- Log every lookup: identifier, timestamp, requesting user.
- Nothing retrieved from DWS is ever sent to an LLM. Only public foreign-issuer
  name, web-derived description, public register facts about the issuer (GLEIF,
  OpenFIGI) and codebook labels go into prompts.
- LLM output is a structured selection from a supplied candidate list (10–15
  candidates after pre-filter), with code, one-sentence justification, confidence.
  Any code not in the list is rejected. Return top 3. Cache by normalized name.
  Model name and provider are configurable; default to the cheapest option and
  measure against /tests/golden before changing.
- Czech entities with a RES record skip the LLM entirely.
- Deployment target is **Vercel** (decided 22 Sept 2026; the earlier "Dockerfile only, no PaaS"
  rule is withdrawn - the Dockerfile may stay for local runs). Design for serverless: no
  writable disk except /tmp, no long-lived process state, lazy startup, no reverse proxy in
  front (a client-settable header is not an identity). See `docs/ROADMAP.md` section 4.
- The repository is PUBLIC as of 22 Sept 2026: never commit the CTS codebooks, `.env`, audit
  logs, real lookups or anything else bank-internal. It must go private before codebooks or
  real data are bundled anywhere (roadmap D2).
- The LLM stays off until an approved endpoint exists (roadmap E9). Keep its tests green; do
  not extend the provider path before then.

## Development commands

All commands run from `app/` (the Python project root; `pyproject.toml` lives there).
The venv is `.venv/` at the repository root, built from Anaconda Python 3.13.9 because no
Python 3.12 exists on this machine. `pyproject.toml` declares `requires-python >= 3.12`, so
code must stay 3.12-compatible (no 3.13-only stdlib or syntax). The bare `python` on PATH
is the Windows Store stub; always use the venv interpreter. The repo path contains spaces,
so quote it.

```bash
../.venv/Scripts/python.exe -m pip install -e ".[dev]"      # install (editable)
../.venv/Scripts/python.exe -m core.sources 49240901        # Tool 2 lookup (--json, --no-dws)
../.venv/Scripts/python.exe -m core.batch klienti.xlsx     # Tool 2 batch (-o OUT, --sheet S)
../.venv/Scripts/python.exe -m core.classify "popis cinnosti"  # Tool 1 shortlist (--verbose)
../.venv/Scripts/python.exe -m core.classify --golden       # pre-filter recall over tests/golden
../.venv/Scripts/python.exe -m pytest                        # full suite (pythonpath="." is set in pyproject)
../.venv/Scripts/python.exe -m pytest tests/identifiers/test_ico.py -k checksum   # one file / one test
../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .   # lint + format check
../.venv/Scripts/python.exe -m core.codebooks                # startup consistency check against data/codebooks
../.venv/Scripts/python.exe -m core.codebooks --no-strict --json --dir PATH        # report even with errors
```

CLI exit codes: 0 consistent, 1 report has errors, 2 a file is missing or has wrong columns.

Test module basenames must be unique across `tests/` (pytest runs in default import mode),
hence `test_codebooks_*.py`. The real-file smoke test skips when the xlsx codebooks are absent
from `app/data/codebooks/` (they are bank-internal and git-ignored); every other test builds
synthetic xlsx fixtures via `tests/codebooks/conftest.py` (`write_xlsx`, `make_codebook_dir`).

## Current state and next step (2026-09-22)

**The tool ships DETERMINISTIC.** No `LLM_API_KEY` is set, and that is deliberate for now,
not an unfinished edge. In this mode it:

* narrows 87 NACE divisions and 56 ESA sectors to ~12 candidates each,
* resolves every candidate's CTS ID up front, so MO can type it straight into CTS,
* shows the shortlist on the page and writes it to the xlsx (`NACE_candidates`,
  `ESA_candidates`),
* states in the panel that it did not choose, and why.

Do not present this as a failure state in UI copy or docs. Measured on the golden cases the
deterministic top pick is right 90% of the time for NACE and 60% for ESA, so it is genuinely
useful on its own - it just cannot justify its choice or resolve the distinctions that turn
on a sentence ("holds no banking licence", "not a money market fund").

**NEXT STEP: follow `docs/ROADMAP.md`** - E0 (scope, private repo, remove the parked Tool 2,
drop the unused `pandas`), then E1 (deploy the deterministic mode on Vercel), E2 (access and
audit), E3-E5 (name lookup, structured hints, more sources), E6-E7 (batch, confirm/history),
E8 (real golden set) alongside. PR #1 (ISIN -> GLEIF/OpenFIGI identity) is the first step
already taken.

**Turning the OpenAI API on is DEFERRED (roadmap E9).** Everything for it is built and tested
against a stub (`core/classify/{prompts,provider,llm,cache,budget}.py`). When an endpoint is
approved, switching it on means:

1. put `LLM_API_KEY` in `app/.env` (git-ignored);
2. confirm `LLM_MODEL` - the default is a cheap placeholder marked TODO;
3. run a handful of real issuers: the provider path has NEVER made a live call, so expect to
   fix something small the first time;
4. have MO check those suggestions and record the confirmed ones in `tests/golden/cases.json`
   with `verified_by` set - that is what finally makes accuracy measurable;
5. `python -m core.classify --usage` to see real spend against the limits.

Spending limits are already enforced and fail closed (see `core/classify/budget.py`).

## Architecture notes (what exists after build steps 1-3 + the Tool 1 pre-filter)

- **Settings** (`config/settings.py`): pydantic-settings `Settings` reading env / `app/.env`.
  Relative paths resolve against `APP_ROOT` (the `app/` directory), never the cwd.
  `get_settings()` is `lru_cache`d; tests build `Settings(...)` directly.
- **Codebooks** (`core/codebooks`): `xlsx.read_table()` (openpyxl only, header row found within
  20 rows, case/accent-insensitive column aliases) → `loaders.load_codebooks()` builds a frozen
  `CodebookSet` → `consistency.check_consistency()` returns a `ConsistencyReport` of `Finding`s
  with stable codes (`E_*` errors, `W_*` warnings, `I_*` info). `loaders.load_and_check()` is the
  startup entry point and raises `CodebookConsistencyError` on any error. The `_build_*`
  helpers in `loaders.py` take plain rows so DWS tables can replace xlsx later without touching
  the models. `versioning.build_version()` derives `cb-<16hex>[+label]` from file hashes; every
  output row later carries `version.id` as `codebook_version`.
- **CTS-ID emission points**: exactly two, `CodebookSet.cts_id_for_esa()` (valid leaf only;
  parents raise `InvalidEsaCodeError`) and `CodebookSet.cts_id_for_nace()`. The latter is the
  only caller of `normalize.nace_to_division()`, which is the single place NACE is truncated
  to 2 digits. Keep it that way. ESA codes compare by a digits-only key (`S.11001` → `11001`);
  CTS IDs are opaque text with leading zeros preserved.
- **Identifiers** (`core/identifiers`): pure functions, no I/O. `normalize_ico()` /
  `normalize_isin()` raise `InvalidIcoError` / `InvalidIsinError` carrying a Literal `reason`
  (e.g. `checksum`, `too_long`, `unsupported_type`); the `is_valid_*` / `try_normalize_*`
  helpers never raise. Pandas NaN blanks must be turned into `None` by the caller.
- **Sources** (`core/sources`): `base.py` holds the record model — a subject is a `ResRecord`
  half plus an `OrRecord` half, each with its own `Provenance` (source, `retrieved_at`,
  `snapshot_at`). NACE is always a list of `NaceAssignment` carrying `revision` (`"2"` /
  `"2.1"`) and `is_main`; full codes are stored verbatim, so `nace_to_division()` is still
  called only by `cts_id_for_nace()`. `ResRecord.nace_mismatch` compares the prevailing codes
  on digits only and returns **`None`, not `False`, when a revision is missing**. Keep it
  three-valued: "cannot tell" is not "they agree".
- **Resolution policy** (`resolver.py`): DWS first, ARES second. A source returning `None`
  means the register lacks the subject; raising `SourceUnavailableError` means it could not be
  asked. Never collapse the two — an outage must not be reported as `not_found`. A partial hit
  is completed from the next source via `SubjectRecord.merge`, which never overwrites a
  higher-priority half. An all-digit query with a bad check digit is `invalid_input`, never a
  name search.
- **DWS** (`dws.py`): UNUSED - the bank is not granting DWS access for this project (decided
  2026-09-22). Tool 1 never touched it; Tool 2 resolves everything through ARES and stamps
  rows `ARES_LIVE`. Kept and tested: setting `DWS_DSN` puts it back in front, nothing else
  changes. Its `TODO(dws-schema)` placeholders are still exactly what would need answering.
  It is the only module with SQL, and the only place naming a table or column.
  All names are placeholders in `TABLES` / `COLUMNS_*` / `REVISION_VALUES` / `ACTIVITY_KINDS`
  marked `TODO(dws-schema)`; confirming the schema is an edit of those dicts, not of the
  queries. `ensure_read_only()` refuses anything that is not a single SELECT/WITH, and the IČO
  is always a bind parameter. `pyodbc` is imported lazily and is deliberately not a dependency
  until the real driver is confirmed.
- **ARES** (`ares.py`): endpoints and JSON field names verified against the live API on
  2026-09-22 (IČO 49240901), documented in the module docstring. `czNace2008` is Rev. 2,
  `czNace` is Rev. 2.1; the ESA sector is `statistickeUdaje.institucionalniSektor2010` as bare
  digits. VR entries carrying `datumVymazu` are historical and are dropped. The name-search
  endpoint is the one thing still unverified (`TODO` in the module). ARES times out on
  back-to-back requests, hence `ares_min_interval_seconds` throttling plus retry with backoff —
  do not remove it when writing the step 3 batch loop.
- **Audit user in the web app**: in a server the OS account is the SERVICE account, so
  `api.main.request_user()` reads the signed-in user from `WEB_USER_HEADER`
  (default `X-Remote-User`), set by the reverse proxy / SSO. **That proxy must strip any
  client-supplied copy** - a header a browser can set is not an identity. Without it every
  lookup is attributed to one name, which does not satisfy 'log the requesting user'.
- **Audit** (`core/audit.py`): every lookup is logged with identifier, timestamp, user,
  sources and outcome, and **never with retrieved content**. Keep it that way so the log can
  be shipped without carrying client data.
- **Tool 2 CLI**: `python -m core.sources ICO_OR_NAME [...] [--file F] [--json] [--no-dws]`.
  Exit codes: 0 all resolved, 1 some not found/ambiguous/invalid, 2 no source could be asked.
  A missing codebook only drops `codebook_version` from the row; it never blocks a lookup.
- **Real-data findings worth knowing**: ARES returns NACE codes of mixed length in one list
  (`"24"`, `"257"`, `"25500"`) and sometimes the placeholder `"00"`, which `nace_to_division()`
  rejects by design. Step 5 must decide what an unmappable code does to a row rather than
  assume every code maps.
- **Output row contract** (`core/export/columns.py`): the single definition of a result row,
  used by the batch sheet and by `python -m core.sources`. Neither builds its own row - keep
  it that way. `CTS_*` names are reserved for step 5; do not emit an always-empty column.
- **Export** (`core/export/xlsx.py`): Subjects + Run sheets. IČO and NACE columns are written
  with the `@` text format so leading zeros survive; timestamps are converted to UTC and
  written naive (Excel has no offsets), which is why those headers say `(UTC)`.
- **Batch** (`core/batch`): `reader.py` tolerates title rows, missing headers, mixed
  name/IČO columns and Excel-eaten leading zeros. Two rules that look like edge cases but are
  the point: a malformed IČO is looked up as given rather than falling back to the name column
  (a wrong IČO must surface, not silently return another company), and with no recognised
  header row 1 is kept as data rather than discarded. `runner.py` writes one row per input
  row **including misses**, and caches by normalized IČO so a repeated client is one lookup.
- **Candidate pre-filter** (`core/classify`): the stage that makes the classifier both
  cheap and safe. `candidates.py` narrows 87 NACE divisions / 56 rest-of-world ESA codes to
  ~12, and every candidate already carries its CTS ID - so a code the classifier returns
  cannot be one CTS does not know, and a code with no CTS ID is never offered in the first
  place. It optimises **recall**, not precision: a code the filter omits is one the model can
  never return. Two mechanisms: the reviewable keyword table in `hints.py` (Czech + English)
  and IDF-weighted lexical overlap in `text.py`, which works on Czech input and barely at all
  on English - the model-based narrowing stage will be the primary path and slots in behind
  the same `CandidateFilter` protocol.
- **ESA is a grid, not a list** (`hints.py`): BA0036 is *entity family* x *control type*
  (Banky / Pojišťovny / Kaptivní ... x veřejné | soukromé národní | pod zahraniční
  kontrolou). The grid is **derived** from the codebook names by stripping the control
  suffix, so a codebook update reshapes it. `build_families()` takes ONE residency block at a
  time - both blocks spell the families identically. The filter picks families and offers all
  their control variants, because the two axes are settled by different evidence.
- **The residual-sector rule**: `BASELINE_ESA_FAMILIES` reserves slots for "Nefinanční
  podniky" before the ranking is applied. Appending it afterwards is not enough - an
  industrial issuer whose description merely says "bonds" and "finance" scores weakly against
  a dozen financial families that then consume every slot. Measured: that was 20% of ESA
  recall on the golden set. Do not turn it into a plain fallback.
- **Golden set** (`tests/golden/`, loader in `classify/golden.py`): a case counts only when
  `verified_by` is set. Verified and provisional are scored separately and **no accuracy may
  be quoted from provisional cases**. All 10 shipped cases are provisional; each one traps a
  specific failure (see the README there). `python -m core.classify --golden` reports
  recall@k with no API key.
- **Issuer identity** (`core/sources/gleif.py`, `openfigi.py`, `identity.py`; added
  2026-09-22): an ISIN is resolved BEFORE the web is searched. GLEIF
  `GET /lei-records?filter[isin]=` gives the LEI record (legal name, country, legal form
  ELF code, entity category GENERAL / FUND / BRANCH / RESIDENT_GOVERNMENT_ENTITY /
  INTERNATIONAL_ORGANIZATION / SOLE_PROPRIETOR, sub-category, entity and registration
  status); `/direct-parent` and `/ultimate-parent` give the parents' records (404 = none
  reported, then `/direct-parent-reporting-exception` says why, e.g. NO_KNOWN_PERSON).
  OpenFIGI `POST /v3/mapping` gives the instrument (name, securityType, marketSector).
  All verified live 2026-09-22 (Deutsche Bank AG, BMW Finance N.V. -> BMW AG, Land Berlin,
  EIB). `IssuerIdentifier.identify()` never raises for a source failure: a register that
  could not be asked is a note, one that has nothing is a note, and the other half is kept.
  * The identity's `fact_sheet()` is Czech prose whose parentheses carry the English
    words the hint table reacts to ("investment fund", "government", "supranational",
    "municipality"), so a GLEIF category reaches the ESA family shortlist with no new
    mechanism; a legal name containing BANK puts 64 and the bank family on the same way.
    The sheet is appended to the description the pre-filter scores and the model reads.
  * The legal name is also the web search query - a twelve-character ISIN never was one.
    What MO typed still wins as the displayed name.
  * Facts are stated, never decided: "the ultimate parent sits in another country" is a
    fact; whether that is "pod zahraniční kontrolou" in BA0036's sense is the classifier's
    call (and part of the open S.12203 question above).
  * Rate limits: GLEIF publishes 60/min (throttle 1.0 s); OpenFIGI answered keyless with
    `ratelimit-policy: 25;w=60` (throttle 2.5 s; a free key gives 250/min). 429 and 5xx
    retry with linear backoff, other 4xx do not. Up to 4 GLEIF requests per ISIN
    (`GLEIF_FETCH_PARENTS=false` makes it 1) and 1 OpenFIGI request.
  * Coverage gap worth knowing: GLEIF has no ISIN mapping for the iShares Core MSCI World
    ETF (IE00B4L5Y983) while OpenFIGI names it - hence both registers, in that order.
  * `Source` grew to DWS / ARES_LIVE / WEB / GLEIF / OPENFIGI. A suggestion row's `source`
    is `GLEIF+OPENFIGI+WEB` for an ISIN lookup and plain `WEB` for a name-only one; the
    audit line, `/health` (`gleif_enabled`, `openfigi_enabled`) and the JSON `identity`
    object follow. Egress the runtime needs: api.gleif.org, api.openfigi.com (443).
- **Web evidence** (`core/sources/web.py`): name or ISIN -> activity description + citable
  sources, stamped `WEB`. The ban on scraping `apl.czso.cz` / `or.justice.cz` is
  ENFORCED by `BLOCKED_HOSTS` / `is_blocked()`, checked both when filtering hits and
  again inside the fetcher, not merely documented. A description the user typed is
  authoritative and skips the web entirely. Nothing here ever fails loudly: every thin
  result (no provider, search down, page 404, PDF, all hits blocked) comes back as
  evidence with no description, which must make the classifier abstain rather than guess
  from the issuer's name. The search provider is a Protocol - which search API a bank may
  call is procurement, not engineering - and `HttpSearchProvider` is configured by URL
  and field paths with TODO markers, defaults following Brave's response shape.
- **The classifier** (`core/classify/llm.py`, `prompts.py`, `provider.py`, `cache.py`):
  the model is handed a shortlist and the JSON schema pins `code` to an enum of exactly
  those codes, so the provider cannot emit anything else; `_accept()` re-checks it and
  builds the Suggestion FROM THE CANDIDATE, so the CTS ID and label come from the
  codebook and cannot be invented. Every failure path - no candidates, no description,
  no key, model down, malformed answer, model declined - becomes an ABSTENTION with a
  readable reason, never a guess and never a lost row. Keep it that way: MO can research
  an issuer themselves, but nobody catches a confident wrong code in CTS.
- **Prompt versioning**: `PROMPT_VERSION` in `prompts.py` is part of the cache key. Change
  the wording or the schema, bump it, or yesterday's answers keep being served.
- **Cache key** (`cache.py`) is kind + normalized name + description + codebook version +
  model + prompt version. Wider than the 'cache by normalized name' rule on purpose:
  caching on the name alone would keep serving CTS IDs derived from a previous codebook.
- **Gotcha worth remembering**: `SqliteCache` defines `__len__`, so an EMPTY cache is
  falsy. `cache or NullCache()` silently discarded it and disabled caching until the
  cache had something in it - which it never would. Use explicit `is None` checks for any
  object that might define `__len__`.
- **Provider** (`provider.py`): OpenAI Chat Completions over plain httpx, no SDK
  dependency, so `llm_base_url` points at any compatible endpoint. Request shape verified
  against the structured-outputs guide 2026-09-22. Token usage is read under BOTH
  `prompt_tokens`/`completion_tokens` and `input_tokens`/`output_tokens`. 429 and 5xx
  retry; 4xx does not (it is a defect in the request).
- **Cost controls, measured not guessed** (`python -m core.classify "..." --estimate`
  reports the figure without calling anything):
  * Candidate definitions are trimmed to `MAX_DEFINITION_CHARS` (1100). NACE_STAT gives
    oddil 46 fifty-six sub-activities (~3,000 chars) - one candidate costing more than
    the rest of the prompt. Typical divisions fit whole and are sent whole. ~4,300 ->
    ~3,400 tokens per issuer.
  * The trim is by CHARACTER BUDGET IN CODEBOOK ORDER, not top-N by relevance. A
    count-based cap ranked by overlap dropped 'Cinnosti ucelovych financnich spolecnosti'
    - the one line putting a captive vehicle in division 64 - because an English
    description scores zero against Czech text, so 'best six' silently meant 'first six'.
    Guarded by tests/classify/test_classify_cost.py.
  * `llm_max_output_tokens` (700) caps generation.
  * Shortlist size is NOT a useful lever: 12 -> 8 saves only ~9%, and ESA recall falls to
    80% at 6 because families come in three control variants, so a shorter list loses a
    whole family. `DEFAULT_LIMIT` stays above that cliff on purpose.
  * The cache is the real saving: a repeated issuer costs nothing.
- **Pipeline** (`core/suggest.py`): input -> evidence -> shortlist -> classifier ->
  suggestions, in one place so the API, the UI and any CLI behave identically. It never
  raises for ordinary failures: a malformed ISIN is a note, a missing description or an
  exhausted budget is an abstention. A reviewer always gets a row with the reason.
- **API/UI** (`api/main.py`, `ui/templates/suggest.html`): `GET /` form, `POST /suggest`
  page, `POST /api/suggest` JSON, `GET /suggest.xlsx` download, `GET /health`.
  * Codebooks load ONCE at startup and an inconsistent set stops the service - emitting
    a CTS ID from a bad codebook is the failure nobody catches downstream.
  * NO server-side session: the download re-runs the request, which is free because the
    classifier caches, and keeps a shared link meaningful.
  * The page states plainly when the model or the search provider is not configured, so
    MO knows it is being shortlisted for rather than answered.
  * Tool 2's `/batch` and `/lookup` are deliberately NOT mounted (that tool is parked);
    `python -m core.batch` still works.
  * The template is generated from `ui/prototype/suggest.html` - keep the prototype in
    step when changing the design, it is the approved reference.
- **Docker** (`app/Dockerfile`): codebooks and `.env` are MOUNTED, never baked in. Mount
  a volume at `/app/data/cache` or the answer cache and usage ledger are lost on every
  restart - which also means the daily budget cannot be enforced and fails closed.
- **Not yet built**: `core/classify/rules.py` (step 5, the deterministic RES -> CTS
  mapping for Czech subjects, and see the open S.12203 question above).
  `tests/fixtures` is empty. The OpenAI key is NOT needed to run or test any of the
  above; it is needed only to measure real accuracy against a verified golden set.
- **Later steps**: `docs/ROADMAP.md` - epics E0-E10 with design, definition of done and the
  open decisions (Vercel account, repo visibility, codebook delivery, database, authentication).
