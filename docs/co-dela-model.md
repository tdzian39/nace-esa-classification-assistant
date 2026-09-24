# Co dělá model (AI) v našeptávači NACE/ESA – a co stojí peníze

## Jednou větou

Model si přečte popis emitenta a ze **zkrácené nabídky kódů** vybere ten, který sedí nejlíp,
a napíše proč. Nic víc.

## Jak to probíhá

1. **Zadáte ISIN** (nebo název či popis činnosti).
2. **Nástroj bez AI sesbírá fakta:** kdo emitent je (registry GLEIF a OpenFIGI) a čím se živí
   (Wikipedie, nebo popis, který jste napsali vy).
3. **Nástroj bez AI zúží číselníky:** z 87 kódů NACE a 56 sektorů ESA nechá zhruba 12 + 12
   kandidátů, každého rovnou s jeho CTS ID.
4. **Teprve teď přijde na řadu model.** Dostane popis a těch 12 kandidátů a vybere
   nejvhodnější tři. U každého napíše jednu větu zdůvodnění a jak si je jistý.
5. **Vy rozhodnete.** Návrh zkontrolujete a kód do CTS zadáte sami.

## Co model **nedělá**

- **Nevymýšlí kódy.** Smí vybrat jen z nabídnutých kandidátů. Kód mimo nabídku nástroj zahodí.
- **Nevymýšlí CTS ID.** Ta bere nástroj vždy z číselníku, nikdy od modelu.
- **Nezapisuje do CTS.** Nikdy. Kód zadáváte vy.
- **Nehádá.** Když nemá dost podkladů, řekne „nevím“ a výběr nechá na vás.
- **Nevidí nic interního.** Dostane jen veřejné údaje: název emitenta, veřejný popis, údaje
  z veřejných registrů a názvy kódů z číselníku. Žádná klientská ani bankovní data.

## Co když model vypnu

Nástroj funguje dál, jen bez AI. Fakta, popis i zúžená nabídka kódů s CTS ID zůstanou.
Kód navrhne jen tam, kde to jasně určí pravidlo (např. „vládní instituce“ → NACE 84). Jinak
napíše „výběr je na vás“. Chybí jen zdůvodnění a volba mezi podobnými kandidáty.

## Co kolik stojí – každá akce

Peníze stojí **jediná věc: dotaz na model**. Jeden nový emitent = 2 dotazy (NACE a ESA) =
asi **0,0011 USD** (zhruba 1,10 USD za 1 000 emitentů). Všechno ostatní je zdarma, pokud
níže nestojí jinak.

### Na webové stránce

| Akce | Cena |
|---|---|
| Otevřít stránku | 0 |
| Hledat podle ISIN (registry emitenta najdou) | **~0,0011 USD** |
| ISIN + vlastní popis činnosti | **~0,0011 USD** |
| Jen popis činnosti (bez ISIN) | **~0,0011 USD** |
| Název + popis činnosti | **~0,0011 USD** |
| Jen název (bez ISIN a bez popisu) | 0 – model se neptá, nemá z čeho vybírat |
| ISIN, který registry neznají, a žádný popis | 0 – totéž |
| Neplatný ISIN a nic dalšího | 0 |
| Stejný dotaz znovu – **na vlastním počítači** | 0 – odpověď se vezme z paměti |
| Stejný dotaz znovu – **na Vercelu** | **~0,0011 USD znovu** – paměť odpovědí je tam vypnutá |
| Stejný emitent, ale jinak napsaný popis (stačí jedno slovo) | **~0,0011 USD** – pro nástroj je to nový dotaz |
| Stáhnout výsledek do Excelu – na vlastním počítači | 0 |
| Stáhnout výsledek do Excelu – **na Vercelu** | **~0,0011 USD** – stažení dotaz zopakuje |
| Model odpoví „nevím, málo podkladů“ | **~0,0011 USD** – dotaz proběhl, platí se i tak |
| Model je vypnutý, nestihne časový limit nebo je vyčerpaný denní strop | 0 – model se nezeptá |
| Stránky `/health` a `/probe` (kontrola provozu) | 0 |

### Na příkazové řádce (vývojář)

| Akce | Cena |
|---|---|
| `core.classify "popis"` – zúžení číselníků (i `--verbose`, `--estimate`) | 0 |
| `--golden` – měření bez modelu | 0 |
| `--golden --model` – měření s modelem, poprvé | **~0,05 USD** (46 emitentů × 2 dotazy) |
| `--golden --model` znovu, stejný model, stejný počítač | skoro 0 – z paměti |
| `--golden --model` s jiným modelem | platí se znovu, podle ceny toho modelu |
| `--golden-capture`, `--usage`, testy (`pytest`), kontroly kódu a číselníků | 0 |

### Provoz a změny nastavení

| Akce | Cena |
|---|---|
| Hosting na Vercelu, plán Hobby (dnes) | 0 – ale jen pro osobní, nekomerční použití |
| Nasazení nové verze | 0 |
| Uložení číselníků ve Vercel Blob | 0 v rámci limitu, který plán obsahuje (soubory mají pár set kB) |
| Registry GLEIF, OpenFIGI, Wikipedie, Wikidata | 0 – veřejné a zdarma |
| GitHub (repozitář, pull requesty) | 0 |
| Zapnout placené vyhledávání na webu (Brave, Bing, Google) | **placené za každé hledání** podle ceníku poskytovatele – dnes vypnuto |
| Vyměnit model za dražší | cena za emitenta se změní, např. Claude Sonnet 5 ≈ **0,011 USD** (asi 10× víc) |
| Vypnout model (`LLM_ENABLED=false`) | 0 – nástroj jede dál bez AI |
| Vývoj nástroje s Claude Code (tato konverzace) | podle vašeho předplatného Claude – mimo nástroj |

Cena za emitenta je průměr skutečných dotazů z 23. 9. 2026 (asi 4 800 vstupních a 150 výstupních
tokenů) při ceníku `gpt-5.6-luna`: 0,20 USD za milion vstupních a 1,20 USD za milion výstupních
tokenů. Skutečnou útratu ukazuje stránka Billing v účtu OpenAI.
