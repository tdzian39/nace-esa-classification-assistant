# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Project: NACE/ESA classification assistant for Raiffeisenbank CZ Finance/MIS

## Context

**Scope since 22 Sept 2026: Tool 1 only.** Tool 2 (RES/OR lookup) is built elsewhere
(`jaeksrampota/res-or-lookup`); its code was removed from this repo in PR #5 (roadmap E0.3,
22 Sept 2026).
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

Tool 2 (REMOVED from this repo in PR #5) – RES/OR lookup for client corrections: for a
list of Czech companies (name or IČO), RES and OR fields side by side. It lives in
`jaeksrampota/res-or-lookup`.

## Data sources, in priority order

1. GLEIF (api.gleif.org) and OpenFIGI (api.openfigi.com) for a foreign issuer given by
   ISIN: the issuer's LEI record (legal name, country, legal form, entity category,
   direct/ultimate parent) and the instrument (market name, security type, market sector).
   Public, keyless, fail-soft; rows are stamped GLEIF / OPENFIGI. Added 2026-09-22.
2. Web search for activity descriptions of foreign issuers only.

DWS (the bank data warehouse; access not granted for this project) and the public ARES
API were Tool 2's sources and left with it in PR #5; nothing here reads them.
Do not scrape apl.czso.cz or or.justice.cz.

## Codebooks (xlsx; on Vercel they come from a private Blob store, roadmap D3)

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
  So a register's ESA sector is **not** a lexical lookup into BA0036 for those sectors. The
  step 5 RES mapping that needed one left with Tool 2; which of those items a bank gets is
  still open (roadmap Q7).
- CTS_OKEC_NACE2.xlsx: ID (CTS ID), VALUE (first 2 chars of NACE), DESCRIPTION.
  **CTS is on CZ-NACE 2025 (NACE Rev. 2.1)** (roadmap Q5, answered 22 Sept 2026): 87 divisions,
  no 45, CTS ID 496 missing exactly where 45 sat. Codes from Rev. 2 sources need mapping.
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
    /identifiers      ico.py (8 digits, mod-11 check; for the batch reader), isin.py (format check)
    /sources          base.py (Source, Provenance, the Source*Error classes), web.py,
                      gleif.py + openfigi.py + identity.py (ISIN -> issuer)
    /codebooks        loaders, versioning, startup consistency check; blob.py (private Vercel Blob, E1)
    probe.py          the /probe register checks (E1)
    /classify         llm.py, candidates.py (pre-filter); rules.py (the E4 rule table) comes later
    /export           columns.py (the suggestion row), xlsx.py (Subjects + Run sheets)
    /batch            reader.py (messy xlsx in; roadmap E6 reuses it)
  /api                FastAPI: GET /, POST /suggest, POST /api/suggest, GET /suggest.xlsx, /health, /probe
  /ui                 minimal: the single lookup page (a batch page comes with E6)
  /tests
    /fixtures         reserved for recorded register payloads (only a README so far)
    /golden           cases.json (fictional traps + real issuers, all provisional), identity.json
  /config             settings via pydantic-settings
  .env.example        the settings with their defaults; copy to app/.env (git-ignored)
  vercel.json, .vercelignore, .python-version   the Vercel config (E1; with [tool.vercel] in pyproject)
  README.md
  pyproject.toml

Stack: Python 3.12, FastAPI, openpyxl, pydantic. Keep the UI server-rendered
(Jinja2 + htmx) unless told otherwise. No JS framework. No pandas (dropped in PR #5, it
was imported nowhere); numpy is a dev extra only, because the identifier and codebook
tests feed numpy scalars to the normalisers.

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

- Every output row carries: source (GLEIF/OPENFIGI/WEB), snapshot or retrieval
  timestamp, codebook version.
- Keep full NACE codes in data; truncate to 2 digits only at the CTS-ID mapping step.
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
- Deployment target is **Vercel** (decided 22 Sept 2026; the earlier "Dockerfile only, no PaaS"
  rule is withdrawn - the Dockerfile may stay for local runs). Design for serverless: no
  writable disk except /tmp, no long-lived process state, lazy startup, no reverse proxy in
  front (a client-settable header is not an identity). See `docs/ROADMAP.md` section 4.
- The repository is PUBLIC and stays public (decided 22 Sept 2026, roadmap D2): never commit
  the CTS codebooks, `.env`, audit logs, real lookups or anything else bank-internal. The
  codebooks reach a deployment only from a private Vercel Blob store (roadmap D3).
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
../.venv/Scripts/python.exe -m core.classify "popis cinnosti"  # Tool 1 shortlist (--verbose)
../.venv/Scripts/python.exe -m core.classify --golden       # pre-filter recall + top-1 over tests/golden
../.venv/Scripts/python.exe -m core.classify --golden-capture   # re-record the golden register answers
../.venv/Scripts/python.exe -m core.classify --golden --model   # the golden set through the model (needs LLM_API_KEY, costs money)
../.venv/Scripts/python.exe -m pytest                        # full suite (pythonpath="." is set in pyproject)
../.venv/Scripts/python.exe -m pytest tests/identifiers/test_ico.py -k checksum   # one file / one test
../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .   # lint + format check
../.venv/Scripts/python.exe -m core.codebooks                # startup consistency check against data/codebooks
../.venv/Scripts/python.exe -m core.codebooks --no-strict --json --dir PATH        # report even with errors
```

CLI exit codes: 0 consistent, 1 report has errors, 2 a file is missing or has wrong columns.

Test module basenames must be unique across `tests/` (pytest runs in default import mode),
hence `test_codebooks_*.py`. Without the real xlsx codebooks in `app/data/codebooks/` (they are
bank-internal and git-ignored) 16 tests skip: the real-file smoke test (2),
`tests/classify/test_classify_real_recall.py` (5) and `test_classify_cost.py` (9). The codebook
tests build synthetic xlsx fixtures via `tests/codebooks/conftest.py` (`write_xlsx`,
`make_codebook_dir`); the classify tests build codebook models in memory.

## Current state and next step (2026-09-22)

**The tool ships DETERMINISTIC.** No `LLM_API_KEY` is set, and that is deliberate for now,
not an unfinished edge. In this mode it:

* narrows 87 NACE divisions and 56 ESA sectors to ~12 candidates each,
* resolves every candidate's CTS ID up front, so MO can type it straight into CTS,
* shows the shortlist on the page and writes it to the xlsx (`NACE_candidates`,
  `ESA_candidates`),
* states in the panel that it did not choose, and why.

Do not present this as a failure state in UI copy or docs. On the ten fictional, provisional
golden cases the deterministic top pick was right 90% of the time for NACE and 60% for ESA -
an indication for developers, not an accuracy figure to quote - so it is genuinely useful on
its own; it just cannot justify its choice or resolve the distinctions that turn on a
sentence ("holds no banking licence", "not a money market fund").

**NEXT STEP: `docs/ROADMAP.md` section 0** - E0, E1 and E8 are done (live on Vercel with the
real codebooks since 22 Sept 2026). The short list: E2 (login + audit, needs D4), the Pro plan,
two cheap E4 rules + English labels, the FIRDS LEI fallback; the rest is optional. Older plan:
E2 (access and audit), E3-E5 (name lookup, structured hints, more sources), E6-E7 (batch,
confirm/history), E8 (real golden set) alongside. Taken so far: PR #1 (ISIN -> GLEIF/OpenFIGI
identity) and E0.3, the removal of the parked Tool 2 and the unused `pandas` (PR #5). E0 is
complete: E0.2 (a private repository) was dropped on 22 Sept 2026 - the repository stays
public and the codebooks go to a private Blob store (roadmap D2, D3). Decided the same day
(roadmap section 7): the E2 shared-password gate is the permanent login (no Entra ID, D5), no
data-classification sign-off gates go-live (D6), and the golden set is built from public
sources without MO, every case provisional (Q8).

**Turning the OpenAI API on is DEFERRED (roadmap E9).** Everything for it is built and tested
against a stub (`core/classify/{prompts,provider,llm,cache,budget}.py`). When an endpoint is
approved, switching it on means:

1. put `LLM_API_KEY` in `app/.env` (git-ignored);
2. confirm `LLM_MODEL` - the default is a cheap placeholder marked TODO;
3. run a handful of real issuers: the provider path has NEVER made a live call, so expect to
   fix something small the first time;
4. have someone check those suggestions against CTS and record the confirmed ones in
   `tests/golden/cases.json` with `verified_by` set - that is what finally makes accuracy
   measurable;
5. `python -m core.classify --usage` to see real spend against the limits.

Spending limits are already enforced and fail closed (see `core/classify/budget.py`).

## Architecture notes (what exists: Tool 1, after the Tool 2 removal in PR #5)

- **Settings** (`config/settings.py`): pydantic-settings `Settings` reading env / `app/.env`.
  Relative paths resolve against `APP_ROOT` (the `app/` directory), never the cwd.
  `get_settings()` is `lru_cache`d; tests build `Settings(...)` directly.
- **Codebooks** (`core/codebooks`): `xlsx.read_table()` (openpyxl only, header row found within
  20 rows, case/accent-insensitive column aliases) → `loaders.load_codebooks()` builds a frozen
  `CodebookSet` → `consistency.check_consistency()` returns a `ConsistencyReport` of `Finding`s
  with stable codes (`E_*` errors, `W_*` warnings, `I_*` info). `loaders.load_and_check()` is the
  startup entry point and raises `CodebookConsistencyError` on any error. The `_build_*`
  helpers in `loaders.py` take plain rows so another source can replace xlsx later without touching
  the models. `versioning.build_version()` derives `cb-<16hex>[+label]` from file hashes; every
  suggestion row carries `version.id` as `codebook_version`.
- **CTS-ID emission points**: exactly two, `CodebookSet.cts_id_for_esa()` (valid leaf only;
  parents raise `InvalidEsaCodeError`) and `CodebookSet.cts_id_for_nace()`. The latter is the
  only caller of `normalize.nace_to_division()`, which is the single place NACE is truncated
  to 2 digits. Keep it that way. ESA codes compare by a digits-only key (`S.11001` → `11001`);
  CTS IDs are opaque text with leading zeros preserved.
- **Identifiers** (`core/identifiers`): pure functions, no I/O. `normalize_ico()` /
  `normalize_isin()` raise `InvalidIcoError` / `InvalidIsinError` carrying a Literal `reason`
  (e.g. `checksum`, `too_long`, `unsupported_type`); the `is_valid_*` / `try_normalize_*`
  helpers never raise. A dataframe's NaN blanks must be turned into `None` by the caller.
  The suggester only ever normalises an ISIN; the IČO half serves `core/batch/reader.py`
  and stays until E6 generalises the reader (roadmap D7).
- **Sources** (`core/sources`): `base.py` holds only what every source shares - the `Source`
  literal (`WEB` / `GLEIF` / `OPENFIGI`), `Provenance` (source, `retrieved_at`, `snapshot_at`,
  `detail`; `timestamp` prefers the snapshot, though nothing reads it yet) and the
  `Source*Error` classes. The fail-soft contract lives there: a source returning `None` means
  the register lacks the issuer; raising `SourceUnavailableError` means it could not be asked.
  Never collapse the two — an outage must not be reported as "not found". `SourceQueryError`
  has had no raiser since the DWS adapter went (PR #5); it stays for the next adapter that
  validates its own requests.
- **Audit user in the web app**: in a server the OS account is the SERVICE account, so
  `api.main.request_user()` reads the signed-in user from `WEB_USER_HEADER`
  (default `X-Remote-User`), set by the reverse proxy / SSO. **That proxy must strip any
  client-supplied copy** - a header a browser can set is not an identity. Without it every
  lookup is attributed to one name, which does not satisfy 'log the requesting user'.
- **Audit** (`core/audit.py`): every lookup is logged with identifier, timestamp, user,
  sources and outcome, and **never with retrieved content**. Keep it that way so the log can
  be shipped without carrying client data.
- **Output row contract** (`core/export/columns.py`): the single definition of a suggestion
  row (`SUGGESTION_COLUMNS`, built by `suggestion_row()`), used by the xlsx download and by
  the `row` object of `POST /api/suggest`. Neither builds its own row - keep it that way. An
  abstention leaves the code columns empty and puts the reason in `notes`.
- **Export** (`core/export/xlsx.py`): Subjects + Run sheets; `Subjects` is a historical name,
  kept because it is what the Tool 1 download has always contained. The code-shaped columns
  in `SUGGESTION_TEXT_COLUMNS` (ISIN, LEI, NACE/ESA codes, CTS IDs, alternatives) are written
  with the `@` text format so leading zeros survive; timestamps are converted to UTC and
  written naive (Excel has no offsets), which is why those headers say `(UTC)`.
- **Batch** (`core/batch`): only `reader.py` is left, and nothing calls it until E6
  generalises it to ISIN and name columns. It tolerates title rows, missing headers, mixed
  name/IČO columns and Excel-eaten leading zeros. Two rules that look like edge cases but are
  the point: a malformed IČO is looked up as given rather than falling back to the name column
  (a wrong IČO must surface, not silently return another company), and with no recognised
  header row 1 is kept as data rather than discarded.
- **Candidate pre-filter** (`core/classify`): the stage that makes the classifier both
  cheap and safe. `candidates.py` narrows 87 NACE divisions / 56 rest-of-world ESA codes to
  ~12, and every candidate already carries its CTS ID - so a code the classifier returns
  cannot be one CTS does not know, and a code with no CTS ID is never offered in the first
  place. It optimises **recall**, not precision: a code the filter omits is one the model can
  never return. Two mechanisms: the reviewable keyword table in `hints.py` (Czech + English)
  and IDF-weighted lexical overlap in `text.py`, which works on Czech input and, for NACE,
  on English through the English division titles in `nace_en.py` (scored only - never shown,
  never in a prompt; PR #8) - the model-based narrowing stage will be the primary path and
  slots in behind the same `CandidateFilter` protocol.
- **Register rules outrank keywords** (`hints.REGISTER_RULES`, PR #8): the GLEIF categories
  `RESIDENT_GOVERNMENT_ENTITY` -> NACE 84 and `INTERNATIONAL_ORGANIZATION` -> 99 add
  `REGISTER_SCORE` on top of the keyword hit, matched on the bracketed code in the fact sheet
  so only the register can fire them. "European Investment Bank" says "bank"; the register
  says what it is. OpenFIGI `Govt` is deliberately not a register rule (Kommuninvest, a bank,
  issues Govt bonds). Four S.125 families have no `Popis` in the CTS file (securitisation,
  dealers, lenders, specialised institutions): they are reached by name and keyword only, so
  each has a keyword entry - keep it that way when the table changes.
- **Navrhovaný kód** (`classify/proposal.py`, PR #8): what the page, the row and the JSON call
  the proposal. The model's first pick; with no model answer, the shortlist's first candidate
  **only if a rule put it there** (score >= 5; lexical scores stop at 1.0) **and no rule for
  a different code or ESA family ties with it** (the captive trap: "bank" and "captive" tie
  for an English description, and the alphabet would pick the bank). A rule's proposal has no
  confidence (`*_confidence` empty in the row), names its rules, and lists tied control
  variants. `answered` and the audit outcome still mean "the model answered".
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
  be quoted from provisional cases**. Every case is provisional: 10 fictional ones, each
  trapping a specific failure (see the README there), and since E8 (PR #7, 22 Sept 2026)
  36 real foreign issuers built from ISINs and public sources without asking MO (Q8), each
  with its reasoning, alternatives, confidence and evidence, `verified_by` empty until
  someone checks it against CTS. ESA codes come from the public CNB list BA0036 v044
  (`tests/golden/ba0036_v044_nonresident.json`, the same 56 non-resident leaves as CTS).
  A real case is scored like the pipeline: description PLUS the register fact sheet,
  replayed from GLEIF/OpenFIGI answers recorded in `tests/golden/identity.json`
  (`core/classify/golden_fixtures.py`; an unrecorded request is an error, never a silent
  miss). `python -m core.classify --golden` reports recall@k and top-1, real and fictional
  apart (needs the real codebooks); `--golden-capture` re-records the answers (network).
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
  * `Source` is WEB / GLEIF / OPENFIGI (DWS, ARES_LIVE left with Tool 2). A suggestion row's
    `source` names the registers that answered - even when the answer was "nothing" - then
    `WEB`: `GLEIF+OPENFIGI+WEB` for an ISIN lookup, `OPENFIGI+WEB` when GLEIF could not be
    asked or is switched off, plain `WEB` for a name-only one; the audit line, `/health`
    (`gleif_enabled`, `openfigi_enabled`) and the JSON `identity` object follow. Egress the
    runtime needs: api.gleif.org, api.openfigi.com (443).
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
  against the structured-outputs guide 2026-09-22 and 2026-09-23. Token usage is read under
  BOTH `prompt_tokens`/`completion_tokens` and `input_tokens`/`output_tokens`. 429 and 5xx
  retry; 4xx does not (it is a defect in the request). Since PR #9: the default model is
  `gpt-5.6-luna`, a reasoning model, so `reasoning_effort` (`LLM_REASONING_EFFORT`, default
  `none`, empty = not sent) goes out and `temperature` only with no effort or `none` -
  OpenAI rejects it otherwise. Connections wait at most 5 s.
- **The lookup deadline** (PR #9): Vercel ends the function at 60 s and the registers alone
  can take ~46 s, so `LlmClassifier` gets `call_seconds` (`worst_case_call_seconds()`: every
  attempt timing out, plus the backoff) and a `deadline` from `SuggestionService`
  (`LOOKUP_DEADLINE_SECONDS`, 50): a call that could end after it is not started, the reason
  says so and the rules' proposal stands. A cached answer is still served; the null provider
  gets `call_seconds=0`, so deterministic mode keeps "no model configured".
- **Blank means unset for `LLM_API_KEY` too** (PR #9): a blank key used to count as
  configured and send `Authorization: Bearer ` (an illegal header) on every call.
  `python -m core.classify --golden --model` runs the golden set through the model and
  refuses to start (exit 3) without a callable model or when the budget would refuse every
  call.
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
  page, `POST /api/suggest` JSON, `GET /suggest.xlsx` download, `GET /health`, `GET /probe`.
  * Codebooks load ONCE per process - in the lifespan or on the first lookup, under a lock -
    and an inconsistent or missing set NEVER serves a suggestion: those requests answer 503
    with the reason (the page keeps the input), `/health` turns 503, the failure is retried
    after 30 s. It must not raise instead: on Vercel a raising lifespan kills the instance,
    `/health` included (roadmap E1). `/health` never loads anything itself.
  * With `CODEBOOK_SOURCE=blob` (the Vercel setting) `load_and_check` first downloads the four
    files from the private Blob store (`core/codebooks/blob.py`: plain GET with the read-write
    token, no list/head calls, atomic writes to /tmp); nothing downstream changes.
  * `/probe` (`core/probe.py`): one fixed, harmless request per register, a status per
    failure mode, secrets shown only as present/absent; never called by `/health`. It found
    that Wikimedia refuses httpx requests whose User-Agent has no contact - keep one in
    `WEB_USER_AGENT`.
  * Vercel config lives in `app/vercel.json`, `[tool.vercel]` + `[tool.uv]` in pyproject,
    `.python-version` and `.vercelignore`; `tests/test_vercel_config.py` keeps them
    consistent. No `api/index.py`, no rewrites (the FastAPI preset would break routing), no
    `requirements.txt` (ignored next to pyproject), uvicorn only in extras.
  * NO server-side session: the download re-runs the request, which is free because the
    classifier caches, and keeps a shared link meaningful.
  * The page states plainly when the model or the search provider is not configured, so
    MO knows it is being shortlisted for rather than answered.
  * The template is generated from `ui/prototype/suggest.html` - keep the prototype in
    step when changing the design, it is the approved reference.
- **Docker** (`app/Dockerfile`, installs the `server` extra for uvicorn): codebooks and
  `.env` are MOUNTED, never baked in. Mount
  a volume at `/app/data/cache` or the answer cache and usage ledger are lost on every
  restart - which also means the daily budget cannot be enforced and fails closed.
- **Not yet built**: `core/classify/rules.py` - roadmap E4's rule table over the register
  facts of a foreign issuer (GLEIF category and sub-category, legal form, parent country,
  OpenFIGI market sector); until then those facts reach the pre-filter as words in the fact
  sheet. The step 5 RES -> CTS mapping for Czech subjects is not coming: it left with Tool 2.
  `tests/fixtures` holds only a README (reserved for recorded payloads of new sources, E3/E5).
  The OpenAI key is NOT needed to run or test any of the above; it is needed only to measure
  real accuracy against a verified golden set.
- **Later steps**: `docs/ROADMAP.md` - epics E0-E10 with design, definition of done, the
  decisions taken (repo visibility, codebook delivery, authentication, sign-off, golden set)
  and those still open (the database, D4; creating the Vercel project waits for Jakub's
  confirmation of the team and plan, D1).
