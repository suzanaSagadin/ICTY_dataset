#!/usr/bin/env python3
"""
ICTY Archive Scraper
====================
Downloads all public court records from the ICTY (International Criminal
Tribunal for the former Yugoslavia) via the UCR API (Unified Court Records)
and the legacy ICTY website.

UCR provides:  Transcripts, judgements, decisions, orders, indictments,
               briefs, motions, and other court filings (DOC/PDF/TIF).
ICTY website:  Press releases and case information sheets.

Output structure:
  icty_archive/
    {case_slug}/
      transcripts/
      judgements/
      decisions_orders/
      indictments/
      briefs_motions/
      other_filings/
      case_info_sheets/
      press_releases/
    _statistics/
      scrape_report.json
      scrape_report.csv
      scrape_summary.txt

Usage:
  python3 icty_scraper.py --ucr-email EMAIL --ucr-password PASS
  python3 icty_scraper.py --cases tadic mladic --limit 2   # quick test
  python3 icty_scraper.py --list-cases
  python3 icty_scraper.py --skip-formats tif,mp4           # text only (default)

Requirements: requests, beautifulsoup4, lxml
  pip install requests beautifulsoup4 lxml
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
from urllib.parse import urljoin, urlparse, unquote, quote
from pathlib import Path
from collections import defaultdict

import requests
import urllib3
from bs4 import BeautifulSoup

# Suppress SSL warnings for icr.icty.org which has certificate issues
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================================
# CONFIGURATION
# ============================================================================

BASE_URL = "https://www.icty.org"
CASES_URL = f"{BASE_URL}/en/cases"
UCR_API_BASE = "https://ucr.irmct.org/api/Summary"
UCR_LOGIN_URL = "https://ucr.irmct.org/Account/JsonLogin"
UCR_BASE = "https://ucr.irmct.org"
DEFAULT_DELAY = 0.5  # seconds between requests
DEFAULT_OUTPUT_DIR = "icty_archive"
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds to wait on failure before retry
REQUEST_TIMEOUT = 60  # seconds

HEADERS = {
    "User-Agent": "ICTY-Research-Archiver/1.0 (Academic research; respectful crawling)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Map UCR document type descriptions to output subdirectory names
DOCTYPE_TO_DIR = {
    "Transcript": "transcripts",
    "Judgement/Sentence": "judgements",
    "Sentence": "judgements",
    "Dissenting Opinion": "judgements",
    "Decision": "decisions_orders",
    "Order": "decisions_orders",
    "Scheduling order": "decisions_orders",
    "Indictment": "indictments",
    "Review of indictments": "indictments",
    "Brief": "briefs_motions",
    "Final brief": "briefs_motions",
    "Pre-trial brief Pre-trial skeleton argument": "briefs_motions",
    "Motions": "briefs_motions",
    "Request": "briefs_motions",
    "Application": "briefs_motions",
    "Submission": "other_filings",
    "Miscellaneous documents": "other_filings",
    "Correspondence": "other_filings",
    "Warrant of arrest": "other_filings",
    "Court Recordings": "court_recordings",
}

# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging(output_dir):
    """Configure logging to both console and file."""
    log_dir = os.path.join(output_dir, "_statistics")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "scrape.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("icty_scraper")


# ============================================================================
# HTTP SESSION WITH RETRY LOGIC
# ============================================================================

class ICTYSession:
    """Manages HTTP requests with rate limiting, retries, and logging."""

    def __init__(self, delay=DEFAULT_DELAY, logger=None):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay = delay
        self.logger = logger or logging.getLogger("icty_scraper")
        self.request_count = 0
        self.failed_urls = []
        self.last_request_time = 0
        self.ucr_authenticated = False

    def ucr_login(self, email, password):
        """Log in to the UCR database to enable proxied downloads."""
        self.logger.info(f"Logging in to UCR as {email}...")
        resp = self.session.post(UCR_LOGIN_URL,
                                 json={"EmailAddress": email, "Password": password},
                                 timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                self.ucr_authenticated = True
                self.logger.info("  UCR login successful")
                return True
            else:
                self.logger.error(f"  UCR login failed: {data.get('errors', data)}")
        else:
            self.logger.error(f"  UCR login HTTP {resp.status_code}")
        return False

    def fix_icr_url(self, url):
        """Fix broken icr.icty.org URLs containing '#' by proxying through ucr.irmct.org."""
        if '#' in url and 'icr.icty.org' in url and self.ucr_authenticated:
            return url.replace('#', '%23').replace('http://icr.icty.org', UCR_BASE)
        return url

    def _wait(self):
        """Ensure minimum delay between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def get(self, url, stream=False):
        """GET request with retry logic and rate limiting."""
        verify = urlparse(url).hostname not in ('icr.icty.org',)
        extra_headers = {}
        if '/api/' in url:
            extra_headers['Accept'] = 'application/json, */*'
        for attempt in range(1, MAX_RETRIES + 1):
            self._wait()
            try:
                self.logger.debug(f"GET {url} (attempt {attempt})")
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT, stream=stream,
                                        verify=verify, headers=extra_headers)
                self.last_request_time = time.time()
                self.request_count += 1

                if resp.status_code == 200:
                    return resp
                elif resp.status_code == 429:
                    wait = RETRY_DELAY * attempt
                    self.logger.warning(f"Rate limited (429). Waiting {wait}s...")
                    time.sleep(wait)
                elif resp.status_code == 404:
                    self.logger.warning(f"Not found (404): {url}")
                    return None
                else:
                    self.logger.warning(f"HTTP {resp.status_code} for {url} (attempt {attempt})")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)

            except requests.exceptions.RequestException as e:
                self.logger.error(f"Request error for {url}: {e} (attempt {attempt})")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

        self.logger.error(f"FAILED after {MAX_RETRIES} attempts: {url}")
        self.failed_urls.append(url)
        return None


# ============================================================================
# CASE DISCOVERY
# ============================================================================

def discover_cases(http):
    """Discover cases from the ICTY website and extract case numbers."""
    http.logger.info("Discovering cases from the ICTY cases page...")
    resp = http.get(CASES_URL)
    if not resp:
        http.logger.error("Could not load cases page!")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cases = []
    seen_slugs = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        match = re.match(r"(?:https?://www\.icty\.org)?/en/case/([a-z0-9_]+)/?$", href)
        if match:
            slug = match.group(1)
            if slug not in seen_slugs:
                seen_slugs.add(slug)
                name = link.get_text(strip=True) or slug
                cases.append({
                    "slug": slug,
                    "name": name,
                    "url": f"{BASE_URL}/en/case/{slug}",
                    "case_number": None,
                })

    # Also look for case info sheet links for additional slugs
    for link in soup.find_all("a", href=True):
        href = link["href"]
        match = re.search(r"/x/cases/([a-z0-9_]+)/cis/", href)
        if match:
            slug = match.group(1)
            if slug not in seen_slugs:
                seen_slugs.add(slug)
                cases.append({
                    "slug": slug,
                    "name": slug,
                    "url": f"{BASE_URL}/en/case/{slug}",
                    "case_number": None,
                })

    http.logger.info(f"Discovered {len(cases)} unique cases")

    # Extract case numbers from each case page
    http.logger.info("Extracting case numbers from case pages...")
    for case in cases:
        resp = http.get(case["url"])
        if resp:
            case_soup = BeautifulSoup(resp.text, "lxml")
            h1 = case_soup.find("h1")
            if h1:
                num_match = re.search(r'(IT-\d+-\d+)', h1.get_text())
                if num_match:
                    case["case_number"] = num_match.group(1)
                    http.logger.info(f"  {case['slug']} -> {case['case_number']}")
            if not case["case_number"]:
                page_text = case_soup.get_text()
                num_match = re.search(r'(IT-\d+-\d+)', page_text)
                if num_match:
                    case["case_number"] = num_match.group(1)
                    http.logger.info(f"  {case['slug']} -> {case['case_number']} (fallback)")

        if not case["case_number"]:
            http.logger.warning(f"  Could not find case number for {case['slug']}")

    return sorted(cases, key=lambda c: c["slug"])


# ============================================================================
# FILE DOWNLOAD HELPERS
# ============================================================================

def sanitize_filename(name, max_length=200):
    """Create a safe filename from a string."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_. ')
    if len(name) > max_length:
        name = name[:max_length]
    return name


def get_file_extension(url, content_type=None):
    """Determine file extension from URL or content type."""
    path = urlparse(url).path
    if '.' in path.split('/')[-1]:
        ext = path.split('/')[-1].rsplit('.', 1)[-1].lower()
        if ext in ('pdf', 'html', 'htm', 'doc', 'docx', 'txt', 'tif', 'tiff', 'mp4'):
            return f".{ext}"
    if content_type:
        ct = content_type.lower()
        if 'pdf' in ct:
            return '.pdf'
        elif 'html' in ct:
            return '.html'
        elif 'msword' in ct:
            return '.doc'
        elif 'tiff' in ct:
            return '.tif'
    return '.html'


def _fix_extension(filepath, http):
    """Check if a downloaded file's actual type mismatches its extension and rename."""
    # Only check files with extensions that might be wrong
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in ('.html', '.htm', '.txt'):
        return filepath
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(8)
        # MS Compound Document (DOC) magic bytes: D0 CF 11 E0 A1 B1 1A E1
        if magic[:4] == b'\xd0\xcf\x11\xe0':
            new_filepath = os.path.splitext(filepath)[0] + '.doc'
            if os.path.exists(new_filepath):
                new_filepath = os.path.splitext(filepath)[0] + '_' + ext.strip('.') + '.doc'
            os.rename(filepath, new_filepath)
            http.logger.debug(f"Renamed misnamed DOC: {os.path.basename(filepath)} -> {os.path.basename(new_filepath)}")
            return new_filepath
        # PDF magic bytes: %PDF
        if magic[:4] == b'%PDF':
            new_filepath = os.path.splitext(filepath)[0] + '.pdf'
            if os.path.exists(new_filepath):
                new_filepath = os.path.splitext(filepath)[0] + '_' + ext.strip('.') + '.pdf'
            os.rename(filepath, new_filepath)
            http.logger.debug(f"Renamed misnamed PDF: {os.path.basename(filepath)} -> {os.path.basename(new_filepath)}")
            return new_filepath
        # TIFF magic bytes: 49 49 2A 00 (little-endian) or 4D 4D 00 2A (big-endian)
        if magic[:4] in (b'\x49\x49\x2a\x00', b'\x4d\x4d\x00\x2a'):
            new_filepath = os.path.splitext(filepath)[0] + '.tif'
            if os.path.exists(new_filepath):
                new_filepath = os.path.splitext(filepath)[0] + '_' + ext.strip('.') + '.tif'
            os.rename(filepath, new_filepath)
            http.logger.debug(f"Renamed misnamed TIF: {os.path.basename(filepath)} -> {os.path.basename(new_filepath)}")
            return new_filepath
    except (OSError, IOError):
        pass
    return filepath


def download_file(http, url, filepath):
    """Download a file to disk. Returns True on success. Fixes mismatched extensions."""
    if os.path.exists(filepath):
        http.logger.debug(f"Already exists, skipping: {filepath}")
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
        _fix_extension(filepath, http)
        return True
    except Exception as e:
        http.logger.error(f"Error writing {filepath}: {e}")
        if os.path.exists(filepath):
            os.remove(filepath)
        return False


def save_html_content(content, filepath):
    """Save HTML content to a file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return True


# ============================================================================
# UCR API DOCUMENT CACHE
# ============================================================================

_ucr_cache = {}

def get_ucr_documents(http, case_number):
    """Fetch all English documents for a case from the UCR API. Cached."""
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
# SCRAPER: ALL UCR COURT DOCUMENTS
# ============================================================================

def scrape_ucr_documents(http, case, output_dir, skip_formats=None, limit=0):
    """
    Scrape all court documents for a case from the UCR API.
    Downloads transcripts, judgements, decisions, orders, indictments,
    briefs, motions, and other filings.
    """
    results = []
    skip_formats = set((skip_formats or []))

    case_number = case.get("case_number")
    if not case_number:
        http.logger.warning(f"  [UCR] No case number for {case['slug']}, skipping")
        return results

    documents = get_ucr_documents(http, case_number)
    if not documents:
        http.logger.warning(f"  [UCR] No documents for {case_number}")
        return results

    # Filter to documents belonging to this case number
    docs = [d for d in documents if case_number in d.get("CaseNumber", "")]

    # Count by type
    from collections import Counter
    type_counts = Counter(d.get("DocTypeDesc", "").strip() for d in docs)
    http.logger.info(f"  [UCR] {len(docs)} documents for {case_number}: " +
                     ", ".join(f"{c} {t}" for t, c in type_counts.most_common()))

    # Filter by file format
    downloadable = []
    skipped_format = 0
    skipped_hash = 0
    for doc in docs:
        url = doc.get("DocumentPath", "")
        if not url:
            continue
        ext = url.rsplit('.', 1)[-1].lower() if '.' in url else ''
        if ext in skip_formats:
            skipped_format += 1
            continue
        if '#' in url and not http.ucr_authenticated:
            skipped_hash += 1
            results.append({
                "case": case["slug"],
                "type": doc.get("DocTypeDesc", "").strip().lower().replace(" ", "_"),
                "url": url,
                "filename": "",
                "filepath": "",
                "success": False,
                "title": doc.get("DocumentTitle", "").strip(),
                "date": doc.get("DocFilingDate", "").strip(),
                "skip_reason": "broken '#' URL — needs --ucr-email/--ucr-password",
            })
            continue
        downloadable.append(doc)

    if skipped_format:
        http.logger.info(f"  [UCR] Skipped {skipped_format} files (formats: {', '.join(skip_formats)})")
    if skipped_hash:
        http.logger.info(f"  [UCR] Skipped {skipped_hash} files with broken '#' URLs (no UCR auth)")

    if limit:
        http.logger.info(f"  [UCR] Limiting to {limit} downloads")
        downloadable = downloadable[:limit]

    # Download each document
    seen_filenames = {}  # track per-directory to avoid collisions
    for doc in downloadable:
        url = doc.get("DocumentPath", "")
        doc_type = doc.get("DocTypeDesc", "").strip()
        title = doc.get("DocumentTitle", "").strip()
        date = doc.get("DocFilingDate", "").strip()

        # Determine output subdirectory
        subdir = DOCTYPE_TO_DIR.get(doc_type, "other_filings")
        doc_dir = os.path.join(output_dir, case["slug"], subdir)
        os.makedirs(doc_dir, exist_ok=True)

        # Fix broken '#' URLs via UCR proxy
        download_url = http.fix_icr_url(url)

        # Build filename
        url_filename = url.split("/")[-1] if "/" in url else ""
        if title:
            filename = sanitize_filename(title[:150])
        elif url_filename:
            filename = sanitize_filename(url_filename.rsplit(".", 1)[0])
        else:
            filename = hashlib.md5(url.encode()).hexdigest()[:12]

        ext = get_file_extension(url)
        known_exts = ('.pdf', '.html', '.htm', '.doc', '.docx', '.tif', '.tiff', '.mp4')
        if not any(filename.lower().endswith(e) for e in known_exts):
            filename += ext

        # Handle filename collisions within same directory
        if doc_dir not in seen_filenames:
            seen_filenames[doc_dir] = set()
        if filename.lower() in seen_filenames[doc_dir]:
            base, fext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
            filename = f"{base}_{hashlib.md5(url.encode()).hexdigest()[:6]}.{fext}"
        seen_filenames[doc_dir].add(filename.lower())

        filepath = os.path.join(doc_dir, filename)
        success = download_file(http, download_url, filepath)
        results.append({
            "case": case["slug"],
            "type": doc_type.lower().replace(" ", "_").replace("/", "_"),
            "url": url,
            "filename": filename,
            "filepath": filepath,
            "success": success,
            "title": title,
            "date": date,
        })

    return results


# ============================================================================
# SCRAPER: CASE INFORMATION SHEETS (ICTY website only)
# ============================================================================

def scrape_case_info_sheets(http, case, soup, output_dir, limit=0):
    """Scrape Case Information Sheet PDFs from the ICTY website."""
    results = []
    cis_dir = os.path.join(output_dir, case["slug"], "case_info_sheets")
    os.makedirs(cis_dir, exist_ok=True)

    links_found = set()

    # Check known URL pattern
    known_url = f"{BASE_URL}/x/cases/{case['slug']}/cis/en/cis_{case['slug']}_en.pdf"
    links_found.add(known_url)

    # Also find CIS links on the page
    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True).lower()
        if '/cis/' in href.lower() or 'case information sheet' in text:
            links_found.add(urljoin(BASE_URL, href))

    http.logger.info(f"  [CIS] Found {len(links_found)} Case Information Sheet links")

    urls = sorted(links_found)
    if limit:
        urls = urls[:limit]

    for url in urls:
        url_path = urlparse(url).path
        filename = sanitize_filename(url_path.split("/")[-1])
        if not filename:
            filename = f"cis_{case['slug']}.pdf"
        if not filename.lower().endswith('.pdf'):
            filename += '.pdf'

        filepath = os.path.join(cis_dir, filename)
        success = download_file(http, url, filepath)
        results.append({
            "case": case["slug"],
            "type": "case_info_sheet",
            "url": url,
            "filename": filename,
            "filepath": filepath,
            "success": success,
        })

    return results


# ============================================================================
# SCRAPER: PRESS RELEASES (ICTY website only)
# ============================================================================

def scrape_press_releases(http, case, soup, output_dir, limit=0):
    """Scrape press releases from the ICTY website."""
    results = []
    pr_dir = os.path.join(output_dir, case["slug"], "press_releases")
    os.makedirs(pr_dir, exist_ok=True)

    links_found = {}

    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)
        href_lower = href.lower()

        is_press = False
        if '/en/sid/' in href:
            is_press = True
        elif '/press/' in href_lower:
            is_press = True
        elif any(kw in href_lower for kw in ['press', 'pr_', 'pr-']):
            is_press = True

        if is_press:
            full_url = urljoin(BASE_URL, href)
            if full_url not in links_found:
                links_found[full_url] = text

    http.logger.info(f"  [Press] Found {len(links_found)} press release links")

    items = sorted(links_found.items())
    if limit:
        http.logger.info(f"  [Press] Limiting to {limit} downloads")
        items = items[:limit]

    for url, title in items:
        if url.lower().endswith('.pdf'):
            url_path = urlparse(url).path
            filename = sanitize_filename(url_path.split("/")[-1])
            if not filename.lower().endswith('.pdf'):
                filename += '.pdf'
            filepath = os.path.join(pr_dir, filename)
            success = download_file(http, url, filepath)
        else:
            resp = http.get(url)
            if resp:
                pr_soup = BeautifulSoup(resp.text, "lxml")
                main_content = (
                    pr_soup.find("div", class_="field-item")
                    or pr_soup.find("article")
                    or pr_soup.find(class_="region-content")
                    or pr_soup.find("main")
                    or pr_soup
                )
                if title:
                    filename = sanitize_filename(title[:120]) + ".html"
                else:
                    sid_match = re.search(r'/sid/(\d+)', url)
                    filename = f"press_{sid_match.group(1)}.html" if sid_match else sanitize_filename(urlparse(url).path.split('/')[-1]) + ".html"
                filepath = os.path.join(pr_dir, filename)
                save_html_content(str(main_content), filepath)
                success = True
            else:
                filename = "unknown"
                filepath = ""
                success = False

        results.append({
            "case": case["slug"],
            "type": "press_release",
            "url": url,
            "title": title,
            "filename": filename,
            "filepath": filepath,
            "success": success,
        })

    return results


# ============================================================================
# STATISTICS & REPORTING
# ============================================================================

def generate_statistics(all_results, output_dir, http, start_time):
    """Generate comprehensive statistics about the scrape."""
    stats_dir = os.path.join(output_dir, "_statistics")
    os.makedirs(stats_dir, exist_ok=True)

    total_files = len(all_results)
    successful = sum(1 for r in all_results if r.get("success"))
    failed = total_files - successful

    case_stats = defaultdict(lambda: defaultdict(lambda: {"total": 0, "success": 0, "failed": 0}))
    for r in all_results:
        c = r["case"]
        dtype = r["type"]
        case_stats[c][dtype]["total"] += 1
        if r.get("success"):
            case_stats[c][dtype]["success"] += 1
        else:
            case_stats[c][dtype]["failed"] += 1

    type_stats = defaultdict(lambda: {"total": 0, "success": 0, "failed": 0})
    for r in all_results:
        dtype = r["type"]
        type_stats[dtype]["total"] += 1
        if r.get("success"):
            type_stats[dtype]["success"] += 1
        else:
            type_stats[dtype]["failed"] += 1

    elapsed = time.time() - start_time

    # JSON Report
    report = {
        "scrape_date": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "total_requests": http.request_count,
        "total_files_attempted": total_files,
        "total_successful": successful,
        "total_failed": failed,
        "failed_urls": http.failed_urls,
        "type_summary": dict(type_stats),
        "case_summary": {c: dict(types) for c, types in sorted(case_stats.items())},
    }
    with open(os.path.join(stats_dir, "scrape_report.json"), 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # CSV Report
    with open(os.path.join(stats_dir, "scrape_report.csv"), 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            "case", "type", "url", "filename", "filepath", "success", "title"
        ])
        writer.writeheader()
        for r in all_results:
            writer.writerow({
                "case": r.get("case", ""),
                "type": r.get("type", ""),
                "url": r.get("url", ""),
                "filename": r.get("filename", ""),
                "filepath": r.get("filepath", ""),
                "success": r.get("success", False),
                "title": r.get("title", ""),
            })

    # Human-readable summary
    summary_lines = [
        "=" * 70,
        "ICTY ARCHIVE SCRAPE REPORT",
        "=" * 70,
        f"Date:            {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Duration:        {elapsed/60:.1f} minutes",
        f"HTTP Requests:   {http.request_count}",
        "",
        f"TOTALS:",
        f"  Files attempted:  {total_files}",
        f"  Successful:       {successful}",
        f"  Failed:           {failed}",
        "",
        f"BY TYPE:",
    ]
    for dtype, stats in sorted(type_stats.items()):
        summary_lines.append(
            f"  {dtype:35s}  OK: {stats['success']:5d}  FAIL: {stats['failed']:5d}  TOTAL: {stats['total']:5d}"
        )

    if http.failed_urls:
        summary_lines.extend([
            "",
            f"FAILED URLs ({len(http.failed_urls)}):",
            "-" * 70,
        ])
        for url in http.failed_urls[:50]:
            summary_lines.append(f"  {url}")
        if len(http.failed_urls) > 50:
            summary_lines.append(f"  ... and {len(http.failed_urls) - 50} more")

    summary_lines.extend(["", "=" * 70])
    summary_text = "\n".join(summary_lines)

    with open(os.path.join(stats_dir, "scrape_summary.txt"), 'w', encoding='utf-8') as f:
        f.write(summary_text)

    print("\n" + summary_text)
    return report


# ============================================================================
# CHECKPOINT / RESUME SUPPORT
# ============================================================================

def load_checkpoint(output_dir):
    cp_path = os.path.join(output_dir, "_statistics", "checkpoint.json")
    if os.path.exists(cp_path):
        with open(cp_path, 'r') as f:
            return json.load(f)
    return {"completed_cases": [], "results": []}


def save_checkpoint(output_dir, completed_cases, results):
    cp_path = os.path.join(output_dir, "_statistics", "checkpoint.json")
    os.makedirs(os.path.dirname(cp_path), exist_ok=True)
    with open(cp_path, 'w', encoding='utf-8') as f:
        json.dump({
            "completed_cases": completed_cases,
            "results": results,
            "timestamp": datetime.now().isoformat(),
        }, f, indent=2)


# ============================================================================
# MAIN SCRAPER ORCHESTRATION
# ============================================================================

def scrape_case(http, case, output_dir, skip_formats=None, limit=0):
    """Scrape all document types for a single case."""
    http.logger.info(f"\n{'='*60}")
    http.logger.info(f"SCRAPING CASE: {case['slug']} ({case['name']}) [{case.get('case_number', 'no case#')}]")
    http.logger.info(f"{'='*60}")
    if limit:
        http.logger.info(f"  (limiting to {limit} downloads per source)")

    results = []

    # 1. All court documents from UCR API
    http.logger.info(f"  Scraping court documents from UCR...")
    results.extend(scrape_ucr_documents(http, case, output_dir, skip_formats, limit))

    # 2. Case page for press releases and case info sheets
    http.logger.info(f"  Loading case page for press releases & CIS...")
    resp = http.get(case["url"])
    if resp:
        soup = BeautifulSoup(resp.text, "lxml")

        http.logger.info(f"  Scraping Case Information Sheets...")
        results.extend(scrape_case_info_sheets(http, case, soup, output_dir, limit))

        http.logger.info(f"  Scraping Press Releases...")
        results.extend(scrape_press_releases(http, case, soup, output_dir, limit))
    else:
        http.logger.warning(f"  Could not load case page for {case['slug']}")

    ok = sum(1 for r in results if r["success"])
    fail = sum(1 for r in results if not r["success"])
    http.logger.info(f"  Case {case['slug']} complete: {ok} OK, {fail} failed, {ok+fail} total")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="ICTY Archive Scraper - Downloads all public court records "
                    "from the UCR database and ICTY website."
    )
    parser.add_argument(
        "--cases", nargs="+",
        help="Specific case slugs to scrape (e.g., tadic mladic). Default: all."
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY,
        help=f"Delay between requests in seconds (default: {DEFAULT_DELAY}s)"
    )
    parser.add_argument(
        "--list-cases", action="store_true",
        help="Just list all available cases and exit"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last checkpoint (skip completed cases)"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit downloads per source per case (0 = unlimited, e.g. --limit 2 for testing)"
    )
    parser.add_argument(
        "--ucr-email",
        help="UCR login email (enables download of all transcripts including broken '#' URLs)"
    )
    parser.add_argument(
        "--ucr-password",
        help="UCR login password"
    )
    parser.add_argument(
        "--skip-formats", default="mp4",
        help="Comma-separated file extensions to skip (default: mp4). "
             "Use 'none' to download everything."
    )

    args = parser.parse_args()
    output_dir = args.output

    # Parse skip formats
    if args.skip_formats.lower() == 'none':
        skip_formats = set()
    else:
        skip_formats = set(f.strip().lower() for f in args.skip_formats.split(',') if f.strip())

    # Setup
    os.makedirs(output_dir, exist_ok=True)
    logger = setup_logging(output_dir)
    http = ICTYSession(delay=args.delay, logger=logger)

    start_time = time.time()
    logger.info("ICTY Archive Scraper starting...")
    logger.info(f"Output directory: {os.path.abspath(output_dir)}")
    logger.info(f"Request delay: {args.delay}s")
    if skip_formats:
        logger.info(f"Skipping formats: {', '.join(sorted(skip_formats))}")

    # UCR login
    if args.ucr_email and args.ucr_password:
        if not http.ucr_login(args.ucr_email, args.ucr_password):
            logger.warning("UCR login failed — transcripts with '#' URLs will be skipped")
    else:
        logger.info("No UCR credentials — some transcripts with '#' URLs will be skipped. "
                     "Use --ucr-email/--ucr-password for full downloads.")

    # Discover cases
    all_cases = discover_cases(http)
    if not all_cases:
        logger.error("No cases found. Exiting.")
        return 1

    # List-only mode
    if args.list_cases:
        print(f"\nFound {len(all_cases)} cases:\n")
        print(f"{'#':<4s} {'Slug':<35s} {'Case Number':<15s} {'Name'}")
        print("-" * 90)
        for i, case in enumerate(all_cases, 1):
            print(f"{i:<4d} {case['slug']:<35s} {case.get('case_number','?'):<15s} {case['name'][:40]}")
        return 0

    # Filter cases
    if args.cases:
        requested = set(c.lower() for c in args.cases)
        all_cases = [c for c in all_cases if c["slug"].lower() in requested]
        not_found = requested - set(c["slug"].lower() for c in all_cases)
        if not_found:
            logger.warning(f"Cases not found: {not_found}")
        logger.info(f"Scraping {len(all_cases)} selected cases")
    else:
        logger.info(f"Scraping all {len(all_cases)} cases")

    # Resume support
    all_results = []
    completed_cases = []
    if args.resume:
        checkpoint = load_checkpoint(output_dir)
        completed_cases = checkpoint.get("completed_cases", [])
        all_results = checkpoint.get("results", [])
        logger.info(f"Resuming: {len(completed_cases)} cases already completed")

    # Scrape each case
    total_cases = len(all_cases)
    for idx, case in enumerate(all_cases, 1):
        if case["slug"] in completed_cases:
            logger.info(f"[{idx}/{total_cases}] Skipping {case['slug']} (already completed)")
            continue

        logger.info(f"\n[{idx}/{total_cases}] Processing: {case['slug']}")
        case_results = scrape_case(http, case, output_dir, skip_formats, args.limit)
        all_results.extend(case_results)

        completed_cases.append(case["slug"])
        save_checkpoint(output_dir, completed_cases, all_results)

    # Final statistics
    logger.info("\nGenerating statistics...")
    generate_statistics(all_results, output_dir, http, start_time)
    logger.info(f"\nDone! Output in: {os.path.abspath(output_dir)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
