# ICTY Dataset

Scraping, parsing, and dataset creation from court transcripts of the International Criminal Tribunal for the former Yugoslavia (ICTY).

## Project Structure

```
scraper/              Web scraper for downloading ICTY court documents
parser/               Transcript parser (DOC/DOCX → JSON), CIS parser (PDF → JSON/CSV),
                      and data-quality tools (attribution, dedup, validation)
dataset_creation/     Accused utterance extraction (parsed JSON → CSV/pickle)
docs/                 Documentation
data_250625/          Generated data (June 2025 archive)
  cis_parsed/           Parsed Case Information Sheets (tracked in git)
  reports/              Derived tables: pleas, attribution, dedup (tracked in git)
  transcripts_parsed/   Parsed transcript JSON (gitignored, regenerable)
  transcripts_txt/      Cached text conversions (gitignored, regenerable)
archive_250625/       Raw scraped archive (gitignored, ~3 GB)
archive/              Old data and docs from previous pipeline runs (gitignored)
```

## Documentation

- [Dataset Overview](docs/dataset_250625_overview.md) — data sources, archive contents, parsing pipeline, and results

## The pipeline

To (re)build the accused-utterances dataset, run these **three steps in this order**:

```bash
# 1. Parse transcripts (DOC → JSON; sets is_accused, applies parser/counsel_exclusions.csv)
.venv/bin/python3 parser/run_parser.py \
    --archive-dir archive_250625/ \
    --output-dir data_250625/transcripts_parsed \
    --txt-dir data_250625/transcripts_txt

# 2. Rebuild the duplicate-hearings table (required — step 3 reads it)
.venv/bin/python3 parser/dedup_hearings.py

# 3. Extract accused utterances → dataset_creation/accused_utterances.csv / .pkl
.venv/bin/python3 dataset_creation/chunk_extracton.py
```

Notes:

- **Step 2 must run between 1 and 3.** `chunk_extracton.py` uses
  `data_250625/reports/hearing_dedup.csv` to skip duplicate copies of hearings
  (same content filed under multiple case numbers). With a stale or missing
  table you get duplicates back — it warns but does not stop.
- **Runtime:** step 1 is only slow (hours) on a fresh archive, when LibreOffice
  converts DOC → txt. With `data_250625/transcripts_txt/` populated, a full
  re-run of all three steps takes ~5 minutes.
- First-time setup:
  ```bash
  python -m venv .venv
  source .venv/bin/activate
  pip install tqdm requests beautifulsoup4 lxml pdfplumber pandas
  ```

### Situational steps (not part of the core chain)

```bash
# Attribution table: which accused do bare THE ACCUSED/THE APPELLANT labels
# belong to, incl. initial/final plea per hearing. Standalone report —
# chunk_extracton.py does not read it (yet). Re-run after a re-parse or after
# editing pleas.csv.
.venv/bin/python3 parser/attribute_accused.py

# CIS pipeline: only when CIS PDFs or plea data change
.venv/bin/python3 parser/cis_parser.py --archive-dir archive_250625/ --output-dir data_250625/cis_parsed
.venv/bin/python3 parser/extract_pleas.py --cis-dir data_250625/cis_parsed

# Parse-quality validation report (compares against logs/validation_baseline.json)
.venv/bin/python3 parser/validate_parse.py
```

If you edit `pleas.csv`: re-run `attribute_accused.py` always; re-run the full
three-step chain only if accused *names* changed (the parser matches speaker
names against them for `is_accused`) — plea-value edits alone don't affect it.

### Manual-correction files (survive re-runs)

| File | Corrects |
|---|---|
| `parser/counsel_exclusions.csv` | Counsel wrongly flagged `is_accused` (surname collision with their client) |
| `data_250625/reports/accused_attribution_overrides.csv` | Per-file accused attribution (create if needed) |
| `data_250625/reports/hearing_dedup_overrides.csv` | Canonical owner of a duplicated hearing (create if needed) |

## Dataset Summary

| Metric | Value |
|---|---|
| Cases with transcripts | 96 |
| Transcript files parsed | 12,651 |
| Total utterances (raw, incl. duplicate hearing copies) | 6,095,041 |
| Duplicate hearing copies (same content under multiple case numbers) | 1,867 files / 936,374 utterances |
| **Accused utterances (final: deduped, counsel collisions excluded)** | **250,529** |
| Accused identified (from CIS) | 116 |
| CIS PDFs parsed | 80 (across 79 cases) |

See [dataset overview](docs/dataset_250625_overview.md) for full details.
