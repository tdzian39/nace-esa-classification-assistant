# Codebooks

Place the four xlsx codebooks here (file names are configurable in `app/.env`):

| File | Columns | Purpose |
|---|---|---|
| `CTS_BA0036_NEW.xlsx` | `ID` (CTS ID), `VALUE` (ESA code), `DESCRIPTION` | CTS codebook for ESA 2010 sectors |
| `BA0036_2024_jen_validni.xlsx` | `Kód` (ESA code), `Název`, `Popis` | Only these leaf codes are valid in CTS; parents are rejected |
| `CTS_OKEC_NACE2.xlsx` | `ID` (CTS ID), `VALUE` (first 2 chars of NACE), `DESCRIPTION` | CTS codebook for 2-digit NACE |
| `NACE_STAT.xlsx` | `NACE` (2-digit), `Zkrtext`, `Text` | NACE labels; multiple rows per 2-digit code |

These files are bank-internal and are ignored by git. They are the codebook source today;
another source can replace the xlsx later without touching the models.

Verify them with (from the `app/` directory):

```bash
python -m core.codebooks
```
