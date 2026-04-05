"""
Fetch HMRC manual sections from Gov.uk for indexing into Azure AI Search.

Each manual section is fetched as structured HTML, cleaned, and written to
data/raw/{manual_name}/{section_ref}.json with metadata.

Usage:
    python fetch_hmrc_sources.py [--manual PTM] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Manual catalogue
# ---------------------------------------------------------------------------

MANUALS: dict[str, dict] = {
    "PTM": {
        "base_url": "https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual",
        "topic_tags": ["pension"],
    },
    "IHTM": {
        "base_url": "https://www.gov.uk/hmrc-internal-manuals/inheritance-tax-manual",
        "topic_tags": ["iht"],
    },
    "CG": {
        "base_url": "https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual",
        "topic_tags": ["cgt"],
    },
    "PIM": {
        "base_url": "https://www.gov.uk/hmrc-internal-manuals/property-income-manual",
        "topic_tags": ["property_income"],
    },
}

# ---------------------------------------------------------------------------
# Citation map: manual section ref → rule_ids
# This is extended as new rules are added to the registry.
# The citations field in each rule YAML is the source of truth;
# tag_rules.py regenerates this map automatically.
# ---------------------------------------------------------------------------

CITATION_MAP: dict[str, list[str]] = {
    # Bucket A — Pension Decumulation
    "PTM063300": ["pension_serious_ill_health_lump_sum"],
    "PTM073000": ["pension_death_benefit_lump_sum"],
    "PTM081000": ["pension_annual_allowance"],
    "PTM044000": ["pension_carry_forward"],
    "PTM052000": ["pension_lsa", "pension_commencement_lump_sum"],
    "PTM088000": ["money_purchase_annual_allowance"],
    # Bucket E — Inheritance Tax
    "IHTM04261": ["iht_nil_rate_band"],
    "IHTM46000": ["iht_residence_nil_rate_band", "iht_rnrb_taper"],
    "IHTM14180": ["iht_annual_gift_exemption", "iht_small_gifts_exemption"],
    "IHTM04057": ["iht_taper_relief"],
    "IHTM04054": ["iht_potentially_exempt_transfer"],
    "IHTM17000": ["iht_business_property_relief"],
    # Bucket F — CGT / Property
    "CG64200":   ["private_residence_relief"],
    "CG68300":   ["prr_letting_relief"],
    # Bucket C — Allowances
    "EIM01530":  ["blind_persons_allowance"],
}

OUTPUT_DIR = Path("data/raw")


def fetch_section(url: str, ref: str, topic_tags: list[str]) -> dict | None:
    """Fetch one manual section page and return a structured dict."""
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"  WARN: failed to fetch {url}: {exc}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Gov.uk manuals put the body in <div class="govuk-govspeak">
    body = soup.find("div", class_="govuk-govspeak")
    if body is None:
        return None

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ref

    content = body.get_text(separator="\n").strip()
    content = re.sub(r"\n{3,}", "\n\n", content)  # collapse blank lines

    return {
        "ref": ref,
        "title": title,
        "content": content,
        "source_url": url,
        "source_type": "manual",
        "topic_tags": topic_tags,
        "rule_ids": CITATION_MAP.get(ref, []),
        "effective_from": "2025-26",
    }


def fetch_manual(manual_name: str, full: bool = False, dry_run: bool = False) -> int:
    """Fetch all known sections for a manual. Returns count of fetched docs.

    When *full* is True, existing cached files are deleted and all sections
    are re-fetched regardless of whether they were previously downloaded.
    """
    cfg = MANUALS[manual_name]
    base_url = cfg["base_url"]
    topic_tags = cfg["topic_tags"]

    out_dir = OUTPUT_DIR / manual_name
    out_dir.mkdir(parents=True, exist_ok=True)

    if full and not dry_run:
        deleted = list(out_dir.glob("*.json"))
        for f in deleted:
            f.unlink()
        if deleted:
            print(f"  Cleared {len(deleted)} cached sections for {manual_name} (--full).")

    # Fetch the manual index to discover all section refs
    print(f"Fetching {manual_name} index from {base_url} …")
    resp = httpx.get(base_url, timeout=15, follow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.select("a[href*='/hmrc-internal-manuals/']")
    section_urls = {
        a["href"].rstrip("/").split("/")[-1].upper(): f"https://www.gov.uk{a['href']}"
        for a in links
        if re.search(r"/[a-z]{2,6}\d{4,}", a["href"], re.I)
    }

    print(f"  Found {len(section_urls)} sections.")
    fetched = 0

    for ref, url in section_urls.items():
        dest = out_dir / f"{ref}.json"
        if dest.exists():
            continue  # skip already-fetched

        if dry_run:
            print(f"  [dry-run] would fetch {ref}")
            continue

        doc = fetch_section(url, ref, topic_tags)
        if doc:
            dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
            fetched += 1

        time.sleep(0.3)  # be polite to Gov.uk

    return fetched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", choices=list(MANUALS), help="Fetch one manual only")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Delete cached sections and re-fetch everything (default: skip cached)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    targets = [args.manual] if args.manual else list(MANUALS)
    total = 0
    for manual in targets:
        total += fetch_manual(manual, full=args.full, dry_run=args.dry_run)

    print(f"\nDone. Fetched {total} new sections.")


if __name__ == "__main__":
    main()
