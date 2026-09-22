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
| `source` | where the description came from |

## Measuring

```bash
python -m core.classify --golden
```

Reports **recall@k** per codebook: the share of cases whose correct code reached the
shortlist at all. That grades the pre-filter, and it is the ceiling on everything
downstream — a code the filter never offers is one the classifier can never return.

Runs with no API key. Once the classifier exists, the same file grades top-1 and top-3
accuracy; **top-3 is the number that matters**, because MO picks from three.

## Cases worth keeping

The current set exists because each one traps a specific failure:

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
