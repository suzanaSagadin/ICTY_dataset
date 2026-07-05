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
.venv/bin/python3 parser/compare_pleas.py --old archive/cis_parsed/pleas.csv --new data_250625/reports/pleas.csv --mapping scraper/case_mapping.csv
```

Result (2025-06-25): 115 rows match, 22 need review (7 differences in existing cases, 15 from new CIS files). Output: `data_250625/reports/pleas_comparison.csv`.

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

*Male* counsel sharing an accused's surname are excluded via
`parser/counsel_exclusions.csv` (loaded by `load_accused_names()` and checked
in `check_is_accused()`). Two confirmed cases (2026-07-05): `MR. LUKIC` in
`milutinovic_et_al_05-87` (Branko Lukić, counsel for accused Sreten Lukić)
and `MR. NIKOLIC` in `popovic_et_al` (Predrag Nikolić, attorney on Beara's
team) — together 9,930 utterances that were wrongly flagged before the fix.
The exclusion is applied at parse time (stored `is_accused` is already
correct); `chunk_extracton.py` applies the same list again as a safety net,
so an out-of-date parse cannot silently reintroduce the false positives.

#### Accused attribution (bare speaker labels)

`is_accused` says *an* accused is speaking, but bare labels (`THE ACCUSED` /
`THE APPELLANT` without a name — 82,503 utterances) don't say *which* one.
That only matters in multi-accused cases (8,149 utterances); in single-accused
cases the answer is trivial.

`parser/attribute_accused.py` resolves this per transcript file and writes
`data_250625/reports/accused_attribution.csv` — one row per file containing
accused utterances:

| Column | Description |
|--------|-------------|
| `case`, `source_file`, `case_id_base`, `date` | File identity |
| `n_accused_in_case` | Accused count from `pleas.csv`/`case_mapping.csv` |
| `n_bare` / `n_named` | Accused utterances with bare vs. named labels |
| `named_accused_speakers` | Named accused labels seen in the file |
| `attributed_accused` | Who the bare labels belong to (if resolved) |
| `plea_initial` | `plea` column from `pleas.csv` (initial plea by convention) |
| `plea_final` | Derived from the `pleas.csv` `note` column ("later changed plea to guilty" → `guilty`); equals `plea_initial` when no change is noted |
| `plea_note` | The raw `note` string, for provenance |
| `method` | How (see below) |
| `evidence` | Registrar case call snippet or mention counts |
| `needs_review` | `yes` when the method is a heuristic fallback |

Methods, strongest first: `manual` (from `accused_attribution_overrides.csv`,
survives re-runs) > `single_accused` > `case_call` (registrar announces "the
Prosecutor versus <name>" in the opening utterances) > `header_scan` /
`header_multi` / `mention_count` (surname frequency heuristics, always
flagged) > `joint_hearing` (all accused present — bare labels genuinely
ambiguous, not attributed) > `unresolved`.

Plea columns are joined at run time, so re-running the script after editing
`pleas.csv` refreshes the whole table without losing manual overrides.

Results (2026-07-05): 4,686 files. Bare utterances: 52,626 resolved
authoritatively (`single_accused` + `case_call`), 23,701 resolved heuristically
(flagged), 6,176 joint/unresolved. For `jankovic_g_et_al` — the only case
whose accused pled differently — 269 of 294 bare utterances resolve via
`case_call`; the remaining 25 sit in 5 flagged files.

#### Hearing deduplication

UCR files the same hearing under multiple related case numbers (joined and
severed cases: Blaškić↔Kordić, Popović↔Tolimir, Đorđević↔Milutinović, both
Karadžić folders, …), so 15.4% of all parsed utterances are duplicate copies
(936,374 excess utterances in 1,854 duplicate groups, verified by full-text
hash).

`parser/dedup_hearings.py` writes `data_250625/reports/hearing_dedup.csv`:
one row per file in a duplicate group, with a `canonical_case` owner and
`is_canonical` flag. Ownership rules in order: manual override
(`hearing_dedup_overrides.csv`) > case number printed in the transcript >
folder with pleas/CIS data > hearing date within exactly one folder's
non-duplicated date range > majority owner of the same folder pair >
alphabetical fallback (flagged `needs_review`; currently 1 group).

`chunk_extracton.py` skips non-canonical copies. Anything computing corpus
statistics should do the same.

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

Results (2026-07-05, current):

| Metric | Value |
|--------|-------|
| Files parsed | 12,651 / 12,651 (0 failed) |
| Cases with transcripts | 96 (of 108 folders) |
| Total utterances (incl. duplicate hearing copies) | 6,095,041 |
| Null dates | 2 |
| `is_accused=True` utterances (stored; counsel excluded, incl. duplicates) | 374,369 |
| **Accused utterances after dedup (final dataset)** | **250,529** |
| UNKNOWN EXAMINER utterances | 441 |

#### Parse-quality audit (2026-07-05)

- **Text coverage**: parsed output vs. line-number-stripped source txt on a
  400-file sample — median 103% (speaker names counted on the output side),
  no file below 94%. No content is lost in parsing.
- **Utterance boundaries**: utterances containing another speaker's label
  mid-text *outside* a quote (the signature of two turns wrongly merged):
  101 of 6.1M (0.002%). Was 369 before fixing `LINE_NUMBER_RE` to accept the
  1996-era `23<TAB>JUDGE STEPHEN:` line-number format (+23,654 utterances
  recovered, mostly in `tadic`). The residual are one-off court-reporter
  quirks (`[Trial Chamber confers]JUDGE LIU:` without a space, stray `*`
  before a label). Mid-text labels *inside* quotes are correct behaviour —
  counsel reading a prior transcript into the record.
- Files parsing to 0 utterances (346) are fully closed/private sessions with
  no public content — correct behaviour.

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

`dataset_creation/chunk_extracton.py` — extracts utterances spoken by the accused from parsed transcript JSONs. Uses the `is_accused` flag computed by the parser (from `pleas.csv` + `case_mapping.csv` + speaker patterns), skips non-canonical duplicate copies via `data_250625/reports/hearing_dedup.csv`, and re-applies `parser/counsel_exclusions.csv` as a safety net. Outputs `accused_utterances.csv` and `accused_utterances.pkl` (250,529 rows).

Full rebuild is three steps, in order (see README for details):

```bash
.venv/bin/python3 parser/run_parser.py --archive-dir archive_250625/ \
    --output-dir data_250625/transcripts_parsed --txt-dir data_250625/transcripts_txt
.venv/bin/python3 parser/dedup_hearings.py
.venv/bin/python3 dataset_creation/chunk_extracton.py
```

Known limitations of the final dataset (documented, not bugs):

- ~24k bare `THE ACCUSED` utterances belong to cases without plea/CIS data
  (mostly the Karadžić/Mladić folders) — no plea to filter on.
- ~6k bare labels in joint trials (mostly `popovic_et_al`) cannot be
  attributed to a specific accused; see `accused_attribution.csv`.
- `accused_attribution.csv` lists every file including duplicate copies —
  when computing statistics from it, filter to canonical files via
  `hearing_dedup.csv`.
