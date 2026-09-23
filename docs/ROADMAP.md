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

## 0. Next steps (23 Sept 2026) — read this first

**Where it stands.** E0, E1, E8 and E4-lite are done (PR #8 merged), and the model is ready to switch on (E9,
PR #9 merged). PR #10 gives the page the look of the RB team gateway and fixes htmx, which had never loaded. The
tool does what the brief asks, in deterministic mode: an ISIN, name or description goes in; the issuer's register
facts and two shortlists — NACE and ESA, every candidate with its CTS ID — come out, and where a rule decided (a
GLEIF category or a keyword) the first candidate is labelled **navrhovaný kód**; a person confirms. It runs on
Vercel (production behind Vercel Authentication, the real codebooks in the private Blob store, `DE0005140008` end
to end in about 5 s; the deployment is still the E1 code until `main` is redeployed). It is not gold-plated, and
should not be: what follows is the short list that separates "works for Jakub" from "MO uses it", then what is
optional.

**Needed before MO uses it**

1. **E2 — the login (S–M).** Vercel Authentication on Hobby admits the owner plus one external user, so MO cannot
   get in yet. Build the shared-password gate with a self-declared name (D5) and the untrusted-header rule. The
   hard rule "log every lookup with its user" needs somewhere to keep the log — Vercel keeps runtime logs 1 hour
   on Hobby — so **decide D4** (a Neon Postgres free tier via the Vercel Marketplace, one `audit_events` table,
   is the smallest honest answer).
2. **Plan (owner decision).** Hobby's terms are personal, non-commercial use; move the project to Pro ($20 per
   month per deploying seat) before the bank relies on it.

**Cheap accuracy wins the golden run found (S each, do them next)**

3. ~~**E4-lite: two rules and English labels.**~~ **Done in PR #8 (23 Sept 2026).** Governments → NACE 84,
   international organisations → 99; the GLEIF categories `RESIDENT_GOVERNMENT_ENTITY` / `INTERNATIONAL_ORGANIZATION`
   outrank any keyword (OpenFIGI `Govt` stays a keyword: Kommuninvest, a bank, issues Govt bonds); English NACE
   division titles in the lexical filter (the official titles in `core/classify/nace_en.py` rather than the
   design source's Wikidata paraphrases); keywords for the four S.125 families without a `Popis`; and the
   **navrhovaný kód** — a rule's first candidate proposed when no model answers, never text similarity alone,
   never when rules for two codes or families tie. **Provisional figures, real issuers (before → after):** NACE
   recall@12 72% → 97%, top-1 53% → 83%; ESA recall@12 97% → 100%, top-1 42% → 47%; fictional traps unchanged
   (NACE 100%/90%, ESA 100%/60%). Proposals with the model off: NACE for 35 of 36 (30 as expected), ESA for 26
   (21 in the expected family, 14 exact — the rest is the control axis, Q7). Left: Unilever (NACE 20 not
   offered; it turns on Q15), EBRD and the EU (GLEIF files them `GENERAL`, so 99 is second), and ESA precedence
   between families (a money-market fund still ranks the non-MMF family first; BNP Paribas the insurers) —
   that is E4 proper, only if MO asks.
3a. **E9: the model is ready to switch on — PR #9 (merged 23 Sept 2026).** The endpoint arriving on
   24 Sept is not known yet, so no new adapter: `app/README.md` → "Enabling the model" has the steps per case
   (OpenAI and Azure v1 need no code; Azure classic and the Claude API need an adapter). `gpt-5.6-luna` with
   `LLM_REASONING_EFFORT=none` replaces the `gpt-4o-mini` placeholder (OpenAI's docs, checked 23 Sept);
   `LLM_DAILY_TOKEN_BUDGET=0` on Vercel (§1); no model call that could outlive the 60 s cap
   (`LOOKUP_DEADLINE_SECONDS`); `python -m core.classify --golden --model` measures a model against the rules.
   **Switch-on** = the env vars in "Switching it on in production", the owner adds `LLM_API_KEY`, redeploy,
   smoke checks; rollback = `LLM_ENABLED=false` and redeploy. Never made a live call yet.
4. **E5-lite: the FIRDS LEI fallback.** GLEIF maps 25 of 36 golden ISINs; the misses (Eurobond, LU/IE funds) include
   all four captive vehicles, the core ESA trap. ESMA FIRDS returns the issuer LEI for them; `/probe` already
   shows the host reachable from Vercel.
5. **Have someone with CTS access check the 36 real golden cases** (an hour's work). It turns provisional figures
   into an accuracy that can be quoted, and settles Q7 and Q15 on the way.

**Optional — only if MO asks**

- E3 (name → issuer via GLEIF full text): a name alone already works through the typed description.
- E6 (batch): only if the one-off CTS clean-up (Q9) is wanted. E7 (confirm/history): only after E2 and D4.
- E10: a one-page Czech user guide when MO starts; the runbook is `app/README.md` → "Deploying on Vercel".
- **Parked:** E9 (the LLM) until an approved endpoint exists; E4b (ECB lists offline).

**Open questions that still matter:** Q7 (control axis — changes 15 golden ESA codes and the candidate order),
Q15 (a listed parent's NACE), D4 (database, for the audit), Q10 (volume; a free OpenFIGI key if volume grows).

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
| 2026-09-22 | **D1 confirmed — Hobby for now.** The project `nace-esa-assistant` lives in Jakub's personal Hobby team (its terms are personal, non-commercial use; Pro is the upgrade path). | Jakub |
| 2026-09-22 | **D1 — there is a Vercel account.** Claude looks up its team and plan and confirms them with Jakub before creating the project. The plan sets `maxDuration` (Hobby 60 s, Pro 300 s). | Jakub |
| 2026-09-22 | **D5 / Q-A1 — no Entra ID app registration.** The E2.1 shared-password gate with a self-declared name becomes the permanent login. E2 keeps the untrusted-header rule and audit persistence; E2.2 (OIDC) and Q-A1 are dropped. | Jakub |
| 2026-09-22 | **D6 — no data-classification sign-off is needed**; E1 go-live is not gated on it. The §4 data-flow list stays as documentation. | Jakub |
| 2026-09-23 | **The daily token budget is 0 on Vercel.** Vercel keeps no usage ledger, so a positive `LLM_DAILY_TOKEN_BUDGET` would refuse every model call (it fails closed). The spending cap in the provider's dashboard is the backstop; the per-request (8,000 tokens) and per-run (200 calls per function instance) limits stay. No Postgres ledger for now (that is D4). | Jakub |
| 2026-09-23 | **The page takes the look of the RB team gateway** (`anorfidien/finance_rb_cz`, after the Raiffeisenbank brand manual 2023). That repository has no licence, so the look is re-implemented and nothing is copied; its logo files stay out because this repository is public (PR #10). | Jakub / Claude |
| 2026-09-23 | **Navrhovaný kód in deterministic mode** (the brief's wording): the first candidate is labelled as the proposal when a rule decided it (GLEIF category or keyword), marked as the rules' and without a confidence; no proposal on text similarity alone or on a tie between rules for different codes (PR #8). | Jakub |
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
- **PR #6** `feat/e1-vercel-deterministic` — the E1 code (22 Sept 2026): lazy codebooks from private Blob,
  503 instead of a dead instance, `/probe`, the Vercel config. Built on PR #5's branch but opened against
  `main` from the start (the lesson of #2), so until #5 merges its diff also shows #5's commits; merge #5
  first.
- **PR #7** `feat/e8-golden-set` — E8 (22 Sept 2026): 36 real foreign issuers, provisional, scored with their
  recorded register facts. Built on #6's branch, opened against `main`; merge after #5 and #6.
- #5 → `53e4621`, #6 → `f32b2c1`, #7 → `5b029cb` (merge commits, 22 Sept 2026 evening); `main` = `5b029cb`.
- **PR #8** `feat/e4-lite-public-sector-navrhovany` — E4-lite, the families without a `Popis`, navrhovaný kód, and
  the deterministic list layout fix (23 Sept 2026). Against `main`.
- **PR #9** `feat/e9-llm-switch-on` — E9 readiness: `gpt-5.6-luna`, the lookup deadline, `--golden --model`, the
  switch-on runbook (23 Sept 2026). Built on #8's branch, against `main`.
- #8 → `8018c41`, #9 → `7911216` (merge commits, 23 Sept 2026); `main` = `7911216`.
- **PR #10** `feat/ui-rb-gateway-look` — the page and `/probe` in the look of the RB team gateway
  (`anorfidien/finance_rb_cz`, look only: no licence), a light/dark toggle, and the htmx fix (the SRI hash was
  wrong, so htmx never loaded) (23 Sept 2026). Against `main`.

### Where the next session starts: §0 above

*Superseded by §0 (the next steps). Kept for the record:* **E1 is deployed** (22 Sept 2026, see E1 below): `nace-esa-assistant` in Jakub's Hobby team, production behind
Vercel Authentication, the private Blob store connected and empty, so `/health` says which file is missing. The
next step is uploading the four codebook files (`app/README.md` → "Deploying on Vercel" → step 3), then checking
`DE0005140008` end to end and measuring the cold start with codebooks. Then E2 (the login). E8's cases are in
PR #7; their first numbers need the same codebook files (`python -m core.classify --golden`).


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

**E1** (§5) puts the deterministic mode on a Vercel preview. **Its code is done in PR #6**: every
Vercel fact it relies on was checked against the docs and the builder source on 22 Sept 2026 (§10), and it
was run locally the way Vercel runs it (`uvicorn api.main:app`, live GLEIF/OpenFIGI, live `/probe`). What
is left of E1 is the deployment itself, and it waits for two things: Jakub's go-ahead to create the project
in his team (D1 — the team is on Hobby, whose terms are personal, non-commercial use only), and the four
codebook files, which are with the repository owner (perhaps in `tdzian39/rb_files`). With both in hand,
follow `app/README.md` → "Deploying on Vercel" and write the measured cold start into E1 below. Start from
a fresh clone and a fresh venv (`pip install -e ".[dev]"`, or `tests/api` will not collect — §3 gotchas).
E8 (the golden set, Q8) can run alongside; E2 (the login) comes next.

---

## 3. What exists today (Tool 1 only, as of PR #5, 22 Sept 2026)

### Stack and layout

Python ≥ 3.12 (dev on 3.13; keep 3.12-compatible), FastAPI + Jinja2 + htmx (no JS framework),
pydantic-settings, httpx, openpyxl, ruff, pytest. Everything under `app/`:

```
app/
  api/main.py            FastAPI: GET / · POST /suggest · POST /api/suggest · GET /suggest.xlsx · GET /health · GET /probe
                         codebooks load once per process (startup or first use); unusable → 503 with the reason (E1)
  ui/templates/suggest.html   (ui/prototype/suggest.html copies its CSS — change both together) · probe.html
  config/settings.py     pydantic-settings; relative paths resolve against app/
  vercel.json · .vercelignore · .python-version · [tool.vercel]/[tool.uv] in pyproject.toml   (E1, §4)
  core/
    suggest.py           the pipeline: request → identity → evidence → shortlist → classifier → IssuerSuggestion
    identifiers/         isin.py (ISO 6166 + Luhn) · ico.py (IČO, only for batch/reader.py until E6)
    codebooks/           xlsx reader, loaders, models (CodebookSet), normalize, consistency check, versioning
                         blob.py (the four files from a private Vercel Blob store, E1)
    probe.py             the /probe checks: one fixed request per register, status per failure mode (E1)
    sources/             base.py (Source literal, Provenance, Source*Error) · gleif.py · openfigi.py · identity.py · web.py
    classify/            candidates.py (pre-filter) · hints.py (keyword table, register rules, ESA family grid)
                         nace_en.py (English division titles, scored only) · text.py (IDF)
                         proposal.py (navrhovaný kód: the model's pick, else a rule's)
                         prompts.py · provider.py · llm.py · cache.py · budget.py (LLM path, off)
                         golden.py (cases, recall@12, top-1) · golden_fixtures.py (register answers: capture / replay)
    export/              columns.py (the suggestion row contract) · xlsx.py (Subjects + Run sheets)
    batch/               reader.py (messy xlsx in — E6 reuses it; nothing calls it yet)
    audit.py             one log line per lookup: identifier, time, user, sources, outcome — never content
  tests/                 1328 passed / 19 skipped (skips = tests needing the real xlsx; 1347 with them); fixtures are trimmed live payloads
    golden/              cases.json (10 fictional trap cases + 36 real issuers, all provisional) · identity.json
                         (recorded GLEIF/OpenFIGI answers) · ba0036_v044_nonresident.json (the public CNB list)
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
   (`limit=12`): keyword hints (`hints.py`, cs+en, +10 score), register rules (a GLEIF category in the fact
   sheet, +5 on top — PR #8) plus IDF-weighted lexical overlap against the codebook texts (NACE also against the
   English division titles, `nace_en.py`); ESA is treated as a **grid of family × control** derived from the
   codebook names; the residual family *nefinanční podniky* always gets reserved slots; every candidate already
   carries its CTS ID.
5. `LlmClassifier.classify_both()` — with `NullLlmProvider` it **abstains** with a readable reason. With a
   model: JSON schema pins `code` to the offered codes, answers are re-checked, suggestions are built from
   candidates (CTS ID cannot be invented), cache keyed on everything that changes the answer, spending limits
   fail closed.
5a. `propose()` (`proposal.py`, PR #8) — the **navrhovaný kód** per codebook: the model's first pick; else the
   shortlist's first candidate if a rule put it there (score ≥ 5, i.e. a keyword or register rule — lexical
   scores stop at 1.0) and no rule for another code or ESA family ties with it; else nothing ("výběr je na vás").
   A rule's proposal has no confidence, names its rules and lists tied control variants.
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
  kontrolou · `2002803` pojišťovny · `2002513` sekuritizace · `2002533` finanční instituce poskytující úvěry (S.125 lenders — not banks) · `2002403` investiční
  fondy jiné než FPT · `2002303` fondy peněžního trhu · `2009031` mezinárodní rozvojové banky; resident block
  `1221300`, `1100300`. Foreign issuers → the `2…` block (`EsaCandidateFilter(resident=False)`).
- **The public source of BA0036** (found 22 Sept 2026, E8): the ČNB SDAT portal, *Metodické informace → Knihovna →
  Číselníky*, code BA0036 "Ekonomické sektory podle ESA2010 v úpravě ČNB", version 044 (valid from 1 Jan 2025;
  the 2024 version 042 has the same 321 items). Its non-resident aggregate `2000000` has exactly **56** elementary
  items — the 56 in CTS, and all 16 codes read from the CTS export match in meaning. The resident aggregate has 54,
  so ČNB has 110 leaves where `BA0036_2024_jen_validni.xlsx` has 109: one resident leaf is missing from the CTS
  file (irrelevant for foreign issuers; whoever has the file can diff it). The E8 golden cases take their ESA codes
  from this list.
- **The S.12203 problem**: RES/ARES report ESA in the `S.xxxxx` form (`12203` for Raiffeisenbank); CTS splits
  S.1220x into banks (1221x) / credit unions (1222x) / other deposit-takers (1224x), so a RES sector is **not** a
  lexical lookup into BA0036. Irrelevant for foreign issuers, relevant if anyone ever maps register sectors.
- **NACE revision question (open, Q5)**: NACE Rev. 2 has 88 divisions, CZ-NACE 2025 (= Rev. 2.1) has 87 —
  division 45 disappears. `CTS_OKEC_NACE2` has 87 divisions (88 CTS IDs). *Whoever has the file: does it contain
  division 45?* Yes → Rev. 2 (then which division is missing?); no → CTS is already on CZ-NACE 2025.
- **Hint family keys** (folded codebook names; `hints.py`): `banky`, `pojistovaci spolecnosti (ic)`,
  `penzijni fondy (pf)`, `investicni fondy jine nez fondy penezniho trhu`, `fondy penezniho trhu`,
  `ucelove financni instituce pro sekuritizaci aktiv`, `kaptivni financni instituce a pujcovatele penez`,
  `obchodnici s cennymi papiry a derivaty`, `financni instituce poskytujici uvery`,
  `specializovane financni instituce` (PR #8), `ustredni vladni instituce`, `narodni vladni instituce`,
  `mistni vladni instituce`, `mezinarodni rozvojove banky`, `ostatni mezinarodni instituce`; baseline
  `nefinancni podniky`. **No `Popis`** in the CTS file (nor at ČNB) for four S.125 families — securitisation,
  dealers, lenders, specialised — and for the NPISH family; the first four are reached by name and keyword only.
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
 Vercel Function, FastAPI preset: api.main:app  ([tool.vercel] entrypoint; one function, no rewrites)
    │  cold start (lifespan): load codebooks (Blob → /tmp → CodebookSet, consistency check, version id);
    │  a failure is reported (503 + /health), never raised
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
| FastAPI `lifespan` **does** run (before the first request), and a lifespan that raises takes the whole instance down, `/health` included (verified 22 Sept 2026) | the lifespan warms the codebooks but never raises; a failure is remembered (retry after 30 s) and answered with 503 + the reason; the first request loads them if the lifespan did not; `/health` never loads anything |
| Function duration is capped: with Fluid compute (on by default for new projects) 300 s on Hobby and up to 800 s on Pro; without it 60 s / 300 s (verified 22 Sept 2026) | `maxDuration` 60 s in `vercel.json`, valid on every plan; one ISIN = up to 4 GLEIF + 1 OpenFIGI requests, spaced 1.0 s / 2.5 s → ~4 s live (measured locally 22 Sept); on Vercel set 5 s timeouts and 2 attempts so the worst case fits; **batch must be chunked** (E6) |
| Request body limit ~4.5 MB | fine for a sheet of ISINs; large sheets are chunked client-side anyway |
| Cold starts scale with bundle size; the whole Root Directory is bundled, and the CLI uploads what `.vercelignore` does not exclude (it never reads `.gitignore`) | no `pandas` (PR #5), uvicorn only in extras (Vercel brings its own), `[tool.uv] package = false`; `.vercelignore` and `excludeFiles` keep out tests, prototype, `data/`, `*.xlsx`, `.env` |
| No reverse proxy, no bank SSO in front | the app must authenticate users itself (E2); `X-Remote-User` is untrusted |
| Runtime logs are kept 1 hour on Hobby, 1 day on Pro; Log Drains are a paid feature | audit events go to Postgres (E2); a codebook failure must be visible on `/health` and `/probe`, not only in the log |
| Outbound internet is open | **no egress/whitelist request** (the biggest simplification vs CodeNOW); the third-party data flows are documented below (D6: no sign-off needed) |
| Env vars ≤ 64 KB total | codebooks cannot travel as env vars → private Blob store (D3) |
| Previews per PR need the GitHub integration, which only the repository owner can connect | deploys run from a checkout with the Vercel CLI; previews are protected by Vercel Authentication by default, production by "All Deployments" protection (free on every plan since 9 Sept 2026) until the E2 login exists |
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

### E1 — Deploy the deterministic mode on Vercel (M) — *code done in PR #6; the deployment waits for D1 and the codebook files*

**Goal:** a preview URL where an ISIN returns the identity facts and the two shortlists with CTS IDs.
**Work** — as built in PR #6, after the plan was checked against the Vercel docs and the builder source on
22 Sept 2026 (§10). The first plan (an `api/index.py`, a catch-all rewrite, framework "Other", a pinned
`requirements.txt`) would not have worked: under the FastAPI preset a rewrite shows the app every request
as one path, and a `requirements.txt` next to `pyproject.toml` is ignored.
1. **Entry and config in `app/`:** Root Directory `app`, FastAPI preset (auto-detected, pinned in
   `vercel.json`), `[tool.vercel] entrypoint = "api.main:app"` in `pyproject.toml` (the file-name search finds
   `api/main.py` only through an undocumented path), one function, no rewrites. `vercel.json`: region `fra1`,
   `maxDuration` 60 s (valid on every plan), `excludeFiles` for tests, prototype, `data/`, `*.xlsx`, `.env`.
   `.python-version` = 3.12 (Vercel's default is announced to move to 3.14). Dependencies come from
   `pyproject.toml` via uv; `[tool.uv] package = false` stops a second copy of the app under `_vendor/`;
   uvicorn moved to the `server`/`dev` extras because Vercel's runtime brings its own.
   `.vercelignore` exists because the CLI never reads `.gitignore`. `tests/test_vercel_config.py` keeps the
   files consistent.
2. **Lazy startup, never fatal:** the codebooks load once per process — in the lifespan, which Vercel runs
   before the first request, or on the first lookup — under a lock, so concurrent first requests share one
   load. A lifespan that raises would take the instance down, `/health` included, so a failed load is
   remembered (retried after 30 s) and suggestion requests answer **503 with the reason** (the page keeps
   what was typed). `/health` never loads anything; it reports `codebooks.state` (`loaded` / `not_loaded` /
   `error` + reason), version, load time, Python, region, commit, and turns 503 after a failed load.
3. **Codebooks from Vercel Blob** (`CODEBOOK_SOURCE=blob`, default `dir`): `core/codebooks/blob.py`
   downloads the four files with plain `GET https://<store>.private.blob.vercel-storage.com/codebooks/<file>`
   and `Authorization: Bearer <BLOB_READ_WRITE_TOKEN>` into `/tmp`, atomically, with no list or head call
   (both are metered, and Hobby stops Blob for 30 days past its quota); the store id comes from
   `BLOB_STORE_ID` or the token. OIDC is not used: in Python the token belongs to a request and is not there
   at startup. Loaders, consistency check and version id are untouched. Upload and update procedure:
   `app/README.md` → "Deploying on Vercel".
4. **`/probe`** (port of the design source's `src/main/probe.py`): one harmless request per register
   (GLEIF, OpenFIGI; `?set=all` adds FIRDS, Wikidata with its follow-up, Wikipedia cs/en), 5 s timeout, no
   retry, a status per failure mode (`ok`, `unexpected_body`, `http_error`, `proxy_auth`, `tls`,
   `proxy_error`, `dns`, `timeout`, `blocked`, `error`), the codebook state, the settings with secrets shown
   as present/absent only, and the runtime (Python, region, commit, library versions, which `settings.py`
   was imported). `PROBE_ENABLED=false` turns it off; E2 puts it behind the login. Its first live run found
   that Wikimedia refuses httpx requests whose User-Agent carries no contact (403 "Please respect our robot
   policy"); the default `WEB_USER_AGENT` now names the repository URL, and all six hosts answer `ok`.
5. **Vercel env** (Production and Preview): `CODEBOOK_SOURCE=blob`, `BLOB_READ_WRITE_TOKEN` (Sensitive),
   `LLM_ENABLED=false`, `LLM_CACHE_PATH=` and `LLM_USAGE_PATH=` empty (an empty value now really switches
   them off — it used to parse as `Path(".")`), `WEB_USER_HEADER=` empty, `GLEIF_TIMEOUT_SECONDS=5`,
   `OPENFIGI_TIMEOUT_SECONDS=5`, both `*_MAX_ATTEMPTS=2`, so the worst case (4 + 1 requests) stays under 60 s.
6. Also fixed on the way: `/suggest.xlsx` returned 500 for an issuer name outside latin-1 ("Česká
   spořitelna"): the name now goes into `filename*` (RFC 5987) with an ASCII fallback.
**Verified locally (22 Sept 2026):** `uvicorn api.main:app` from `app/` (what Vercel runs), synthetic
codebooks: loaded at startup in 46 ms; `DE0005140008` → Deutsche Bank via GLEIF + OpenFIGI in about 4 s;
`/probe?set=all` all six hosts `ok` in about 2.3 s; with `CODEBOOK_SOURCE=blob` and no token the app starts,
`/health`, the API, the page and the download answer 503 with the reason and the empty form still opens.
**DoD:** preview deployment renders `DE0005140008` with LEI, facts, both shortlists and CTS IDs; `/health` and
`/probe` answer; cold-start time measured and written here; the Dockerfile still builds for local use.
**Deployed 22 Sept 2026** (from a clean checkout of PR #6 with the Vercel CLI) to production,
`https://nace-esa-assistant.vercel.app`, behind Vercel Authentication: build and deploy 21 s; Python 3.12.14,
region `fra1`, entrypoint `api/main.py`, the source copy of the app imported (`/var/task/config/settings.py`);
`/probe?set=all` from `fra1`: all six hosts `ok` in 1.7 s; `/health` 503 "codebooks/CTS_BA0036_NEW.xlsx is not
in the Blob store" — the store was reached with the connected token and is still empty, exactly as designed;
cold start about 1 s without codebooks. Protected deployments are checked with `vercel curl <path>` from a
linked checkout (it handles the protection bypass).
**Status: DONE (22 Sept 2026).** The four codebooks were uploaded to the private store the same evening;
on Vercel `/health` reports `ok`, version `cb-cc2c7e89a673069c`, codebooks fetched, parsed and checked in
348 ms; `DE0005140008` returns Deutsche Bank AG via GLEIF + OpenFIGI in about 5 s, NACE `64` (CTS 512)
first and the bank family (`2002213` CTS 635, `2002212` 634, `2002211` 633) first on ESA. Uploading needed
`BLOB_STORE_ID` as a project variable (the CLI refuses the OIDC token without it).
**Depends on:** no epic (E0.2 was dropped, D2); D1 confirmed. Before real data: the four codebook files from the
repository owner (perhaps in `tdzian39/rb_files`, where Jakub has a pending invite), uploaded with
`vercel blob put` as in `app/README.md`.

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
   was typed and no provider is configured. Descriptive `User-Agent` with a contact (Wikimedia requires it; 200/min; `/probe?set=all` showed a 403 without one).
2. `core/sources/firds.py`: ISIN → CFI code, issuer LEI (fallback when GLEIF has no mapping), instrument full
   name, currency. CFI → structured hints: `DA…`/`DG…` asset/mortgage-backed → securitisation; `C…` collective
   investment → funds; `DN…` municipal → local government; `E…` equity of the issuer itself.
3. Register order in `IssuerIdentifier`: GLEIF → FIRDS (if no LEI yet) → OpenFIGI → Wikidata → Wikipedia;
   all fail-soft, all cited, `/probe?set=all` covers the hosts.
   *Measured on the E8 golden set (22 Sept 2026): GLEIF's ISIN filter resolves 25 of 36 real ISINs; the misses
   are Eurobond (XS), Luxembourg and Irish fund ISINs - among them all four captive vehicles, the core ESA trap,
   whose fact sheet therefore lacks the parent. OpenFIGI knows all 36. The FIRDS LEI fallback is the first thing
   E5 should add; the golden run shows its effect directly.*
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
**Status: done in PR #7 (22 Sept 2026) except the numbers.** 36 real issuers - 6 governments, 6 supranationals,
KfW, 7 banks, 2 insurers, 4 corporates, 5 financing vehicles, 4 funds, 1 securitisation vehicle - researched by
four agents from GLEIF, OpenFIGI, FIRDS, issuer reports and the ECB lists, each group re-checked by an independent
reviewer who tried to refute it (all 36 ISINs and LEIs held; six descriptions, one ISIN and some citations were
corrected). Confidence: 20 high, 13 medium, 3 low; 15 cases depend on Q7. Notable traps: the **EIB** is
`2002211` banky veřejné, not `2009031` - the CNB's own note to `2009031` says it has been reported among
non-resident banks since 2010; **Toyota Motor Credit** lends to customers, so `2002533`, not a captive; the
**Unilever** and **TotalEnergies** NACE turn on Q15. The register answers are recorded (146, trimmed) in
`tests/golden/identity.json`. The first recall/top-1 run needs the real codebooks; the numbers go here and in the
README labelled provisional.
**First run with the real codebooks (22 Sept 2026), all cases provisional — a measurement of the pre-filter, not
an accuracy figure:** real issuers NACE recall@12 72% (top-1 53%), ESA recall@12 97% (top-1 42%); fictional
traps NACE 100% (90%), ESA 100% (60%). NACE misses: all six governments (84 never offered), EBRD and EU (99),
Volkswagen (29), Unilever (20); ESA miss: the Amundi money-market fund. §0 item 3 targets them. With the real
codebooks present the full suite runs without skips: 1264 passed; the 100%-recall tests assert the trap
cases only — the real ones are measured, not gated.
**Second run, after E4-lite (PR #8, 23 Sept 2026), same cases, still provisional:** real issuers NACE recall@12
97% (top-1 83%), ESA recall@12 100% (top-1 47%); fictional traps unchanged. The one NACE miss left is Unilever
(20; Q15). Top-1 misses: EBRD 64 and the EU 84 before 99 (GLEIF files both `GENERAL`), Allianz 64 before 65, Siemens
62 before 27, Toyota Motor Credit 46 before 64 (by 0.01). The ESA top-1 misses are mostly the control digit (Q7).

### E9 — LLM second opinion (M) — *ready to switch on (PR #9, 23 Sept 2026); the endpoint arrives 24 Sept*

What exists: `prompts.py` (versioned, register facts already flow into the prompt via `classifier_text`),
`provider.py` (OpenAI Chat Completions over httpx, structured output, 429/5xx retry), `llm.py` (abstain, not
guess), `cache.py`, `budget.py` (fail-closed limits), `--estimate`/`--usage` CLI. Never made a live call.
**PR #9 adds** the `gpt-5.6-luna` default with `LLM_REASONING_EFFORT=none` (OpenAI docs, 23 Sept), the steps per
endpoint (`app/README.md` → "Enabling the model"), `LLM_DAILY_TOKEN_BUDGET=0` on Vercel (§1), the lookup deadline
(`LOOKUP_DEADLINE_SECONDS`, no call that could outlive Vercel's 60 s), the page states tested with the stub, and
`python -m core.classify --golden --model`. Switching on is env vars plus a redeploy; the runbook has the list.

**Governance note (what the model sees, where it goes, what stays behind).**
- *Sent*, per lookup, two requests (NACE, ESA): the issuer name (typed, or GLEIF's legal name), the activity
  description (MO's typed text, or a web page's text once a search provider exists), the register fact sheet
  (GLEIF: legal name, country, legal form, entity category, status, parents; OpenFIGI: instrument name, type,
  market sector), and the shortlist as codes with their codebook labels and definitions (Czech NACE_STAT texts,
  BA0036 names and `Popis`). All of it public issuer data or codebook text.
- *Never sent*: client data, anything from DWS (the tool has no DWS connection since PR #5), user identities, CTS
  IDs (the prompt carries codes and labels; the IDs are attached afterwards from the codebook), and the audit log.
  MO's typed description is the one free-text input: it should describe the issuer from public sources, nothing
  about the bank's clients or positions — the Czech user guide (E10) says so.
- *Where*: the endpoint in `LLM_BASE_URL` (the README table lists OpenAI, Azure OpenAI and the Claude API);
  requests leave from Vercel's `fra1` function over HTTPS with the key from `LLM_API_KEY` (a Sensitive Vercel
  env var, added by the owner; never in the repository, never logged — it is a `SecretStr`). That provider's own
  retention and training terms for API data apply; check them for the chosen endpoint before real use.
- *Logged by the tool*: one audit line per lookup (identifier, time, user, sources, outcome — never content);
  on a failure, a warning with the reason (the provider's error text, at most 300 characters; no prompt). On
  Vercel the cache and the usage ledger are off, so no answers or token counts are stored beyond the function's
  runtime logs (1 hour on Hobby); locally both are SQLite files under `app/data/cache/`.
**When approved:** (1) the gateway: if OpenAI-compatible (OpenAI, Azure OpenAI's v1 API), `LLM_BASE_URL` + key;
if Azure OpenAI's classic endpoints, a small adapter (`api-key` header, `api-version` query, deployment name in the
path); if the Claude API, a Messages API adapter built from the `claude-api` skill (README table); (2) cache and
usage ledger → Postgres or KV (D4) — until then the daily budget is 0 on Vercel (§1) and the provider dashboard's
cap is the backstop; (3) run `python -m core.classify --golden --model`, compare with the rules' top-1, keep the
cheaper model that passes; (4) the governance note above. Show the model as a labelled second opinion with its
one-sentence justification; keep the shortlist visible (it is: the navrhovaný kód card carries the confidence).

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
  Vercel API the same day: Jakub's personal Vercel team, plan **Hobby**; **confirmed the same evening: use
  Hobby for now (Jakub)**, knowing its terms (below). Project `nace-esa-assistant` created then — Root Directory
  `app`, FastAPI preset, `fra1`, Vercel Authentication on all deployments — with the private Blob store
  `nace-esa-codebooks` (`fra1`) connected, which put `BLOB_READ_WRITE_TOKEN` into every environment. Checked against the Vercel docs on 22 Sept 2026: 60 s / 300 s are the limits *without* Fluid
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
  value always exactly the first two digits? Special items (unknown / not applicable)? **Answer:** 2026-09-22 —
  read from the real file: **CTS is on CZ-NACE 2025 (NACE Rev. 2.1).** 87 divisions, **no 45**, the set equal to
  CZ-NACE 2025's, Rev. 2.1 titles (e.g. 63 "Poskytování počítač. infrastr., zprac. dat, hosting…"); CTS IDs
  455–542 with one gap, **496, exactly where 45 sat** in the Rev. 2 order — 45 was dropped when CTS moved.
  `NACE_STAT` has the same 87. The value is the first two digits (the updated brief says so); no special items.
  Consequence: codes from Rev. 2 sources (Wikidata P4496, E5) need mapping — 45 → 46/47, and the J/K re-cut.
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
  `BA0036_2024_jen_validni.xlsx`'s `Popis` column? **Answer:** 2026-09-22 — no: the updated brief names
  `BA0036_2024_jen_validni.xlsx` itself as the dictionary ("upravený číselník s popisy významů", the list of
  the only elementary codes CTS accepts). `Popis` is filled for 81 of 109 leaves; empty for the S.125 split
  (securitisation, securities dealers, lenders, specialised institutions) and NPISH — empty in the CNB's SDAT
  list too, so no public source fills them. The one resident leaf of BA0036 v044 missing from the CTS list
  is `1312000` (S.1312 state government, which has no Czech units). The file set loads with 0 errors as
  version `cb-cc2c7e89a673069c` (the id hashes file bytes; the copies differ from tdzian39's by save stamp).
- **Q15. NACE of a listed group parent** (raised by E8, 22 Sept 2026): does CTS record the legal unit's own
  activity - for Unilever PLC or TotalEnergies SE that is 70 (activities of head offices), as the national
  registers, ESA 2010 2.14 and FINREP (EBA Q&A 2022_6672) do - or the group's main activity (20 soaps and
  detergents, 06 oil and gas extraction)? The golden cases take the group view with 70 as the first alternative.
  Captive financing vehicles stay 64 either way: financing is their own activity. **Answer:**

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
../.venv/Scripts/python.exe -m pytest -q          # 1328 passed, 19 skipped without the real xlsx (skips are expected); 1347 with them
../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .
../.venv/Scripts/python.exe -m core.codebooks     # startup consistency check against data/codebooks (needs the xlsx)
../.venv/Scripts/python.exe -m core.classify "popis cinnosti" --verbose   # shortlist for a description
../.venv/Scripts/python.exe -m core.classify --golden        # recall@12 + top-1 over tests/golden (needs the xlsx)
../.venv/Scripts/python.exe -m core.classify --golden-capture  # re-record the register answers (network, ~2 min)
../.venv/Scripts/python.exe -m core.classify --golden --model  # through the model: its top-1 vs the rules', tokens (needs LLM_API_KEY)
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
- **Settings added by E1 (PR #6)**: `CODEBOOK_SOURCE` (`dir` | `blob`), `BLOB_READ_WRITE_TOKEN`, `BLOB_STORE_ID`,
  `CODEBOOK_BLOB_PREFIX` (`codebooks/`), `CODEBOOK_BLOB_TIMEOUT_SECONDS` (10), `CODEBOOK_BLOB_MAX_ATTEMPTS` (2),
  `CODEBOOK_DOWNLOAD_DIR` (system temp), `PROBE_ENABLED` (true). Changed: an empty `LLM_CACHE_PATH` /
  `LLM_USAGE_PATH` now means off (it parsed as `Path(".")`); the default `WEB_USER_AGENT` carries the repository
  URL as contact (Wikimedia refuses httpx requests without one).
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

*The first four were answered on 22 Sept 2026 from the Vercel docs, the changelog and the builder source
(`vercel/vercel`, `@vercel/python` 14.x, `vercel-runtime` 0.23): three researchers, then a skeptic who
re-checked every design-driving claim (109 confirmed, 4 corrected, 2 left open). Re-check at the first real
deployment: the public source can lag what the build machines run.*

- ~~Python pin / dependency file / lifespan~~ — **answered:** 3.12 is the default (3.13, 3.14 available; the
  default is announced to move to 3.14), pinned with `app/.python-version`, which wins over `requires-python`.
  Dependencies come from `pyproject.toml`, installed with uv (`uv sync --no-dev`, extras not installed); a
  `requirements.txt` next to it is ignored. **`lifespan` runs** before the first request, under Vercel's own
  uvicorn, and a raising lifespan stops the server. Open: whether a Hobby instance serves concurrent requests
  (the code is safe either way).
- ~~`maxDuration` / body limit / `/tmp`~~ — **answered:** Fluid compute (on by default for new projects): Hobby
  300 s default and maximum, Pro 300 s default and 800 s maximum; without Fluid 60 s / 300 s. Request and
  response bodies 4.5 MB (413 above). `/tmp` 500 MB, per instance, shared by concurrent requests. Memory on
  Hobby fixed at 2 GB / 1 vCPU. Runtime logs 1 h on Hobby, 1 day on Pro.
- ~~Vercel Blob private access from Python~~ — **answered:** private stores are GA on all plans since 30 June
  2026; a private blob is a plain `GET https://<store>.private.blob.vercel-storage.com/<path>` with
  `Authorization: Bearer <BLOB_READ_WRITE_TOKEN>`; the store id is in the token. The Python SDK (`vercel` on
  PyPI) is not needed and has no OIDC; OIDC tokens arrive per request, so they cannot serve a cold start.
  No read-only token exists: the read-write token can delete the store.
- ~~Deployment Protection~~ — **answered:** new projects get Standard Protection (previews and generated URLs
  behind Vercel Authentication; the production domain public); "All Deployments" is free on every plan since
  9 Sept 2026, but on Hobby admits only the owner, one external user and one shareable link. Password
  Protection is not on Hobby ($20 per project per month on Pro) — the app's own gate (E2, D5) is the login.
  Only the repository owner can connect the GitHub integration; CLI deploys need nothing from him.
- GLEIF full-text search filter name (`filter[fulltext]`) and its ranking; `filter[entity.legalName]` behaviour
  with partial names.
- ECB list reuse terms before bundling anything (E4b); Wikimedia `User-Agent` policy wording (E5) — *seen in
  practice on 22 Sept 2026: Wikimedia answered httpx requests without a contact in the User-Agent with 403
  "Please respect our robot policy" and with 200 once the repository URL was in it (curl passed either way);
  the default `WEB_USER_AGENT` carries it now.*
- Neon Postgres via Vercel Marketplace: connection pooling for serverless (use the pooled DSN).
