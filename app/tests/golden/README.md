# Golden set — verified issuers and their correct codes

This is how we tell whether the classifier works, and the only honest basis for choosing a
cheaper model. Without it, model selection is guesswork you pay for monthly.

## The rule that matters

**A case counts only when someone at the bank has confirmed both codes.** Until then
`verified_by` is `null` and the case is *provisional*.

Provisional cases are still useful — they exercise the machinery and encode the traps we
expect — but scoring reports the two populations separately and **no accuracy figure may be
quoted from provisional cases**. A golden set that silently mixes them is worse than no
golden set, because it manufactures confidence.

Every case shipped today is provisional, and the issuer names are fictional. Real issuers are
coming (roadmap E8, decided 22 Sept 2026): about thirty, built from ISINs and public sources
without asking MO. A real name is allowed because the case says what it is - codes worked out
from public sources, `verified_by` empty - and it stays provisional, never quoted as accuracy,
until someone has checked it against CTS.

## Verifying a case

1. Check how the issuer is coded in CTS (or have MO classify it as they normally would).
2. If that agrees with `expected_nace` / `expected_esa`, fill in:
   ```json
   "verified_by": "jméno / tým",
   "verified_on": "2026-10-01"
   ```
3. If they disagree, **change the expected code** and record why in `note`. A disagreement
   is the most valuable thing this file can capture.

Twenty to thirty verified cases is enough to choose a model with.

## Case format

| Field | Meaning |
|---|---|
| `id` | stable slug, appears in failure messages |
| `issuer` | issuer name as MO would type it |
| `description` | the activity text the classifier reasons over — required |
| `expected_nace` | correct 2-digit division, or omit to skip NACE grading |
| `expected_esa` | correct 7-digit BA0036 code, or omit |
| `verified_by` | who confirmed it; `null` means provisional |
| `verified_on` | ISO date of that confirmation |
| `note` | why the case is interesting — usually the trap it sets |
| `source` | where the description came from (`fictional` for the trap cases) |
| `isin` | a real, outstanding instrument of the issuer; marks a **real** case (E8) |
| `lei`, `country` | the issuer's LEI and legal-address country, a check on the recorded identity |
| `category` | bank, insurer, corp, vehicle, gov, supra, fund, fvc, agency |
| `confidence` | how sure the author of a provisional case is of the codes: high / medium / low |
| `evidence` | URLs behind the description and the codes |
| `nace_reasoning`, `esa_reasoning` | why those codes — so a checker sees the argument, not just the answer |
| `nace_alternatives`, `esa_alternatives` | codes a careful reviewer might defend instead |
| `depends_on_q7` | the ESA code rests on the control-axis convention below and changes under the other |

## Measuring

```bash
python -m core.classify --golden
```

Reports **recall@k** and the **deterministic top-1** per codebook, for the real issuers and
the fictional trap cases separately, verified and provisional apart: recall is the share of
cases whose correct code reached the shortlist at all. That grades the pre-filter, and it is
the ceiling on everything downstream — a code the filter never offers is one the classifier
can never return.

Runs with no API key, but needs the real codebooks (the CTS IDs are part of what is scored).
Once the classifier exists, the same file grades top-1 and top-3 accuracy; **top-3 is the
number that matters**, because MO picks from three.

A real case is scored the way the pipeline works: its description **plus the issuer's register
fact sheet** (GLEIF legal form, category, parents; OpenFIGI security type and sector). The
register answers are not fetched live - that would make the numbers drift and put the network
into the tests - but replayed from `identity.json`, captured once, trimmed to the fields the
parsers read and dated. To re-record them (live, throttled, a few minutes):

```bash
python -m core.classify --golden-capture
```

A request with no recorded answer stops the run with that message rather than scoring a
different fact sheet.

## The real issuers (roadmap E8, 22 Sept 2026)

About thirty real foreign issuers of the kinds MO sets up: sovereigns and a federal state and
a city, supranationals and a public development agency, banks (one publicly owned, one a
foreign-owned mortgage bank), insurers, corporates, captive financing vehicles and a finance
company that lends to customers, investment funds and money-market funds, and a securitisation
vehicle. They were researched from public sources - GLEIF, OpenFIGI, ESMA FIRDS, issuer
reports, the ECB lists of MFIs, investment funds and FVCs - and each one was re-checked by an
independent reviewer who tried to refute it. **Nobody at the bank has checked them against
CTS.** They stay provisional, `verified_by` empty, and no accuracy may be quoted from them.

Conventions the codes rest on, stated so a checker can overturn them wholesale if CTS differs:

- **ESA codes come from ČNB BA0036 version 044** (SDAT portal, Knihovna → Číselníky; the 56
  non-resident leaves it lists are the 56 in CTS).
- **Control axis (roadmap Q7):** judged from the issuer's own country, as ESA 2010 does —
  veřejné if its government controls it, pod zahraniční kontrolou if a unit resident in another
  country does, soukromé národní otherwise (BMW Finance N.V. in NL, owned by BMW AG in DE:
  foreign-controlled; widely held Deutsche Bank AG: national private). If CTS judges control
  from the Czech point of view instead, every case with `depends_on_q7` changes.
- **NACE:** Rev. 2 divisions; where CZ-NACE 2025 (Rev. 2.1) would differ, the reasoning says
  so (roadmap Q5).

## The fictional trap cases

Kept next to the real ones, because each traps a specific failure with a controlled text:

- **captive-funding-spv** — group name contains "Bank" but it is not a bank. The core ESA
  trap: `2002703` vs `2002213`.
- **captive-funding-spv-cs** — the same case in Czech, to measure what the pre-filter loses
  when the description is not in the codebook's language.
- **foreign-carmaker** — no financial vocabulary at all. Caught a real bug: the residual
  sector "Nefinanční podniky" was unreachable without a keyword hit, so the filter returned
  zero ESA candidates.
- **securitisation-vehicle** — says "asset-backed notes", not "securitisation". Caught the
  second real bug: prospectuses name the instrument, not the technique.
- **supranational-bank** — contains "bank" but belongs to its own sector, `2009031`.
- **retailer** — "Holdings" in the name, to check it is not dragged toward a holding-company
  classification.
