# Dataset Overview

## Data sources

| Source | What | File |
|--------|------|------|
| **UCR** (ucr.irmct.org) | Case numbers, transcripts | `scraper/ucr_cases.html` -> `scraper/ucr_base_cases.csv` |
| **ICTY** (icty.org) | Case slugs, CIS PDFs | `scraper/icty_cases.html` -> `scraper/icty_cases.csv` |

- `ucr_base_cases.csv` — 109 unique IT-* base case numbers with names, extracted from the UCR search dropdown.
- `icty_cases.csv` — 96 slug-to-case-number mappings, extracted from the icty.org case number dropdown.

## Case mapping

`scraper/case_mapping.csv` — merged view of both sources (109 rows).

| Column | Description |
|--------|-------------|
| `case_number` | UCR case ID (e.g. IT-94-1) — authoritative |
| `name` | Case name from UCR |
| `icty_slug` | Matching icty.org slug(s), if any |
| `has_cis` | Whether a CIS PDF was found on icty.org |
| `folder` | Unique folder name in the archive |

**94** case numbers match an ICTY slug (after manually fixing Dokmanovic IT-95-13/IT-95-13a).
**15** UCR-only cases have no ICTY slug (no CIS possible):

| Case number | Name | Reason |
|-------------|------|--------|
| IT-00-39&40 | Krajisnik and Plavsic | Joint case; split into IT-00-39 + IT-00-40 on icty.org |
| IT-00-40 | Plavsic | Listed as IT-00-39&40/1 on icty.org |
| IT-01-50 | Milosevic | Pre-joinder; merged into IT-02-54 |
| IT-01-51 | Milosevic | Pre-joinder; merged into IT-02-54 |
| IT-02-55 | Miscellaneous 6 | Administrative, no case page |
| IT-04-80 | Tolimir | Listed as IT-05-88/2 on icty.org |
| IT-95-3 | Borovnica | Early case, withdrawn/dropped |
| IT-95-5 | Karadzic and Mladic | Superseded by IT-95-5/18 |
| IT-95-6 | Deferral Proceedings | Procedural, no case page |
| IT-95-18 | Karadzic and Mladic | Superseded by IT-95-5/18 |
| IT-96-19 | Djukic | Duplicate; same person as IT-96-20 |
| IT-96-23 | Kunarac et al. | Superseded by IT-96-23&23/1 |
| IT-98-30 | Kvocka et al. | Listed as IT-98-30/1 on icty.org |
| IT-98-33/2 | Pandurevic | Listed as IT-05-86 on icty.org |
| IT-99-37 | Milutinovic et al. | Superseded by IT-05-87 |

Note: IT-95-13 (Dokmanovic) was initially unmatched because icty.org lists it as IT-95-13a. Fixed manually in the mapping.

## Archive: `archive_250625/`

Scraped 2025-06-25 using `scraper/icty_scraper_v2.py`.

- **Transcripts**: downloaded from UCR API (English only, all text-based formats). Filtered to `DocTypeDesc=="Transcript"`, excluding audio/video.
- **CIS files**: downloaded from icty.org using ICTY slugs. Three URL patterns tried: `cis_{slug}.pdf`, `cis_{slug}_en.pdf`, plus page-link scraping fallback. Four additional files found with hyphen pattern `cis-{name}.pdf`.

### Contents

| Type | Count | Notes |
|------|-------|-------|
| DOC transcript files | 10,749 | Main bulk |
| DOCX transcript files | 1 | Single file (Perisic) |
| TIF transcript files | 193 | Scanned images, early cases — not yet parsed (need OCR) |
| PDF transcript files | ~70 | Not yet parsed |
| CIS PDFs | 94 | Across 79 cases (some have both `_en` and non-`_en` versions) |
| Cases with transcripts | 96 | |
| Total case folders | 92 | |

### CIS coverage

- **78 cases** have `has_cis=yes` in the mapping
- **16 cases** have an icty.org slug but no CIS PDF (`has_cis=no`) — these are `_old`/pre-joinder case pages whose CIS data is in the joint-case CIS (needs manual verification)
- **15 cases** have no icty.org slug at all (see table above)
- **11 more CIS files** compared to the old archive, which had 69

### Transcripts vs old archive

The old archive had 16,514 DOC files across 82 cases. The new archive has 12,659 DOC/DOCX across 96 cases. The reduction is because the old scraper duplicated transcripts across related cases (e.g. IT-95-14 family: blaskic, aleksovski, kordic_cerkez all had 644 identical files). The new scraper queries UCR per case number, so each case only gets its own transcripts.

## Parsing pipeline

### CIS parsing

```bash
.venv/bin/python3 parser/cis_parser.py --archive-dir archive_250625/ --output-dir data_250625/cis_parsed
.venv/bin/python3 parser/extract_pleas.py --cis-dir data_250625/cis_parsed
```

- `cis_parser.py` — extracts structured metadata from CIS PDFs using pdfplumber. Outputs per-case JSON + `all_cases_full.json`.
- `extract_pleas.py` — reads `all_cases_full.json`, splits multi-defendant CIS by accused, extracts plea info. Outputs `pleas.csv`.
- Parser fix (2025-06-25): `find_defendants()` now also splits on `Convicted` appearing on the same line as a name (previously only `Found` was handled). This fixed the missing Boskoski entry.

### Pleas comparison

`parser/compare_pleas.py` — maps old pleas.csv (icty.org slugs) to new pleas.csv (folder names) via `case_mapping.csv` and diffs them.

```bash
.venv/bin/python3 parser/compare_pleas.py --old archive/cis_parsed/pleas.csv --new data_250625/cis_parsed/pleas.csv --mapping scraper/case_mapping.csv
```

Result (2025-06-25): 115 rows match, 22 need review (7 differences in existing cases, 15 from new CIS files). Output: `data_250625/cis_parsed/pleas_comparison.csv`.

### Transcript parsing

```bash
.venv/bin/python3 parser/run_parser.py --archive-dir archive_250625/ --output-dir data_250625/transcripts_parsed --txt-dir data_250625/transcripts_txt
```

- Converts DOC/DOCX to text via LibreOffice headless, then parses into structured JSON (speaker segmentation, Q/A linking, role classification).
- Skips French transcripts (`Compte_rendu*`) and correction files.
- TIF and PDF transcripts are not yet handled (need OCR / separate conversion).

#### Speaker detection

A speaker label is recognized by `SPEAKER_RE` matching a line of the form `LABEL:` (with optional `[Interpretation]` tag). Recognized label patterns:

| Pattern | Examples |
|---------|----------|
| `JUDGE <name>` | `JUDGE ORIE`, `JUDGE KARIBI WHYTE` |
| `THE PRESIDING JUDGE` | |
| `MR. <name>` (up to 3 words) | `MR. NICE`, `MR. DI FAZIO`, `MR. J. SIMIC` |
| `MS. <name>` / `MRS. <name>` / `MISS <name>` | `MS. KORNER`, `MISS HOLLIS` |
| `DR. <name>` | `DR. ZAKIC` |
| `THE WITNESS` | |
| `THE REGISTRAR` / `THE REGISTRY` | |
| `THE INTERPRETER` / `THE FRENCH INTERPRETER` | |
| `THE COURT REPORTER` / `THE USHER` | |
| `THE ACCUSED` / `THE ACCUSED <name>` | `THE ACCUSED`, `THE ACCUSED PRALJAK` |
| `THE APPELLANT` / `THE APPELLANT <name>` | `THE APPELLANT KORDIC` |
| `THE PROSECUTION` | |

Anything not matching these patterns is treated as continuation text of the previous speaker.

#### Role classification

Each utterance gets a `role` field. Classification is done in two stages:

**Stage 1 — Speaker label (`classify_role()`):**

| Condition | Role |
|-----------|------|
| Starts with `JUDGE` or equals `THE PRESIDING JUDGE` / `THE COURT` | `judge` |
| Equals `THE WITNESS` | `witness` |
| Equals `THE REGISTRAR` / `THE REGISTRY` | `registrar` |
| Equals `THE INTERPRETER` / `THE FRENCH INTERPRETER` | `interpreter` |
| Equals `THE USHER` / `THE COURT REPORTER` | `procedural` |
| Equals `THE ACCUSED` / `THE APPELLANT` (+ optional name) | `accused` |
| Equals `THE PROSECUTION` | `prosecution` |
| Starts with `DR.` | `counsel` |
| Everything else (`MR.`, `MS.`, `MRS.`, `MISS`, named speakers) | `counsel` |

**Important:** The `role` field reflects the speaker label pattern, not the person's actual position. An accused person addressed as `MR. ALEKSOVSKI` gets `role=counsel` even though he is the defendant. Use the `is_accused` field (see below) to identify utterances by the accused regardless of their role label.

**Stage 2 — Q/A context override:**

During examination sections, `Q.` and `A.` lines override the speaker and role:

- `Q.` line → speaker = current examiner, role = examiner's `classify_role()` (usually `counsel`)
- `A.` line → speaker = current witness name (from `WITNESS:` declaration), role = `witness`

The examiner is set by examination headers like `"Cross-examination by Mr. Nice:"` or `"Questioned by the Court:"`. If no examination header has been seen, the most recent counsel speaker (`MR./MS.` etc.) is used as fallback.

#### `is_accused` field

Each utterance has a boolean `is_accused` field that identifies whether the speaker is one of the accused persons in that case, regardless of their functional role. This separates identity (who the person is) from role (what they are doing when speaking).

Accused names are loaded from two sources:
- **`pleas.csv`** — primary source for cases with CIS data (78 cases)
- **`case_mapping.csv`** — fallback for cases without CIS data, extracting names from the `name` column (31 additional cases)

Matching rules (applied in order, checked by `check_is_accused()`):

| Condition | Result | Example |
|-----------|--------|---------|
| Speaker is `THE ACCUSED` / `THE ACCUSED <name>` | always `true` | `THE ACCUSED PRALJAK` |
| Speaker is `THE APPELLANT` / `THE APPELLANT <name>` | always `true` | `THE APPELLANT KORDIC` |
| Speaker is `MR. <lastname>` and lastname matches an accused | `true` | `MR. MILOSEVIC` (self-representing) |
| Speaker is `MS.` / `MRS.` / `MISS` | always `false` | `MS. NIKOLIC` (counsel, not the accused Nikolic) |
| Speaker's full name (after stripping titles like GENERAL, DR.) matches an accused | `true` | `TIHOMIR BLASKIC (Resumed)` |
| Otherwise | `false` | |

All ICTY accused are male, so the `MS./MRS./MISS` exclusion prevents false positives with female counsel who share a surname with an accused.

#### Examination type tracking

Examination headers are detected by `EXAM_HEADER_RE`, matching both noun and past-participle forms:

- `Examination by Mr. Zivanovic:` / `Examined by Mr. Zivanovic:`
- `Cross-examination by Ms. Nikolic: [Continued]` / `Cross-examined by Ms. Nikolic:`
- `Further re-examination by Mr. Karadzic:`
- `Questioned by the Court:` / `Questioned by Judge Orie:`
- `Questioned by the Accused Praljak:`

Recognized examiner patterns: `Mr./Ms./Mrs./Miss/Dr. <name>`, `Judge <name>`, `the Court`, `the Accused`, `Assigned Counsel`.

Supported suffixes: `[Continued]`, `(Continued)`, `[Cont'd]`, `[Contd.]`, `[Resumed]`, `[Interpretation]`. Trailing colon or period is optional.

Each question utterance gets an `examination` field with one of: `direct`, `cross`, `re-direct`, `re-cross`, or the raw string if unrecognized.

Results (2025-06-29, after parser fixes):

| Metric | Value |
|--------|-------|
| Files parsed | 12,651 / 12,651 (0 failed) |
| Cases with transcripts | 96 (of 108 folders) |
| Total utterances | 6,071,387 |
| Null dates | 2 |
| `is_accused=True` utterances | 384,299 |
| UNKNOWN EXAMINER utterances | 441 |

Each utterance includes an `is_accused` boolean field, derived from matching speaker names against accused names in `pleas.csv` and `case_mapping.csv`. This identifies utterances by the accused regardless of their functional role (testifying as witness, cross-examining as self-represented counsel, or making statements).

12 case folders have empty `transcripts/` directories (no DOC/DOCX files on UCR):

| Folder | Case number |
|--------|-------------|
| `_statistics` | — (not a case) |
| `banovic` | IT-95-8/1 |
| `bobetko` | IT-04-81 |
| `jokic_01-42_1` | IT-01-42/1 |
| `karadzic_and_mladic_95-18` | IT-95-18 |
| `kunarac_et_al_96-23_23_1` | IT-96-23&23/1 |
| `kvocka_et_al_98-30_1` | IT-98-30/1 |
| `marinic` | IT-95-15 |
| `plavsic_00-39_40_1` | IT-00-39&40/1 |
| `rasevic_todovic` | IT-97-25/1 |
| `raznatovic` | IT-97-27 |
| `talic` | IT-99-36/1 |

Possible reasons: case was superseded by a joint case number (transcripts filed under the joint case), accused died or charges were withdrawn before trial, quick guilty plea with no transcript files uploaded, or case was transferred to another jurisdiction.

### Dataset creation

`dataset_creation/chunk_extracton.py` — extracts utterances spoken by the accused from parsed transcript JSONs. Uses the `is_accused` flag computed by the parser (from `pleas.csv` + `case_mapping.csv` + speaker patterns). Outputs `accused_utterances.csv` and `accused_utterances.pkl`.

```bash
.venv/bin/python3 dataset_creation/chunk_extracton.py
```
