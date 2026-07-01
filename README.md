# ICTY Dataset

Scraping, parsing, and dataset creation from court transcripts of the International Criminal Tribunal for the former Yugoslavia (ICTY).

## Project Structure

```
scraper/              Web scraper for downloading ICTY court documents
parser/               Transcript parser (DOC/DOCX → JSON) and CIS parser (PDF → JSON/CSV)
dataset_creation/     Accused utterance extraction (parsed JSON → CSV)
docs/                 Documentation
data_250625/          Generated data (June 2025 archive)
  cis_parsed/           Parsed Case Information Sheets (tracked in git)
  transcripts_parsed/   Parsed transcript JSON (gitignored, regenerable)
  transcripts_txt/      Cached text conversions (gitignored, regenerable)
archive_250625/       Raw scraped archive (gitignored, ~3 GB)
archive/              Old data and docs from previous pipeline runs (gitignored)
```

## Documentation

- [Dataset Overview](docs/dataset_250625_overview.md) — data sources, archive contents, parsing pipeline, and results

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install tqdm requests beautifulsoup4 lxml pdfplumber

# Parse CIS PDFs
.venv/bin/python3 parser/cis_parser.py \
    --archive-dir archive_250625/ \
    --output-dir data_250625/cis_parsed

# Extract pleas from CIS data
.venv/bin/python3 parser/extract_pleas.py \
    --cis-dir data_250625/cis_parsed

# Parse all transcripts (requires archive_250625/ with scraped DOC files)
.venv/bin/python3 parser/run_parser.py \
    --archive-dir archive_250625/ \
    --output-dir data_250625/transcripts_parsed \
    --txt-dir data_250625/transcripts_txt

# Extract accused utterances from parsed transcripts
.venv/bin/python3 dataset_creation/chunk_extracton.py
```

## Dataset Summary

| Metric | Value |
|---|---|
| Cases with transcripts | 96 |
| Transcript files parsed | 12,651 |
| Total utterances | 6,071,387 |
| Accused identified (from CIS) | 116 |
| CIS PDFs parsed | 80 (across 79 cases) |

See [dataset overview](docs/dataset_250625_overview.md) for full details.
