# Roadmap — ESA/NACE suggester (Tool 1) on Vercel

**Written 22 September 2026 for whoever continues this repository — human or model.** It holds
every decision, fact and open question needed to pick the work up cold. Read it after
`CLAUDE.md` (the living architecture notes) and before touching code. When an epic lands,
update its status here and the matching note in `CLAUDE.md`; when a question is answered,
write the answer inline in §7 with the date.

Reading order for a fresh session: `CLAUDE.md` → this file → the description of
[PR #1](https://github.com/tdzian39/nace-esa-classification-assistant/pull/1) → the epic you are
picking up. Then run the tests (§8) before changing anything.

---

## 1. Decisions log

| Date | Decision | By |
|---|---|---|
| 2026-09-22 | This repository is the **primary codebase of Tool 1** (the ESA/NACE suggester for foreign issuers). Jakub's earlier repo `jaeksrampota/esa-nace-naseptavac` (CodeNOW Flask scaffold + design docs) is the *design source*, not a parallel implementation any more. | Jakub |
| 2026-09-22 | **Tool 2 (RES/OR lookup) is out of scope here** — it is built elsewhere (`jaeksrampota/res-or-lookup`). Its code in this repo is parked and will be removed (E0). | Jakub |
| 2026-09-22 | **Deployment target is Vercel**, not CodeNOW. The old hard rule "no PaaS config, Dockerfile only" is withdrawn; the Dockerfile may stay for local runs. Design for serverless (§4). | Jakub |
| 2026-09-22 | **The LLM stays off** until an approved endpoint exists. Everything for it is built and tested against a stub; nothing more is done on it before E9. The tool ships *deterministic* (shortlist with CTS IDs, a human picks). | Jakub |
| 2026-09-22 | DWS access is not granted for this project (from `CLAUDE.md`); irrelevant to Tool 1, which never touched it. | bank |
| 2026-09-22 | Issuer identification by ISIN (GLEIF + OpenFIGI) added in PR #1; the pattern for every further source: public, keyless, fail-soft, trimmed live payloads as test fixtures. | Jakub / Claude |
| 2026-09-22 | **D2 — the repository stays public.** E0.2 (make it private) is dropped and E1 no longer waits for it. The rule "nothing bank-internal in git" stays, so the codebooks live only in a private Vercel Blob store — which settles **D3 as Blob** (the CSV option needed a private repo). | Jakub |
| 2026-09-22 | **D1 — there is a Vercel account.** Claude looks up its team and plan and confirms them with Jakub before creating the project. The plan sets `maxDuration` (Hobby 60 s, Pro 300 s). | Jakub |
| 2026-09-22 | **D5 / Q-A1 — no Entra ID app registration.** The E2.1 shared-password gate with a self-declared name becomes the permanent login. E2 keeps the untrusted-header rule and audit persistence; E2.2 (OIDC) and Q-A1 are dropped. | Jakub |
| 2026-09-22 | **D6 — no data-classification sign-off is needed**; E1 go-live is not gated on it. The §4 data-flow list stays as documentation. | Jakub |
| 2026-09-22 | **Q8 / E8 — the golden set is built without MO.** Claude builds ~30 real issuers from the E8 seed list, mixing banks, corporates, funds, governments, supranationals and financing vehicles, from ISINs and public sources. `verified_by` stays empty on every case until someone checks it against CTS; codes worked out this way stay provisional and no accuracy is quoted from them. | Jakub |

---

## 2. Context

### The brief (from `ESA-a-NACE-naseptavac-pro-MO.docx`, 17 Sept 2026)

When MO (Middle Office, treasury back office) sets up a **foreign securities issuer in CTS**, they
must pick a **NACE code** (CTS keeps only the two-digit division, codebook `CTS_OKEC_NACE2`) and an
**ESA 2010 institutional-sector code** (ČNB codebook `BA0036`), each stored in CTS as a *codebook
item ID*. Today that means googling the issuer and guessing; it costs time and produces wrong or
inconsistent records. Input: ISIN and/or issuer name and/or activity description (at least one).
Output: issuer name, activity description, suggested NACE + its CTS ID, suggested ESA + its CTS ID,
alternatives, evidence. Goals: **less MO time per issuer, better CTS data quality**. A suggester,
not an oracle: **a human confirms every code**.

### Repositories and people

| What | Where | Notes |
|---|---|---|
| **Primary code (this repo)** | `github.com/tdzian39/nace-esa-classification-assistant` | **PUBLIC, and it stays public** (D2, decided 22 Sept 2026): nothing bank-internal is ever committed. Branch `main`, no branch protection. Owner `tdzian39` ("floral_lobster"); Jakub (`jaeksrampota`) is a collaborator with push access. |
| Design source | `github.com/jaeksrampota/esa-nace-naseptavac` (private) | `docs/design.md` (3-layer design, rule table §5.1), `docs/api-details.md` + `docs/egress-endpoints.md` (verified API facts for a security review), `docs/questions.md` (Q1–Q12), `data/reference/*.csv` (public NACE/ESA/BA0036 codelists, English NACE labels), `src/main/probe.py` (diagnostics page design), `src/main/isin.py` (`COUNTRY_CS` map of ISIN prefixes). |
| Name matcher to port | `jaeksrampota/lei-lookup-tool` (private; `jak-rb/lei-lookup-tool` public copy) | `core/matcher.py` (name scoring, thresholds), `clients/gleif.py`, `clients/openfigi.py`, `services/isin.py`, batch xlsx helpers, `translations/cs.json`. FastAPI/async — port the logic, not the files. |
| Tool 2 lives here | `jaeksrampota/res-or-lookup` | RES/OR lookup for monthly client corrections. Not our concern. |
| Codebooks (xlsx) | **not in any repo**; tdzian39 has them locally; a pending GitHub invite for Jakub to `tdzian39/rb_files` (private, write) is probably where they are shared | Never committed. At runtime they live only in a private Vercel Blob store (D3). |
| Users | MO treasury (a handful of people), Czech-speaking; UI is Czech | Confirmers must be identifiable (audit). |

### PRs so far

- **PR #1** `feat/isin-issuer-identity-gleif-openfigi` — ISIN → GLEIF → OpenFIGI identification;
  commits `4908511`, `89a5202`; 1052 tests pass. **Merged to `main` 22 Sept 2026 19:03 UTC** with
  the owner's agreement (communicated to Jakub directly, not on GitHub).
- **PR #3** this document + the scope edits (`38fa467`). **Merged 22 Sept 2026 19:04 UTC.** (PR #2
  was the same content stacked on #1's branch; GitHub *closed* it when that branch was deleted on
  merge instead of retargeting it — lesson: merge a stacked PR's base without `--delete-branch`, or
  open the follow-up against `main` from the start.)
- **PR #4** `docs/roadmap-status-2026-09-22` — this section once #1 and #3 had merged, plus the E0.3
  removal map (`5d5302d`). **Merged 22 Sept 2026 19:06 UTC.**
- `main` after all three: `7f75af3`.
- **PR #5** `chore/remove-parked-tool2` — "Remove the parked Tool 2" (E0.3, below; 22 Sept 2026). When
  it merges, add the merge time here and the new `main` hash to the line above, as for #1, #3 and #4.

### Where the next session starts: E1

E0.3 is done in PR #5. It deleted the twelve Tool 2 files — `core/sources/{dws,ares,resolver,__main__}.py`
and `core/batch/{runner,__main__}.py` (1 829 lines) and their six test modules (1 270 lines) — cut
`core/sources/base.py` down to `Source` (now `WEB`/`GLEIF`/`OPENFIGI`), `Provenance` and the `Source*Error`
classes and `core/export/columns.py` down to the suggestion row, and dropped the `DWS_*`/`ARES_*` settings
and `pandas` (numpy, which only pandas pulled in, is now a dev extra: the identifier and codebook tests
feed it to the normalisers). Measured with `git diff --stat` over everything but the three docs (this file,
`CLAUDE.md`, `app/README.md`) and counting the new `tests/export/conftest.py`: 52 files, +848 / −4 467
lines; the suite went from 1 052 to 963 passed (16 skipped before and after). The earlier estimate of
2 088 + 1 669 lines also counted `core/identifiers/ico.py` and its tests, which stay for the batch reader
until E6 (D7). Measured on `main` before the removal, the owner's (`tdzian39`) Tool 1 code that stays is
7 750 lines and his tests that stay 5 889 (PR #1 added 2 029): roughly four fifths of the original work
stays, and all of Tool 1 is his.

**E1** (§5) puts the deterministic mode on a Vercel preview. D2 and D3 are settled (the repository stays
public; the codebooks go to a private Blob store), so nothing blocks the code. Creating the Vercel project
waits for Jakub's confirmation of the team and plan (D1), and a preview with real data waits for the four
codebook files, which are with the repository owner (perhaps in `tdzian39/rb_files`); §10 lists what to
verify about the Python runtime. Start from a fresh clone and a
fresh venv (`pip install -e ".[dev]"`, or `tests/api` will not collect — §3 gotchas). Write E1's
`requirements.txt` from the runtime list in E1 item 1, not from `pip freeze`: a venv from before PR #5
still carries `pandas`, and every dev venv carries `numpy`, `pytest` and `ruff`.

---

## 3. What exists today (Tool 1 only, as of PR #5, 22 Sept 2026)

### Stack and layout

Python ≥ 3.12 (dev on 3.13; keep 3.12-compatible), FastAPI + Jinja2 + htmx (no JS framework),
pydantic-settings, httpx, openpyxl, ruff, pytest. Everything under `app/`:

```
app/
  api/main.py            FastAPI: GET / · POST /suggest · POST /api/suggest · GET /suggest.xlsx · GET /health
  ui/templates/suggest.html   (generated from ui/prototype/suggest.html — keep the prototype in step)
  config/settings.py     pydantic-settings; relative paths resolve against app/
  core/
    suggest.py           the pipeline: request → identity → evidence → shortlist → classifier → IssuerSuggestion
    identifiers/         isin.py (ISO 6166 + Luhn) · ico.py (IČO, only for batch/reader.py until E6)
    codebooks/           xlsx reader, loaders, models (CodebookSet), normalize, consistency check, versioning
    sources/             base.py (Source literal, Provenance, Source*Error) · gleif.py · openfigi.py · identity.py · web.py
    classify/            candidates.py (pre-filter) · hints.py (keyword table + ESA family grid) · text.py (IDF)
                         prompts.py · provider.py · llm.py · cache.py · budget.py · golden.py (LLM path, off)
    export/              columns.py (the suggestion row contract) · xlsx.py (Subjects + Run sheets)
    batch/               reader.py (messy xlsx in — E6 reuses it; nothing calls it yet)
    audit.py             one log line per lookup: identifier, time, user, sources, outcome — never content
  tests/                 963 passed / 16 skipped (skips = tests needing the real xlsx); fixtures are trimmed live payloads
```

### The pipeline, concretely

1. `SuggestionRequest.cleaned()` — trims, validates the ISIN (a bad ISIN becomes a note and is never sent anywhere).
2. `IssuerIdentifier.identify(isin)` — GLEIF `lei-records?filter[isin]=` → `LeiRecord` (legal name, country,
   legal form ELF, entity category, sub-category, status, direct/ultimate parent or the reporting-exception
   reason); OpenFIGI `POST /v3/mapping` → `FigiInstrument` (market name, security type, market sector).
   Result `IssuerIdentity` with `fact_sheet()` (Czech one-liners whose parentheses carry English hint words),
   citable record pages, sources that answered, notes for what is missing.
3. `WebEvidenceGatherer.gather(name=typed or legal name, isin, description)` — a typed description wins and
   skips the web; otherwise a pluggable `SearchProvider` (HTTP JSON, Brave-shaped defaults, **none configured**)
   + stdlib HTML text extraction. `apl.czso.cz` / `or.justice.cz` are refused in code.
4. `classifier_text = description + "\n\n" + fact_sheet` → `NaceCandidateFilter` / `EsaCandidateFilter`
   (`limit=12`): keyword hints (`hints.py`, cs+en, +10 score) plus IDF-weighted lexical overlap against the
   codebook texts; ESA is treated as a **grid of family × control** derived from the codebook names; the
   residual family *nefinanční podniky* always gets reserved slots; every candidate already carries its CTS ID.
5. `LlmClassifier.classify_both()` — with `NullLlmProvider` it **abstains** with a readable reason; the
   shortlist is the result. With a model: JSON schema pins `code` to the offered codes, answers are re-checked,
   suggestions are built from candidates (CTS ID cannot be invented), cache keyed on everything that changes the
   answer, spending limits fail closed.
6. Page / JSON (`identity` object included) / xlsx (`SUGGESTION_COLUMNS` incl. `issuer_lei`, `issuer_country`,
   `source` = the registers that answered + `WEB`, e.g. `GLEIF+OPENFIGI+WEB`, `OPENFIGI+WEB`, or `WEB` for a
   name) / audit line.

### Data facts you would otherwise have to rediscover

- **Codebooks** (real files received 22 Sept 2026, version `cb-3b12e64837840ca0`, load with 0 errors):
  `CTS_BA0036_NEW.xlsx` headers `.ID`, `Popis`, `Hodnota`, 110 rows, one non-leaf placeholder `0000000`;
  `BA0036_2024_jen_validni.xlsx` 109 valid leaves (+3 ignored columns `pritomnost v …`);
  `CTS_OKEC_NACE2.xlsx` sheet `Sheet1`, **87 divisions**, CTS IDs 455–542; `NACE_STAT.xlsx` sheet
  `KLAS80143_CS`, 969 rows, `uroven` 2/3/4 = division/group/class, median 9 rows per division, max 56 (oddíl 46).
- **BA0036 codes are 7-digit ČNB codes**, not `S.122`: first digit residency (1 rezident, 2 nerezident), then
  sector/type, last digit control (1 veřejné, 2 soukromé národní, 3 pod zahraniční kontrolou). Examples used in
  tests: `2002703` kaptivní finanční instituce pod zahraniční kontrolou · `2002213` banky pod zahraniční
  kontrolou (`2002212` soukromé národní, `2002211` veřejné) · `2001003` nefinanční podniky pod zahraniční
  kontrolou · `2002803` pojišťovny · `2002513` sekuritizace · `2002533` úvěrové instituce · `2002403` investiční
  fondy jiné než FPT · `2002303` fondy peněžního trhu · `2009031` mezinárodní rozvojové banky; resident block
  `1221300`, `1100300`. Foreign issuers → the `2…` block (`EsaCandidateFilter(resident=False)`).
- **The S.12203 problem**: RES/ARES report ESA in the `S.xxxxx` form (`12203` for Raiffeisenbank); CTS splits
  S.1220x into banks (1221x) / credit unions (1222x) / other deposit-takers (1224x), so a RES sector is **not** a
  lexical lookup into BA0036. Irrelevant for foreign issuers, relevant if anyone ever maps register sectors.
- **NACE revision question (open, Q5)**: NACE Rev. 2 has 88 divisions, CZ-NACE 2025 (= Rev. 2.1) has 87 —
  division 45 disappears. `CTS_OKEC_NACE2` has 87 divisions (88 CTS IDs). *Whoever has the file: does it contain
  division 45?* Yes → Rev. 2 (then which division is missing?); no → CTS is already on CZ-NACE 2025.
- **Hint family keys** (folded codebook names; `hints.py`): `banky`, `pojistovaci spolecnosti (ic)`,
  `penzijni fondy (pf)`, `investicni fondy jine nez fondy penezniho trhu`, `fondy penezniho trhu`,
  `ucelove financni instituce pro sekuritizaci aktiv`, `kaptivni financni instituce a pujcovatele penez`,
  `obchodnici s cennymi papiry a derivaty`, `financni instituce poskytujici uvery`, `ustredni vladni instituce`,
  `narodni vladni instituce`, `mistni vladni instituce`, `mezinarodni rozvojove banky`,
  `ostatni mezinarodni instituce`; baseline `nefinancni podniky`.
- **Measured on the (provisional, fictional) golden cases**: deterministic top pick right 90 % for NACE, 60 %
  for ESA. **Do not quote these** — no case has `verified_by` set; §E8 fixes that.
- **Prompt budget** (real codebooks, ~4 chars/token): all 87 NACE short labels ≈ 895 tokens; every label of every
  division ≈ 10 400; the full texts of a 12-division shortlist ≈ 1 440 → two-stage design is justified.

### Verified external sources (live, 22 Sept 2026)

| Source | Endpoint | Gives | Limit | Note |
|---|---|---|---|---|
| GLEIF | `GET https://api.gleif.org/api/v1/lei-records?filter[isin]=…&page[size]=1`; `/lei-records/{lei}`; `/lei-records/{lei}/direct-parent`, `/ultimate-parent` (404 = none), `/direct-parent-reporting-exception`; `/lei-records/{lei}/isins` | legal name, addresses, jurisdiction, category, subCategory, legalForm.id/other, entity status, registration status, parents | 60/min published | no ISIN mapping for `IE00B4L5Y983` (iShares Core MSCI World) — coverage is wide, not complete |
| OpenFIGI | `POST https://api.openfigi.com/v3/mapping` `[{"idType":"ID_ISIN","idValue":…}]`; key header `X-OPENFIGI-APIKEY` | name, ticker, securityType/2, marketSector, exchCode; `{"warning":"No identifier found."}` / `{"error":"Invalid idValue format."}` | keyless `ratelimit-policy: 25;w=60` (read from live headers); 250/min with a free key | knows `FR0129895324`? no (BMW Finance bond); knows the iShares ETF: yes |
| ESMA FIRDS | `GET https://registers.esma.europa.eu/solr/esma_registers_firds/select?q=isin:…&wt=json&rows=1` | instrument full name, **CFI code**, issuer LEI, currency, venue | unpublished — be gentle | verified 17 Sept from Jakub's laptop; not yet used here (E5) |
| Wikidata / Wikipedia | `https://www.wikidata.org/w/api.php?action=query&list=search&srsearch=haswbstatement:P1278=<LEI>`; `Special:EntityData/Qxx.json`; `https://{cs,en}.wikipedia.org/api/rest_v1/page/summary/<title>` | item by LEI (P1278); P452 industry → **P4496 NACE Rev. 2 code**; P1454 legal form; sitelinks → summaries cs/en | 200/min with a descriptive `User-Agent`, 10/min without | 53 364 items with a LEI, 2 432 with an industry carrying a NACE code; supranationals thin (EIB not found); not yet used (E5) |
| ECB lists (MFI, IF, FVC, IC, PF) | downloads on ecb.europa.eu | IF/FVC/IC/PF carry a **LEI** column (78 000 / 5 700 / 2 200 / 5 400 rows); MFI list (≈ 6 000) has no LEI | files, monthly/daily | check reuse terms before bundling (E4b) |

Sample ISINs for tests and demos: `DE0005140008` Deutsche Bank AG (GENERAL, no parent, `NO_KNOWN_PERSON`) ·
`FR0129895324` BMW Finance N.V. (NL, parent Bayerische Motoren Werke AG in DE — the captive case) ·
`DE000A351PG2` Land Berlin (`RESIDENT_GOVERNMENT_ENTITY`/`STATE_GOVERNMENT`, OpenFIGI `Govt`) ·
`IE00B4L5Y983` iShares Core MSCI World (OpenFIGI only, `ETP`/`Mutual Fund`) · LEIs: EIB `5493006YXS1U5GIHE750`
(`INTERNATIONAL_ORGANIZATION`, jurisdiction `EU`), BMW AG `YEH5ZCD6E441RHVHD759`.

### Known gaps and gotchas

- A **name-only** lookup never touches a register (E3). An ISIN with no web provider yields register facts only —
  that is the intended deterministic pilot mode.
- The web search provider is **not configured** and is a procurement question; Wikipedia summaries (E5) give a
  free description for well-known issuers and may make a paid provider unnecessary for the pilot.
- `pandas` is gone (PR #5). `numpy` stays a **dev** extra only: the identifier and codebook tests feed numpy scalars
  (`np.int64`, a NaN `np.float64`) to the normalisers. Keep both out of the runtime dependencies.
- `py -m pytest` from `app/` **without** `pip install -e .` fails to collect `tests/api` (the `tests/api` package
  shadows the `api` package under pytest's prepend import mode). The documented editable install works.
- `SqliteCache` defines `__len__`, so an empty cache is falsy — use `is None` checks (already fixed once).
- Audit relies on `WEB_USER_HEADER` (`X-Remote-User`) set by a trusted reverse proxy. **On Vercel there is no such
  proxy and the header is client-controlled** — it must be disabled there (E2).
- One known failing test remains in the *design source* repo (diacritics search) — irrelevant here.

---

## 4. Target architecture on Vercel

```
 browser (MO, Czech UI, htmx)
    │  HTTPS, Vercel edge
    ▼
 Vercel Serverless Function  app/api/index.py  →  FastAPI ASGI app (api.main:app)
    │  cold start: load codebooks (Blob → /tmp → CodebookSet, consistency check, version id)
    │  per request: identity (GLEIF, OpenFIGI, later FIRDS/Wikidata/Wikipedia) → evidence → shortlist → page/JSON/xlsx
    ├──► api.gleif.org · api.openfigi.com · registers.esma.europa.eu · wikidata.org · wikipedia.org   (outbound, no whitelist needed)
    ├──► Vercel Blob (private): the four codebooks                                            (D3)
    ├──► Postgres (Neon via Vercel Marketplace): audit_events, confirmed_mappings, later llm_cache/usage  (D4, E2/E7/E9)
    (no identity provider: the app's own shared-password gate says who is asking — self-declared name, D5, E2)
```

**Constraints that shape the design** (verify the plan-dependent numbers when implementing — §10):

| Vercel fact | Consequence |
|---|---|
| Python functions are stateless; **no writable disk except `/tmp`** (ephemeral, per instance) | codebooks are fetched at cold start, SQLite caches/ledgers are impossible → in-memory per instance now, Postgres/KV later; `tempfile` for the xlsx download still works |
| FastAPI `lifespan` may not run under Vercel's ASGI handler | load codebooks **lazily** (`functools.lru_cache`-ed `get_service()`), keep `/health` dependency-free |
| Function duration is capped (default ~10–15 s; up to 60 s on Hobby, 300 s on Pro; set `maxDuration` explicitly) | one ISIN = up to 4 GLEIF + 1 OpenFIGI requests, spaced 1.0 s / 2.5 s → ~3–6 s live; cap per-request timeouts so the worst case fits (8 s, 2 attempts); **batch must be chunked** (E6) |
| Request body limit ~4.5 MB | fine for a sheet of ISINs; large sheets are chunked client-side anyway |
| Cold starts scale with bundle size | no `pandas` (dropped in PR #5); keep `openpyxl`; `.vercelignore` tests, prototype, data |
| No reverse proxy, no bank SSO in front | the app must authenticate users itself (E2); `X-Remote-User` is untrusted |
| Runtime logs are kept briefly; Log Drains are a paid feature | audit events go to Postgres (E2) |
| Outbound internet is open | **no egress/whitelist request** (the biggest simplification vs CodeNOW); the third-party data flows are documented below (D6: no sign-off needed) |
| Env vars ≤ 64 KB total | codebooks cannot travel as env vars → private Blob store (D3) |
| Preview deployment per PR | free review of every change; previews are protected by Vercel Authentication (team login), production by E2 |
| Concurrency: each instance throttles on its own | per-instance throttle is enough at MO's volume (a few lookups a day); a shared limiter (KV) only if volume grows |

**Data that leaves the bank** (documentation; D6 decided 22 Sept 2026 that no data-classification sign-off is
needed and go-live is not gated on one): ISINs, issuer names and typed descriptions
(public issuer data; a typed description is MO's own text) → third-party registers and Vercel; the CTS codebooks
(internal codelist IDs and Czech labels) → Vercel Blob/DB; user identities and lookup history → Postgres.
No client data, nothing from DWS, ever.

---

## 5. Epics

Sizes: **S** ≈ half a session, **M** ≈ one session, **L** ≈ two. Order and dependencies in §5.12. Every epic ends
the way this repo has always worked: tests green, `CLAUDE.md` + this file updated, a PR with a description that
explains *why*, then wait for a go-ahead.

### E0 — Scope and housekeeping (S) — *done: docs in PR #3, E0.3 in PR #5; E0.2 dropped (D2)*

**Goal:** the repo says what it is: Tool 1, Vercel, LLM later; nothing bank-internal can leak.
**Work:**
1. `CLAUDE.md` / `README.md` scope, deployment rule, next step → this document (done, PR #3).
2. ~~Make the repository private~~ — **dropped 22 Sept 2026 (D2): the repository stays public.** What E0.2
   protected is kept by rule instead: nothing bank-internal is ever committed (no codebook, CSV, screenshot or
   real lookup), and the codebooks live only in a private Vercel Blob store (D3).
3. Remove the parked Tool 2 in its own PR ("Remove the parked Tool 2"): `core/sources/{dws,ares,resolver}.py`,
   `core/sources/__main__.py`, `core/batch/runner.py`, `core/batch/__main__.py`, `core/identifiers/ico.py`,
   `SUBJECT_COLUMNS`/`record_row` in `core/export/columns.py`, `DWS_*`/`ARES_*` settings and `.env.example`
   lines, their tests and README/CLAUDE sections. **Keep** `core/batch/reader.py` (E6 reuses it),
   `core/export/xlsx.py`, `core/audit.py`, `core/identifiers/isin.py`. Drop `pandas` from `pyproject.toml`.
   Expect roughly −4 000 lines; the suite must stay green. *Done in PR #5 (−4 467 / +848 lines, 963 passed);
   `core/identifiers/ico.py` stayed, because the reader needs it until E6 (D7).*
4. Port `docs/questions.md` from the design source as the answer slots in §7 here (done in this file).
**DoD:** `python -m pytest` green with Tool 2 gone; no `pandas`; README/CLAUDE describe one tool; nothing
bank-internal in the repository.

### E1 — Deploy the deterministic mode on Vercel (M)

**Goal:** a preview URL where an ISIN returns the identity facts and the two shortlists with CTS IDs.
**Work:**
1. Entry and config in `app/`:
   ```
   api/index.py     from api.main import app          # Vercel serves this ASGI app
   vercel.json      {"rewrites":[{"source":"/(.*)","destination":"/api/index"}],
                     "functions":{"api/index.py":{"maxDuration":60}}}
   requirements.txt pinned export of the runtime deps (fastapi, pydantic, pydantic-settings, jinja2,
                    python-multipart, httpx, openpyxl — no pandas, no uvicorn needed in prod)
   .vercelignore    tests/  ui/prototype/  data/  .venv/  *.sqlite3
   ```
   Vercel project: Root Directory `app`, framework "Other", Python 3.12 (pin the way the docs prescribe — §10).
   The existing `api/main.py` also becomes a function under `/api/main`; harmless, but everything is rewritten
   to `/api/index`.
2. **Lazy startup**: replace the `_state` dict filled in `lifespan` with a cached `get_service()` that loads and
   checks the codebooks on first use; keep `lifespan` for local `uvicorn`; `/health` reports `status`,
   `codebook_version` (only if already loaded — it must not force a load), `python`, `region`, `cold` flag.
   An inconsistent codebook set must still refuse to serve suggestions (HTTP 503 with the report summary).
3. **Codebooks from Vercel Blob** (`CODEBOOK_SOURCE=blob|dir`, default `dir` locally): at cold start download the
   four xlsx from a private Blob store (token `BLOB_READ_WRITE_TOKEN`, keys `codebooks/<file>.xlsx`) into
   `/tmp/codebooks/` and hand the existing loaders the paths — loaders, consistency check and version id stay
   untouched. Document the upload procedure (E10 runbook). Blob is the only delivery (D3, 22 Sept 2026): the
   repository stays public, so the CSV-in-git alternative is gone.
4. **`/probe` diagnostics page** (port of the design source's `src/main/probe.py`): one harmless request per
   register (`api.gleif.org`, `api.openfigi.com`; `?set=all` adds FIRDS, Wikidata, Wikipedia), 5 s timeout,
   status per host (`ok`, `timeout`, `tls`, `dns`, `blocked`, `http_error`, `unexpected_body`), runtime facts
   (Python, region, cold/warm, codebook version, library versions). Never called by `/health`. Behind the E2 gate.
5. Timeouts inside the cap: `GLEIF_TIMEOUT_SECONDS=8`, `OPENFIGI_TIMEOUT_SECONDS=8`, attempts 2 on Vercel (env),
   so the worst case (4 + 1 requests) stays under `maxDuration`.
6. Vercel env: `LLM_ENABLED=false`, `LLM_CACHE_PATH=` and `LLM_USAGE_PATH=` empty, `WEB_USER_HEADER=` empty,
   `CODEBOOK_SOURCE=blob`, `WEB_USER_AGENT` naming the bank tool.
**DoD:** preview deployment renders `DE0005140008` with LEI, facts, both shortlists and CTS IDs; `/health` and
`/probe` answer; cold-start time measured and written here; the Dockerfile still builds for local use.
**Depends on:** no epic (E0.2 was dropped, D2). Before the Vercel project is created: Jakub's confirmation of
the team and plan (D1). Before a preview with real data: the four codebook files from the repository owner.

### E2 — Access and audit on Vercel (M)

**Goal:** only MO can open it, and every lookup is attributed to a named person (self-declared, D5) and kept.
**Work:**
1. **The login — permanent (D5, 22 Sept 2026):** middleware requiring a shared secret `APP_ACCESS_PASSWORD`
   (env), Czech login page, signed session cookie (Starlette `itsdangerous`), a *self-declared* display name
   stored in the cookie and written to the audit as `jmeno (self-declared)`. `/health` exempt. Honest and cheap;
   not an identity, and the audit says so. Rotating the password is the way to revoke access.
2. ~~OIDC with Microsoft Entra ID~~ — **dropped 22 Sept 2026 (D5):** there will be no Entra ID app
   registration (Q-A1 dropped with it), so item 1 is the login for good.
3. **Untrusted header off:** `WEB_USER_HEADER` is only honoured when `TRUST_PROXY_USER_HEADER=true`; default
   false; Vercel env leaves it false. A header a browser can set is not an identity.
4. **Audit persistence:** `core/audit.py` gains a sink; `audit_events(id, at, user, identifier, isin, outcome,
   sources, detail)` in Postgres (D4); the log line stays. Retention: decide with MO (Q-A2); no retrieved content
   is ever stored, as today.
**DoD:** unauthenticated request → login; audit rows appear in Postgres for page, JSON and download; tests cover
the gate and the header rule.

### E3 — Name → issuer (M)

**Goal:** typing "Deutsche Bank" resolves to the legal entity the same way an ISIN does.
**Work:**
1. `GleifSource.search_by_name(name, limit=10)` — `GET /lei-records?filter[fulltext]=<name>&page[size]=10`
   (verify the filter name against the live API; `filter[entity.legalName]` is the exact-match fallback).
2. Port the matcher from `lei-lookup-tool/core/matcher.py`: casefold + strip diacritics, strip legal-form tokens
   (AG, SE, N.V., B.V., S.A., S.p.A., PLC, Ltd, Inc, GmbH, a.s.), token overlap with prefix bonus, country boost
   when the user gives one, thresholds → `high / medium / ambiguous`.
3. `SuggestionRequest.lei` (new optional field) and `IssuerIdentifier.identify(isin=None, lei=…)` →
   `GleifSource.fetch(lei)`.
4. UI: a name-only lookup with no confident match shows up to 5 candidates (name, LEI, country, category,
   status); clicking re-runs with `lei=`. A confident match proceeds directly and says so in the notes.
**DoD:** recorded payloads for "Deutsche Bank", "BMW Finance", an ambiguous name; tests for the matcher; the
page flow works in a preview.

### E4 — Structured hints and the rule table (M)

**Goal:** the deterministic shortlist uses the register facts as *data*, not as words in a sentence.
**Work:**
1. `StructuredHints` (category, sub_category, legal_form_id, country, parent_country, parent_abroad,
   market_sector, security_type, cfi later) built from `IssuerIdentity`;
   `CandidateFilter.shortlist(text, *, limit, structured=None)`.
2. Rule table from the design source `design.md` §5.1, as data: `RESIDENT_GOVERNMENT_ENTITY` + sub-category →
   S.1311/1312/1313/1314 families + NACE 84; `INTERNATIONAL_ORGANIZATION` → `mezinarodni rozvojove banky` /
   `ostatni mezinarodni instituce` + NACE 99; `FUND` → both fund families (MMF vs non-MMF stays open) + 64;
   OpenFIGI `Govt` → government families, `Mtge` → securitisation, `Muni` → local government; name tokens
   (Bank/Banque/Banca/Sparkasse/Landesbank → `banky`; Insurance/Assurance/Versicherung/Re → `pojistovaci`;
   Leasing/Credit/Factoring → `financni instituce poskytujici uvery`; Finance/Funding/Capital + N.V./B.V./S.A.
   with a non-financial parent → `kaptivni`); **control ordering**: `parent_abroad=True` → foreign-controlled
   variant first, government parent → public first. Each rule writes an evidence string on the candidate
   (`"GLEIF category FUND → investiční fondy"`). Keep the prose glosses too — harmless and readable.
3. Optional **E4b — ECB lists offline**: build `ecb_lei_sector.csv` (LEI → S.12x type, list date, ≈ 90 000 rows,
   IF/FVC/IC/PF; MFI by normalised name + country) refreshed monthly; a LEI hit is decisive for banks, funds,
   insurers and FVCs. Store in Blob; confirm ECB reuse terms first.
**DoD:** unit tests per rule; recall@12 on the real golden set (E8) not below the pre-E4 value; evidence strings
visible on the page.

### E5 — More sources: Wikidata/Wikipedia and FIRDS (M)

**Goal:** a description without a paid search provider, and a structured NACE path for well-known issuers.
**Work:**
1. `core/sources/wikidata.py`: LEI → item (`haswbstatement:P1278=`), `P452` industry → `P4496` NACE code →
   division candidates with evidence ("Wikidata: automotive industry → NACE 29"); description (cs/en) and
   sitelinks → `wikipedia.py` REST summary (cs first, en fallback) as the evidence description when nothing
   was typed and no provider is configured. Descriptive `User-Agent` (Wikimedia requires it; 200/min).
2. `core/sources/firds.py`: ISIN → CFI code, issuer LEI (fallback when GLEIF has no mapping), instrument full
   name, currency. CFI → structured hints: `DA…`/`DG…` asset/mortgage-backed → securitisation; `C…` collective
   investment → funds; `DN…` municipal → local government; `E…` equity of the issuer itself.
3. Register order in `IssuerIdentifier`: GLEIF → FIRDS (if no LEI yet) → OpenFIGI → Wikidata → Wikipedia;
   all fail-soft, all cited, `/probe?set=all` covers the hosts.
**DoD:** Volkswagen AG's ISIN yields a NACE 29 candidate with Wikidata evidence; Deutsche Bank gets a Czech or
English summary as description; recorded fixtures for all three sources.

### E6 — Tool 1 batch, chunked and stateless (M–L)

**Goal:** the one-off CTS cleanup and the daily "ten issuers at once": upload a sheet, get it back enriched with
suggestions, CTS IDs and a *liší se od CTS* flag.
**Design (serverless):**
```
POST /batch/parse      xlsx/csv in (≤ 4.5 MB) → JSON rows out (ISIN, název, popis, current NACE CTS ID,
                       current ESA CTS ID — reader.py generalised with these header aliases); nothing stored
POST /batch/rows       ≤ 5 rows in → suggestion rows out (each row = one pipeline run, ≈ 5 s worst case);
                       the browser (htmx) posts chunks sequentially and appends the rendered rows to the page
POST /batch.xlsx       the accumulated JSON rows in → workbook out (SUGGESTION_COLUMNS + IN_* echo +
                       NACE_differs_from_cts / ESA_differs_from_cts) via export/xlsx.py; Run sheet as today
```
No job storage, no background workers, no session — the browser is the state, which suits a few dozen rows.
Misses stay in the sheet as rows with the reason. One audit event per row. Rate limits hold because chunks are
sequential (GLEIF 60/min → 5 rows ≈ 25 s worst case).
**DoD:** a 50-row sheet round-trips in a preview; differs-flags correct against a sheet that carries current
CTS IDs; the Run sheet names user, codebook version and sources.
**Depends on:** E1, E2 (identity of the uploader).

### E7 — Confirm and history: the memory (M)

**Goal:** the same issuer is always coded the same way, and MO's decisions are kept.
**Work:**
1. Postgres `confirmed_mappings(id, lei, isin, normalized_name, nace_code, nace_cts_id, esa_code, esa_cts_id,
   confirmed_by, confirmed_at, note, codebook_version)`.
2. Pipeline step 0: a hit by LEI (else by normalised name) becomes the top suggestion with confidence *high*,
   evidence "potvrzeno <kdo> <kdy>", alternatives still shown.
3. `POST /confirm` from the result page (both codes + optional note); `GET /history` (Historie) with filter and
   CSV export; the batch flags "liší se od potvrzeného" as well.
4. Periodic re-check parking-lot item: flag confirmed issuers whose GLEIF status changed (merged, retired).
**DoD:** confirm → next lookup of the same LEI returns it first; history exports; tests with a fake repository.
**Depends on:** E2 (who confirmed), D4.

### E8 — Golden set and measurement (S, continuous from the first pilot)

**Goal:** a measurement on real issuers that is honest about what it is: provisional until someone checks the
codes against CTS.
**Work (decided 22 Sept 2026, Q8 — MO is not asked):** Claude builds ~30 real foreign issuers, starting from the
seed list below and mixing banks, corporates, funds, governments, supranationals and financing vehicles. Each
case carries a real ISIN (checked against GLEIF, OpenFIGI and FIRDS), the LEI, a short activity description
written from public sources, and a NACE division and a 7-digit BA0036 code with the reasoning and the sources
behind them; the control axis is stated as an assumption until Q7 is answered. **`verified_by` stays empty on
every case until someone checks it against CTS** — codes worked out this way are provisional and no accuracy is
quoted from them. The ten fictional trap cases stay, marked as fictional, because each pins a known pre-filter
failure. Add an `isin` field and extend `golden.py` so `python -m core.classify --golden` runs identity through
recorded fixtures (captured once, dated) and reports **recall@12** and the **deterministic top-1** per codebook,
verified and provisional separately (the run needs the real codebooks). Seed list from the design source:
Deutsche Bank AG, Volkswagen AG, EIB, Land Berlin, BMW Finance N.V., Volkswagen International Finance N.V.,
Toyota Motor Credit Corporation, Amundi Funds, iShares Core MSCI World UCITS ETF, Allianz SE, Nordea Kredit
Realkreditaktieselskab, Unilever PLC.
**DoD:** ~30 real cases with ISIN, sources and reasoning, all provisional; recorded identity fixtures; the runner
reports recall@12 and top-1 labelled *provisional* wherever the numbers appear; no accuracy claim anywhere
without `verified_by`; a case turns verified only when someone has checked it against CTS.

### E9 — LLM second opinion (M) — *deferred until an approved endpoint exists*

What exists: `prompts.py` (versioned, register facts already flow into the prompt via `classifier_text`),
`provider.py` (OpenAI Chat Completions over httpx, structured output, 429/5xx retry), `llm.py` (abstain, not
guess), `cache.py`, `budget.py` (fail-closed limits), `--estimate`/`--usage` CLI. Never made a live call.
**When approved:** (1) the gateway: if OpenAI-compatible, `LLM_BASE_URL` + key; if Azure OpenAI, a small adapter
(`api-key` header, `api-version` query, deployment name in the path); (2) cache and usage ledger → Postgres or
KV (SQLite is impossible on Vercel; the daily budget cannot be enforced without a ledger and then **refuses to
spend**); (3) run the golden set, compare with deterministic top-1, keep the cheaper model that passes;
(4) governance note: what is sent (public issuer data, register facts, codebook labels — never DWS, never
client data), which endpoint, what is logged. Show the model as a labelled second opinion with its one-sentence
justification; keep the shortlist visible.

### E10 — Hardening and handover (S–M)

Czech user guide for MO (one page: what to paste, how to read confidence and evidence, what to do when the tool
abstains); runbook (codebook update: upload to Blob → new version id → smoke test; register outage → `/probe`;
rate-limit hits; rotating secrets; Vercel env); monitoring (Vercel alerts on 5xx, a weekly `/probe`);
retrospective against the brief's goals — minutes per issuer before/after, share of suggestions accepted
unchanged, corrections in CTS after setup, same LEI → same codes across CTS.

### 5.12 Order and dependencies

```
E0 ──► E1 ──┬──► E2 ──► E6 ──► E7
            ├──► E3
            ├──► E4 ──► E4b (optional)
            └──► E5
E8 runs alongside from the first pilot; E9 only after E8 has numbers and an endpoint is approved; E10 last.
```
E0 + E1 give MO a usable pilot (shortlist with CTS IDs, deterministic). E2 makes it safe to hand out.
E3–E5 raise deterministic accuracy and coverage. E6–E7 make it the daily tool and the cleanup tool.

---

## 6. Porting map from `esa-nace-naseptavac`

| From the design source | Into this repo | Epic | Verdict |
|---|---|---|---|
| Identify layer: GLEIF, OpenFIGI (design §4.1, verified) | `core/sources/{gleif,openfigi,identity}.py` | — | **done, PR #1** |
| `docs/api-details.md`, `docs/egress-endpoints.md` | `docs/third-party-apis.md` — what data goes to which third party, verified request/response shapes, rate limits; the *whitelist* part is moot on Vercel | E1 / D6 | optional documentation (D6: no sign-off needed) |
| `src/main/probe.py` (status taxonomy, env report, JSON form) | `api` route `/probe` + `core/probe.py` | E1 | port |
| Rule table `design.md` §5.1 (ESA three dimensions: residency, sector, control) | `core/classify/rules.py` as data + `StructuredHints` | E4 | port as data, not prose |
| ECB institution lists as offline LEI → sector | `ecb_lei_sector.csv` in Blob | E4b | optional |
| Wikidata `P1278 → P452 → P4496`, Wikipedia summaries; ESMA FIRDS (CFI, LEI) | `core/sources/{wikidata,wikipedia,firds}.py` | E5 | port (verified live from Jakub's laptop) |
| `data/reference/nace_rev2_divisions.csv` — **English** NACE labels from Wikidata | extra `labels_en` on `NaceDivision` for the lexical pre-filter (their own note: English input scores ~0 against Czech texts) | E4 | small, useful |
| `data/reference/esa2010_sectors_cz_ciss.csv`, `cnb_ba0036_esa95_items.csv` | nothing — the real codebooks supersede them | — | drop |
| `src/main/isin.py` `COUNTRY_CS` (Czech names of ISIN prefixes, XS = Euroclear/Clearstream) | a small map for the issuer meta line ("předčíslí DE – Německo") | E3 | nice-to-have |
| `lei-lookup-tool` name matcher | `core/sources/match.py` | E3 | port the logic |
| Batch xlsx design (design §6.3): upload → enriched sheet → *liší se od CTS* | E6 as chunked serverless flow on `batch/reader.py` + `export/xlsx.py` | E6 | port the idea |
| Confirm/history "memory" (design §3, §7) | Postgres `confirmed_mappings`, `/confirm`, `/history` | E7 | port the idea |
| `docs/questions.md` Q1–Q12 | §7 below | E0 | ported |
| CodeNOW Flask scaffold, `/health` shape, pinned `requirements.txt` for the pylint gate, dictionary browser | nothing | — | not needed on Vercel / superseded |

---

## 7. Open decisions and questions — answer inline, with the date

### Platform (Vercel)

- **D1. Vercel account and plan.** Whose team, which plan (Hobby caps functions at 60 s and has no password
  protection; Pro gives 300 s, Log Drains, more concurrency), region (`fra1` Frankfurt). *Needed by E1.*
  **Answer:** 2026-09-22 — there is a Vercel account (Jakub); its team and plan are confirmed with Jakub before
  the project is created, and the plan sets `maxDuration` (Jakub: Hobby 60 s, Pro 300 s). Looked up through the
  Vercel API the same day: Jakub's personal team `10930795-6863s-projects`, plan **Hobby** — awaiting his
  confirmation. Checked against the Vercel docs on 22 Sept 2026: 60 s / 300 s are the limits *without* Fluid
  compute; Fluid is on by default for new projects and allows 300 s on Hobby and 800 s on Pro, so E1 sets
  `maxDuration` to 60 s explicitly, which is valid on every plan. Also verified: the Hobby plan is for
  personal, non-commercial use only (Vercel fair-use guidelines), and Pro is $20 per month per deploying seat.
- **D2. Repository visibility.** Make `tdzian39/nace-esa-classification-assistant` **private** (owner action)
  before any codebook, real data or preview URL is around. *Needed by E0/E1.*
  **Answer:** 2026-09-22 — the repository **stays public** (Jakub). E0.2 is dropped and E1 does not wait for
  it. The rule "nothing bank-internal in git" stays, so the codebooks live only in a private Vercel Blob store
  (→ D3).
- **D3. Codebook delivery.** Private Vercel Blob at cold start (recommended: nothing bank-internal in git) vs
  CSV committed to the (private) repo (simpler; needs MO to say the codelists are not sensitive). *E1.*
  **Answer:** 2026-09-22 — **private Vercel Blob**, settled by D2: the CSV option needed a private repository
  (Jakub).
- **D4. Database.** Neon Postgres via the Vercel Marketplace (recommended: audit, confirmations, later the LLM
  cache and ledger in one place) vs Vercel KV. *E2/E7/E9.*
  **Answer:**
- **D5. Authentication.** OIDC with Microsoft Entra ID needs an app registration from IT (Q-A1). Is the interim
  password gate with a self-declared name acceptable for the pilot, and for how long? *E2.*
  **Answer:** 2026-09-22 — there will be **no Entra ID app registration** (Jakub). The E2.1 shared-password gate
  with a self-declared name becomes the permanent login; E2 also keeps the untrusted-header rule and audit
  persistence. E2.2 (OIDC) and Q-A1 are dropped.
- **D6. Data-classification sign-off** for running on a public PaaS: ISINs/issuer names/typed descriptions,
  CTS codebook IDs and labels, user identities and lookup history (§4 lists the flows). Who signs? *E1 go-live.*
  **Answer:** 2026-09-22 — **no sign-off is needed**; E1 go-live is not gated on it (Jakub). The §4 list of data
  flows stays as documentation.
- **D7. When to delete the Tool 2 code** — now (E0.3, recommended) or after E6 reuses the reader. **Answer:**
  2026-09-22 - now, as E0.3 (PR #5); the IČO normaliser stays for the batch reader until E6 (Jakub).
- ~~**Q-A1.** Entra ID app registration: client id, tenant, redirect URI (`https://<project>.vercel.app/auth/callback`),
  group for MO.~~ **Answer:** 2026-09-22 — dropped with D5: there will be no app registration (Jakub).
- **Q-A2.** Audit retention (months) and who may read the history. **Answer:**

### Domain (MO / Ivča / CTS owners)

- **Q5. NACE revision.** Does `CTS_OKEC_NACE2` contain division **45**? (87 divisions vs Rev. 2's 88.) Is the CTS
  value always exactly the first two digits? Special items (unknown / not applicable)? **Answer:**
- **Q7. BA0036 semantics for foreign issuers.** Does CTS use the control split (…1/…2/…3) for non-residents at
  all, and how — is "pod zahraniční kontrolou" judged from the issuer's own country? Two concrete examples settle
  it: how are **Deutsche Bank AG** and a **US Treasury** issuer coded in CTS today? Also the S.12203 question:
  which 1221x/1222x/1224x item does a plain S.122 bank get? **Answer:**
- **Q8. Golden set** (§E8): ~30 recently created foreign issuers with CTS values, marked correct/unsure. **Answer:**
  2026-09-22 — MO is not asked (Jakub). Claude builds ~30 real issuers from the E8 seed list — banks, corporates,
  funds, governments, supranationals, financing vehicles — from ISINs and public sources. `verified_by` stays
  empty on every case until someone checks it against CTS; the codes stay provisional and no accuracy is quoted
  from them.
- **Q9. Batch cleanup.** Is a one-off run over all existing CTS foreign issuers in scope? Extract with issuer
  name, ISIN/LEI if stored, current NACE ID, current ESA ID; how many rows? **Answer:**
- **Q10. Volume and OpenFIGI key.** New foreign issuers per month; request a free OpenFIGI key (250/min) under
  whose account? **Answer:**
- **Q11. Users.** Who (roles, count); must confirmations carry the person's identity (yes, per the brief's audit
  rule) — which is why E2 precedes E7. **Answer:**
- **Q12. Hand-off into CTS.** Copying the two CTS IDs from the page is the pilot; is an Excel hand-off or a CTS
  API call wanted later? **Answer:**
- **Q13. Web search provider.** Is a paid search API (Brave/Bing/Google CSE) acceptable and procurable, or do we
  rely on Wikipedia summaries (E5) + typed descriptions? **Answer:**
- **Q14. ESA descriptions dictionary** ("Ivča ještě dodá" in the brief): does it exist beyond
  `BA0036_2024_jen_validni.xlsx`'s `Popis` column? **Answer:**

### Answered / decided log

- 2026-09-22 — Deployment: Vercel (Jakub). LLM: later (Jakub). Scope: Tool 1 only (Jakub). DWS: not granted (bank).
- 2026-09-22 — Q1 (egress whitelist for the runtime) is **moot on Vercel**: outbound internet is open. Kept only
  as the `/probe` page and the third-party data-flow document (D6).
- 2026-09-22 — Q2 (approved LLM endpoint) deferred by decision; Q3 (persistence) becomes D4.
- 2026-09-22 — D2: the repository stays public, E0.2 dropped; D3: private Vercel Blob; D5 + Q-A1: no Entra ID,
  the shared-password gate is the permanent login; D6: no data-classification sign-off; Q8: Claude builds the
  golden set from public sources, all cases provisional; D1: the Vercel account exists — its team and plan are
  confirmed with Jakub before the project is created. All Jakub; details in §1 and inline above.

---

## 8. Working conventions (how this repo is developed)

```bash
cd app
python -m venv ../.venv && ../.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows paths; the repo path may contain spaces — quote it
../.venv/Scripts/python.exe -m pytest -q          # 963 passed, 16 skipped without the real xlsx (skips are expected)
../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .
../.venv/Scripts/python.exe -m core.codebooks     # startup consistency check against data/codebooks (needs the xlsx)
../.venv/Scripts/python.exe -m core.classify "popis cinnosti" --verbose   # shortlist for a description
../.venv/Scripts/python.exe -m uvicorn api.main:app --port 8000           # local web tool (needs the xlsx)
```

- **Branch → PR → review by the other collaborator.** No direct pushes to `main`. PR descriptions explain *why*
  and how it was verified; live verifications carry the date. Stack PRs when they depend on an open one.
- **Tests never touch the network.** Registers are driven through `httpx.MockTransport` with trimmed copies of
  live payloads (captured, dated) in `tests/sources/conftest.py`; the LLM through `StubLlmProvider`.
  Test names read as sentences and say what would go wrong.
- **Fail-soft is a contract**: `None` = the register has nothing; `SourceUnavailableError` = could not ask;
  never collapse the two; a source failure is a note on the row, never a lost row; the classifier abstains
  rather than guesses.
- **CTS IDs come only from the codebooks** (`cts_id_for_esa`, `cts_id_for_nace` — the only two emission points);
  NACE is truncated to two digits in exactly one place. Only valid ESA leaves may be emitted.
- **Hard rules kept**: never scrape `apl.czso.cz` / `or.justice.cz` (enforced in code); nothing from DWS ever
  reaches a model (moot now, keep the rule); every lookup is audited with identifier, time, user — never with
  retrieved content; nothing bank-internal in the repo, which stays public (D2); secrets only in env / `app/.env`.
- **Czech UI, English code and docs.** Codebook labels are Czech; hint triggers are Czech + English.
- `CLAUDE.md` is the living architecture note: every PR that changes behaviour updates it; this roadmap gets a
  status line per epic. Keep `ui/prototype/suggest.html` in step with the template.
- Style of the codebase: module docstrings state the *why* and the verified facts with dates; frozen
  dataclasses with `slots`; `Protocol`s for pluggable parts; settings via pydantic-settings with a description
  per field; Python 3.12-compatible only.

---

## 9. Reference card

- **Settings added by PR #1**: `GLEIF_ENABLED`, `GLEIF_BASE_URL`, `GLEIF_TIMEOUT_SECONDS` (15), `GLEIF_MIN_INTERVAL_SECONDS`
  (1.0), `GLEIF_MAX_ATTEMPTS` (3), `GLEIF_FETCH_PARENTS` (true), `OPENFIGI_ENABLED`, `OPENFIGI_BASE_URL`,
  `OPENFIGI_API_KEY` (optional), `OPENFIGI_TIMEOUT_SECONDS` (15), `OPENFIGI_MIN_INTERVAL_SECONDS` (2.5),
  `OPENFIGI_MAX_ATTEMPTS` (3); both use `WEB_USER_AGENT`.
- **Fact-sheet wording contract** (`LeiRecord.facts()`, `FigiInstrument.facts()`): Czech sentences; category and
  sector glosses carry the English hint words in parentheses: FUND → "investment fund", RESIDENT_GOVERNMENT_ENTITY
  → "government", LOCAL_GOVERNMENT → "municipality", INTERNATIONAL_ORGANIZATION → "supranational", OpenFIGI `Govt`
  → "government, sovereign", `Mtge` → "mortgage-backed, asset-backed". E4 makes this structural; until then the
  words are the mechanism — do not "clean them up".
- **Settings removed by PR #5**: `DWS_DSN`, `DWS_USER`, `DWS_PASSWORD`, `DWS_SCHEMA`, `DWS_TIMEOUT_SECONDS` and the
  six `ARES_*`; an old `app/.env` that still sets them loads fine (unknown variables are ignored).
- **`Source` literal**: `WEB`, `GLEIF`, `OPENFIGI` (`DWS` and `ARES_LIVE` left with Tool 2 in PR #5); row `source` =
  registers that answered + `WEB`.
- **Glossary**: *MO* Middle Office treasury back office · *CTS* the securities master system where the issuer
  record and both codes live · *BA0036* ČNB codelist of ESA 2010 sectors as 7-digit codes · *ESA control axis*
  veřejné / soukromé národní / pod zahraniční kontrolou · *NACE division* the two-digit level CTS stores ·
  *LEI* Legal Entity Identifier (GLEIF) · *ELF* ISO 20275 legal-form code (`6QQB` German AG, `B5PM` Dutch N.V.,
  `8888`/`9999` no code) · *CFI* ISO 10962 instrument classification (from FIRDS) · *FVC* financial vehicle
  corporation (securitisation) · *MMF* money-market fund.

---

## 10. Verify at implementation (things this document could not check live)

- Vercel Python runtime: how to pin **Python 3.12** for the project (config file vs project setting); whether
  dependencies are read from `pyproject.toml` or only `requirements.txt`; whether **FastAPI `lifespan`** runs
  under the runtime (design assumes *not* — lazy init either way).
- `maxDuration` ceilings and default on the chosen plan; request body limit (assumed 4.5 MB); `/tmp` size.
- Vercel Blob private access from Python (REST with `BLOB_READ_WRITE_TOKEN`) — the only delivery since D3.
- Deployment Protection: Vercel Authentication on previews (assumed available on all plans); Password Protection
  on production (assumed paid) — hence the app-level gate in E2.
- GLEIF full-text search filter name (`filter[fulltext]`) and its ranking; `filter[entity.legalName]` behaviour
  with partial names.
- ECB list reuse terms before bundling anything (E4b); Wikimedia `User-Agent` policy wording (E5).
- Neon Postgres via Vercel Marketplace: connection pooling for serverless (use the pooled DSN).
