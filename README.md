# ICTY Dataset

Scraping and parsing of court transcripts from the International Criminal Tribunal for the former Yugoslavia (ICTY).

## Project Structure

```
scraper/          Web scraper for downloading ICTY court documents
parser/           Transcript parser (.doc → structured JSON)
docs/             Documentation
examples/         Example cases with full data pipeline (for review)
data/             Generated data — archive, converted text, parsed JSON (gitignored)
logs/             Parse logs and reports (gitignored)
```

## Documentation

- [Project Overview](docs/project_overview.md) — full description of the archive, transcript structure, parser pipeline, output format, and known limitations
- [Scraper README](scraper/ICTY_SCRAPER_README.md) — how to run the scraper, CLI options, and data source details

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install tqdm requests beautifulsoup4 lxml

# Parse all transcripts (requires data/archive/ with scraped .doc files)
cd parser
python run_parser.py

# Parse specific cases
python run_parser.py --cases mladic karadzic
```

## Examples

The `examples/` folder contains three representative cases with all pipeline stages (raw .doc, converted .txt, parsed .json):

| Case | Era | Significance | Transcripts |
|---|---|---|---|
| Erdemovic | 1996–98 | First guilty plea at ICTY | 14 |
| Deronjic | 2003–05 | Later-era compact case | 13 |
| Krstic | 2000–04 | First genocide conviction (Srebrenica) | 127 |

## Full Dataset

The complete dataset (8.8 GB raw archive, 1.8 GB converted text, 2.6 GB parsed JSON — 16,505 transcripts across 82 cases) is too large for GitHub. It is currently stored locally on the working computer of Suzana Sagadin.
