#!/usr/bin/env python3
"""
ICTY Transcript & CIS Scraper v2

Downloads transcripts (from UCR API) and Case Information Sheets (from icty.org)
using the authoritative case list in case_mapping.csv.

Usage:
    python3 icty_scraper_v2.py --ucr-email EMAIL --ucr-password PASS
    python3 icty_scraper_v2.py --ucr-email EMAIL --ucr-password PASS --limit 2
    python3 icty_scraper_v2.py --ucr-email EMAIL --ucr-password PASS --cases IT-94-1 IT-98-33
    python3 icty_scraper_v2.py --list-cases
    python3 icty_scraper_v2.py --resume --ucr-email EMAIL --ucr-password PASS
"""

import os
import sys
import re
import json
import csv
import time
import hashlib
import logging
import argparse
from datetime import datetime
from urllib.parse import urlparse, urljoin
from pathlib import Path

import requests
import urllib3
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================================
# CONFIGURATION
# ============================================================================

ICTY_BASE = "https://www.icty.org"
UCR_API_BASE = "https://ucr.irmct.org/api/Summary"
UCR_LOGIN_URL = "https://ucr.irmct.org/Account/JsonLogin"
UCR_BASE = "https://ucr.irmct.org"

DEFAULT_OUTPUT_DIR = "archive_250625"
DEFAULT_DELAY = 0.5
MAX_RETRIES = 3
RETRY_DELAY = 5
REQUEST_TIMEOUT = 60

CASE_MAPPING_CSV = os.path.join(os.path.dirname(__file__), "case_mapping.csv")

HEADERS = {
    "User-Agent": "ICTY-Research-Archiver/2.0 (Academic research; respectful crawling)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ============================================================================
# LOGGING
# ============================================================================

def setup_logging(output_dir):
    stats_dir = os.path.join(output_dir, "_statistics")
    os.makedirs(stats_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(stats_dir, "scrape.log"), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("icty_scraper_v2")


# ============================================================================
# HTTP SESSION
# ============================================================================

class Session:
    def __init__(self, delay=DEFAULT_DELAY, logger=None):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay = delay
        self.logger = logger or logging.getLogger("icty_scraper_v2")
        self.request_count = 0
        self.last_request_time = 0
        self.ucr_authenticated = False

    def ucr_login(self, email, password):
        self.logger.info(f"Logging in to UCR as {email}...")
        resp = self.session.post(
            UCR_LOGIN_URL,
            json={"EmailAddress": email, "Password": password},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                self.ucr_authenticated = True
                self.logger.info("  UCR login successful")
                return True
            self.logger.error(f"  UCR login failed: {data.get('errors', data)}")
        else:
            self.logger.error(f"  UCR login HTTP {resp.status_code}")
        return False

    def fix_icr_url(self, url):
        if '#' in url and 'icr.icty.org' in url and self.ucr_authenticated:
            return url.replace('#', '%23').replace('http://icr.icty.org', UCR_BASE)
        return url

    def _wait(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def get(self, url, stream=False):
        verify = urlparse(url).hostname not in ('icr.icty.org',)
        extra_headers = {}
        if '/api/' in url:
            extra_headers['Accept'] = 'application/json, */*'
        for attempt in range(1, MAX_RETRIES + 1):
            self._wait()
            try:
                resp = self.session.get(
                    url, timeout=REQUEST_TIMEOUT, stream=stream,
                    verify=verify, headers=extra_headers,
                )
                self.last_request_time = time.time()
                self.request_count += 1
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 404:
                    self.logger.warning(f"Not found (404): {url}")
                    return None
                if resp.status_code == 429:
                    wait = RETRY_DELAY * attempt
                    self.logger.warning(f"Rate limited (429). Waiting {wait}s...")
                    time.sleep(wait)
                else:
                    self.logger.warning(f"HTTP {resp.status_code} for {url} (attempt {attempt})")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Request error for {url}: {e} (attempt {attempt})")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
        self.logger.error(f"FAILED after {MAX_RETRIES} attempts: {url}")
        return None


# ============================================================================
# CASE LIST
# ============================================================================

def load_cases(csv_path=CASE_MAPPING_CSV):
    cases = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cases.append(row)
    return cases


# ============================================================================
# FILE HELPERS
# ============================================================================

def sanitize_filename(name, max_length=200):
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_. ')
    return name[:max_length]


def get_file_extension(url):
    path = urlparse(url).path
    if '.' in path.split('/')[-1]:
        ext = path.split('/')[-1].rsplit('.', 1)[-1].lower()
        if ext in ('pdf', 'html', 'htm', 'doc', 'docx', 'txt', 'tif', 'tiff'):
            return f".{ext}"
    return '.html'


def fix_extension(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in ('.html', '.htm', '.txt'):
        return filepath
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(8)
        if magic[:4] == b'\xd0\xcf\x11\xe0':
            new = os.path.splitext(filepath)[0] + '.doc'
            os.rename(filepath, new)
            return new
        if magic[:4] == b'%PDF':
            new = os.path.splitext(filepath)[0] + '.pdf'
            os.rename(filepath, new)
            return new
        if magic[:4] in (b'\x49\x49\x2a\x00', b'\x4d\x4d\x00\x2a'):
            new = os.path.splitext(filepath)[0] + '.tif'
            os.rename(filepath, new)
            return new
    except (OSError, IOError):
        pass
    return filepath


def download_file(http, url, filepath):
    if os.path.exists(filepath):
        return True
    resp = http.get(url, stream=True)
    if not resp:
        return False
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    try:
        with open(filepath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        fix_extension(filepath)
        return True
    except Exception as e:
        http.logger.error(f"Error writing {filepath}: {e}")
        if os.path.exists(filepath):
            os.remove(filepath)
        return False


# ============================================================================
# UCR API
# ============================================================================

_ucr_cache = {}

def get_ucr_documents(http, case_number):
    if case_number in _ucr_cache:
        return _ucr_cache[case_number]
    api_url = f"{UCR_API_BASE}/ByCaseDocsByLang?CaseNumber={case_number}&Lang=eng"
    http.logger.info(f"  [UCR] Querying API for {case_number}...")
    resp = http.get(api_url)
    if not resp:
        _ucr_cache[case_number] = None
        return None
    try:
        raw = resp.json()
        documents = json.loads(raw["data"])
        _ucr_cache[case_number] = documents
        return documents
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        http.logger.error(f"  [UCR] Failed to parse API response for {case_number}: {e}")
        _ucr_cache[case_number] = None
        return None


# ============================================================================
# TRANSCRIPT SCRAPER
# ============================================================================

def scrape_transcripts(http, case, output_dir, limit=0):
    results = []
    case_number = case["case_number"]

    documents = get_ucr_documents(http, case_number)
    if not documents:
        http.logger.warning(f"  [UCR] No documents for {case_number}")
        return results

    transcripts = [
        d for d in documents
        if d.get("DocTypeDesc", "").strip() == "Transcript"
        and case_number in d.get("CaseNumber", "")
    ]
    http.logger.info(f"  [UCR] {len(transcripts)} transcripts for {case_number}")

    skipped_hash = 0
    downloadable = []
    for doc in transcripts:
        url = doc.get("DocumentPath", "")
        if not url:
            continue
        ext = url.rsplit('.', 1)[-1].lower() if '.' in url else ''
        if ext in ('mp4', 'mp3', 'wav', 'avi', 'wmv'):
            continue
        if '#' in url and not http.ucr_authenticated:
            skipped_hash += 1
            results.append({
                "case_number": case_number,
                "folder": case["folder"],
                "type": "transcript",
                "url": url,
                "filename": "",
                "success": False,
                "title": doc.get("DocumentTitle", "").strip(),
                "date": doc.get("DocFilingDate", "").strip(),
                "skip_reason": "broken '#' URL — needs UCR auth",
            })
            continue
        downloadable.append(doc)

    if skipped_hash:
        http.logger.info(f"  [UCR] Skipped {skipped_hash} transcripts with '#' URLs (no UCR auth)")

    if limit:
        downloadable = downloadable[:limit]

    transcript_dir = os.path.join(output_dir, case["folder"], "transcripts")
    os.makedirs(transcript_dir, exist_ok=True)
    seen = set()

    for doc in downloadable:
        url = doc.get("DocumentPath", "")
        title = doc.get("DocumentTitle", "").strip()
        date = doc.get("DocFilingDate", "").strip()
        download_url = http.fix_icr_url(url)

        url_filename = url.split("/")[-1] if "/" in url else ""
        if title:
            filename = sanitize_filename(title[:150])
        elif url_filename:
            filename = sanitize_filename(url_filename.rsplit(".", 1)[0])
        else:
            filename = hashlib.md5(url.encode()).hexdigest()[:12]

        ext = get_file_extension(url)
        known_exts = ('.pdf', '.html', '.htm', '.doc', '.docx', '.tif', '.tiff')
        if not any(filename.lower().endswith(e) for e in known_exts):
            filename += ext

        if filename.lower() in seen:
            base, fext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
            filename = f"{base}_{hashlib.md5(url.encode()).hexdigest()[:6]}.{fext}"
        seen.add(filename.lower())

        filepath = os.path.join(transcript_dir, filename)
        success = download_file(http, download_url, filepath)
        results.append({
            "case_number": case_number,
            "folder": case["folder"],
            "type": "transcript",
            "url": url,
            "filename": filename,
            "success": success,
            "title": title,
            "date": date,
        })

    return results


# ============================================================================
# CIS SCRAPER
# ============================================================================

def scrape_cis(http, case, output_dir, limit=0):
    results = []
    slugs = case.get("icty_slug", "")
    if not slugs:
        return results

    cis_dir = os.path.join(output_dir, case["folder"], "case_info_sheets")
    os.makedirs(cis_dir, exist_ok=True)

    for slug in slugs.split("; "):
        slug = slug.strip()
        if not slug:
            continue

        links = set()
        links.add(f"{ICTY_BASE}/x/cases/{slug}/cis/en/cis_{slug}.pdf")
        links.add(f"{ICTY_BASE}/x/cases/{slug}/cis/en/cis_{slug}_en.pdf")

        case_page_url = f"{ICTY_BASE}/en/case/{slug}"
        resp = http.get(case_page_url)
        if resp:
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True).lower()
                if '/cis/' in href.lower() or 'case information sheet' in text:
                    links.add(urljoin(ICTY_BASE, href))

        http.logger.info(f"  [CIS] {len(links)} CIS links for slug '{slug}'")

        urls = sorted(links)
        if limit:
            urls = urls[:limit]

        for url in urls:
            url_path = urlparse(url).path
            filename = sanitize_filename(url_path.split("/")[-1])
            if not filename:
                filename = f"cis_{slug}.pdf"
            if not filename.lower().endswith('.pdf'):
                filename += '.pdf'

            filepath = os.path.join(cis_dir, filename)
            success = download_file(http, url, filepath)
            results.append({
                "case_number": case["case_number"],
                "folder": case["folder"],
                "type": "case_info_sheet",
                "url": url,
                "filename": filename,
                "success": success,
            })

    return results


# ============================================================================
# CHECKPOINT / RESUME
# ============================================================================

def load_checkpoint(output_dir):
    path = os.path.join(output_dir, "_statistics", "checkpoint.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"completed": []}


def save_checkpoint(output_dir, checkpoint):
    path = os.path.join(output_dir, "_statistics", "checkpoint.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(checkpoint, f, indent=2)


# ============================================================================
# REPORTS
# ============================================================================

def write_reports(output_dir, all_results, logger):
    stats_dir = os.path.join(output_dir, "_statistics")
    os.makedirs(stats_dir, exist_ok=True)

    csv_path = os.path.join(stats_dir, "scrape_report.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        fields = ["case_number", "folder", "type", "url", "filename", "success", "title", "date"]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_results)

    ok = sum(1 for r in all_results if r.get("success"))
    fail = sum(1 for r in all_results if not r.get("success"))
    transcripts = sum(1 for r in all_results if r["type"] == "transcript" and r.get("success"))
    cis = sum(1 for r in all_results if r["type"] == "case_info_sheet" and r.get("success"))

    summary = (
        f"ICTY Scraper v2 Report\n"
        f"{'=' * 40}\n"
        f"Date: {datetime.now().isoformat()}\n\n"
        f"TOTALS:\n"
        f"  Successful:   {ok}\n"
        f"  Failed:       {fail}\n"
        f"  Total:        {ok + fail}\n\n"
        f"BY TYPE:\n"
        f"  Transcripts:  {transcripts}\n"
        f"  CIS files:    {cis}\n"
    )

    summary_path = os.path.join(stats_dir, "scrape_summary.txt")
    with open(summary_path, "w") as f:
        f.write(summary)

    logger.info(f"\n{summary}")
    logger.info(f"Reports written to {stats_dir}/")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="ICTY Transcript & CIS Scraper v2 — downloads transcripts "
                    "and Case Information Sheets using case_mapping.csv."
    )
    parser.add_argument("--cases", nargs="+",
                        help="Specific case numbers to scrape (e.g. IT-94-1 IT-98-33)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR,
                        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Delay between requests (default: {DEFAULT_DELAY}s)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max downloads per type per case (for testing)")
    parser.add_argument("--ucr-email", default=os.environ.get("UCR_EMAIL"),
                        help="UCR login email (or set UCR_EMAIL in .env)")
    parser.add_argument("--ucr-password", default=os.environ.get("UCR_PASSWORD"),
                        help="UCR login password (or set UCR_PASSWORD in .env)")
    parser.add_argument("--list-cases", action="store_true", help="List cases and exit")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--transcripts-only", action="store_true", help="Skip CIS downloads")
    parser.add_argument("--cis-only", action="store_true", help="Skip transcript downloads")
    args = parser.parse_args()

    cases = load_cases()

    if args.list_cases:
        print(f"{'Case Number':<20s} {'Name':<40s} {'Folder':<40s} CIS")
        print("-" * 105)
        for c in cases:
            print(f"{c['case_number']:<20s} {c['name']:<40s} {c['folder']:<40s} {c['has_cis']}")
        print(f"\n{len(cases)} cases total")
        return

    if args.cases:
        selected = set(args.cases)
        cases = [c for c in cases if c["case_number"] in selected]
        if not cases:
            print(f"No matching cases found for: {args.cases}")
            return

    logger = setup_logging(args.output)
    logger.info(f"ICTY Scraper v2 starting — {len(cases)} cases")
    logger.info(f"Output: {os.path.abspath(args.output)}")

    http = Session(delay=args.delay, logger=logger)

    if args.ucr_email and args.ucr_password:
        if not http.ucr_login(args.ucr_email, args.ucr_password):
            logger.warning("UCR login failed — transcripts with '#' URLs will be skipped")
    else:
        logger.info("No UCR credentials — some transcripts will be skipped")

    checkpoint = load_checkpoint(args.output) if args.resume else {"completed": []}
    all_results = []

    for idx, case in enumerate(cases, 1):
        cn = case["case_number"]

        if args.resume and cn in checkpoint["completed"]:
            logger.info(f"[{idx}/{len(cases)}] Skipping {cn} ({case['folder']}) — already completed")
            continue

        logger.info(f"\n{'=' * 60}")
        logger.info(f"[{idx}/{len(cases)}] {cn} — {case['name']} -> {case['folder']}")
        logger.info(f"{'=' * 60}")

        case_results = []

        if not args.cis_only:
            logger.info(f"  Downloading transcripts...")
            case_results.extend(scrape_transcripts(http, case, args.output, args.limit))

        if not args.transcripts_only and case["has_cis"] == "yes":
            logger.info(f"  Downloading Case Information Sheets...")
            case_results.extend(scrape_cis(http, case, args.output, args.limit))

        ok = sum(1 for r in case_results if r.get("success"))
        fail = sum(1 for r in case_results if not r.get("success"))
        logger.info(f"  Done: {ok} OK, {fail} failed")

        all_results.extend(case_results)
        checkpoint["completed"].append(cn)
        save_checkpoint(args.output, checkpoint)

    write_reports(args.output, all_results, logger)
    logger.info(f"Finished. {http.request_count} HTTP requests made.")


if __name__ == "__main__":
    main()
