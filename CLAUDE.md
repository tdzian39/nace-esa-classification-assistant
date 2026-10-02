# CLAUDE.md

Guidance for Claude Code working in this repository.

# NACE/ESA classification assistant — Raiffeisenbank CZ Finance/MIS

## Context

**Tool 1 only** (since 22 Sept 2026). MO treasury sets up foreign securities issuers in CTS
and must pick a 2-digit NACE code and an elementary ESA 2010 sector code. Input: ISIN and/or
issuer name and/or activity description - or a Czech issuer's IČO typed into the name field.
Output: issuer name, description, suggested NACE + CTS ID, suggested ESA + CTS ID, top 3
candidates each, with evidence. **A Czech (resident) issuer takes both codes from RES** (since
2 Oct 2026: MO asked through Reporting, Jakub decided), so MO need not care who is resident.

Deploys to **Vercel**. Tool 2 (RES/OR lookup) moved to `jaeksrampota/res-or-lookup` in PR #5,
taking DWS and ARES with it. **ARES is back for one thing only** (2 Oct 2026, reversing "nothing
here reads ARES" for residents): RES's prevailing NACE and institutional sector of a resident
issuer, read inside this tool - not merged with res-or-lookup; OR (the VR endpoint) adds no
codes and is not called; DWS stays out. **The plan, every decision and every open question live
in `docs/ROADMAP.md`: read it after this file and keep both in step.**

Sources, in order: **GLEIF** (`api.gleif.org`) + **OpenFIGI** (`api.openfigi.com`) for an
issuer given by ISIN; **RES through ARES** (`ares.gov.cz`) for a Czech issuer's codes;
**Wikidata/Wikipedia by that LEI or by the issuer's name**, then the **model's own web search**
(every lookup since 30 Sept 2026), for activity descriptions.
Never scrape `apl.czso.cz` or `or.justice.cz` - ARES is the official API, not scraping.

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
  BA0036: for a resident issuer the reviewable table `core/codebooks/res_esa.py` converts
  RES's code (2 Oct 2026); for a foreign one it stays open (roadmap Q7).
- **CTS is on CZ-NACE 2025 (Rev. 2.1)**: 87 divisions, no 45, CTS ID 496 missing where 45 sat.
  Rev. 2 codes need mapping.
- All four load with 0 errors/warnings as `cb-3b12e64837840ca0`; 87 divisions match 1:1.
- Prompt budget (~4 chars/token): 87 short labels ≈ 895 tokens; every label of every division
  ≈ 10,400; a 12-division shortlist's full texts ≈ 1,440. Hence the two-stage classifier —
  narrow on short labels, decide on full texts — not one prompt holding the codebook.

## Layout (everything under `/app`)

```
core/identifiers  isin.py, ico.py (an IČO typed into the name field)
core/sources      base.py, gleif.py, openfigi.py, identity.py (ISIN -> issuer), web.py, wikimedia.py,
                  names.py (name matching), ecb.py (ECB lists of financial institutions),
                  firds.py (ESMA FIRDS: ISIN -> LEI when GLEIF has no mapping),
                  llm_web.py (the model's web search for the description),
                  ares.py (RES through ARES: a Czech issuer's NACE and sector)
core/codebooks    loaders, versioning, consistency; blob.py (private Vercel Blob);
                  res_esa.py (RES sector -> BA0036 resident code, the reviewable table)
core/classify     candidates.py (pre-filter), hints.py, llm.py, proposal.py, golden.py,
                  budget.py (limits, usage ledger), usage_report.py (the ledger as Excel),
                  residents.py (what RES settles for a resident), golden_residents.py
core/export       columns.py (the row), xlsx.py
core/probe.py     the /probe register checks     core/auth.py  sign-in (users, cookie)
core/reports.py   error reports (the button): request + result row + note, to the db, a dir or Blob
core/db.py        the central Postgres (D4): ledger, cache, audit events, reports; SQLite engine for tests
core/admin.py     the developer page's numbers: the priced ledger and the complaints, filtered
api/  GET / · POST /suggest · POST /report · POST /api/suggest · GET /suggest.xlsx · /health · /probe
      GET|POST /login · POST /logout · /admin (+ /login, /logout, usage.xlsx, reports.xlsx)
ui/   suggest.html, login.html, admin.html, admin_login.html + prototype/suggest.html
tests/golden  cases.json, identity.json; residents.json, residents_registers.json (RES cases)
config/settings.py · .env.example · vercel.json · .python-version · pyproject.toml
```

Stack: Python 3.12, FastAPI, openpyxl, pydantic. UI server-rendered (Jinja2 + htmx), no JS
framework. No pandas; numpy is a dev extra only (tests feed numpy scalars to the normalisers).

## Hard rules

- Every row carries source (GLEIF/OPENFIGI/RES/WEB), retrieval timestamp, codebook version.
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
- The model is **on in production** since 23 Sept 2026 (the owner's decision, roadmap §1): OpenAI
  `gpt-5.6-luna`, the key a Sensitive Vercel variable, never in the repo. Rollback:
  `LLM_ENABLED=false` and redeploy. Keep its tests green.

## Commands (from `app/`)

```bash
../.venv/Scripts/python.exe -m pip install -e ".[dev]"
../.venv/Scripts/python.exe -m core.classify "popis cinnosti"      # shortlist (--verbose, --estimate)
../.venv/Scripts/python.exe -m core.classify --golden [--model]    # recall + top-1 (--model costs money)
../.venv/Scripts/python.exe -m core.classify --golden-capture      # re-record register answers (network)
../.venv/Scripts/python.exe -m core.classify --usage               # limits and today's spend
../.venv/Scripts/python.exe -m core.classify --usage-xlsx [PATH]   # every call, tokens and cost, as Excel
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .
../.venv/Scripts/python.exe -m core.codebooks [--no-strict --json --dir PATH]
../.venv/Scripts/python.exe -m core.reports --list [--dir PATH] [--xlsx PATH]   # the error reports
../.venv/Scripts/python.exe -m core.sources.ecb --refresh | --lei LEI   # ECB lists into the database
../.venv/Scripts/python.exe set_admin_password.py   # asks twice; one hash into .env as APP_ and ADMIN_PASSWORD_HASH
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

Production runs **with the model** since 23 Sept 2026 and **behind the app's own login** since
24 Sept 2026 (Vercel Authentication is **off**, so that login is the only gate and the only
guard on the model's cost). Since the evening of 24 Sept 2026 (code at `main` `1ec4e7b`) it
also has the Wikipedia description, the error reports and the central database: `/health`
shows `database: postgres …` and `reports: db`. Since 29 Sept 2026 (`main` `055f717`) name-only
lookups via GLEIF, the ECB lists, the FIRDS LEI fallback, code choice on the page and the
six-column export are live too; production runs `main` `7a966ce` since 30 Sept 2026. The
developer page `/admin` is on there too,
behind its own `ADMIN_PASSWORD_HASH` (Sensitive, Production; not MO's password). The
deterministic result is still what a
codebook shows whenever the model is off or declines — typically ESA when the evidence does not
say who owns the issuer (the control axis, Q7): Deutsche Bank by ISIN alone gets the rules' tied
bank family, and a one-line popis stating the ownership lets the model pick.

Czech (resident) issuers take their codes from RES since PR #26 (2 Oct 2026; not merged or
deployed when written): on the 15 resident golden cases the proposal with the model off is the
brief's code in 15 of 15 (the ING branch as the tie it must be).

Figures are **provisional** (no case is `verified_by`-confirmed) and must not be quoted as
accuracy. Next steps are `docs/ROADMAP.md` §0: a signed-in production lookup confirming the
database rows and FIRDS answering from Vercel. No Vercel Pro; nothing can be checked in CTS (Jakub,
23 Sept 2026).

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
  * The legal name is the web search query (an ISIN never was one). **The name shown and
    exported is the one found** (`suggest.issuer_name_of`, 30 Sept 2026, Jakub: "find the
    issuer's name"): GLEIF's legal name, then the name the model's web search found, then what
    MO typed, then OpenFIGI's market name; a typed name that differs is shown under it
    (`typed_name_apart`, "zadaný název: …"). Until then the typed name won.
  * **With no ISIN, the typed name is asked in GLEIF** (`GLEIF_NAME_MATCH`, 29 Sept 2026):
    `filter[entity.legalName]` is a word search ("adidas" → adidas AG + 30 subsidiaries), so
    only an ACTIVE entity whose name *is* the query counts (`names.fold`), else the same base
    and legal form with long = short (`names.legal_form_key`: "OMV AG" = "OMV
    AKTIENGESELLSCHAFT", not "OMV - S.P.A."), else equal with the legal form stripped
    ("adidas" = "adidas AG"); exactly one per level, or none ("OMV" alone ties three).
    The search needs every word, so a typed legal form costs a second search without it. Its LEI then finds the
    Wikipedia article like an ISIN's does. Flagged "podle názvu, ne podle ISIN"; OpenFIGI is
    not asked. An ISIN always wins over the name.
  * **Facts are stated, never decided**: "the ultimate parent sits abroad" is a fact; whether
    that is "pod zahraniční kontrolou" is the classifier's call (Q7).
  * Rate limits: GLEIF 60/min (throttle 1.0 s), OpenFIGI keyless 25/min (throttle 2.5 s). 429
    and 5xx retry, other 4xx do not. Up to 4 GLEIF requests per ISIN
    (`GLEIF_FETCH_PARENTS=false` makes it 1) and 1 OpenFIGI.
  * GLEIF has no ISIN mapping for some funds (iShares Core MSCI World) that OpenFIGI names —
    hence both registers, in that order.
  * **FIRDS fallback** (`firds.py`, `FIRDS_ENABLED`, 29 Sept 2026): when GLEIF maps no ISIN,
    ESMA FIRDS (`registers.esma.europa.eu/solr/esma_registers_firds/select?q=isin:`) gives the
    issuer LEI and GLEIF `fetch(lei)` the record; `FIRDS` joins `sources`. Fixed BMW Finance,
    Shell International Finance, iShares, Bavarian Sky. `tests/conftest.py` sets
    `FIRDS_ENABLED=false`; the golden replay passes `firds=lambda isin: None`.
  * `source` names the registers that answered, even when the answer was "nothing", then `WEB`.
    Egress needed: `api.gleif.org`, `api.openfigi.com`, and `ares.gov.cz` for Czech
    issuers (443).
- **Web evidence** (`web.py`): the scraping ban is **enforced** by `BLOCKED_HOSTS`/`is_blocked()`,
  checked when filtering hits and again inside the fetcher. A typed description is authoritative
  and stays first, but the web is still consulted (30 Sept 2026, Jakub: always look at the web):
  Wikipedia's lead follows it as "Podle Wikipedie (cs): …". Every thin result (no provider, search down, 404, PDF, all blocked) returns
  evidence with no description, which must make the classifier **abstain rather than guess from
  the name**. The provider is a Protocol — which search API a bank may call is procurement.
- **Wikipedia description** (`wikimedia.py`, E5.1, revived 24 Sept 2026): for every lookup (a
  typed description too, since 30 Sept 2026) the gatherer asks Wikidata for the item whose P1278
  is the LEI, then the Wikipedia REST summary (`WIKIPEDIA_LANGUAGES`, `cs,en`), before any search
  provider.
  * Matched on the **LEI first**; when no item carries it (or there is none), the **issuer's
    name is searched on Wikipedia** (`WIKIMEDIA_NAME_MATCH`, `describe_by_name`, 30 Sept 2026):
    legal form stripped, English edition first (a Czech search gave the *town* for "Kongsberg
    Gruppen ASA"), among hits whose title shares a distinctive word with the name
    (`names_agree`) the one sharing most (`shared_words`) is tried first, disambiguations and
    family names skipped, and the item's Czech article preferred for the text. **No uniqueness
    test and no LEI guard**: Jakub called the strict exact-label match (24-29 Sept) nonsense,
    since it described almost no fund or vehicle. Accepted cost: a vehicle may get its group's
    article (live: BMW Finance N.V. -> "BMW Bank"). Live 30 Sept: Kongsberg Gruppen, the EIB (cs)
    found; iShares Core MSCI World, Nordkap Funding, Bundesrepublik Deutschland not - the model's
    web search is for those.
    The item is sometimes the group or brand (BMW AG -> "BMW"), so the page always says to
    check; a name match says "podle shody názvu, ne identifikátoru".
  * The description is the article's lead plus Wikidata's one-liner and P452 industry labels
    (cs with en in parentheses), so it counts as `WEB` evidence: the `source` column is unchanged.
  * Narrow calls only: the full item is 443 KB and industry items with claims 250 KB, so the
    industries' NACE codes (P4496) are **not** read; the labels usually are NACE titles.
  * Up to 5 requests; `WIKIMEDIA_TIMEOUT_SECONDS` 5, and none starts that could outlive the lookup
    deadline (a `SourceUnavailableError`, noted, never "not found"). Hosts `www.wikidata.org`,
    `{cs,en}.wikipedia.org`, in `/probe`'s default set while `WIKIMEDIA_ENABLED`.
  * Test helpers not about it set `wikimedia_enabled=False`, or a LEI in a fixture reaches the
    real Wikimedia (a name reaches the real Wikipedia search too, since 30 Sept 2026).
- **The model's web search** (`llm_web.py`, 30 Sept 2026, Jakub: "use llm for each"): every
  lookup with a model asks it to search the web for the issuer - OpenAI's **Responses API**
  (`POST /responses`) with the `web_search` tool, `tool_choice: "required"`,
  `filters.blocked_domains` = `BLOCKED_HOSTS`, `include: ["web_search_call.action.sources"]`,
  `reasoning.effort` `LLM_WEB_REASONING_EFFORT` (low; gpt-5 "minimal" cannot search).
  `gpt-5.6-luna` lists the tool (model page, 30 Sept 2026). It answers in Czech, "NÁZEV: … /
  POPIS: …" (activity, kind of institution, seat, group, **who owns or controls it** - Q7) or
  "NENALEZENO"; `parse_answer` reads it. The sentences follow the typed/Wikipedia description
  as "Podle webu (vyhledal model …): …", the cited pages join the evidence (a page on a blocked
  host is dropped), and the name is `IssuerEvidence.found_name`.
  * One `Prompt` with `kind="WEB"`, `web_search=True` through the classifier's **budgeted
    provider**, so the ledger records it as `WEB` for the signed-in user; `usage_report` adds
    `WEB_SEARCH_CALL_USD` (10 USD per 1,000 searches) to those rows. ~0.01 USD a lookup, about
    ten times the two classification calls, and a third call per lookup: the 200-call cap per
    instance lasts ~66 lookups.
  * **Cached** in the classifications table as text (`get_text`/`put_text`, kind `WEB`), key =
    name + ISIN + LEI + typed text + model + `WEB_PROMPT_VERSION`: the page, the download and a
    report's re-run read the same text, so the classification cache still hits.
  * Starts only if a classification call still fits after it (`2 × call_seconds` left before
    `LOOKUP_DEADLINE_SECONDS`), else a note. Fail-soft: no model = no call and no note
    (`build_web_search` returns `None`), a failed call = a note. `LLM_WEB_SEARCH=false` turns
    it off. It drops the gatherer's `NO_SEARCH_PROVIDER_NOTE` - the web *is* searched.
  * **Live since 30 Sept 2026** (`main` `7fbf8cd`), checked signed in: iShares Core MSCI World
    (no Wikipedia) got "…spravován BlackRock Asset Management Ireland, skupina BlackRock", ESA
    2002403 high; Kongsberg Gruppen got "norský stát vlastní 50,004 % akcií", ESA **2001001
    veřejné** high (was 2001002 without it); Deutsche Bank "rozptýlení akcionáři", 2002212 high.
    A lookup takes 17-19 s instead of ~11; cited URLs carry OpenAI's `utm_source=openai`.
- **ECB lists** (`ecb.py`, 29 Sept 2026): the LEI from GLEIF is looked up in the ECB's lists of
  financial institutions - MFI (central banks, credit institutions, MMFs, other deposit-takers),
  IF, FVC, IC, PF - loaded into the central database's `ecb_institutions` table by `python -m
  core.sources.ecb --refresh` (~30 s, 69k LEIs; MFI daily CSV, the rest monthly/quarterly
  xlsx zips; refresh by hand, no scheduler yet). A member gets a fact-sheet line with a
  bracketed code (`[ECB_MFI:CREDIT_INSTITUTION]`, `[ECB_IF]`, ...) and `hints.ESA_REGISTER_RULES`
  settle the **ESA family** from it (register score, like GLEIF's category for NACE) - never
  the control variant (Q7). A LEI on no list is stated as absent (with the lists' date), never
  decided; an empty table states nothing; a database failure is a note. Needs `DATABASE_URL`
  (`ECB_ENABLED`). The `source` column is unchanged; the citation is in the evidence list.
- **Czech (resident) issuers** (`ares.py`, `residents.py`, `res_esa.py`; 2 Oct 2026): resident =
  GLEIF's legal seat CZ, or (no LEI record) an IČO typed - the register's seat, never the ISIN
  prefix. The IČO is GLEIF's `registeredAs` (CZ + 8 digits, whatever the `registeredAt`), else
  the typed one; an IČO alone is asked in GLEIF too (`filter[entity.registeredAs]`), for the LEI
  the ECB lists key on. RES (`GET ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty-
  res/{ico}`, keyless, ~0.1 s) gives `czNacePrevazujici` (else the 2008 code, with a note),
  `institucionalniSektor2010`, `pravniForma`, `obchodniJmeno` (the name shown) and
  `datumAktualizace` ("stav k"). `ARES_ENABLED`, 5 s x 2 attempts (no Vercel variable needed).
  * NACE: the division of RES's code is a register candidate at a fixed `RES_SCORE` (100); the
    full code stays in the reason. No rule for a division CTS lacks (45) or without a code.
  * ESA: `res_esa.py` maps the ČSÚ sector to the BA0036 resident code - BA0036 codes only, its
    53 targets are the 53 resident leaves, a miss is `W_RES_ESA_TARGET_MISSING`. S.122: legal
    form 205 = cooperative, an ECB MFI credit institution = bank, else (lists read for the
    LEI) another deposit-taker; S.125: the ECB FVC list = securitisation, NACE 64.91/64.92 =
    lender. Otherwise the possible codes **tie** at RES's control digit and the shortlist is
    only them (`EsaCandidateFilter.only`). Never decided from the name.
  * An axis RES settles is **not sent to the model**; a tie goes to it with only the tied
    codes. A resident's ESA shortlist comes from the resident block. RES wins over the model
    and GLEIF categories; notes say what RES could not settle, and when an ECB list files the
    issuer elsewhere (ČEB and NRB credit institutions, EGAP an insurer; RES: 13110).
  * 404 = "RES nemá záznam" (ARES lags ČSÚ ~3 weeks and drops dissolved subjects); an outage
    is a note, never a 404. A Czech fund has no IČO (`RA999999`): resident, no RES.
  * No ARES name search yet: a Czech name GLEIF cannot match gets "zadejte IČO".
    `tests/conftest.py` sets `ARES_ENABLED=false`; the golden residents replay a recording.
- **Candidate pre-filter** (`candidates.py`): narrows to ~12 per codebook, each already carrying
  its CTS ID, so a returned code cannot be one CTS does not know. It optimises **recall**: a code
  the filter omits is one the model can never return. Mechanisms: the reviewable keyword table
  (`hints.py`, cs+en) and IDF-weighted overlap (`text.py`), plus English division titles
  (`nace_en.py`, scored only — never shown, never in a prompt).
- **A register outranks the model** (`proposal.propose`, 29 Sept 2026): when exactly one shortlisted
  code carries a `register:` reason and the model picked another, the register's code is the
  proposal (`basis="rules"`, `overridden` = the model's code, shown as the first alternative) - the
  EIB was answered 64 from "Bank" in its name. A register *family* (control variants) is never
  forced. The EU, which GLEIF files as a GENERAL Belgian public-law body and OpenFIGI as Govt, is
  recognised by its **ISIN country code `EU`** (ISO 6166 reserves it for EU institutions): the
  fact sheet line `[ISIN_EU]` -> NACE 99, ESA `ostatni mezinarodni instituce`. **Rules come from
  structured register data (identifiers, categories, list membership), never from one issuer's
  name** - the owner's rule, 29 Sept 2026.
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
  **before** ranking, whenever the family would not *fit* in the limit (29 Sept 2026: it used to
  check only "matched at all", so a weak match was ranked last and cut - 47 of 108 test lookups).
  With `[ECB_NONE]` + `[GENERAL]` and no financial/public keyword, `register_esa_families`
  settles it as a register rule. A rules proposal that ties control variants proposes the
  family, not a code: the page shows the family and every tied variant alike (codebook order, no
  radio checked), the row's code stays empty and its label names the family - the first variant is
  only the family's order (30 Sept 2026: the page used to show it as the navrhovaný kód). Appending afterwards is not enough — an industrial issuer whose description
  says only "bonds" and "finance" loses every slot to financial families. Measured: 20% of ESA
  recall. Do not turn it into a plain fallback.
- **Navrhovaný kód** (`proposal.py`): the model's first pick; with no model answer, the
  shortlist's first candidate **only if a rule put it there** (score ≥ 5; lexical stops at 1.0)
  **and no rule for another code or ESA family ties with it** — the captive trap, where "bank"
  and "captive" tie and the alphabet would pick the bank. A rule's proposal has no confidence,
  names its rules and, for a tie of control variants, offers them as `choices` under its
  `family` ("navrhovaná skupina"). `answered` still means the model answered.
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
  * `--usage-xlsx` (`usage_report.py`) writes the ledger as a workbook — Summary by model,
    codebook and day; Calls; Prices — priced at `PRICES` (OpenAI's standard prices, checked
    23 Sept 2026). A model missing there gets empty cost cells, never a guess. The ledger
    keeps the provider's cached-input count (`cached_prompt_tokens`, column added in place
    to older ledgers), priced at the cached rate; a row without it (older, or an estimated
    prompt count) is priced all-uncached and flagged as an upper bound — NULL is "not
    recorded", never zero. Without `DATABASE_URL` it knows only this machine's calls; with it,
    every device's, production's included (since 24 Sept 2026). Before that, production spend
    is only on the provider's usage page.
  * **A daily budget fails closed on a read error** (29 Sept 2026): both ledgers'
    `totals_since` return `None` (unknown, never zero) when the read fails, and
    `BudgetedProvider` then refuses the call, so with `LLM_DAILY_TOKEN_BUDGET > 0` a database
    outage makes the model abstain and the rules' proposal stands. Live since 30 Sept 2026;
    production's budget is still 0.
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
  * **The download is the brief's six columns** (`EXPORT_COLUMNS`, 29 Sept 2026: Emitent, Popis
    činnosti, NACE, NACE – CTS ID, ESA, ESA – CTS ID); source, time, codebook version, ISIN,
    LEI, evidence and notes are on the Run sheet, so the hard rule still holds. The JSON `row`
    keeps every column. On the page every shortlisted code has a "vybrat do exportu" radio
    (proposal pre-selected, `form="download"`, no JS); `/suggest.xlsx?nace=&esa=` takes the
    choice, refuses a code off the shortlist (400) and records "NACE/ESA vybral" on Run.
- **Sign-in** (`core/auth.py`, roadmap E2/D5): optional — `APP_PASSWORD_HASH` set turns it on;
  **on in production since 24 Sept 2026** (both variables Sensitive, Production only; rollback:
  delete the hash and redeploy — after turning Vercel Authentication back on, or the site is open).
  **One shared password plus a self-declared name**; the hash comes from `--hash-password` (the
  repo is public, so never the password). `normalize_name()` makes the name lower case, without
  diacritics, single-spaced — so one person is one name everywhere; the page asks users to
  type it that way. The session is a cookie signed with `SESSION_SECRET` over
  name, expiry and a fingerprint of the password hash (a new password ends every session). No
  server-side state. **It fails closed**: a hash without a secret, or an unreadable hash, lets
  nobody in. Middleware gates every path but `/login`, `/logout`, `/health`, `/api/version`;
  `/api/*` gets 401, htmx gets 401 + `HX-Redirect`, a GET a 303 with `next` (same-site only).
  No rate limit — a serverless function keeps no counter — so the slow hash is the only brake.
  `tests/conftest.py` blanks `APP_PASSWORD_HASH`/`SESSION_SECRET` so a developer's `.env` cannot
  gate the page tests.
- **The central database** (`core/db.py`, roadmap D4, 24 Sept 2026): `DATABASE_URL` (or
  `POSTGRES_URL`, what the Vercel Marketplace's Neon integration sets) moves four things into
  one Postgres, from every device and user: the usage ledger (`llm_usage`, so
  `LLM_DAILY_TOKEN_BUDGET` is enforceable in production), the answer cache
  (`classifications`, shared by every instance), the audit events (`audit_events`: lookups
  and reports, identifier/user/outcome, **never content**) and the error reports
  (`error_reports`). Same protocols as the SQLite and file stores, chosen in `build_ledger`
  / `build_cache` / `build_report_store` (`REPORTS_SOURCE=auto` → `db`), and `set_audit_sink()`
  at startup. **One SQL, two engines**: `?` placeholders and ISO-8601 text timestamps, Postgres
  gets `%s` and `BIGSERIAL`; the SQLite engine (`sqlite:///path`) runs the same code in the
  tests and on a laptop. A connection per operation (Neon's pooled URL), connect timeout 5 s,
  schema `CREATE TABLE IF NOT EXISTS` once per instance, no migration tool. Writes fail soft
  (a warning), reads are honest (`records()` raises). psycopg 3 with bundled libpq is a
  runtime dependency, imported only when the URL is set. **Verified live on 24 Sept 2026**
  against the Neon database `nace-esa-db` (eu-central-1, connected to the Vercel project):
  schema created, a lookup wrote `audit_events`, `llm_usage` and `classifications`, a report
  wrote `error_reports`, the report's re-run was served from the shared cache, and `--usage`
  / `core.reports --list` read them back. `describe()` never includes the password.
- **Error reports** (`core/reports.py`, `POST /report`, 24 Sept 2026): the result page has one
  button, "Nahlásit k prověření", with an optional note (`REPORTS_MAX_NOTE_CHARS`, 500). The
  lookup is **re-run like the download** (cached, so it is the result on screen) and the
  request, the output row (`json_row(suggestion_row())`), the lookup's notes, the user, the
  time and the app facts (codebook version, model, prompt version, commit) are stored as one
  JSON. `REPORTS_SOURCE`: `dir` (default; `REPORTS_DIR`, git-ignored, `<date>/<id>.json`,
  atomic, never overwritten), `blob` (the Vercel setting: the SDK's `put` over httpx, `PUT
  {REPORTS_BLOB_API_URL}/?pathname=`, `x-api-version 12`, `x-vercel-blob-access private`;
  read back in the Vercel dashboard, no list call), `off` (no button). A store failure is
  **said on the page**, never a 500; a `dir` store on Vercel gets a page warning because
  `/tmp` does not outlive the instance. **A report is content**: never a log line - the audit
  log gets `log_report()` (identifier, user, stored or not) only. Review: `python -m
  core.reports --list | --xlsx` (the database when `DATABASE_URL` is set).
- **The developer page** (`/admin`, `core/admin.py`, 24 Sept 2026): the whole cost ledger
  (every call, who it was charged to, priced at `PRICES`; totals by user, model, codebook and
  day; filters by user, model, codebook and date; click-to-sort) and every complaint (the
  error reports with the result row and the note; filters and free-text search), plus the
  two workbook downloads. **Its own password**: `ADMIN_PASSWORD_HASH` (same
  `--hash-password`), a separate cookie `nace_esa_admin` signed with `SESSION_SECRET`
  + `/admin`, scoped to `/admin`; unset = 404 and no link. When the MO sign-in is on it comes
  first, and the developer session carries the MO name. The link "Pro vývojáře" is in the
  top bar only when configured (`admin_enabled()`, a Jinja global). Reads only: no audit row
  for looking. Data comes from the same builders as everything else, so it shows the central
  database when `DATABASE_URL` is set and the local files otherwise; a Blob report store
  cannot list, so it shows no complaints.
- **Audit**: `request_user()` is the signed-in name when sign-in is on, and then
  `WEB_USER_HEADER` is ignored — a header a browser can send is not an identity. With sign-in
  off it reads `WEB_USER_HEADER` (default `X-Remote-User`) because in a server the OS account
  is the *service* account; **the proxy must strip any client-supplied copy.**
  `core/audit.py` logs identifier, timestamp, user, sources and outcome — **never retrieved
  content**, so the log can ship without carrying client data.
- **Spend per user**: `api._run` wraps the lookup in `budget.spending_as(user)`, a context
  variable `BudgetedProvider` reads when it records a call, so the pipeline carries no name.
  The ledger's `user` column was added in place; rows from before it are `unknown`, as is
  any call made outside `spending_as`. `--golden --model` is charged to the OS account.
  `--usage-xlsx` has a By user block and a User column. Production records it in the central
  database (D4) since 24 Sept 2026 (evening); before that, Vercel kept no ledger.
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
  README. The step 5 RES → CTS mapping, which left with Tool 2, is back for resident issuers
  (`res_esa.py`, `residents.py`, 2 Oct 2026).
