# NACE/ESA classification assistant (Raiffeisenbank CZ Finance/MIS)

**Since 22 September 2026 this repository is Tool 1 only, deploys to Vercel and keeps the
LLM off until an endpoint is approved** - the plan is in [`../docs/ROADMAP.md`](../docs/ROADMAP.md).
Tool 2 is built elsewhere; its code here is parked until the roadmap's E0 removes it.

Originally two internal tools sharing one codebase, for Middle Office treasury and Reporting:

- **Tool 1 – ESA/NACE suggester for foreign issuers.** From an ISIN, issuer name and/or
  activity description, suggest a 2-digit NACE code and an elementary ESA 2010 sector
  code together with their CTS codebook IDs, top 3 candidates each, with evidence.
- **Tool 2 – RES/OR lookup for client corrections.** For a list of Czech companies (name
  or IČO) return one row per company with RES and OR fields side by side, exported as xlsx.

The full project brief, data-source priorities and hard rules live in `../CLAUDE.md`.
This README describes what exists, how it is laid out and how to run it.

## Status

**Running mode: deterministic.** No model key is configured, by choice. The tool narrows each
codebook to about a dozen candidates, each with its CTS ID resolved, and a human picks. The
page and the xlsx both carry that shortlist; the panel says plainly that no code was chosen.

Measured on the (still provisional) golden cases, the deterministic top pick is correct **90%**
of the time for NACE and **60%** for ESA - useful on its own, but unable to explain itself or
to resolve distinctions that turn on a sentence.

**An ISIN is now enough to start.** GLEIF resolves it to the issuer's LEI record (legal
name, country, legal form, entity category, direct and ultimate parent) and OpenFIGI to the
instrument (market name, security type, market sector); both are public and keyless. The
legal name becomes the web query and the facts go into the shortlist and the prompt, so a
bank is a bank because the register says so. See "Tool 1: issuer identification by ISIN".

**Next step: enable the OpenAI API.** The classifier, prompts, cache and spending limits are
built and tested against a stub; see "Enabling the model" below.


Build steps 1-3 are complete. Steps 4-6 are scaffolded as docstring-only packages.

| Step | Scope | Status |
|---|---|---|
| 1 | Codebook loaders + versioning + startup consistency check; IČO/ISIN identifiers; settings | **done** |
| 2 | DWS adapter (`sources/dws.py`), ARES fallback, resolver, audit log, Tool 2 via CLI | **done** |
| 3 | Batch xlsx in/out with messy-input tolerance (`core/batch`, `core/export`) | **done** |
| 4 | Single-lookup API + server-rendered UI (Jinja2 + htmx) | **done** (Tool 1) |
| 5 | Deterministic classifier: RES ESA sector -> BA0036 ID, RES 2-digit NACE -> OKEC_NACE2 ID | pending |
| 6 | LLM classifier for foreign issuers (structured selection from a candidate list) | pending |

Work stops after each step: tests run, the state is summarised, and the next step waits
for an explicit go-ahead.

## Repository layout

Everything lives under `app/` (mounted as `/app` in the container later).

```
app/
  core/
    identifiers/    ico.py (normalize to 8 digits, mod-11 check), isin.py (format + Luhn)   [step 1]
    codebooks/      xlsx reader, loaders, models, versioning, consistency check, CLI       [step 1]
    sources/        base.py, dws.py, ares.py, resolver.py, CLI                              [step 2]
                    web.py (foreign-issuer evidence, blocklist enforced)                    [step 6]
                    gleif.py, openfigi.py, identity.py (ISIN -> issuer, public registers)   [step 6]
    audit.py        lookup audit trail (identifier, timestamp, user)                       [step 2]
    classify/       candidates.py + hints.py + text.py (pre-filter), golden.py, CLI          [step 6]
                    prompts.py, provider.py, llm.py, cache.py (the model call)               [step 6]
    export/         columns.py (the row contract), xlsx.py (Subjects + Run sheets)          [step 3]
    batch/          reader.py (messy xlsx in), runner.py, CLI                               [step 3]
  api/              FastAPI: GET /, POST /suggest, POST /api/suggest, /suggest.xlsx         [step 4]
  ui/               templates/suggest.html (generated from prototype/suggest.html)          [step 4]
  config/           settings.py (pydantic-settings), .env.example
  data/codebooks/   the four bootstrap xlsx files (git-ignored, bank-internal)
  tests/
    identifiers/    unit tests for IČO and ISIN
    codebooks/      unit tests with synthetic xlsx fixtures + real-file smoke test
    sources/        source tests: mocked ARES transport, fake DWS driver                     [step 2]
    fixtures/       verified IČO -> expected output                                          [step 3+]
    golden/         verified issuer name/description -> expected NACE/ESA                    [step 6]
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

All four real codebooks are present on this machine and load with 0 errors
(version `cb-3b12e64837840ca0`), so the whole suite runs with no skips. The real-file
smoke test (`tests/codebooks/test_codebooks_real_files.py`) still skips
automatically when the xlsx files are absent; every other test builds its own xlsx
fixtures in a temporary directory.

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
| `LOG_LEVEL` | `INFO` | Logging level |

### Step 2 variables

| Variable | Default | Meaning |
|---|---|---|
| `DWS_DSN` | empty | ODBC DSN / connection string of the read-only account. **Empty disables DWS**, and every lookup falls back to ARES |
| `DWS_USER`, `DWS_PASSWORD` | empty | Read-only credentials; the password is a `SecretStr` and is never logged |
| `DWS_SCHEMA` | empty | Schema qualifying the DWS objects |
| `DWS_TIMEOUT_SECONDS` | `30` | Per-query timeout |
| `ARES_ENABLED` | `true` | Query the public ARES API for IČOs DWS does not have |
| `ARES_BASE_URL` | `https://ares.gov.cz` | Base URL of the public API |
| `ARES_TIMEOUT_SECONDS` | `15` | HTTP timeout per request |
| `ARES_MIN_INTERVAL_SECONDS` | `0.25` | Minimum delay between two ARES requests (see below) |
| `ARES_MAX_ATTEMPTS` | `3` | Attempts per request, including the first |
| `ARES_USER_AGENT` | internal tool string | Sent so the API operator can identify the caller |
| `LOOKUP_USER` | OS login name | Requesting user written to the audit log |

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

The LLM variables remain commented placeholders in `.env.example` for step 6.

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
  inherently ambiguous once a leading zero has been lost; RES/DWS values are expected to
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
raises `CodebookConsistencyError` (carrying the report) when any error is present. The
FastAPI startup hook (step 4) will call it; today the CLI does.

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
and row count. Output rows in later steps carry `version.id` as their `codebook_version`.
When the DWS tables replace the xlsx files, the version should be computed over the
normalised rows instead.

## Identifiers (`core/identifiers`)

- `normalize_ico(value)` accepts text, integers and floats as they come out of Excel or
  pandas: whitespace (including non-breaking spaces) is removed, `1350.0` and `1.35E3`-style
  artefacts are resolved exactly, the result is zero-padded to 8 digits (more than 8 digits,
  including surplus leading zeros, is rejected as `too_long`) and validated with
  the mod-11 checksum (weights 8..2, check digit `(11 - sum mod 11) mod 10`). Failures raise
  `InvalidIcoError` with a machine-readable `reason`. A `CZ`-prefixed DIČ is rejected unless
  `allow_dic_prefix=True`. `is_valid_ico()` and `try_normalize_ico()` never raise.
- `normalize_isin(value)` uppercases, strips whitespace, checks the ISO 6166 layout
  (`^[A-Z]{2}[A-Z0-9]{9}[0-9]$`) and the Luhn checksum over the letter-expanded string.
  `isin_country_code()` returns the two-letter prefix. For both identifiers any non-string,
  non-numeric input (including a pandas NaN blank) is `unsupported_type`; callers turn blanks
  into `None` first.

## Data sources (`core/sources`)

### Priority and what a miss means

`SubjectResolver` asks DWS first and ARES only afterwards. The distinction that makes the
fallback trustworthy:

- a source returning `None` means **the register does not hold this subject** - a fact;
- a source raising `SourceUnavailableError` means **we could not ask** - not a fact.

So a warehouse outage never turns into "not registered": the resolver records the outage,
falls through to ARES, and reports `status="error"` only when every source failed. A partial
DWS hit (RES row but no OR row) is completed from ARES and the halves keep their own
provenance, so a row can legitimately read `DWS+ARES_LIVE`.

### The record model (`base.py`)

One subject is two independent halves kept side by side, plus provenance:

- `ResRecord` - name, `nace` (a **list** of `NaceAssignment`, never a single field, both
  revisions distinguished by `revision`), `esa_sector` (canonical `S.12203`), `founded_on`.
- `OrRecord` - obchodní firma, předmět podnikání, předmět činnosti, datum vzniku a zápisu.
- `Provenance` - source, `retrieved_at`, `snapshot_at`; `timestamp` is the snapshot when the
  source states one, otherwise the retrieval time.

`nace_mismatch` compares the prevailing Rev. 2 and Rev. 2.1 codes on digits only, so `64.19`
and `6419` are equal. It returns **`None`, not `False`, when either revision is missing**:
"cannot tell" is a different answer from "they agree" and a reviewer must see the difference.

Full NACE codes are stored verbatim with leading zeros intact. Nothing in this package
truncates to a division; that still happens only in `CodebookSet.cts_id_for_nace()`.

### DWS adapter (`dws.py`)

The only module in the application containing SQL. Read-only by construction:

- every statement is a literal built from the `TABLES` / `COLUMNS_*` maps, never from caller
  input - the IČO always travels as a bind parameter;
- `ensure_read_only()` refuses anything that is not a single `SELECT`/`WITH`, so a later edit
  cannot smuggle in a write;
- the account itself is read-only.

**Every table and column name is a placeholder** marked `TODO(dws-schema)` and gathered in
those maps. Correcting them once the warehouse team confirms the schema is an edit of the
dictionaries, not of the query logic. The same applies to `REVISION_VALUES` (how the NACE
table spells a revision) and `ACTIVITY_KINDS`. A row whose revision is not recognised is
skipped with a warning rather than guessed at, because filing a code under the wrong revision
would corrupt `nace_mismatch`.

The ODBC driver is imported lazily, so the package installs and the tests run on a machine
with no driver at all. `pyodbc` is deliberately not yet a dependency - add it once the
confirmed driver is known.

### ARES fallback (`ares.py`)

Endpoints verified against the live API on 2026-09-22 (IČO 49240901):

| Endpoint | Gives |
|---|---|
| `/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty-res/{ico}` | the RES half: `czNace2008`/`czNacePrevazujici2008` (Rev. 2), `czNace`/`czNacePrevazujici` (Rev. 2.1), `statistickeUdaje.institucionalniSektor2010` (the ESA sector, digits only), `datumVzniku`, `datumAktualizace` |
| `/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty-vr/{ico}` | the OR half: `obchodniJmeno[]`, `cinnosti.predmetPodnikani[]`, `cinnosti.predmetCinnosti[]`, `datumZapisu`, `spisovaZnacka[]` |
| `POST .../ekonomicke-subjekty/vyhledat` | name search - **not verified**, marked TODO in the module |

Details that matter:

- VR list entries carrying `datumVymazu` are historical and are dropped, so a review row
  never shows a deleted trade name or a withdrawn activity.
- The ESA sector is normalised through the codebook normaliser to `S.xxxxx`; an unparseable
  value is dropped with a warning rather than passed on.
- Rows are stamped `ARES_LIVE`.
- **Throttling**: ARES starts timing out on back-to-back requests (observed while building
  this adapter), so requests are spaced by `ARES_MIN_INTERVAL_SECONDS` and transient failures
  (timeout, connection error, 5xx) are retried with linear backoff. A 4xx is not retried.

### Audit trail (`core/audit.py`)

Every lookup is logged to the `core.audit` logger with identifier, timestamp, requesting
user, sources consulted and outcome - as a message and as structured fields in
`record.audit` for a JSON handler. **No retrieved content is ever logged**: the trail holds
the question and the outcome, never the answer, so it can be kept and shipped without
carrying client data.

### Tool 2 from the command line

```bash
python -m core.sources 49240901                   # one subject
python -m core.sources --file icos.txt --json     # a list, machine-readable
python -m core.sources 49240901 --no-dws          # force the public fallback
```

Identifiers may be IČOs or names, mixed. An all-digit query with a bad check digit is
rejected as `invalid_input` rather than searched as a company name, so a typo surfaces
instead of silently returning nothing. A name matching several subjects returns
`ambiguous` with the candidates listed; nothing is guessed. `--file` tolerates a UTF-8 BOM,
blank lines and `#` comments.

Output carries the three attribution fields required of every row (`source`, `timestamp`,
`codebook_version`) and prefixes the halves `RES_` / `OR_`, matching the xlsx export of
step 3. A missing codebook does not block a lookup - the row simply carries no codebook
version, since codebooks are needed to emit CTS IDs (step 5), not to read a register.

Exit codes: `0` every identifier resolved, `1` at least one was not found / ambiguous /
invalid, `2` nothing could be asked at all.

## Batch xlsx in/out (`core/batch`, `core/export`)

```bash
python -m core.batch klienti.xlsx                      # -> klienti_lookup.xlsx beside it
python -m core.batch klienti.xlsx -o kontrola.xlsx --sheet "Flagged"
python -m core.batch klienti.xlsx --no-dws --no-echo-input
```

Exit codes: `0` every row resolved, `1` some rows need a human, `2` the batch could not run
(unreadable input, no source configured, or every row failed).

### Reading input nobody cleaned up

The reader assumes almost nothing: the header may sit under title rows or be missing, the
IČO and name may be in separate columns or mixed in one, IČOs arrive as numbers with their
leading zeros eaten by Excel (`177041`) or spaced by hand (`00 177 041`), and rows are blank
or duplicated. Header matching is case- and accent-insensitive (`ico` = `IČO` = `Ičo`) and
accepts a suffix (`IČO klienta (RES)`).

Two decisions worth knowing:

- **A malformed IČO is never silently replaced by the name column.** The row is looked up as
  given and comes back `invalid_input` with the reason. Falling back to the name would return
  data for a *different* company than the sheet names - the one error a reviewer could not
  catch.
- **When no header is recognised, row 1 is kept as data.** A stray junk row surfaces as
  `invalid_input`; discarding a row that turned out to hold a real company would be silent
  data loss.

### The result workbook

One row per input row, in input order, **including rows that resolved to nothing** - a
reviewer needs to see the empty lines, not find them missing. Columns: `IN_*` (the echoed
input, so the sheet can be worked in place), then the subject columns defined once in
`core/export/columns.py` and shared with the single-lookup CLI. `CTS_*` is reserved for
step 5 and no CTS column is emitted yet.

Excel-specific care:

- `IČO` and NACE codes are written as text with the `@` format, so `00177041` does not come
  back as `177041` - the corruption Tool 2 exists to find must not be reintroduced on export;
- dates are real dates with an ISO format; timestamps are converted to UTC and the headers
  say `(UTC)`, because Excel cannot store an offset;
- control characters openpyxl refuses are stripped and over-long texts are truncated at
  Excel's 32 767-character cell limit rather than raising.

A second **Run** sheet records the input file, row counts per status, the user, the sources
and the codebook version, so a workbook that has been emailed on still answers "where did
this come from?".

Repeated identifiers are resolved once: the cache is keyed on the normalized IČO, so
`49240901`, `  49 240 901 ` and the Excel-mangled `49240901.0` are one call to ARES.

## Tool 1: the candidate pre-filter (`core/classify`)

```bash
python -m core.classify "captive funding vehicle of a banking group" --verbose
python -m core.classify --golden        # recall over tests/golden, no API key needed
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

**Fail-soft, like ARES.** A register that has nothing returns `None` and leaves a note
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
| `GET /health` | status, codebook version, whether the model, search and the registers are configured |

`POST /api/suggest` also returns an `identity` object (LEI, legal name, country, category,
legal form, ultimate parent, instrument, the registers that answered and the facts) for a
script that wants the register data rather than the prose.

**Codebooks load once at startup and an inconsistent set stops the service.** Emitting a CTS
ID derived from a bad codebook is the one failure nobody catches downstream, so it fails at
boot rather than in a report.

**There is no server-side session.** The download re-runs the request; that is free because
the classifier caches, and it means a shared or bookmarked link behaves the same for
everyone.

**The page says when it is only shortlisting.** With no `LLM_API_KEY` it shows a banner and
each panel explains that no code was chosen and why - never a blank panel that looks like a
bug.

The template is generated from `ui/prototype/suggest.html`; keep the prototype in step when
changing the design, since it is the reference that was reviewed.

### Container

```bash
docker build -f app/Dockerfile -t naceesa:0.1 app
```

Codebooks and `.env` are mounted, never baked into the image. Mount a volume at
`/app/data/cache` too, or the answer cache and usage ledger are lost on every restart.

## Enabling the model (next step)

1. Put the key in `app/.env` (git-ignored, never in code or chat):
   ```
   LLM_API_KEY=sk-...
   ```
2. Confirm `LLM_MODEL`. The default is a cheap placeholder marked TODO; model names change.
3. Run a few real issuers. **The provider path has never made a live call** - the request
   shape is verified against the docs and tested against a mock, but expect to fix something
   small the first time.
4. Have MO check those suggestions, then record the confirmed ones in
   `tests/golden/cases.json` with `verified_by` filled in. That is what turns "seems right"
   into a number, and what justifies keeping the cheap model.
5. Watch the spend:
   ```bash
   python -m core.classify --usage
   ```

Limits are already enforced (8,000 tokens/request, 200 calls/run, 500,000 tokens/day) and
fail closed: an unenforceable daily cap refuses to spend rather than quietly disappearing.
Set a cap in the provider dashboard as well - that is the backstop.

## Hard rules that already shape the code

- Read-only everywhere; nothing writes to DWS or CTS.
- Full NACE codes stay in the data; truncation to two digits happens only in the CTS-ID
  mapping step.
- Only valid ESA leaf codes can be emitted; parents are rejected.
- Every loaded codebook set is versioned so every output row can carry the version.
- Nothing retrieved from DWS will ever reach an LLM (relevant from step 6).
- Every lookup is audited; no retrieved content enters the audit log.
- Rows answered by the public API are marked `ARES_LIVE` so live data is never mistaken
  for governed warehouse data.
- A suggestion row names every register that answered (`GLEIF+OPENFIGI+WEB`), so an
  ISIN-backed lookup is distinguishable from a name-only one (`WEB`). Register data about a
  foreign issuer is public and may join the web description in a prompt; DWS data may not.

## Development notes

- Lint and format with ruff: `ruff check .` and `ruff format .` from `app/`.
- Test module basenames are unique across `tests/` (`test_ico.py`, `test_isin.py`,
  `test_codebooks_*.py`, `test_sources_*.py`, `test_settings.py`, `test_audit.py`,
  `test_suggest.py`) because
  pytest runs in the default import mode.
- The source tests never touch the network or a database: ARES, GLEIF and OpenFIGI are
  driven through `httpx.MockTransport` clients answering with trimmed live payloads, and DWS
  through a fake DBAPI connection, all in `tests/sources/conftest.py`.
- No deployment config for any public PaaS will be added; a Dockerfile comes with a later step.
