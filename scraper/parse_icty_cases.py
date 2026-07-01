"""
Parse ICTY case slugs and case numbers from the icty.org case dropdown HTML.

Usage:
    python3 parse_icty_cases.py icty_cases.html

Input: HTML file containing the <option> elements from the icty.org case number dropdown.
Output: CSV file with columns: slug, case_number
"""

import re
import csv
import sys
import html
from pathlib import Path


def parse_icty_cases(html_text):
    """Extract slug and case number from ICTY HTML options."""
    results = []

    for match in re.finditer(
        r'<option\s+value="/case/([^"]+)">(.*?)</option>', html_text
    ):
        slug = match.group(1)
        case_number = html.unescape(match.group(2)).strip()
        # Normalize spaces around & (e.g. "IT-00-39 & 40/1" -> "IT-00-39&40/1")
        case_number = re.sub(r'\s*&\s*', '&', case_number)
        results.append({"slug": slug, "case_number": case_number})

    return results


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <html_file>")
        sys.exit(1)

    html_file = Path(sys.argv[1])
    html_text = html_file.read_text(encoding="utf-8")
    cases = parse_icty_cases(html_text)

    out_file = html_file.with_name("icty_cases.csv")
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["slug", "case_number"])
        writer.writeheader()
        writer.writerows(cases)

    print(f"Extracted {len(cases)} cases -> {out_file}")
    for c in cases:
        print(f"  {c['slug']:<35s} {c['case_number']}")


if __name__ == "__main__":
    main()
