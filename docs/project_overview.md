# ICTY Archive — Project Overview

## Project Structure

```
ICTY/
  docs/                      # Documentation
    transcript_analysis.md
  scraper/                   # Web scraper for ICTY archive
    icty_scraper.py
    ICTY_SCRAPER_README.md
  parser/                    # Transcript and CIS parsers
    icty_parser.py           # Transcript parser (.doc → structured JSON)
    run_parser.py            # Transcript parser CLI runner
    cis_parser.py            # Case Information Sheet parser (PDF → JSON/CSV)
  data/                      # All generated data (gitignored)
    archive/                 # Raw scraped files (.doc/.tif/.pdf) — 8.8 GB
    converted_txt/           # Cached .txt conversions — 1.8 GB
    parsed/                  # Structured JSON output — 2.6 GB
    cis_parsed/              # Parsed Case Information Sheets — ~2 MB
  logs/                      # Parse logs and reports (gitignored)
  .venv/                     # Python 3.12 virtual environment (gitignored)
```

## Overview

The `data/archive/` folder (8.8 GB) contains scraped documents from the International Criminal Tribunal for the former Yugoslavia (ICTY). It is organized into **85 case folders** (named after defendants), each containing subfolders for different document types: `transcripts`, `judgements`, `decisions_orders`, `indictments`, `press_releases`, `case_info_sheets`, `briefs_motions`, and `other_filings`.

**3 cases have no transcripts:** `bobetko`, `marini_zoran`, `zeljko_raznatovic` (plus the `_statistics` folder, which is metadata).

## Transcript Statistics

| Metric | Value |
|---|---|
| Total transcript files | 16,721 |
| `.doc` files | 16,513 (98.8%) |
| `.tif` files (scanned images) | 202 (1.2%) |
| `.pdf` files | 5 |
| `.docx` files | 1 |
| Cases with transcripts | 82 |

### Largest cases by transcript count

| Case | Files |
|---|---|
| Tolimir | 714 |
| Popovic | 714 |
| Kordic/Cerkez | 655 |
| Blaskic | 655 |
| Aleksovski | 655 |
| Prlic | 568 |
| Karadzic | 556 |
| Mladic | 543 |
| Milutinovic | 531 |
| Djordjevic | 531 |
| Slobodan Milosevic | 494 |

## File Formats

### `.doc` files (vast majority)
Machine-readable Microsoft Word documents (Composite Document File V2). These can be converted to plain text via LibreOffice. They contain the verbatim court transcript text.

### `.tif` files (early cases)
Scanned page images (TIFF, bi-level Group 4 compression, 2472×3507 pixels). These are **not machine-readable** without OCR. Found in 12 cases, predominantly early proceedings (1995–1996):
- `aleksovski`, `blaskic`, `djukic`, `dokmanovic`, `dragan_nikolic`, `erdemovic`, `karadzic`, `kordic_cerkez`, `martic`, `mrksic`, `mucic`, `tadic`
- `tadic` has the most: 116 `.tif` files (alongside 145 `.doc` files)

Most cases with `.tif` files also have `.doc` files — the `.tif` files tend to represent the earliest hearings (draft transcripts from 1995–1996) while `.doc` files cover later proceedings.

## Transcript Content Structure

All `.doc` transcripts follow the standard ICTY verbatim court reporting format, though with **two distinct layout eras**:

### Early format (roughly 1996–2002)
- Numbered lines (1–25 per page), but with **wider indentation** and slightly different spacing
- Header block at the start with case metadata: tribunal name, case number, date, chamber, judge names, counsel appearances
- Example (Erdemovic, 1998):
  ```
           1                      Wednesday, 14th January 1998
           2            (2.45 pm)
           3            JUDGE MUMBA:  Good afternoon, ladies and
           4  gentlemen.  I would like to check first of all if the
           5  interpreters are all ready?  Yes, they are.
  ```

### Later format (roughly 2003 onward)
- More compact, consistent 25-lines-per-page layout
- Date on line 1, session info on line 2, then proceedings
- Example (Mladic, 2016):
  ```
   1                           Tuesday, 1 March 2016
   2                           [Open session]
   3                           [The accused entered court]
   4                           --- Upon commencing at 9.30 a.m.
   5             JUDGE ORIE:  Good morning to everyone in around this courtroom.
  ```

### Common elements across all transcripts

- **Speaker labels** in uppercase: `JUDGE [NAME]:`, `MR./MS. [NAME]:`, `THE WITNESS:`, `THE REGISTRAR:`, `THE INTERPRETER:`, `THE ACCUSED:`
- **Examination markers**: `Q.` (question) and `A.` (answer) for witness testimony
- **Procedural annotations** in square brackets: `[Open session]`, `[Closed session]`, `[Private session]`, `[The witness entered court]`, `[The accused entered court]`, `[The Chambre de première instance se concerte]`, `[Interpretation]`
- **Session breaks**: `--- Upon commencing at ...`, `--- Recess taken at ...`, `--- On resuming at ...`, `(The hearing adjourned)`
- **Page numbering**: lines restart 1–25 on each page
- **Exhibit references**: e.g., `P797`, `D123`
- **Transcript cross-references**: references to other transcript page numbers

## Languages

Most transcripts are in **English**. A subset of transcripts exists in **French**, found in 6 cases:
- `dragomir_milosevic`, `galic`, `limaj`, `oric`, `slobodan_milosevic`, `zupljanin_stanisicm`

French transcripts are identifiable by filename prefix `Compte_rendu_d'audience_publique_du_...` and follow the same structural conventions but in French (e.g., `M. LE JUGE ROBINSON : [interprétation]`, `L'ACCUSÉ : [interprétation]`, `[Audience publique]`).

## Filename Conventions

Several naming patterns exist:

| Pattern | Example | Prevalence |
|---|---|---|
| `Public_Transcript_of_Hearing_DD_Month_YYYY.doc` | `Public_Transcript_of_Hearing_01_March_2016.doc` | Most common (modern) |
| `Public_Transcript_of_Hearing_Month_DDth,_YYYY.doc` | `Public_Transcript_of_Hearing_January_14th,_1998.doc` | Older cases |
| `Draft_transcript_of_the_hearing_of_DDth_Month_YYYY.tif` | `Draft_transcript_of_the_hearing_of_25th_July_1995_(afternoon).tif` | Early scanned drafts |
| `Compte_rendu_d'audience_publique_du_DD_month_YYYY.doc` | `Compte_rendu_d'audience_publique_du_22_février_2006.doc` | French transcripts |
| `Transcript_of_...` | Various | Rare variants |
| `Corrections_of_the_transcript_...` | Various | Transcript corrections |

Some filenames include a hash suffix (e.g., `_f8e485`) — likely a deduplication artifact from scraping when multiple versions of the same hearing exist.

## Parser

A Python parser (`parser/icty_parser.py` + `parser/run_parser.py`) converts the English `.doc` transcripts into structured JSON. It processes all `.doc` files, skipping French transcripts (`Compte_rendu_*`), correction files (`Corrections_*`), `.tif`, and `.pdf`.

### Pipeline

1. **File discovery** — walks `data/archive/*/transcripts/`, filters eligible `.doc` files
2. **Text extraction** — converts `.doc` → `.txt` via LibreOffice headless (cached in `data/converted_txt/{case}/`)
3. **Line number stripping** — removes court-reporter page/line numbers (1–25 per page)
4. **Speaker segmentation** — state machine identifies speakers by labels (`JUDGE X:`, `MR. X:`, `THE WITNESS:`, etc.)
5. **Q/A linking** — tracks examination headers to attribute `Q.` to the examiner, `A.` to the witness, and links each answer to its question via `reply_to`
6. **Role classification** — maps speakers to roles: `judge`, `witness`, `counsel`, `registrar`, `interpreter`, `accused`

### Output format

One pretty-printed `.json` file per hearing, organized in case folders:

```
data/parsed/{case_slug}/{transcript_name}.json
```

Each file contains: `case`, `case_id`, `date`, `source_file`, `utterances[]`, `procedural_notes[]`.

Each utterance has: `id`, `speaker`, `role`, `type` (statement/question/answer), `text`, and optionally `examination` (direct/cross/re-direct/re-cross) and `reply_to` (utterance id of the corresponding question).

### Full corpus parse results

| Metric | Value |
|---|---|
| Files processed | 16,505 / 16,505 (0 failures) |
| Cases | 82 |
| Total utterances | 7,885,346 |
| Questions | 2,634,269 |
| Answers | 2,505,896 |
| Q/A link rate | 99.4% (see below) |
| Procedural notes | 736,622 |
| Processing time | ~38 min (first run with conversion) |
| Re-parse time (cached txt) | seconds |

### Caching

Converted `.txt` files are saved in `data/converted_txt/{case}/`. On subsequent runs, the parser reads from cache and skips LibreOffice conversion, making re-runs near-instant.

### Usage

```bash
cd parser
source ../.venv/bin/activate
python run_parser.py                          # Parse all cases
python run_parser.py --cases mladic karadzic   # Parse specific cases
python run_parser.py --limit 10                # First 10 files only
```

### Q/A link rate explained

The Q/A link rate (99.4%) measures how many utterances tagged as `type: "answer"` have a `reply_to` field pointing to the corresponding question. Out of 2,505,896 answers, ~15,000 are unlinked — meaning the parser found an `A.` marker but couldn't determine which `Q.` it belongs to. This can happen when:

- A `Q.` was never detected before the first `A.` (e.g., examination starts without a header, or the question was missed due to unusual formatting)
- A judge interjection contains an `A.` that gets misclassified as an answer

This metric does **not** reflect overall parsing quality — speaker separation works on all 16,505 files with zero failures. The unlinked edge cases need further examination to determine if they can be fixed or are inherent ambiguities in the source text.

### Known limitations

- `MR. ERDEMOVIC` (accused addressed as `MR.` in early transcripts) is classified as `counsel` instead of `accused`
- Counsel side (`prosecution` vs `defence`) is not yet resolved for `MR./MS.` speakers — currently all classified as `counsel`
- ~0.6% of answers not linked to a question (mostly due to judge interjections or unusual formatting)

## Case Information Sheet (CIS) Parser

A separate parser (`parser/cis_parser.py`) extracts structured metadata from the **Case Information Sheet** PDFs stored in `data/archive/*/case_info_sheets/`. These are official ICTY summary documents that contain key facts about each case.

### Pipeline

1. **PDF discovery** — walks `data/archive/*/case_info_sheets/`, selects files matching `cis_*.pdf`
2. **Text extraction** — uses pdfplumber to extract text page by page
3. **Field extraction** — regex-based extraction of defendant names, case IDs, charges, outcomes, sentences, key dates, trial statistics, related cases, and more
4. **Output** — one JSON per case, plus combined outputs (full JSON, summary JSON, CSV)

### Usage

```bash
cd parser
source ../.venv/bin/activate
pip install pdfplumber                          # Additional dependency
python cis_parser.py                            # Parse all CIS PDFs
python cis_parser.py --archive-dir data/archive --output-dir data/cis_parsed
```

### Output format

Output is written to `data/cis_parsed/`:

- **69 individual JSON files** (`{case_slug}_cis.json`) — one per case, without full text
- `all_cases_full.json` — combined JSON with full extracted text (for downstream NLP)
- `all_cases_summary.json` — combined JSON without full text (for quick loading)
- `all_cases.csv` — flat CSV for statistical analysis
- `report.md` — summary report with aggregate statistics

Each per-case JSON contains: `case_slug`, `case_id`, `case_name`, `defendant_name`, `defendants[]` (for multi-defendant cases), `plea`, `trial_outcome`, `final_outcome`, `sentence_years`, `life_sentence`, `charge_categories[]`, `crime_types[]`, key dates (`born`, `indictment_date`, `arrested_date`, `trial_judgement`, `appeals_judgement`, etc.), trial statistics (`trial_days`, `prosecution_witnesses`, `defence_witnesses`, etc.), and `related_cases[]`.

### CIS corpus results

| Metric | Value |
|---|---|
| Cases parsed | 69 / 85 case folders |
| Convicted | 45 |
| Acquitted | 15 |
| Deceased (before trial) | 5 |
| Guilty pleas | 22 |
| Life sentences | 8 |
| Mean sentence (determinate) | 16.7 years |
| Multi-defendant cases | 12 |

The 16 missing cases are duplicate/legacy folders, the `_statistics` metadata folder, or co-defendant cases covered by the joint CIS of their parent trial.

### Known limitations

- CIS PDFs come in at least 4 different formats spanning ~20 years; extraction is not fully consistent
- 27 cases use a newer PDF format that does not include geographic cluster names
- Some fields may be missing or imprecise — see `data/cis_parsed/report.md` for per-field completeness statistics
- Multi-defendant case outcomes reflect the primary defendant; individual defendant outcomes are in the `defendants[]` array

## Unprocessed files (future work)

1. **OCR needed for `.tif` files**: 202 files (mostly early Tadic case) are scanned images and require OCR to extract text.
2. **French transcripts**: Present in 6 cases, skipped by the parser. Need separate handling or translation.
3. **Redacted sections**: Some transcripts contain redacted passages (closed/private sessions), marked as e.g., `Pages 44 to 60 – redacted – closed session.` These are captured as procedural notes.
4. **Duplicate/variant files**: Some hearings have multiple file versions (hash suffixes) — both are processed.
5. **Encoding**: `.doc` files use Code page 1252 (Windows Western European), converted to UTF-8 by LibreOffice.
