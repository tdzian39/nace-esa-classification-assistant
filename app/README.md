# NACE/ESA classification assistant (Raiffeisenbank CZ Finance/MIS)

**Since 22 September 2026 this repository is Tool 1 only and deploys to Vercel; the model is on
in production since 23 September 2026** - the plan is in [`../docs/ROADMAP.md`](../docs/ROADMAP.md).
Tool 2 is built elsewhere; its code was removed from this repository in PR #5 (roadmap E0.3).

Originally two internal tools sharing one codebase, for Middle Office treasury and Reporting:

- **Tool 1 – ESA/NACE suggester for foreign issuers.** From an ISIN, issuer name and/or
  activity description, suggest a 2-digit NACE code and an elementary ESA 2010 sector
  code together with their CTS codebook IDs, top 3 candidates each, with evidence.
- **Tool 2 – RES/OR lookup for client corrections** (Czech companies by name or IČO, RES
  and OR fields side by side). It now lives in `jaeksrampota/res-or-lookup`.

The full project brief, data-source priorities and hard rules live in `../CLAUDE.md`.
This README describes what exists, how it is laid out and how to run it.

## Status

**Running mode: with the model (production, since 23 Sept 2026).** The tool narrows each
codebook to about a dozen candidates, each with its CTS ID resolved, and the model (OpenAI
`gpt-5.6-luna`) may pick only from those: its first pick is the **navrhovaný kód**, with a
confidence and a one-sentence reason. When the model is off or declines - typically ESA when
the evidence does not say who owns the issuer - the deterministic result stands: where a rule
decided - a GLEIF category or a keyword - the first candidate is shown as the **navrhovaný kód**,
marked "podle pravidel · ověřte" and without a confidence; where only text similarity ranks the
list, or rules for two codes tie, nothing is proposed and the panel says the choice is MO's. The
page and the xlsx carry the same proposal and the whole shortlist; a person confirms every code.

On the golden set (real codebooks, all cases provisional - roadmap E8) the deterministic top pick
of the 36 real issuers is right 83% of the time for NACE and 47% for ESA, where most ESA misses
are the control digit (Q7); the ten fictional trap cases give 90% and 60%. These are indications,
not accuracy figures: no accuracy is quoted until cases have been checked against CTS. Useful on
its own, but unable to explain itself or to resolve distinctions that turn on a sentence. The
model's own figures come from `python -m core.classify --golden --model`, not recorded yet.

**An ISIN is now enough to start.** GLEIF resolves it to the issuer's LEI record (legal
name, country, legal form, entity category, direct and ultimate parent) and OpenFIGI to the
instrument (market name, security type, market sector); both are public and keyless. The
legal name becomes the web query and the facts go into the shortlist and the prompt, so a
bank is a bank because the register says so. See "Tool 1: issuer identification by ISIN".

**Running on Vercel (roadmap E1, 22 Sept 2026).** Production is up behind Vercel
Authentication, with the real codebooks in the private Blob store; the app loads them lazily,
reports a codebook problem as HTTP 503 instead of dying, and has a `/probe` page for the
registers; see "Deploying on Vercel". What comes next is in `docs/ROADMAP.md` section 0. The model (E9) is on
in production since 23 Sept 2026 (OpenAI `gpt-5.6-luna`, switched on with environment
variables); the adapter, spending limits, a lookup deadline inside Vercel's 60 s and the golden
run through the model are in "Enabling the model" below.


The original build steps are history now; the plan from here is the roadmap's epics.

| Step | Scope | Status |
|---|---|---|
| 1 | Codebook loaders + versioning + startup consistency check; IČO/ISIN identifiers; settings | **done** |
| 2 | Audit log; the DWS adapter, ARES fallback, resolver and CLI of this step were Tool 2's | audit **done**; the rest removed in PR #5 |
| 3 | Batch xlsx in/out with messy-input tolerance (`core/batch`, `core/export`) | reader and writer **done**; the Tool 2 batch runner removed in PR #5 |
| 4 | Single-lookup API + server-rendered UI (Jinja2 + htmx) | **done** (Tool 1) |
| 5 | Deterministic classifier: RES ESA sector -> BA0036 ID, RES 2-digit NACE -> OKEC_NACE2 ID | dropped with Tool 2; roadmap E4 adds a rule table for foreign issuers |
| 6 | LLM classifier for foreign issuers (structured selection from a candidate list) | **done**; on in production since 23 Sept 2026 (E9) |

Work stops after each step: tests run, the state is summarised, and the next step waits
for an explicit go-ahead.

## Repository layout

Everything lives under `app/` (mounted as `/app` in the container later).

```
app/
  core/
    identifiers/    ico.py (8 digits, mod-11; for batch/reader.py), isin.py (format + Luhn) [step 1]
    codebooks/      xlsx reader, loaders, models, versioning, consistency check, CLI       [step 1]
                    blob.py (the four files from a private Vercel Blob store)              [E1]
    probe.py        the /probe checks: one fixed request per register, runtime facts       [E1]
    sources/        base.py (Source, Provenance, the Source*Error classes)                  [step 2]
                    web.py (foreign-issuer evidence, blocklist enforced)                    [step 6]
                    gleif.py, openfigi.py, identity.py (ISIN -> issuer, public registers)   [step 6]
    audit.py        lookup audit trail (identifier, timestamp, user)                       [step 2]
    classify/       candidates.py + hints.py + text.py (pre-filter), golden.py, CLI          [step 6]
                    nace_en.py (English division titles), proposal.py (navrhovaný kód)      [E4-lite]
                    golden_fixtures.py (recorded register answers for the golden run)       [E8]
                    prompts.py, provider.py, llm.py, cache.py (the model call)               [step 6]
                    budget.py (limits, usage ledger), usage_report.py (ledger as Excel)       [E9]
    export/         columns.py (the suggestion row), xlsx.py (Subjects + Run sheets)        [step 3]
    batch/          reader.py (messy xlsx in; roadmap E6 reuses it)                         [step 3]
  api/              FastAPI: GET /, POST /suggest, POST /api/suggest, /suggest.xlsx         [step 4]
                    /health, /probe; lazy codebook loading, 503 when unavailable             [E1]
  ui/               templates/suggest.html (generated from prototype/suggest.html)          [step 4]
                    templates/probe.html                                                    [E1]
  config/           settings.py (pydantic-settings)
  .env.example      the settings with their defaults; copy to .env (git-ignored)
  vercel.json       FastAPI preset, region fra1, maxDuration 60 s, bundle excludes          [E1]
  .vercelignore     what a CLI deploy must never upload (.env, data/, *.xlsx, tests/)       [E1]
  .python-version   3.12, the Python Vercel builds with                                     [E1]
  data/codebooks/   the four xlsx codebooks (git-ignored, bank-internal)
  tests/
    identifiers/    unit tests for IČO and ISIN
    codebooks/      unit tests with synthetic xlsx fixtures + real-file smoke test
    sources/        source tests: GLEIF, OpenFIGI and web through mocked httpx transports   [step 6]
    fixtures/       reserved for recorded payloads (nothing yet)
    golden/         cases.json: 10 fictional traps + 36 real issuers, all provisional        [E8]
                    identity.json (recorded GLEIF/OpenFIGI answers), BA0036 v044 reference
  README.md
  pyproject.toml
```

## Getting started

Requirements: Python 3.12 or newer. Development on this machine uses a venv at the
repository root created from Anaconda Python 3.13; the code stays 3.12-compatible.

```bash
python -m venv ../.venv
../.venv/Scripts/python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` if you need to change any default (nothing is required
for step 1). Place the four codebook files in `data/codebooks/` (see the README there),
then run the startup check:

```bash
python -m core.codebooks
```

Exit codes: `0` consistent, `1` the report contains errors, `2` a file is missing or has
the wrong columns. Add `--no-strict` to see a report that contains errors, `--json` for
machine-readable output and `--dir PATH` to check another directory.

Run the tests from `app/`:

```bash
python -m pytest -q
```

Without the real codebooks (a fresh clone: they are bank-internal and git-ignored) the
suite reports **1328 passed, 19 skipped** (1347 passed with them). The skips are the tests that need the four real
xlsx files - the real-file smoke test (`tests/codebooks/test_codebooks_real_files.py`),
the real-recall and most of the cost tests in `tests/classify/` - and they run on a machine
that has them, where all four load with 0 errors (version `cb-3b12e64837840ca0`). Every
other test builds its own codebooks: synthetic xlsx files in a temporary directory, or
codebook models in memory.

## Configuration

All settings are read by `config/settings.py` from the environment or `app/.env`
(case-insensitive names). Relative paths resolve against `app/`, never the current
working directory. Connection details are never hardcoded.

| Variable | Default | Meaning |
|---|---|---|
| `CODEBOOK_DIR` | `data/codebooks` | Directory holding the four xlsx codebooks |
| `CODEBOOK_CTS_BA0036_FILE` | `CTS_BA0036_NEW.xlsx` | CTS codebook for ESA sectors |
| `CODEBOOK_BA0036_VALID_FILE` | `BA0036_2024_jen_validni.xlsx` | Valid ESA leaf codes |
| `CODEBOOK_CTS_OKEC_NACE2_FILE` | `CTS_OKEC_NACE2.xlsx` | CTS codebook for 2-digit NACE |
| `CODEBOOK_NACE_STAT_FILE` | `NACE_STAT.xlsx` | NACE labels, several rows per code |
| `CODEBOOK_VERSION_LABEL` | empty | Optional label appended to the computed version |
| `CODEBOOK_SOURCE` | `dir` | `dir` reads `CODEBOOK_DIR`; `blob` downloads the four files from a private Vercel Blob store once per instance (the Vercel setting) |
| `BLOB_READ_WRITE_TOKEN` | empty | Token of that store. It can also delete the store: a Sensitive variable, and locally only in `app/.env.local` |
| `BLOB_STORE_ID` | from the token | Store id (`store_` prefix optional) |
| `CODEBOOK_BLOB_PREFIX` | `codebooks/` | Pathname prefix of the four files in the store |
| `CODEBOOK_BLOB_TIMEOUT_SECONDS` / `CODEBOOK_BLOB_MAX_ATTEMPTS` | `10` / `2` | Per-file download timeout and attempts (timeouts and 5xx only) |
| `CODEBOOK_DOWNLOAD_DIR` | system temp dir | Where downloaded codebooks go (`/tmp` on Vercel) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOOKUP_USER` | OS login name | Requesting user written to the audit log |
| `PROBE_ENABLED` | `true` | Serve the `/probe` diagnostics page |
| `WEB_USER_AGENT` | `RBCZ-NACE-ESA-assistant/0.1 (+<repo URL>)` | Sent to every register; Wikimedia refuses httpx requests whose User-Agent has no contact, so keep a URL or mailbox in it |

The `DWS_*` and `ARES_*` variables went with Tool 2 (PR #5); an old `app/.env` that still
sets them loads fine, because unknown variables are ignored.

### Issuer identification variables (GLEIF, OpenFIGI)

| Variable | Default | Meaning |
|---|---|---|
| `GLEIF_ENABLED` | `true` | Resolve an ISIN to its issuer through the GLEIF LEI API |
| `GLEIF_BASE_URL` | `https://api.gleif.org/api/v1` | Base URL of the LEI API |
| `GLEIF_TIMEOUT_SECONDS` | `15` | HTTP timeout per request |
| `GLEIF_MIN_INTERVAL_SECONDS` | `1.0` | Spacing between requests; the published limit is 60/min |
| `GLEIF_MAX_ATTEMPTS` | `3` | Attempts per request; 429 and 5xx are retried |
| `GLEIF_FETCH_PARENTS` | `true` | Also read the direct and ultimate parent (two more requests) |
| `OPENFIGI_ENABLED` | `true` | Describe the instrument through OpenFIGI |
| `OPENFIGI_BASE_URL` | `https://api.openfigi.com/v3` | Base URL of the mapping API |
| `OPENFIGI_API_KEY` | empty | Optional free key; raises the limit from 25 to 250 requests/min |
| `OPENFIGI_TIMEOUT_SECONDS` | `15` | HTTP timeout per request |
| `OPENFIGI_MIN_INTERVAL_SECONDS` | `2.5` | Spacing between requests; keyless limit `25;w=60` read live |
| `OPENFIGI_MAX_ATTEMPTS` | `3` | Attempts per request; 429 and 5xx are retried |

Both registers use `WEB_USER_AGENT`. Hosts the runtime must be allowed to reach:
`api.gleif.org` and `api.openfigi.com` (HTTPS, port 443).

The model variables (`LLM_*`) are live entries in `.env.example` with their defaults; only
`LLM_API_KEY` is commented out, and that is what keeps the tool deterministic until roadmap
E9 (see "Enabling the model").

## Codebooks (`core/codebooks`)

### Files and columns

| File | Columns | Role |
|---|---|---|
| `CTS_BA0036_NEW.xlsx` | `ID`, `VALUE` (ESA code), `DESCRIPTION` | CTS IDs for ESA 2010 sectors |
| `BA0036_2024_jen_validni.xlsx` | `Kód`, `Název`, `Popis` | The only ESA codes that may be emitted (leaf codes) |
| `CTS_OKEC_NACE2.xlsx` | `ID`, `VALUE` (2-digit NACE), `DESCRIPTION` | CTS IDs for NACE divisions |
| `NACE_STAT.xlsx` | `NACE`, `Zkrtext`, `Text` | Labels per division, several rows per code |

The reader (`xlsx.py`) uses openpyxl only, finds the header row within the first 20 rows
(title rows above the header are tolerated), matches column names case- and
accent-insensitively (`Kód` = `Kod` = `KOD`), normalises every cell to text (Excel float
artefacts such as `1001.0` become `1001`, integer `1` in a NACE column becomes `01`) and
raises `CodebookSchemaError` listing the headers it found when a required column is missing.

### Normalisation rules

- **ESA codes** are compared by a canonical key: uppercase, whitespace removed, the
  optional `S.`/`S` prefix dropped, leaving 1 to 5 digits. `S.11001`, `s11001`, `11001`
  and the integer `11001` all map to the key `11001`; anything else (`S.1.5`, `S.1A`) is
  malformed rather than guessed. The display form is `S.11001`.
- **NACE** is kept in full everywhere in the data. The single truncation point is
  `nace_to_division()`, called only by `CodebookSet.cts_id_for_nace()`; `62.01`, `6201`,
  `62` and the integer `62` all resolve to division `62`. An integer such as `111` is
  inherently ambiguous once a leading zero has been lost; NACE values are expected to
  arrive as text with their zeros intact.
- **CTS IDs** are opaque text and are never altered (leading zeros are kept).

### Lookups that may emit a CTS ID

There are exactly two:

- `CodebookSet.cts_id_for_esa(code)` returns the `CtsEntry` for a **valid leaf** ESA code.
  A code that exists in the CTS codebook but is not in the valid list (a parent such as
  `S.11`) raises `InvalidEsaCodeError`; an unknown code raises `UnknownEsaCodeError`.
- `CodebookSet.cts_id_for_nace(nace)` accepts a full NACE code, truncates to the division
  and returns the `CtsEntry`, or raises `UnknownNaceCodeError`.

Both raise `MalformedCodeError` for input that is not a code at all.

### Startup consistency check

`load_and_check()` loads the four files, fingerprints them, runs `check_consistency()` and
raises `CodebookConsistencyError` (carrying the report) when any error is present, or returns
the report when called with `strict=False`. The web app calls it that way once per process
(at startup, or on the first lookup) and on any error serves **no suggestion**: those
requests answer 503 with the report summary and `/health` turns 503, while the form, `/health`
and `/probe` keep answering so the reason can be seen. `python -m core.codebooks` runs the
same check on demand. With `CODEBOOK_SOURCE=blob` the files are downloaded from the private
Blob store first (`core/codebooks/blob.py`); the check is identical.

| Code | Severity | Meaning |
|---|---|---|
| `E_EMPTY_CODEBOOK` | error | A codebook has no usable rows |
| `E_DUPLICATE_CTS_ID` | error | A CTS ID appears twice in one CTS codebook |
| `E_DUPLICATE_CTS_VALUE` | error | A code appears twice in one CTS codebook (ambiguous mapping) |
| `E_MALFORMED_ROW` | error | A row whose code cannot be normalised |
| `E_VALID_ESA_WITHOUT_CTS_ID` | error | A valid ESA leaf has no CTS ID and could never be emitted |
| `E_NACE_STAT_WITHOUT_CTS_ID` | error | A NACE division from `NACE_STAT` has no CTS ID |
| `E_EMITTED_ID_MISSING` | error | Self-check: an emittable CTS ID is not in the loaded codebook |
| `W_DUPLICATE_VALID_ESA` | warning | Duplicate code in the valid list |
| `W_CTS_NACE_NOT_IN_STAT` | warning | A CTS NACE division has no labels |
| `W_MISSING_DESCRIPTION` | warning | Empty descriptions or names |
| `W_SKIPPED_ROWS` | warning | Blank rows were skipped while loading |
| `I_CTS_ESA_NOT_VALID_LEAF` | info | CTS ESA entries that are parents, listed for transparency |
| `I_EMITTABLE_IDS` | info | Counts of emittable ESA and NACE CTS IDs plus the version id |

### Versioning

Every loaded set carries a `CodebookVersion`: `id` is `cb-` plus 16 hex characters of a
SHA-256 over the four file hashes (derived from file bytes, so independent of path and
modification time; note that re-saving an xlsx in Excel changes its bytes and therefore
the id even when no cell changed), optionally suffixed with `+<label>`; `loaded_at` is a
timezone-aware UTC timestamp; `files` lists each file's name, path, SHA-256, size, mtime
and row count. Every suggestion row carries `version.id` as its `codebook_version`.
If another source ever replaces the xlsx files, the version should be computed over the
normalised rows instead.

## Identifiers (`core/identifiers`)

- `normalize_ico(value)` accepts text, integers and floats as they come out of Excel:
  whitespace (including non-breaking spaces) is removed, `1350.0` and `1.35E3`-style
  artefacts are resolved exactly, the result is zero-padded to 8 digits (more than 8 digits,
  including surplus leading zeros, is rejected as `too_long`) and validated with
  the mod-11 checksum (weights 8..2, check digit `(11 - sum mod 11) mod 10`). Failures raise
  `InvalidIcoError` with a machine-readable `reason`. A `CZ`-prefixed DIČ is rejected unless
  `allow_dic_prefix=True`. `is_valid_ico()` and `try_normalize_ico()` never raise. The
  suggester never sees an IČO: this half serves the batch reader (`core/batch/reader.py`),
  which recognises IČO columns until roadmap E6 generalises it to ISIN and name columns.
- `normalize_isin(value)` uppercases, strips whitespace, checks the ISO 6166 layout
  (`^[A-Z]{2}[A-Z0-9]{9}[0-9]$`) and the Luhn checksum over the letter-expanded string.
  `isin_country_code()` returns the two-letter prefix. For both identifiers any non-string,
  non-numeric input is `unsupported_type`, and a NaN standing for a blank cell is refused
  (`unsupported_type` for the ISIN, `nan` for the IČO); callers turn blanks into `None` first.

## Data sources (`core/sources`)

### Priority and what a miss means

For an ISIN, `IssuerIdentifier` asks GLEIF first and OpenFIGI second; the web then supplies
the activity description, unless the user typed one, which wins. The "Tool 1" sections on
issuer identification and web evidence below describe them. The distinction that makes all
of them trustworthy:

- a source returning `None` means **the register does not hold this issuer** - a fact;
- a source raising `SourceUnavailableError` means **we could not ask** - not a fact.

So a register outage never turns into "not registered": `IssuerIdentifier` records it as a
note and keeps whatever the other register answered. The row's `source` names the registers
that answered - even when the answer was "nothing" - and then `WEB`: `GLEIF+OPENFIGI+WEB`
for an ISIN lookup, `OPENFIGI+WEB` when GLEIF could not be asked, plain `WEB` for a name.

### Provenance and errors (`base.py`)

`base.py` holds only what every source shares:

- `Source` - the stamp on every record: `GLEIF`, `OPENFIGI` or `WEB`.
- `Provenance` - source, `retrieved_at`, `snapshot_at` (GLEIF fills it from the record's
  `lastUpdateDate`), `detail`; `timestamp` is the snapshot when the source states one,
  otherwise the retrieval time.
- `SourceError` and its subclasses: `SourceUnavailableError` (could not ask - network error,
  timeout, 5xx, or 429 once retries are spent), `SourceResponseError` (answered, but not
  understandably) and `SourceQueryError` (refused by the adapter before sending; nothing
  raises it today).

The issuer records themselves live with their adapters: `LeiRecord` in `gleif.py`,
`FigiInstrument` in `openfigi.py`, `IssuerIdentity` in `identity.py` and `IssuerEvidence`
in `web.py`.

### Audit trail (`core/audit.py`)

Every lookup is logged to the `core.audit` logger with identifier, timestamp, requesting
user, sources consulted and outcome - as a message and as structured fields in
`record.audit` for a JSON handler. **No retrieved content is ever logged**: the trail holds
the question and the outcome, never the answer, so it can be kept and shipped without
carrying client data.

## Batch xlsx in/out (`core/batch`, `core/export`)

Two halves outlived the Tool 2 batch runner (removed in PR #5): the reader, which roadmap
E6 reuses for the Tool 1 batch and which nothing in the app calls yet, and the writer behind
the Tool 1 download (`GET /suggest.xlsx`).

### Reading input nobody cleaned up

The reader assumes almost nothing: the header may sit under title rows or be missing, the
IČO and name may be in separate columns or mixed in one, IČOs arrive as numbers with their
leading zeros eaten by Excel (`177041`) or spaced by hand (`00 177 041`), and rows are blank
or duplicated. Header matching is case- and accent-insensitive (`ico` = `IČO` = `Ičo`) and
accepts a suffix (`IČO klienta (RES)`).

Two decisions worth knowing:

- **A malformed IČO is never silently replaced by the name column.** The row keeps the IČO
  as its identifier, with a note saying so, so the error surfaces wherever the rows are
  looked up (nothing looks them up until E6). Falling back to the name would return data
  for a *different* company than the sheet names - the one error a reviewer could not catch.
- **When no header is recognised, row 1 is kept as data.** A stray junk row then shows up as
  an invalid identifier instead of vanishing; discarding a row that turned out to hold a
  real company would be silent data loss.

### The result workbook

One row per issuer, with the columns defined once in `core/export/columns.py`
(`SUGGESTION_COLUMNS`) and shared with the `row` object of `POST /api/suggest`: `IN_isin` and
`IN_name` (the request, echoed), `issuer_*` and `description`, then per codebook the top pick
with its CTS ID, label, confidence and justification, two alternatives and the whole
shortlist (`NACE_candidates`, `ESA_candidates`), then `source`, `retrieved_at`,
`codebook_version`, `model`, `prompt_version`, `evidence_urls` and `notes`. An abstention
leaves the code columns empty and puts the reason in `notes`. The sheet is still called
`Subjects`, which is what the download has always contained.

Excel-specific care:

- the code-shaped columns (ISIN, LEI, NACE and ESA codes, CTS IDs, alternatives) are written
  as text with the `@` format, so a NACE division `01` or a CTS ID with leading zeros does
  not come back as `1` - a code copied into CTS must be exactly what the codebook says;
- dates are real dates with an ISO format; timestamps are converted to UTC and the headers
  say `(UTC)`, because Excel cannot store an offset;
- control characters openpyxl refuses are stripped and over-long texts are truncated at
  Excel's 32 767-character cell limit rather than raising.

A second **Run** sheet records the tool, when the result was made, the user, the codebook
version and the model, so a workbook that has been emailed on still answers "where did this
come from?".

## Tool 1: the candidate pre-filter (`core/classify`)

```bash
python -m core.classify "captive funding vehicle of a banking group" --verbose
python -m core.classify --golden        # recall + top-1 over tests/golden, no API key needed
python -m core.classify --golden-capture   # re-record the golden register answers (network)
python -m core.classify --golden --model   # the same cases through the model: top-1 vs the rules, tokens (costs money)
```

The classifier is never asked to *produce* a code; it chooses from a list. Every candidate
already carries its CTS ID, so a hallucinated code is not something to detect afterwards -
it is unrepresentable. The pre-filter builds that list.

**Why two stages**, measured on the real codebooks: all 87 NACE short labels are ~895
tokens, but the full `NACE_STAT.Text` definitions of all 87 are ~10,400. Narrowing to a
dozen and sending only *their* definitions costs ~1,440 - a 4.5x saving on the part of the
prompt that decides the answer. The filter optimises **recall**; precision is the
classifier's job.

**ESA is a grid, not a list.** BA0036 is entity family x control type, derived from the
codebook names rather than hardcoded. The filter picks families and offers every control
variant, because "what is it" and "who owns it" are answered by different sentences.

**The residual-sector rule.** "Nefinanční podniky" gets reserved slots before ranking.
Without that, an industrial issuer whose description merely mentions "bonds" and "finance"
matched a dozen financial families weakly, which consumed every slot and left the one sector
it belongs to unoffered - 20% of ESA recall on the golden set.

See `tests/golden/README.md` for how cases get verified and why provisional ones must never
be quoted as accuracy.

## Tool 1: issuer identification by ISIN (`core/sources/gleif.py`, `openfigi.py`, `identity.py`)

The brief's first input is an ISIN, and until this step an ISIN on its own produced
nothing: it was validated, then used as a search string, and with no search provider
configured the page said "Emitent neurčen". Now it is resolved first:

```
ISIN -> GLEIF    lei-records?filter[isin]=   legal name, country, legal form (ELF), entity
                                             category, sub-category, status; then
                 /direct-parent, /ultimate-parent (404 = none reported; the
                 /direct-parent-reporting-exception says why, e.g. NO_KNOWN_PERSON)
     -> OpenFIGI POST /v3/mapping             market name, security type, market sector
```

Both are public registers (GLEIF data is CC0, OpenFIGI is Bloomberg's open symbology),
keyless, and were verified live on 2026-09-22 with Deutsche Bank AG (`DE0005140008`), BMW
Finance N.V. (parent BMW AG, DE), Land Berlin (`RESIDENT_GOVERNMENT_ENTITY` /
`STATE_GOVERNMENT`) and the European Investment Bank (`INTERNATIONAL_ORGANIZATION`,
jurisdiction `EU`).

**What it changes in the pipeline.** The register's legal name becomes the web search
query and the name on the page (what MO typed still wins). The identity's *fact sheet* -
Czech one-liners such as `GLEIF (LEI 7LTW…): DEUTSCHE BANK AKTIENGESELLSCHAFT. Země sídla:
DE. Právní forma: ELF 6QQB. Kategorie subjektu podle GLEIF: běžná právnická osoba
[GENERAL]. …` - is appended to the description that the pre-filter scores and the model
reads. The glosses in parentheses carry the English words the keyword table already reacts
to (`investment fund`, `government`, `supranational`, `municipality`), so a GLEIF category
reaches the ESA family shortlist without a new mechanism, and a legal name with `BANK` in
it puts division 64 and the bank family on the list the same way.

**Facts, not decisions.** "The ultimate parent sits in another country than the issuer" is
stated as a fact; whether that makes the issuer *pod zahraniční kontrolou* in BA0036's
sense is left to the classifier (and to the open S.12203 question in `CLAUDE.md`).

**Fail-soft.** A register that has nothing returns `None` and leaves a note
(`GLEIF nemá k tomuto ISIN přiřazen LEI emitenta`); a register that cannot be asked raises
`SourceUnavailableError`, which `IssuerIdentifier` turns into a note while keeping the other
half. A malformed ISIN is never sent anywhere. Coverage is why both are asked: GLEIF has no
ISIN mapping for the iShares Core MSCI World ETF (`IE00B4L5Y983`) while OpenFIGI names it.

**Provenance.** Rows are stamped with every register that answered - `GLEIF+OPENFIGI+WEB`
for an ISIN lookup, plain `WEB` for a name - the audit line carries the same, the xlsx gains
`issuer_lei (GLEIF)` and `issuer_country (sídlo podle GLEIF)`, and the record pages
(`search.gleif.org/#/record/<LEI>`, `openfigi.com/search`) lead the "Podklady" list.

**Rate limits, measured.** GLEIF publishes 60 requests/minute (`GLEIF_MIN_INTERVAL_SECONDS`
1.0); OpenFIGI answered with `ratelimit-policy: 25;w=60` without a key
(`OPENFIGI_MIN_INTERVAL_SECONDS` 2.5; a free key gives 250/minute). 429 and 5xx retry with
linear backoff, other 4xx do not. An ISIN costs up to four GLEIF requests (record, two
parents, exception) and one OpenFIGI request; `GLEIF_FETCH_PARENTS=false` cuts it to one.

## Tool 1: web evidence (`core/sources/web.py`)

Turns a name or an ISIN into a short activity description plus the sources behind it, so the
suggestion can be checked rather than trusted.

**The register ban is enforced, not documented.** `apl.czso.cz` and `or.justice.cz` are
refused by `is_blocked()` when filtering search hits *and* again inside the fetcher, so a
result that slips through a provider still cannot be read. Non-HTTP schemes are refused too.

**A description the user typed wins.** The web is consulted only to fill a gap, and the
result says which happened.

**Nothing raises on a thin result.** No provider configured, search down, page 404s, the hit
is a PDF, every hit blocked - each comes back as evidence with no description and a note.
That has to make the classifier abstain; an issuer the web cannot describe must not be
guessed at from its name.

**The search provider is pluggable.** Which search API a bank may call is a procurement
question, so `SearchProvider` is a protocol; `HttpSearchProvider` is configured by URL and
response field paths (`WEB_SEARCH_*`), with defaults following Brave's shape and TODO markers
until the provider is confirmed. With none configured the tool still works from typed
descriptions.

Page text is extracted with the stdlib `html.parser` - no extra dependency - preferring the
meta description and dropping scripts, styles, nav and footers so the prompt is not filled
with page furniture.

## Tool 1: the classifier (`core/classify/llm.py`)

```bash
python -m core.classify "captive funding vehicle of a banking group" --estimate
```

The model is handed a shortlist and asked to choose. The JSON schema pins `code` to an enum
of exactly the offered codes, so the provider itself cannot emit anything else; the answer is
then re-checked, and each `Suggestion` is built **from the candidate**, so the CTS ID and the
label come from the codebook and cannot be invented.

**Everything that can go wrong becomes an abstention**, never a guess and never a lost row:
no candidates, no description, no key configured, the model unreachable, a malformed answer,
or the model itself declining. MO's fallback is to research the issuer, which they can do; a
confident wrong code typed into CTS is the outcome nobody catches.

**Reproducibility**: temperature 0, and every result carries the model, the prompt version
and the timestamp. `PROMPT_VERSION` is part of the cache key, so changing the wording cannot
serve yesterday's answers.

**Cost**, measured rather than assumed - `--estimate` reports it without calling anything:

| Control | Effect |
|---|---|
| Candidate definitions trimmed to a character budget | ~4,300 -> ~3,400 tokens/issuer |
| `LLM_MAX_OUTPUT_TOKENS` | caps generation at 700 |
| Cache | a repeated issuer costs nothing |
| Shortlist size | *not* a useful lever - see below |

The trim keeps definitions **in codebook order within a budget**, not the top N by
relevance. Ranking them dropped the one line that puts a captive funding vehicle in
division 64, because an English description scores zero against Czech text, so "best six"
silently meant "first six". Only the giant divisions are expensive (oddil 46 has 56
sub-activities); a typical division fits whole and is sent whole.

Shrinking the shortlist from 12 to 8 saves only ~9%, and ESA recall drops to 80% at 6
because families come in three control variants - a shorter list loses a whole family
rather than one code. The default stays above that cliff.

The cache key covers everything that can change the answer - including the codebook
version, because caching on the issuer name alone would keep serving IDs derived from a
previous one.

**The provider is an adapter.** OpenAI Chat Completions over plain `httpx` with no SDK
dependency, so `LLM_BASE_URL` points at any compatible endpoint. `StubLlmProvider` is how the
whole classifier was built and tested before any key existed; with nothing configured the
tool abstains rather than failing.

## Running the web tool

```bash
../.venv/Scripts/python.exe -m uvicorn api.main:app --port 8000
```

Then open <http://127.0.0.1:8000/>.

| Endpoint | Purpose |
|---|---|
| `GET /` | the form |
| `POST /suggest` | the form result page (htmx) |
| `POST /api/suggest` | the same lookup as JSON |
| `GET /suggest.xlsx` | download one row as xlsx |
| `GET /health` | status, codebook state (`loaded` / `not_loaded` / `error` with the reason), version, whether the model, search and the registers are configured, Python, region, commit; 503 after a failed codebook load; never loads anything itself |
| `GET /probe` | diagnostics: one fixed, harmless request per register with a status per failure mode (`ok`, `timeout`, `dns`, `tls`, `blocked`, `http_error`, `unexpected_body`, ...), the codebook state, the settings (secrets as present/absent only) and the runtime; `?set=all` adds the E5 hosts (FIRDS, Wikidata, Wikipedia), `?format=json` for scripts; `PROBE_ENABLED=false` turns it off |

`POST /api/suggest` also returns an `identity` object (LEI, legal name, country, category,
legal form, ultimate parent, instrument, the registers that answered and the facts) for a
script that wants the register data rather than the prose.

**Codebooks load once per process, and a bad set never serves a suggestion.** They load at
startup (uvicorn, and Vercel, run the lifespan before the first request) or on the first
lookup, once even when several requests arrive together. If they cannot be fetched, read or
checked, suggestion requests answer **503 with the reason** - the page keeps what was typed -
and `/health` turns 503; the failure is retried at most every 30 seconds. Emitting a CTS ID
derived from a bad codebook is the one failure nobody catches downstream, so it is refused;
but it no longer stops the process, because on Vercel a failed startup takes the whole
instance down, `/health` included.

**There is no server-side session.** The download re-runs the request; that is free because
the classifier caches, and it means a shared or bookmarked link behaves the same for
everyone.

**The page says when it is only shortlisting.** With no `LLM_API_KEY` it shows a banner and
each panel explains that no code was chosen and why - never a blank panel that looks like a
bug.

**The look** is the RB team gateway's (`anorfidien/finance_rb_cz`, after the Raiffeisenbank
brand manual 2023), re-implemented in the template's own CSS. That repository has no licence,
so nothing was copied, and its logo files stay out because this repository is public. Light
and dark follow the system. The button in the top bar switches between them and the browser
remembers the choice (`localStorage` key `nace-esa-theme`, shared with `/probe`).

**htmx must load, and must show errors.** htmx 1.9.12 comes from cdnjs with the SRI hash cdnjs
publishes. A wrong hash makes the browser block the script without a word, and the page falls
back to plain form posts: that is how it ran until PR #10. htmx does not swap 4xx/5xx answers
by default, so an `htmx:beforeSwap` handler in the page head swaps them. Without it, a 503
"Číselníky nejsou k dispozici" would leave the page silent. `tests/test_templates.py` pins
both, and checks that each state label ("navrhovaný kód", "podle pravidel", "jistota") occurs
in the template only where it is rendered. CSS comments are sent with the page.

`ui/prototype/suggest.html` is the static design reference: sample data, no server, open it in
a browser. Its CSS, theme script, top bar and masthead are copied verbatim from the template,
so change both together.

### Container

```bash
docker build -f app/Dockerfile -t naceesa:0.1 app
```

Codebooks and `.env` are mounted, never baked into the image. Mount a volume at
`/app/data/cache` too, or the answer cache and usage ledger are lost on every restart. The
image installs the `server` extra (uvicorn); its health check fails while `/health` answers
503, i.e. after a failed codebook load.

## Deploying on Vercel (roadmap E1)

The app runs on Vercel as **one Python function under the FastAPI preset**. What the platform
needs is in `app/`: `vercel.json` (preset, region `fra1`, `maxDuration` 60 s, bundle
excludes), `[tool.vercel] entrypoint = "api.main:app"` and `[tool.uv] package = false` in
`pyproject.toml`, `.python-version` (3.12) and `.vercelignore`. There is no `api/index.py`
and no rewrite: under the FastAPI preset a catch-all rewrite would show the app every request
as that one path. `pyproject.toml` is the only dependency file Vercel reads (a
`requirements.txt` next to it would be ignored), and uvicorn is not a runtime dependency -
Vercel's Python runtime brings its own. Each of these facts was checked against the Vercel
docs and the builder source on 22 Sept 2026 (roadmap section 10); `tests/test_vercel_config.py`
keeps the files consistent with each other and with the code.

**One-time setup.** The Vercel project is created only after roadmap D1 is confirmed.

1. Project: Root Directory `app`; the framework is detected as FastAPI. Only the repository
   owner can connect the GitHub integration, so deploys run from a checkout with the Vercel CLI.
2. A private Blob store for the codebooks, in the function's region (a store's region cannot
   be changed later); Vercel CLI 50.20 or newer:
   ```bash
   vercel blob create-store nace-esa-codebooks --access private --region fra1 --yes
   ```
3. Upload the four files once; keep their master copies outside Vercel, because the store's
   token can also overwrite and delete them:
   ```bash
   vercel blob put CTS_BA0036_NEW.xlsx --pathname codebooks/CTS_BA0036_NEW.xlsx --access private
   ```
   and the same for `BA0036_2024_jen_validni.xlsx`, `CTS_OKEC_NACE2.xlsx` and `NACE_STAT.xlsx`.
4. Environment variables for Production and Preview:

   | Variable | Value | Why |
   |---|---|---|
   | `CODEBOOK_SOURCE` | `blob` | download the codebooks from the store |
   | `BLOB_READ_WRITE_TOKEN` | the store's token, Sensitive | check it exists after connecting the store; add it by hand if not |
   | `LLM_ENABLED` | `false` | deterministic mode until the model is switched on ("Enabling the model") |
   | `LLM_CACHE_PATH`, `LLM_USAGE_PATH` | empty | only `/tmp` is writable; empty switches both SQLite files off |
   | `LLM_DAILY_TOKEN_BUDGET` | `0` | with no usage ledger a positive daily cap refuses every model call; the spending cap in the provider's dashboard is the backstop, the per-request and per-run limits stay (decided 23 Sept 2026) |
   | `WEB_USER_HEADER` | empty | Vercel passes client headers through; a browser could name itself (E2) |
   | `GLEIF_TIMEOUT_SECONDS`, `OPENFIGI_TIMEOUT_SECONDS` | `5` | with the next row, the worst case (4 GLEIF + 1 OpenFIGI requests) stays under the 60 s cap |
   | `GLEIF_MAX_ATTEMPTS`, `OPENFIGI_MAX_ATTEMPTS` | `2` | |
   | `LLM_TIMEOUT_SECONDS` | `15` (now the default, no need to set it) | with the next row one model call takes at most 20 s (5 s to connect, 15 s to answer) |
   | `LLM_MAX_ATTEMPTS` | `1` (now the default) | a retry would leave no room for the second call; a failed call abstains and the rules' proposal shows. **Raising either row switches the model off** rather than making it patient: startup warns when two worst-case calls no longer fit the deadline |
   | `LOOKUP_DEADLINE_SECONDS` | `50` (the default, no need to set it) | no model call is started that could end after 50 s. The registers take ~5 s, so both calls fit (5 + 2 x 20 = 45 s); in their worst case (~46 s, every GLEIF parent request timing out twice) no call starts and the rules' proposal is the answer - under Vercel's 60 s either way |

5. Deployment Protection: previews are protected by Vercel Authentication by default, the
   production URL is not. Until the E2 login exists, protect "All Deployments" (free on every
   plan since 9 Sept 2026), so nobody without access sees CTS IDs.

**Deploy** from the repository root of a checkout. The CLI reads `.vercelignore`, never
`.gitignore` - that is why the codebooks and `.env` are listed there. The first deploy of a new
project goes to production; later ones are previews unless `--prod` is given:

```bash
vercel link
vercel deploy
vercel deploy --prod
```

Never `vercel build` or `vercel deploy --prebuilt` from a checkout that holds `app/.env` or the
xlsx files: the builder bundles whatever is on disk.

**Check it:** `/health` shows `codebooks.state` `loaded`, the version and `region: fra1`;
`/probe` shows every register `ok`. A codebook problem - a missing token, a file not uploaded -
is a 503 on `/health` with the reason. Behind Vercel Authentication, check from a linked checkout
with `vercel curl /health` or `vercel curl "/probe?format=json&set=all"`: it handles the protection
bypass. (`vercel link` writes a short-lived token to `.env.local`; Vercel never uploads that file.)

**Update the codebooks:** upload with `--allow-overwrite` (or to a new prefix and set
`CODEBOOK_BLOB_PREFIX`), wait a minute - the store's CDN can serve the old file for up to 60 s -
and redeploy; the new version id appears on `/health`.

**Plan:** Hobby (roadmap D1); Vercel Pro is not needed (Jakub, 23 Sept 2026).

## Enabling the model (roadmap E9)

### Which endpoint - decide this first

The adapter speaks OpenAI Chat Completions with a strict JSON schema. Checked against the
providers' docs on 23 Sept 2026:

| Endpoint | Code needed | What to set |
|---|---|---|
| **OpenAI** | none | the defaults: `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_MODEL=gpt-5.6-luna`, `LLM_REASONING_EFFORT=none`. The structured-outputs guide lists everything `provider.py` sends as supported in strict mode (`enum`, `description`, array `maxItems`, `additionalProperties: false` with every field required); `gpt-5.6-luna` is OpenAI's cost-sensitive model ($0.20 / $1.20 per million input / output tokens, about a tenth of a cent per issuer), supports Chat Completions and structured outputs, and, being a reasoning model, rejects `temperature` unless the effort is `none` - which the adapter handles. |
| **Azure OpenAI, v1 API** (`https://<resource>.openai.azure.com/openai/v1/`) | none expected | `LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1`, `LLM_MODEL=<deployment name>`, `LLM_REASONING_EFFORT` as for the deployed model (empty for a non-reasoning one such as gpt-4.1-nano). Microsoft's v1 examples point the standard OpenAI client at that URL with the Azure API key, and that client sends the key as `Authorization: Bearer` like this adapter; their raw REST example uses an `api-key` header instead. **If the first call answers 401**, the key has to travel in `api-key`: that is the small adapter below. |
| **Azure OpenAI, classic** (`.../openai/deployments/<name>/chat/completions?api-version=...`) | the small adapter in roadmap E9 | an `api-key` header, the `api-version` query and the deployment in the path, tested against a fake transport like `OpenAiProvider`. Not built. |
| **Claude API** (Anthropic) | a Messages API adapter, not built | build it from the `claude-api` skill with the official `anthropic` SDK, not from memory and not through Anthropic's OpenAI-compatibility shim: `POST https://api.anthropic.com/v1/messages` with `x-api-key` and `anthropic-version: 2023-06-01`, the schema in `output_config.format`, no `temperature` on current models (a 400), and no `maxItems` (not supported there). The model is the owner's choice - the skill's default is `claude-opus-5` ($5 / $25 per million tokens), `claude-sonnet-5` ($2 / $10) and `claude-haiku-4-5` ($1 / $5) are cheaper - and the golden run through the model decides. |
| **Another OpenAI-compatible gateway** (a bank proxy, LiteLLM, vLLM) | none if it passes the request through | `LLM_BASE_URL=<gateway>/v1`, `LLM_MODEL` as the gateway names it, usually `LLM_REASONING_EFFORT=` (empty). Whether it forwards `response_format` with a strict `json_schema`, a Bearer key and `max_completion_tokens` **cannot be told without its docs**: run the smoke test below; a 400 that names a parameter says which one it refuses (an unknown `reasoning_effort`: set it empty; only `max_tokens` accepted: a one-line change in `provider.py`). |

Every model or endpoint change is measured with the golden run through the model before it
stays (below).

### Switching it on locally

1. Put the key in `app/.env` (git-ignored, never in code or chat):
   ```
   LLM_API_KEY=sk-...
   ```
2. Set `LLM_BASE_URL`, `LLM_MODEL` and `LLM_REASONING_EFFORT` for the endpoint (table above).
3. Measure before trusting it - the golden set through the model, with the real codebooks in
   `data/codebooks/` (92 calls, about 160,000 input tokens at ~3,400 per issuer, roughly $0.04
   at `gpt-5.6-luna` prices):
   ```bash
   python -m core.classify --golden --model
   ```
   It prints each case's rules' pick next to the model's, both top-1 figures and the tokens the
   provider reported. The provider path first ran live on 23 Sept 2026, in production with
   OpenAI `gpt-5.6-luna`, and needed no change; on another endpoint, the abstention reason
   printed per case says what to fix.
4. Have someone check the suggestions against CTS, then record the confirmed ones in
   `tests/golden/cases.json` with `verified_by` filled in. That is what turns "seems right"
   into a number, and what justifies keeping the cheap model.
5. Watch the spend:
   ```bash
   python -m core.classify --usage
   python -m core.classify --usage-xlsx
   ```
   `--usage` prints the limits and today's total. `--usage-xlsx` writes every recorded call
   with its tokens and cost to an Excel workbook next to the ledger
   (`data/cache/llm_usage.xlsx` by default, git-ignored; or give a path): a Summary by model,
   codebook and day, the Calls table and the Prices it used - OpenAI's standard prices,
   checked 23 Sept 2026; update `PRICES` in `core/classify/usage_report.py` when they change.
   Only calls made from this machine are in the ledger; production on Vercel keeps none, so
   its spend is on the provider's usage page. The costs are an upper bound: cached input is
   billed at a tenth, but the ledger does not record it.

Limits are already enforced (8,000 tokens/request, 200 calls/run, 500,000 tokens/day) and
fail closed: an unenforceable daily cap refuses to spend rather than quietly disappearing.
Set a cap in the provider dashboard as well - that is the backstop.

### Switching it on in production (Vercel)

**Done on 23 Sept 2026** (OpenAI `gpt-5.6-luna`, the owner's key; roadmap §0 item 3a). The steps
stay here for a key rotation, another endpoint or a rollback.

Before anything else, set a monthly spending cap in the provider's dashboard: on Vercel the
daily budget is 0 (no usage ledger), so that cap is the backstop.

1. **Environment variables** (Production; Preview too if you use previews). Change existing ones
   in the dashboard (Settings -> Environment Variables) or with `vercel env rm <NAME> production`
   followed by `vercel env add <NAME> production`, which prompts for the value:

   | Variable | Value | Note |
   |---|---|---|
   | `LLM_ENABLED` | `true` | was `false` |
   | `LLM_PROVIDER` | `openai` | the default; the adapter for every OpenAI-compatible endpoint |
   | `LLM_BASE_URL` | the endpoint (table above) | `https://api.openai.com/v1` is the default |
   | `LLM_MODEL` | `gpt-5.6-luna` for OpenAI, the deployment name on Azure | the default is `gpt-5.6-luna` |
   | `LLM_REASONING_EFFORT` | `none` for `gpt-5.6-luna`; a single space for a model without the parameter | a single space means unset, like the other blank values there |
   | `LLM_DAILY_TOKEN_BUDGET` | `0` | a positive cap refuses every call without a ledger (roadmap §1) |
   | `LLM_TIMEOUT_SECONDS` | `15` (the default) | with the next row a call takes at most 20 s |
   | `LLM_MAX_ATTEMPTS` | `1` (the default) | `LOOKUP_DEADLINE_SECONDS` stays at its default, 50 |
   | `LLM_CACHE_PATH`, `LLM_USAGE_PATH` | stay a single space | only `/tmp` is writable |
   | `LLM_API_KEY` | the key, **added by the owner**: `vercel env add LLM_API_KEY production`, pasted at the prompt, marked Sensitive | never in a file, a chat, a commit or a log |

2. **Redeploy** - environment changes reach only new deployments. Deploy `main` (with PRs #8 and
   #9 merged) from a clean checkout, as in "Deploying on Vercel": `vercel deploy --prod`.
3. **Smoke checks**, from a linked checkout (`vercel curl` handles Deployment Protection):
   - `vercel curl /health` - `llm_configured: true`, `model` as set, `codebooks.state: loaded`.
   - One lookup through the model: a file `body.json` holding `{"isin": "DE0005140008"}`, then
     `vercel curl /api/suggest -X POST -H "content-type: application/json" --data-binary @body.json`
     (PowerShell mangles inline JSON quotes, hence the file). Expect `"answered": true` and
     `nace.proposal.basis` `"model"` with a confidence; `nace.reason` explains any abstention.
   - The page: `DE0005140008` shows the navrhovaný kód with "jistota ..." instead of "podle
     pravidel".
   - The golden run through the model runs locally (step 3 of the local list), not on Vercel.
4. **Reading a failure** (the reason on the page, in `nace.reason`, or in the golden run):
   `401` - the key, or an Azure endpoint that wants the `api-key` header (adapter); `400` naming
   a parameter - the endpoint table; "cannot be enforced" - `LLM_DAILY_TOKEN_BUDGET` is not 0;
   "no time left for the model" - the registers were slow, the rules' proposal stands.
5. **Rollback**: `LLM_ENABLED=false` and redeploy - the tool is back in deterministic mode, with
   the rules' proposals.

## Hard rules that already shape the code

- Read-only everywhere; nothing writes to DWS or CTS.
- Full NACE codes stay in the data; truncation to two digits happens only in the CTS-ID
  mapping step.
- Only valid ESA leaf codes can be emitted; parents are rejected.
- Every loaded codebook set is versioned so every output row can carry the version.
- Nothing retrieved from DWS will ever reach an LLM (no module here reads DWS; the rule stays).
- Every lookup is audited; no retrieved content enters the audit log.
- A suggestion row names every register that answered (`GLEIF+OPENFIGI+WEB`), so an
  ISIN-backed lookup is distinguishable from a name-only one (`WEB`). Register data about a
  foreign issuer is public and may join the web description in a prompt; DWS data may not.

## Development notes

- Lint and format with ruff: `ruff check .` and `ruff format .` from `app/`.
- Test module basenames are unique across `tests/` (`test_ico.py`, `test_isin.py`,
  `test_codebooks_*.py`, `test_sources_*.py`, `test_api_*.py`, `test_settings.py`,
  `test_audit.py`, `test_probe.py`, `test_vercel_config.py`,
  `test_suggest.py`) because
  pytest runs in the default import mode.
- The source tests never touch the network: GLEIF and OpenFIGI are driven through
  `httpx.MockTransport` clients answering with trimmed live payloads, kept in
  `tests/sources/conftest.py`, and the web evidence through mock transports and a static
  search provider.
- `numpy` is a dev extra only: the identifier and codebook tests feed numpy scalars to the
  normalisers. `pandas` is not a dependency (dropped in PR #5; it was imported nowhere).
- Deployment target is Vercel (roadmap E1, "Deploying on Vercel" above); `app/Dockerfile`
  stays for local runs. `uvicorn` comes with the `dev` and `server` extras, not the runtime
  dependencies.
