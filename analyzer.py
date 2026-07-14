"""Core logic: render a newspaper PDF to page images, ask Claude Sonnet to
estimate the advertising coverage of each page, then combine into a single
area-weighted overall percentage.
"""

import base64
import json
import os
import re
import sys

import fitz  # PyMuPDF
from anthropic import Anthropic

__version__ = "1.2"

MODEL = "claude-sonnet-5"

# Cap the long edge of each rendered page so vision token cost stays reasonable.
TARGET_LONG_EDGE_PX = 1568
MAX_ZOOM = 2.0

SYSTEM_PROMPT = (
    "You are a newspaper layout analyst. Estimate what percentage of THIS page's "
    "printed area is occupied by paid advertisements (display ads, classifieds, "
    "inserts). Editorial content (articles, photos accompanying stories), headers, "
    "page numbers, and the masthead are NOT ads. Judge purely by the fraction of the "
    "physical page area the ads cover."
)

USER_PROMPT = (
    'Respond with ONLY strict JSON and nothing else: {"ad_percentage": <number 0-100>}'
)


def app_dir():
    """Folder the app lives in — next to the .exe when frozen, else this file."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_api_key():
    """Read the Anthropic API key from config.ini next to the app.

    Returns the key string, or raises RuntimeError with a friendly message.
    """
    import configparser

    path = os.path.join(app_dir(), "config.ini")
    if not os.path.exists(path):
        raise RuntimeError(
            "config.ini not found. Create a file named 'config.ini' next to the app "
            "with your Anthropic API key (see config.example.ini)."
        )
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    key = parser.get("anthropic", "api_key", fallback="").strip()
    if not key or key.startswith("sk-ant-REPLACE"):
        raise RuntimeError(
            "No API key set in config.ini. Add your key under [anthropic] api_key = ..."
        )
    return key


def page_count(pdf_path):
    """Number of pages in a PDF (used to size cumulative progress up front)."""
    doc = fitz.open(pdf_path)
    try:
        return doc.page_count
    finally:
        doc.close()


def render_pages(pdf_path):
    """Yield (page_index, total_pages, png_bytes, area_points) for each page."""
    doc = fitz.open(pdf_path)
    try:
        total = doc.page_count
        for i in range(total):
            page = doc.load_page(i)
            rect = page.rect
            area = rect.width * rect.height  # in PDF points^2, used for weighting
            long_edge = max(rect.width, rect.height) or 1
            zoom = min(MAX_ZOOM, TARGET_LONG_EDGE_PX / long_edge)
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            yield i, total, pix.tobytes("png"), area
    finally:
        doc.close()


def _parse_percentage(text):
    """Pull a 0-100 number out of the model's reply; return float or None."""
    try:
        data = json.loads(text)
        return float(data["ad_percentage"])
    except (ValueError, KeyError, TypeError):
        pass
    match = re.search(r'"?ad_percentage"?\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)', text)
    if not match:
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if match:
        return float(match.group(1))
    return None


def estimate_page_ad_pct(client, png_bytes):
    """One vision call: return the estimated ad percentage (0-100) for a page."""
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    pct = _parse_percentage(text)
    if pct is None:
        return 0.0
    return max(0.0, min(100.0, pct))


def _analyze_pdf_pages(client, pdf_path, done_offset, grand_total, progress_cb):
    """Vision-analyze every page of one PDF.

    Reports cumulative progress as progress_cb(done, grand_total), where `done`
    counts pages across the whole run (paper + all special sections), starting
    from done_offset. Returns (pcts, areas_pts, per_page):
    - pcts:      list of ad percentages (0-100), one per page
    - areas_pts: list of PDF point^2 areas, one per page (for size ratios)
    - per_page:  list of {"page": n, "ad_percentage": p}
    """
    pcts = []
    areas_pts = []
    per_page = []
    for index, _total, png_bytes, area in render_pages(pdf_path):
        if progress_cb:
            progress_cb(done_offset + index + 1, grand_total)
        pct = estimate_page_ad_pct(client, png_bytes)
        pcts.append(pct)
        areas_pts.append(area)
        per_page.append({"page": index + 1, "ad_percentage": pct})
    return pcts, areas_pts, per_page


def _section_footprint(areas_pts, paper_page_area_in2):
    """Turn a special section's per-page point areas into a real footprint.

    A special section's size is always a multiple of half a paper page, and its
    PDF is laid out how it will appear in the paper. We size it by ratio to the
    largest page in the section (assumed to be one full paper page), snapping
    each page to the nearest half page. This is scale-independent — only the
    in-section ratios matter — then we anchor to the publication's authoritative
    paper page area so the units match the paper and inserts.

    Limitation: a section made ENTIRELY of half-pages would treat its largest
    half-page as a full page and overcount 2x. Sections almost always contain at
    least one full page, so this is acceptable.

    Returns (units_per_page, size_pages, footprint_in2).
    """
    full_ref = max(areas_pts) if areas_pts else 0.0
    units = []
    for area in areas_pts:
        if full_ref <= 0:
            u = 0.5
        else:
            u = max(0.5, round((area / full_ref) / 0.5) * 0.5)
        units.append(u)
    size_pages = sum(units)
    footprint_in2 = size_pages * paper_page_area_in2
    return units, size_pages, footprint_in2


def analyze_pdf(
    pdf_path,
    progress_cb=None,
    paper_page_area_in2=None,
    inserts=None,
    special_sections=None,
):
    """Analyze the paper PDF and fold in any inserts and special sections.

    progress_cb(done, grand_total) is called before each page is analyzed;
    `done` is 1-based and counts cumulatively across the paper and every special
    section so a single progress bar spans the whole run.

    Inserts and special sections are combined in real square inches, using the
    selected publication's authoritative paper page area (paper_page_area_in2)
    rather than PDF points, so ratios are independent of how a PDF was scaled.

    - paper_page_area_in2: the publication's page area in square inches. When
      None (paper-only quick check) the original PDF-points area-weighted
      average is used and inserts / special sections are ignored.
    - inserts: optional list of {"size_in2": float, "pages": int}; each insert
      page is treated as 100% advertising.
    - special_sections: optional list of {"name": str, "pdf_path": str}. Unlike
      inserts these are NOT 100% ad — each is vision-analyzed page by page like
      the paper, and its footprint is derived from its PDF (see
      _section_footprint).

    Returns (overall_pct, per_page_list, breakdown). breakdown is a dict with
    "paper", "sections" and "inserts" contributions (see module callers).
    """
    client = Anthropic(api_key=load_api_key())

    sections = special_sections or []
    # Cumulative progress spans the paper plus every special section.
    grand_total = page_count(pdf_path)
    if paper_page_area_in2 is not None:
        for sec in sections:
            grand_total += page_count(sec["pdf_path"])

    pcts, areas_pts, per_page = _analyze_pdf_pages(
        client, pdf_path, 0, grand_total, progress_cb
    )

    total_area_pts = sum(areas_pts)
    if total_area_pts == 0:
        raise RuntimeError("The PDF has no analyzable pages.")

    if paper_page_area_in2 is None:
        # Paper-only: original PDF-points area-weighted average.
        weighted_sum_pts = sum(a * p for a, p in zip(areas_pts, pcts))
        overall = weighted_sum_pts / total_area_pts
        breakdown = {
            "paper": {
                "ad_pct": overall,
                "pages": len(pcts),
                "total_in2": None,
                "adv_in2": None,
            },
            "sections": [],
            "inserts": {"count": 0, "total_in2": 0.0},
        }
        return overall, per_page, breakdown

    # Inches-based combine using the publication's real paper page area.
    a = paper_page_area_in2
    n = len(pcts)
    paper_total = a * n
    paper_adv = a * sum(pcts) / 100.0
    paper_ad_pct = sum(pcts) / n if n else 0.0

    ins_total = 0.0
    ins_adv = 0.0
    for ins in inserts or []:
        area = ins["size_in2"] * ins["pages"]
        ins_total += area
        ins_adv += area  # inserts are 100% advertising

    sec_total = 0.0
    sec_adv = 0.0
    section_breakdown = []
    done_offset = len(pcts)
    for sec in sections:
        s_pcts, s_areas, _s_per_page = _analyze_pdf_pages(
            client, sec["pdf_path"], done_offset, grand_total, progress_cb
        )
        done_offset += len(s_pcts)
        units, size_pages, footprint_in2 = _section_footprint(s_areas, a)
        unit_sum = sum(units)
        adv_in2 = a * sum(u * p / 100.0 for u, p in zip(units, s_pcts))
        sec_ad_pct = (
            sum(u * p for u, p in zip(units, s_pcts)) / unit_sum
            if unit_sum
            else 0.0
        )
        sec_total += footprint_in2
        sec_adv += adv_in2
        section_breakdown.append({
            "name": sec["name"],
            "ad_pct": sec_ad_pct,
            "size_pages": size_pages,
            "total_in2": footprint_in2,
            "adv_in2": adv_in2,
        })

    overall_total = paper_total + ins_total + sec_total
    overall_adv = paper_adv + ins_adv + sec_adv
    overall = overall_adv / overall_total * 100.0

    breakdown = {
        "paper": {
            "ad_pct": paper_ad_pct,
            "pages": n,
            "total_in2": paper_total,
            "adv_in2": paper_adv,
        },
        "sections": section_breakdown,
        "inserts": {"count": len(inserts or []), "total_in2": ins_total},
    }
    return overall, per_page, breakdown
