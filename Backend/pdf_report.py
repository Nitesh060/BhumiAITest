"""
pdf_report.py
=============
Generates a professional, SatSource-style PDF report from the exact data
already computed by /calculate — same numbers shown on the dashboard, no
new/fabricated values.

Sections (mirrors the SatSource sample the user shared):
  1. Cover header + Overall FarmScore gauge + factor breakdown
  2. Farm Details (coordinates, land use, irrigation, cropping intensity)
  3. Cropping History (3-year Kharif/Rabi table, if available)
  4. Water Conditions (rainfall + groundwater trend charts)
  5. Regional Parameters (soil, AEZ, temp range, prosperity, water body)
  6. Score Bands / Colour Ranges legend
  7. Glossary
  8. Disclaimer

Charts are rendered with matplotlib to PNG in a temp dir, then placed as
Image flowables in a reportlab Platypus document — no external services.
"""

from __future__ import annotations

import io
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import requests

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, HRFlowable
)

from glossary import GLOSSARY_TERMS
from enrichment_service import fetch_farm_thumbnail_url

logger = logging.getLogger(__name__)

BRAND_BLUE = colors.HexColor("#1a56c4")
BRAND_BLUE_DARK = colors.HexColor("#0f3a92")
BRAND_GREEN = colors.HexColor("#2f9e63")
GREY = colors.HexColor("#666666")
LIGHT_GREY = colors.HexColor("#f2f2f2")
PAGE_W, PAGE_H = A4
HEADER_H = 22 * mm
FOOTER_H = 14 * mm

GRADE_COLOR = {
    "Poor": "#d64545", "Fair": "#e8912d", "Average": "#e8c02d",
    "Good": "#7fbf3f", "Excellent": "#2f9e63",
}


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("ReportTitle", parent=ss["Heading1"], fontSize=16, textColor=BRAND_BLUE, spaceAfter=2))
    ss.add(ParagraphStyle("SectionHeading", parent=ss["Heading2"], fontSize=11.5, textColor=BRAND_BLUE_DARK,
                           spaceBefore=16, spaceAfter=8, borderPadding=(0, 0, 4, 0)))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], fontSize=8, textColor=GREY))
    ss.add(ParagraphStyle("Caption", parent=ss["Normal"], fontSize=7.5, textColor=GREY, alignment=1, spaceBefore=3))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], fontSize=9, leading=12))
    return ss


def _section_heading(text: str, ss) -> list:
    """A section heading with a small green accent bar to its left,
    instead of a plain bold line — closer to how the SatSource sample
    breaks up sections."""
    bar = Table([[""]], colWidths=[4], rowHeights=[13])
    bar.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), BRAND_GREEN),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
    ]))
    heading = Table(
        [[bar, Paragraph(text, ss["SectionHeading"])]],
        colWidths=[4, 160 * mm],
    )
    heading.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
        ("TOPPADDING", (1, 0), (1, 0), 0),
        ("BOTTOMPADDING", (1, 0), (1, 0), 0),
    ]))
    return [heading]


# ---------------------------------------------------------------------------
# Chart builders (matplotlib -> PNG bytes)
# ---------------------------------------------------------------------------

def _gauge_chart(score: int, tmpdir: str) -> str:
    """Semi-circular gauge, 300-900 scale, colour bands from scoring.py."""
    fig, ax = plt.subplots(figsize=(3.2, 2.0), subplot_kw={"projection": "polar"})
    bands = [(300, 421, "#d64545"), (421, 541, "#e8912d"), (541, 661, "#e8c02d"),
             (661, 781, "#7fbf3f"), (781, 901, "#2f9e63")]
    span = 900 - 300

    for lo, hi, color in bands:
        theta1 = 3.14159 * (1 - (lo - 300) / span)
        theta2 = 3.14159 * (1 - (hi - 300) / span)
        ax.bar(x=(theta1 + theta2) / 2, height=0.3, width=(theta1 - theta2),
               bottom=0.7, color=color, edgecolor="white")

    needle_theta = 3.14159 * (1 - (score - 300) / span)
    ax.plot([needle_theta, needle_theta], [0, 0.7], color="black", linewidth=2)
    ax.set_theta_zero_location("W")
    ax.set_theta_direction(-1)
    ax.set_thetamin(0)
    ax.set_thetamax(180)
    ax.set_ylim(0, 1)
    ax.axis("off")

    path = str(Path(tmpdir) / "gauge.png")
    fig.savefig(path, dpi=180, transparent=True, bbox_inches="tight")
    plt.close(fig)
    return path


def _bar_chart(labels, values, avg_value, color, ylabel, title, tmpdir, fname):
    fig, ax = plt.subplots(figsize=(6.0, 2.2))
    ax.bar([str(l) for l in labels], values, color=color, width=0.6)
    if avg_value is not None:
        ax.axhline(avg_value, color="#1a56c4", linewidth=1.2)
        ax.text(0, avg_value, f"{avg_value:.0f}", fontsize=7, va="bottom", color="#1a56c4")
    ax.set_ylabel(ylabel, fontsize=8)
    ax.set_title(title, fontsize=9, loc="left")
    ax.tick_params(labelsize=7)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    fig.tight_layout()

    path = str(Path(tmpdir) / fname)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _cover_section(data: Dict[str, Any], ss, tmpdir: str) -> list:
    story = []
    score = data.get("score", 0)
    grade = data.get("grade", "—")

    story += _section_heading("Overall Bhumi AI Score", ss)

    gauge_path = _gauge_chart(score, tmpdir)
    gauge_img = Image(gauge_path, width=75 * mm, height=47 * mm)

    components = data.get("components", {})
    rows = [["Parameter", "Raw Value", "Sub-score", "Weight", "Source"]]
    for key, c in components.items():
        rows.append([
            key.upper(), f"{c.get('raw_value')}{c.get('unit','')}",
            f"{c.get('sub_score')}/100", f"{c.get('weight')}%", c.get("source", "—"),
        ])
    factor_table = Table(rows, colWidths=[22*mm, 26*mm, 22*mm, 16*mm, 28*mm])
    factor_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    score_label = Paragraph(
        f'<font size="20" color="{GRADE_COLOR.get(grade, "#000000")}"><b>{score}</b></font>'
        f'<br/><font size="11" color="{GRADE_COLOR.get(grade, "#000000")}">{grade}</font>',
        ss["Body"])

    header_table = Table([[gauge_img, score_label]], colWidths=[85*mm, 30*mm])
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    story.append(header_table)
    story.append(Spacer(1, 6))
    story.append(factor_table)
    return story


def _farm_location_section(data: Dict[str, Any], ss, tmpdir: str) -> list:
    """Real Sentinel-2 true-colour image of the farm, not a static map
    screenshot — same satellite source used for the NDVI/NDMI numbers
    elsewhere in this report, so what you see visually matches what was
    measured.
    """
    coords = data.get("coordinates", {})
    lat, lng = coords.get("lat"), coords.get("lng")
    if lat is None or lng is None:
        return []

    story = _section_heading("Farm Location", ss)

    try:
        polygon = data.get("farm_polygon") or data.get("polygon")
        thumb_url = fetch_farm_thumbnail_url(lat, lng, polygon)
        if not thumb_url:
            raise ValueError("No thumbnail URL returned")

        resp = requests.get(thumb_url, timeout=20)
        resp.raise_for_status()

        img_path = str(Path(tmpdir) / "farm_photo.png")
        with open(img_path, "wb") as f:
            f.write(resp.content)

        img = Image(img_path, width=90 * mm, height=90 * mm)
        story.append(img)
        story.append(Paragraph(
            f"Sentinel-2 true-colour composite, ~700 m around {lat}° N, {lng}° E. "
            f"Cloud-free scenes, 2023–2024.", ss["Caption"]))
    except Exception:
        logger.exception("Could not embed farm thumbnail in PDF")
        story.append(Paragraph(
            "Satellite image unavailable for this report (imagery service did not respond in time).",
            ss["Small"]))

    return story


def _farm_details_section(data: Dict[str, Any], ss) -> list:
    story = _section_heading("Farm Details", ss)
    coords = data.get("coordinates", {})
    enrichment = data.get("enrichment", {}) or {}
    irrigation = enrichment.get("irrigation") or {}
    cropping_intensity = enrichment.get("cropping_intensity") or {}
    soil = enrichment.get("soil_type") or {}
    aez = enrichment.get("agro_ecological_zone") or {}
    yield_pred = data.get("yield_prediction")

    rows = [
        ["Farm Centroid", f"{coords.get('lat')}° N, {coords.get('lng')}° E"],
        ["Land Use Type", "Agricultural"],
        ["Irrigation Condition", "Irrigated" if irrigation.get("likely_irrigated") else
         ("Not Irrigated" if irrigation.get("likely_irrigated") is False else "—")],
        ["Cropping Intensity", cropping_intensity.get("label", "—")],
        ["Soil Type", soil.get("label", "—")],
        ["Agro-Ecological Zone", aez.get("zone", "—")],
    ]
    if yield_pred:
        total = f" (~{yield_pred['estimated_total_yield_quintal']} quintal on {yield_pred['area_ha']} ha)" if yield_pred.get("estimated_total_yield_quintal") is not None else ""
        rows.append([f"Est. Yield ({yield_pred['crop']})", f"{yield_pred['estimated_yield_kg_per_ha']} kg/ha{total}"])

    t = Table(rows, colWidths=[50*mm, 100*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_GREY),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    if yield_pred:
        story.append(Paragraph(
            "Yield is a formula-based estimate (NDVI-proportional to national average yield) — not a trained ML prediction or a measured harvest.",
            ss["Small"]))
    return story


def _cropping_history_section(data: Dict[str, Any], ss) -> list:
    enrichment = data.get("enrichment", {}) or {}
    history = enrichment.get("cropping_history")
    if not history or not history.get("years"):
        return []

    story = _section_heading("Cropping History (Satellite-derived, 3-year)", ss)
    rows = [["Year", "Kharif NDVI", "Kharif Status", "Rabi NDVI", "Rabi Status"]]
    for y in history["years"]:
        k, r = y.get("kharif", {}), y.get("rabi", {})
        rows.append([
            str(y.get("year")),
            f"{k.get('ndvi')}" if k.get("ndvi") is not None else "—",
            "Cropped" if k.get("cropped") else "Fallow/No signal",
            f"{r.get('ndvi')}" if r.get("ndvi") is not None else "—",
            "Cropped" if r.get("cropped") else "Fallow/No signal",
        ])
    t = Table(rows, colWidths=[18*mm, 28*mm, 34*mm, 28*mm, 34*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
    ]))
    story.append(t)
    story.append(Paragraph(
        "Note: this reflects satellite-detected vegetation presence per season, "
        "not confirmed crop-species identification.", ss["Small"]))
    return story


def _water_conditions_section(data: Dict[str, Any], ss, tmpdir: str) -> list:
    story = _section_heading("Water Conditions", ss)

    rainfall_monthly = data.get("rainfall_monthly") or []
    gw_trend = data.get("groundwater_trend") or []

    charts_added = False
    if rainfall_monthly:
        labels = [m["month"] for m in rainfall_monthly]
        values = [m.get("mm_per_day") or 0 for m in rainfall_monthly]
        avg = sum(values) / len(values) if values else None
        path = _bar_chart(labels, values, avg, "#7fb2e8", "mm/day", "Rainfall (growing-season months)", tmpdir, "rain.png")
        story.append(Image(path, width=150*mm, height=55*mm))
        charts_added = True

    if gw_trend:
        labels = [t["year"] for t in gw_trend]
        values = [t.get("groundwater") or 0 for t in gw_trend]
        avg = sum(values) / len(values) if values else None
        path = _bar_chart(labels, values, avg, "#7fa89e", "kg/m²", "Groundwater Trend (yearly)", tmpdir, "gw.png")
        story.append(Image(path, width=150*mm, height=55*mm))
        charts_added = True

    if not charts_added:
        story.append(Paragraph("No trend data available for this location.", ss["Small"]))
    return story


def _regional_parameters_section(data: Dict[str, Any], ss) -> list:
    enrichment = data.get("enrichment", {}) or {}
    story = _section_heading("Regional Parameters", ss)

    temp_range = enrichment.get("temperature_annual_range") or {}
    prosperity = enrichment.get("regional_prosperity") or {}
    water_body = enrichment.get("nearest_water_body") or {}
    land_cover = enrichment.get("adjacent_land_cover") or {}
    topo = enrichment.get("topography") or {}
    pop = enrichment.get("village_population") or {}
    drought = enrichment.get("drought_instances") or {}

    top_land = ", ".join(f"{b['class']} {b['percent']}%" for b in (land_cover.get("breakdown") or [])[:3]) or "—"

    rows = [
        ["Annual Temperature Range",
         f"{temp_range.get('min_c','—')}°C – {temp_range.get('max_c','—')}°C" if temp_range.get("min_c") is not None else "—"],
        ["Water Body within 2 km", "Present" if water_body.get("water_present") else "Not detected"],
        ["Regional Prosperity (proxy)", prosperity.get("tier", "—")],
        ["Adjacent Land (top classes)", top_land],
        ["Topography", f"{topo.get('terrain','—')} ({topo.get('elevation_m','—')}m, {topo.get('slope_degrees','—')}° slope)" if topo.get("terrain") else "—"],
        ["Population (nearby, proxy)", f"~{pop.get('estimated_population'):,} within {pop.get('radius_m')}m" if pop.get("estimated_population") is not None else "—"],
        ["Drought Years (district-scale, since 2000)",
         ", ".join(str(y) for y in drought.get("drought_years", [])) or ("None detected" if drought.get("drought_years") is not None else "—")],
    ]
    t = Table(rows, colWidths=[55*mm, 95*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_GREY),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Paragraph(
        "Regional Prosperity and Population are satellite-derived proxies, not official government indices/Census figures. "
        "Drought years are approximated from CHIRPS rainfall (<75% of local long-term average), not an official drought declaration.",
        ss["Small"]))
    return story


def _colour_ranges_section(ss) -> list:
    story = _section_heading("Score Bands", ss)
    rows = [
        ["Category", "Interval (300-900 scale)"],
        ["Poor", "300 – 420"],
        ["Fair", "421 – 540"],
        ["Average", "541 – 660"],
        ["Good", "661 – 780"],
        ["Excellent", "781 – 900"],
    ]
    t = Table(rows, colWidths=[40*mm, 60*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
    ]))
    story.append(t)
    return story


def _glossary_section(ss) -> list:
    story = _section_heading("Glossary", ss)
    for term in GLOSSARY_TERMS:
        name = term["term"] + (f" ({term['full_form']})" if term.get("full_form") else "")
        story.append(Paragraph(f"<b>{name}</b> — {term['explanation']}", ss["Small"]))
        story.append(Spacer(1, 3))
    return story


def _disclaimer_section(ss) -> list:
    text = (
        "Disclaimer: This is a system-generated report using satellite remote sensing and "
        "publicly available datasets (Sentinel-2, CHIRPS, MODIS, GLDAS, ESA WorldCover, "
        "OpenLandMap, JRC Global Surface Water, VIIRS). Values are subject to the limitations "
        "of these technologies and are indicative, not certified ground-truth measurements. "
        "This report is intended for internal evaluation support only and should not be the "
        "sole basis for a lending decision."
    )
    return [Spacer(1, 10), HRFlowable(width="100%", color=colors.HexColor("#dddddd")),
            Spacer(1, 6), Paragraph(text, ParagraphStyle("Disclaimer", fontSize=7.5, textColor=GREY, leading=10))]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _draw_header_footer(canvas, doc, data: Dict[str, Any]):
    coords = data.get("coordinates", {})
    canvas.saveState()

    # ---- Header band ----
    canvas.setFillColor(BRAND_BLUE_DARK)
    canvas.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, fill=1, stroke=0)

    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(16 * mm, PAGE_H - 14 * mm, "\U0001F331 Bhumi AI Report")

    canvas.setFont("Helvetica", 8)
    ref_text = f"{coords.get('lat', '—')}, {coords.get('lng', '—')}"
    date_text = datetime.now().strftime("%Y-%m-%d")
    canvas.drawRightString(PAGE_W - 16 * mm, PAGE_H - 9 * mm, f"Reference: {ref_text}")
    canvas.drawRightString(PAGE_W - 16 * mm, PAGE_H - 14 * mm, f"Generated On: {date_text}")

    # ---- Footer band ----
    canvas.setFillColor(LIGHT_GREY)
    canvas.rect(0, 0, PAGE_W, FOOTER_H, fill=1, stroke=0)
    canvas.setFillColor(GREY)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(16 * mm, 6 * mm, "Generated by Bhumi AI \u2014 satellite remote-sensing data, for internal evaluation support.")
    canvas.drawRightString(PAGE_W - 16 * mm, 6 * mm, f"Page {doc.page}")

    canvas.restoreState()


def generate_pdf_report(data: Dict[str, Any]) -> bytes:
    """Build the full PDF report from a /calculate response dict.
    Returns raw PDF bytes.
    """
    ss = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=HEADER_H + 6 * mm, bottomMargin=FOOTER_H + 6 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm,
    )

    def _on_page(canvas, doc_):
        _draw_header_footer(canvas, doc_, data)

    with tempfile.TemporaryDirectory() as tmpdir:
        story = []
        story += _cover_section(data, ss, tmpdir)
        story += _farm_location_section(data, ss, tmpdir)
        story += _farm_details_section(data, ss)

        history_story = _cropping_history_section(data, ss)
        if history_story:
            story.append(PageBreak())
            story += history_story

        story.append(PageBreak())
        story += _water_conditions_section(data, ss, tmpdir)
        story += _regional_parameters_section(data, ss)

        story.append(PageBreak())
        story += _colour_ranges_section(ss)
        story += _glossary_section(ss)
        story += _disclaimer_section(ss)

        doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)

    return buf.getvalue()
