# ICTY Archive Scraper

A Python tool for downloading **all public court records** from the ICTY (International Criminal Tribunal for the former Yugoslavia). It pulls documents from two sources:

1. **UCR API** (Unified Court Records at `ucr.irmct.org`) — transcripts, judgements, decisions, orders, indictments, briefs, motions, and other court filings
2. **ICTY website** (`icty.org`) — press releases and case information sheets (these are editorial content not available in UCR)

## What It Downloads

| Source | Document Types | Format |
|--------|---------------|--------|
| **UCR API** | Transcripts | DOC |
| **UCR API** | Judgements, Sentences, Dissenting Opinions | DOC, PDF |
| **UCR API** | Decisions, Orders, Scheduling Orders | DOC, PDF |
| **UCR API** | Indictments, Reviews of Indictments | DOC, PDF |
| **UCR API** | Briefs, Final Briefs, Motions, Requests, Applications | DOC, PDF |
| **UCR API** | Submissions, Correspondence, Miscellaneous, Warrants | DOC, PDF |
| **UCR API** | Image scans (skipped by default) | TIF |
| **UCR API** | Court recordings (skipped by default) | MP4 |
| **ICTY website** | Case Information Sheets | PDF |
| **ICTY website** | Press Releases | HTML |

### Scale

- **~40,500 documents** across **114 ICTY parent cases** (86 case pages, many sharing case numbers)
- **~32,500 DOC + PDF** and **~8,000 TIF** files downloaded by default (only MP4 video skipped)
- **Estimated ~20+ GB** total download size including TIF image scans
- **~2,000+ press releases** and **~86 case info sheets** from the ICTY website
- Duplication from shared case numbers adds ~30-40% more files (see Known Limitations)

## Output Structure

```
icty_archive/
├── tadic/
│   ├── transcripts/           # Hearing transcripts (DOC)
│   ├── judgements/             # Judgements, sentences, dissenting opinions
│   ├── decisions_orders/       # Decisions, orders, scheduling orders
│   ├── indictments/            # Indictments, reviews of indictments
│   ├── briefs_motions/         # Briefs, motions, requests, applications
│   ├── other_filings/          # Submissions, correspondence, miscellaneous
│   ├── case_info_sheets/       # Case Information Sheet PDFs (from icty.org)
│   └── press_releases/         # Press releases (from icty.org)
├── mladic/
│   └── ...
├── karadzic/
│   └── ...
├── ... (all ~86 cases)
└── _statistics/
    ├── scrape_report.json      # Full machine-readable report
    ├── scrape_report.csv       # One row per downloaded file
    ├── scrape_summary.txt      # Human-readable summary
    ├── checkpoint.json         # Resume checkpoint (saved after each case)
    └── scrape.log              # Full scrape log
```

## Requirements

- Python 3.8+
- Libraries: `requests`, `beautifulsoup4`, `lxml`

```bash
pip install requests beautifulsoup4 lxml
```

Or use the virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests beautifulsoup4 lxml
```

## Usage

### Full scrape (all cases, text documents only)

```bash
python3 icty_scraper.py --ucr-email YOUR_EMAIL --ucr-password YOUR_PASSWORD
```

UCR credentials are **required** for a complete download. Without them, transcripts with `#` in their URL paths (~50% of all transcripts) will be skipped. Registration is free at [ucr.irmct.org](https://ucr.irmct.org).

### Quick test (2 files per source)

```bash
python3 icty_scraper.py --cases tadic --limit 2 --ucr-email EMAIL --ucr-password PASS
```

### Scrape specific cases

```bash
python3 icty_scraper.py --cases tadic mladic karadzic --ucr-email EMAIL --ucr-password PASS
```

### List all available cases

```bash
python3 icty_scraper.py --list-cases
```

### Resume after interruption

```bash
python3 icty_scraper.py --resume --ucr-email EMAIL --ucr-password PASS
```

The scraper saves a checkpoint after each case. Use `--resume` to skip already-completed cases. Safe to interrupt with Ctrl+C.

### Include image scans and video

```bash
python3 icty_scraper.py --skip-formats none --ucr-email EMAIL --ucr-password PASS
```

By default, TIF (image scans) and MP4 (court recordings) are skipped. Use `--skip-formats none` to download everything (~40,500 files).

### Custom delay between requests

```bash
python3 icty_scraper.py --delay 1.0 --ucr-email EMAIL --ucr-password PASS
```

Default is 0.5 seconds. The ICTY/UCR servers handle this well.

### All CLI options

| Option | Default | Description |
|--------|---------|-------------|
| `--cases` | all | Specific case slugs to scrape |
| `--output` | `icty_archive` | Output directory |
| `--delay` | `0.5` | Seconds between requests |
| `--limit` | `0` (unlimited) | Max downloads per source per case (for testing) |
| `--ucr-email` | — | UCR login email (strongly recommended) |
| `--ucr-password` | — | UCR login password |
| `--skip-formats` | `tif,mp4` | Comma-separated extensions to skip, or `none` |
| `--list-cases` | — | List cases and exit |
| `--resume` | — | Resume from last checkpoint |

## How It Works

### UCR API

The scraper queries `ucr.irmct.org/api/Summary/ByCaseDocsByLang?CaseNumber={case}&Lang=eng` for each ICTY case number. This returns all English-language documents with metadata (type, title, date, download URL).

Document files are hosted on `icr.icty.org`. However, many case sub-numbers contain `#` characters (e.g., `IT-95-14/2` is stored as `IT-95-14#2` in the file path). The IIS server at `icr.icty.org` cannot serve these URLs — it returns HTTP 500 regardless of encoding.

**The fix**: When UCR credentials are provided, the scraper proxies these downloads through `ucr.irmct.org` (replacing `http://icr.icty.org` → `https://ucr.irmct.org` and `#` → `%23`). This is how the UCR web interface itself handles these files.

### ICTY Website

Press releases and case information sheets are editorial content published on `icty.org`, not court filings. They do not exist in the UCR database. The scraper discovers them by parsing each case's page on the ICTY website.

### Case Discovery

Cases are discovered from `icty.org/en/cases`. Each case page is then visited to extract the official case number (e.g., `IT-94-1` for Tadić). Multiple case page slugs can map to the same case number (e.g., `aleksovski`, `blaskic`, and `kordic_cerkez` all map to `IT-95-14`).

## After the Scrape

Check `_statistics/scrape_summary.txt` for results:

```
TOTALS:
  Files attempted:  32500
  Successful:       32450
  Failed:           50

BY TYPE:
  transcript                         OK: 25000  FAIL:  30  TOTAL: 25030
  judgement_sentence                 OK:   200  FAIL:   0  TOTAL:   200
  decision                           OK:  3000  FAIL:  10  TOTAL:  3010
  ...

FAILED URLs (50):
  http://icr.icty.org/...
```

The CSV files (`scrape_report.csv`) can be opened in Excel/Sheets for further analysis. Each row contains: case, document type, URL, filename, file path, success status, and title.

## Known Limitations & What's NOT Included

### Not downloaded by this scraper

- **Exhibits and evidence** — These are in the UCR but are not downloaded by this scraper (it only fetches documents classified by type like Transcript, Decision, etc., not exhibits).
- **Non-English documents** — The scraper queries for English (`Lang=eng`) only. Documents in French (the other ICTY official language) and BCS (Bosnian/Croatian/Serbian) are available in UCR but not downloaded.
- **Confidential/sealed documents** — Not available via the public UCR API.
- **IRMCT (Mechanism) documents** — The UCR also contains records from the IRMCT (the successor tribunal). This scraper only queries ICTY case numbers (`IT-*`).
- **Multimedia** — Audio/video court recordings (MP4) are skipped by default.

### Known issues

- **1 case page not found**: `zeljko_raznjatovic` (Arkan) returns 404 on `icty.org`. The indictment was withdrawn after his assassination, so his case page may have been removed. His documents may still exist in UCR under the case number.
- **Duplicate documents across related cases**: The ICTY often jointly indicted multiple accused for crimes in the same region, assigning them a shared base case number. Proceedings were then severed into separate trials with sub-numbers (e.g., `/1`, `/2`). The ICTY website gives each accused their own case page, but the UCR API groups all documents under the shared base number. This means cases that share a case number will each receive a copy of the same documents. Examples:
  - **IT-95-14** (Lašva Valley): `aleksovski` (`/1`), `kordic_cerkez` (`/2`), `blaskic` (base) — 842 docs downloaded 3×
  - **IT-99-36** (Krajina): `brdanin`, `talic`, `zupljanin` — downloaded 3×
  - **IT-02-60** (Srebrenica): `blagojevic_jokic`, `nikolic`, `obrenovic` — downloaded 3×
  - 16 case numbers are shared in total across multiple slugs

  This is intentional — the shared filings (joint hearings, procedural decisions) genuinely belong to all co-accused. Deduplication is not applied because each case folder should be self-contained.
- **Filename collisions**: When two documents in the same directory would have the same filename, an MD5 suffix is appended to disambiguate.
- **File sizes**: The dataset is too large for GitHub (>11 GB). Keep it local or use Git LFS / cloud storage.

## Project Structure

```
Scraper/
├── icty_scraper.py              # The scraper (single file, ~930 lines)
├── ICTY_SCRAPER_README.md       # This file
├── .venv/                       # Python virtual environment
└── icty_archive/                # Output directory (created on first run)
```
