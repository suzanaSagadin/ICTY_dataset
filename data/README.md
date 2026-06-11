# ICTY Data

Court documents from the **International Criminal Tribunal for the former Yugoslavia (ICTY)**, scraped from the [IRMCT Unified Court Records](https://ucr.irmct.org/) and [icty.org](https://www.icty.org/). The English hearing transcripts have been parsed into structured JSON, and the Case Information Sheets into structured JSON/CSV.

The dataset covers **82 cases** and **16,505 parsed English hearing transcripts** spanning from 1996 to 2017, plus structured metadata for **69 cases** extracted from Case Information Sheet PDFs.

For detailed documentation on the archive structure, transcript formats, parser pipeline, output schemas, and known limitations, see the [project overview](https://github.com/suzanaSagadin/ICTY_dataset/blob/main/docs/project_overview.md). The source code for scraping and parsing is in the same [GitHub repository](https://github.com/suzanaSagadin/ICTY_dataset).

## Folder structure

```
data/
  archive/              Raw scraped documents (~8.8 GB)
  converted_txt/        Intermediate plain-text conversions (~1.8 GB)
  parsed/               Structured JSON output (~2.6 GB)
  cis_parsed/           Parsed Case Information Sheets (~2 MB)
```

### `archive/`

Raw files downloaded from the ICTY/IRMCT online archives. Organised into **85 case folders** (named after defendants), each containing subfolders by document type:

- `transcripts/` — verbatim hearing transcripts (.doc, .tif, .pdf)
- `judgements/`
- `decisions_orders/`
- `indictments/`
- `briefs_motions/`
- `press_releases/`
- `case_info_sheets/`
- `other_filings/`

There is also an `_statistics/` folder with scrape metadata (`scrape_report.json`, `scrape_report.csv`, `scrape_summary.txt`, `checkpoint.json`).

**Transcript file breakdown:** 16,513 `.doc` | 202 `.tif` (scanned images, no OCR) | 5 `.pdf` | 1 `.docx`

Three case folders have no transcripts: `bobetko`, `marini_zoran`, `zeljko_raznatovic`.

### `converted_txt/`

Plain-text versions of the `.doc` transcripts, converted via LibreOffice headless. One `.txt` file per `.doc`, mirroring the case folder structure. These serve as a cache so the parser can re-run without repeating the slow LibreOffice conversion step.

Encoding: UTF-8 (source `.doc` files use Windows-1252).

### `parsed/`

One structured JSON file per English hearing transcript, organised in case folders:

```
parsed/{case_slug}/{transcript_name}.json
```

Each JSON file contains:

| Field | Description |
|---|---|
| `case` | Case name |
| `case_id` | Standardised case identifier |
| `date` | Hearing date |
| `source_file` | Original `.doc` filename |
| `utterances[]` | Array of speaker turns (see below) |
| `procedural_notes[]` | Bracketed annotations like `[Open session]`, `[The witness entered court]` |

Each utterance has:

| Field | Description |
|---|---|
| `id` | Sequential utterance ID within the transcript |
| `speaker` | Speaker name as it appears in the transcript |
| `role` | One of: `judge`, `witness`, `counsel`, `registrar`, `interpreter`, `accused` |
| `type` | `statement`, `question`, or `answer` |
| `text` | The spoken text |
| `examination` | *(optional)* `direct`, `cross`, `re-direct`, or `re-cross` |
| `reply_to` | *(optional)* ID of the question this answer responds to |

**Corpus totals:** 7,885,346 utterances | 2,634,269 questions | 2,505,896 answers | 736,622 procedural notes | 99.4% question-answer link rate.

### `cis_parsed/`

Structured metadata extracted from the **Case Information Sheet (CIS)** PDFs found in `archive/*/case_info_sheets/`. These are official ICTY summary documents that contain key facts about each case: defendant names, charges, outcomes, sentences, key dates, trial statistics, and related cases.

Produced by `parser/cis_parser.py` using pdfplumber text extraction. Contains:

- **69 individual JSON files** (`{case_slug}_cis.json`) — one per case
- `all_cases_full.json` — combined JSON with full extracted text (for NLP)
- `all_cases_summary.json` — combined JSON without full text (for quick loading)
- `all_cases.csv` — flat CSV for statistical analysis
- `pleas.csv` — one row per accused (116 accused) with their individual
  **initial** plea; later plea changes (e.g. not guilty → guilty under a plea
  agreement) are recorded in the `note` column. Produced by
  `parser/extract_pleas.py` (awaiting manual revision, see `TODO_data.md`)
- `report.md` — summary report with aggregate statistics

Each per-case JSON contains:

| Field | Description |
|---|---|
| `case_slug` | Case folder name |
| `case_id` | ICTY case number (e.g. `IT-96-22`) |
| `case_name` | Geographic/operation name (e.g. `PILICA FARM`) |
| `defendant_name` | Primary defendant |
| `defendants[]` | All defendants (for multi-defendant cases) |
| `plea` | `guilty` or `not guilty` — case-level; for the plea of each individual accused use `pleas.csv` |
| `trial_outcome` | Trial chamber result |
| `final_outcome` | Final result after appeals (`convicted`, `acquitted`, `deceased`, `withdrawn`) |
| `sentence_years` | Length of sentence in years (null for life/acquittal) |
| `life_sentence` | Boolean |
| `charge_categories[]` | e.g. `genocide`, `crimes against humanity` |
| `crime_types[]` | e.g. `murder`, `torture`, `deportation` |
| Key dates | `born`, `indictment_date`, `arrested_date`, `trial_judgement`, `appeals_judgement`, etc. |
| Trial statistics | `trial_days`, `prosecution_witnesses`, `defence_witnesses`, `prosecution_exhibits`, etc. |
| `related_cases[]` | Cross-references to related ICTY cases |

**Note on data quality:** The CIS PDFs come in at least 4 different formats spanning ~20 years. Extraction is not fully consistent across all cases — some fields may be missing or imprecise. Always verify against the original PDFs for authoritative data. See `report.md` for detailed completeness statistics.

## How the data was created

### 1. Scraping

A Python scraper (`scraper/icty_scraper.py`) downloaded documents from the UCR (Unified Court Records) API at `ucr.irmct.org` and supplemented with press releases and case information sheets from `icty.org`. The scraper walks all ICTY cases, classifies documents by type, and saves them into the `archive/` folder structure.

### 2. Text conversion

The parser batch-converts `.doc` files to `.txt` using LibreOffice in headless mode. Only `.doc` files are converted; `.tif` scans (202 files from early cases, mostly Tadic 1995-96) would require OCR and are not processed. French transcripts (identifiable by `Compte_rendu_` filename prefix, present in 6 cases) and correction files (`Corrections_*`) are skipped.

### 3. Parsing

The parser (`parser/icty_parser.py`) processes the plain text through several stages:

1. **Line number stripping** — removes court-reporter page/line numbers (1-25 per page)
2. **Speaker segmentation** — identifies speakers by label patterns (`JUDGE X:`, `MR. X:`, `THE WITNESS:`, etc.)
3. **Q/A linking** — tracks examination context to attribute `Q.` lines to the examiner and `A.` lines to the witness, linking each answer back to its question
4. **Role classification** — maps speaker names to roles (judge, witness, counsel, etc.)

The full corpus parse (16,505 files, 82 cases) takes roughly 38 minutes on the first run (dominated by LibreOffice conversion). Re-parsing from the cached `.txt` files takes seconds.

### 4. CIS parsing

A separate parser (`parser/cis_parser.py`) extracts structured metadata from the Case Information Sheet PDFs using pdfplumber. It reads PDFs from `archive/*/case_info_sheets/`, extracts fields like defendant names, charges, outcomes, sentences, key dates, and trial statistics via regex patterns, and writes per-case JSON files plus combined outputs (full JSON, summary JSON, CSV) to `cis_parsed/`.

69 out of 85 case folders have a parseable CIS PDF. Of the remaining 16, seven are duplicate/legacy folders, eight are co-defendant cases covered by the joint CIS of their parent trial (e.g. `beara` by `popovic`), and one (`sikirica`) has no CIS PDF in the archive at all — its accused are missing from the CIS data entirely.

A follow-up extraction (`parser/extract_pleas.py`) resolves the joint cases to individual accused: the 69 sheets cover **116 accused**, listed with their individual pleas in `pleas.csv`.

## Known limitations

The parsed data has several known limitations (unprocessed `.tif` scans, skipped French transcripts, role classification edge cases, CIS format inconsistencies, etc.). See the [project overview](https://github.com/suzanaSagadin/ICTY_dataset/blob/main/docs/project_overview.md) for the full list.

## Contact

Created by Suzana Sagadin with Claude Code. See the [GitHub repository](https://github.com/suzanaSagadin/ICTY_dataset) for full source code, documentation, and example data.
