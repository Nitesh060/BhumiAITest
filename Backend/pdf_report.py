"""Bhumi AI PDF report generator.

The report is intentionally a decision-report layout rather than a raw
API dump. It only uses values already present in the /calculate payload
(and optional crop-intelligence data attached by the frontend). No score
or satellite value is recomputed here.
"""

from __future__ import annotations

import html
import io
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
    PageBreak, KeepTogether
)

from glossary import GLOSSARY_TERMS
from enrichment_service import fetch_farm_thumbnail_url

logger = logging.getLogger(__name__)

BLUE = colors.HexColor("#1a56c4")
BLUE_DARK = colors.HexColor("#0f3a92")
GREEN = colors.HexColor("#2f9e63")
GREY = colors.HexColor("#666666")
LIGHT = colors.HexColor("#f2f2f2")
BORDER = colors.HexColor("#d9d9d9")
GRADE = {
    "Poor": colors.HexColor("#d64545"),
    "Fair": colors.HexColor("#e8912d"),
    "Average": colors.HexColor("#e8c02d"),
    "Good": colors.HexColor("#7fbf3f"),
    "Excellent": colors.HexColor("#2f9e63"),
}


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Title2", parent=ss["Title"], fontSize=19, leading=23, textColor=BLUE_DARK, spaceAfter=5))
    ss.add(ParagraphStyle("Subtitle2", parent=ss["Normal"], fontSize=8.5, leading=11, textColor=GREY))
    ss.add(ParagraphStyle("Section2", parent=ss["Heading2"], fontSize=12, leading=15, textColor=BLUE_DARK, spaceBefore=13, spaceAfter=7))
    ss.add(ParagraphStyle("Body2", parent=ss["Normal"], fontSize=8.7, leading=11.5, textColor=colors.HexColor("#222222")))
    ss.add(ParagraphStyle("Small2", parent=ss["Normal"], fontSize=7.2, leading=9.2, textColor=GREY))
    ss.add(ParagraphStyle("Tiny2", parent=ss["Normal"], fontSize=6.4, leading=8, textColor=GREY))
    ss.add(ParagraphStyle("Cell", parent=ss["Normal"], fontSize=7, leading=8.5))
    ss.add(ParagraphStyle("CellSmall", parent=ss["Normal"], fontSize=6.3, leading=7.5))
    return ss


def _esc(value: Any) -> str:
    return html.escape("—" if value is None else str(value))


def _p(value: Any, style):
    return Paragraph(_esc(value), style)


def _section(title: str, ss):
    bar = Table([[""]], colWidths=[3.5 * mm], rowHeights=[6 * mm])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GREEN), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    heading = Table([[bar, Paragraph(_esc(title), ss["Section2"])]], colWidths=[5 * mm, 155 * mm])
    heading.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                 ("TOPPADDING", (0, 0), (-1, -1), 0),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                                 ("LEFTPADDING", (1, 0), (1, 0), 7)]))
    return heading


def _table(rows, widths, header=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.45, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        commands += [("BACKGROUND", (0, 0), (-1, 0), BLUE), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                     ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 7)]
        if len(rows) > 1:
            commands.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]))
    t.setStyle(TableStyle(commands))
    return t


def _gauge(score: int, tmp: str) -> str:
    fig, ax = plt.subplots(figsize=(3.4, 1.8), subplot_kw={"projection": "polar"})
    bands = [(300, 421, "#d64545"), (421, 541, "#e8912d"), (541, 661, "#e8c02d"),
             (661, 781, "#7fbf3f"), (781, 901, "#2f9e63")]
    span = 600
    for lo, hi, col in bands:
        a = 3.14159265 * (1 - (lo - 300) / span)
        b = 3.14159265 * (1 - (hi - 300) / span)
        ax.bar((a + b) / 2, 0.28, width=a - b, bottom=.72, color=col, edgecolor="white")
    score = max(300, min(900, int(score or 300)))
    theta = 3.14159265 * (1 - (score - 300) / span)
    ax.plot([theta, theta], [0, .72], color="black", linewidth=2)
    ax.set_theta_zero_location("W"); ax.set_theta_direction(-1); ax.set_thetamin(0); ax.set_thetamax(180)
    ax.set_ylim(0, 1); ax.axis("off")
    path = str(Path(tmp) / "score_gauge.png")
    fig.savefig(path, dpi=180, transparent=True, bbox_inches="tight")
    plt.close(fig)
    return path


def _bars(points, value_key, label_key, title, unit, tmp, filename):
    points = [p for p in (points or []) if p.get(value_key) is not None]
    if not points:
        return None
    labels = [str(p.get(label_key, "—")) for p in points]
    values = [float(p[value_key]) for p in points]
    fig, ax = plt.subplots(figsize=(6.1, 2.2))
    ax.bar(labels, values, width=.62)
    ax.set_title(title, fontsize=9, loc="left")
    ax.set_ylabel(unit, fontsize=7)
    ax.tick_params(labelsize=6.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    path = str(Path(tmp) / filename)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return path


def _score_section(data, ss, tmp):
    score = int(data.get("score") or 300)
    grade = data.get("grade") or "—"
    components = data.get("components") or {}
    rows = [["Parameter", "Observed", "Sub-score", "Weight", "Source"]]
    for key, c in components.items():
        rows.append([_p(c.get("label") or key, ss["Cell"]),
                     _p(f"{c.get('raw_value')}{c.get('unit', '')}", ss["Cell"]),
                     _p(f"{c.get('sub_score')}/100" if c.get("sub_score") is not None else "N/A", ss["Cell"]),
                     _p(f"{c.get('weight', 0)}%", ss["Cell"]),
                     _p(c.get("source") or "—", ss["CellSmall"])])
    gauge = Image(_gauge(score, tmp), width=76 * mm, height=40 * mm)
    grade_col = GRADE.get(grade, colors.black)
    summary = Table([[gauge, Paragraph(f'<font size="27" color="{grade_col.hexval()}"><b>{score}</b></font><br/><font size="12" color="{grade_col.hexval()}"><b>{_esc(grade)}</b></font><br/><font size="7">FarmScore scale: 300–900</font>', ss["Body2"])]], colWidths=[85 * mm, 45 * mm])
    summary.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return [_section("1. Overall FarmScore & Parameter Breakdown", ss), summary, Spacer(1, 5), _table(rows, [47*mm, 28*mm, 23*mm, 20*mm, 38*mm])]


def _location_section(data, ss, tmp):
    coords = data.get("coordinates") or {}
    story = [_section("2. Farm Location & Executive Snapshot", ss)]
    e = data.get("enrichment") or {}
    irrigation = e.get("irrigation") or {}
    soil = e.get("soil_type") or {}
    intensity = e.get("cropping_intensity") or {}
    climate = data.get("climate_risk") or {}
    crops = data.get("recommended_crops") or {}
    primary = crops.get("primary") or {}
    rows = [
        ["Coordinates", f"{coords.get('lat')}° N, {coords.get('lng')}° E"],
        ["Land use", "Agricultural"],
        ["Soil", soil.get("label") or "—"],
        ["Irrigation signal", "Likely irrigated" if irrigation.get("likely_irrigated") else ("Likely rainfed" if irrigation.get("likely_irrigated") is False else "—")],
        ["Cropping intensity", intensity.get("label") or "—"],
        ["Top recommended crop", f"{primary.get('crop', '—')} ({primary.get('score', '—')}%)"],
        ["Climate risk", climate.get("level") or "—"],
    ]
    story.append(_table([["Field", "Value"]] + [[_p(a, ss["Cell"]), _p(b, ss["Cell"])] for a, b in rows], [50*mm, 106*mm]))
    story.append(Spacer(1, 5))
    try:
        lat, lng = coords.get("lat"), coords.get("lng")
        if lat is not None and lng is not None:
            url = fetch_farm_thumbnail_url(lat, lng, data.get("farm_polygon") or data.get("polygon"))
            if url:
                r = requests.get(url, timeout=15); r.raise_for_status()
                path = str(Path(tmp) / "farm_satellite.png")
                Path(path).write_bytes(r.content)
                story += [Image(path, width=92*mm, height=72*mm), Paragraph("Satellite true-colour farm context. Image is provided for visual context; scoring uses the measured satellite-derived parameters above.", ss["Small2"])]
    except Exception:
        logger.info("Farm thumbnail unavailable for PDF", exc_info=True)
    return story


def _crop_section(data, ss):
    ci = data.get("crop_intelligence") or {}
    ident = ci.get("identification") or {}
    stage = ci.get("growth_stage") or {}
    sh = ci.get("sowing_harvest_prediction") or {}
    rotation = ci.get("crop_rotation") or {}
    calendar = ci.get("crop_calendar") or {}
    story = [_section("3. Crop Intelligence", ss)]
    if not ci:
        story.append(Paragraph("Crop Intelligence was not attached to this report payload. Re-open the Extended Report so the page can retrieve the latest crop-intelligence result before downloading the PDF.", ss["Body2"]))
        return story
    rows = [
        ["Likely crop", ident.get("identified_crop") or "—"],
        ["Confidence", ident.get("confidence") or "—"],
        ["Peak NDVI", f"{ident.get('peak_ndvi')} (month {ident.get('peak_ndvi_month')})" if ident.get("peak_ndvi") is not None else "—"],
        ["Flood / paddy signature", "Detected" if ident.get("flood_signature_detected") else "Not detected"],
        ["Growth stage", stage.get("stage") or "—"],
        ["Current NDVI / peak", f"{stage.get('current_ndvi')} / {stage.get('peak_ndvi')}" if stage.get("current_ndvi") is not None else "—"],
        ["Estimated sowing", sh.get("sowing_estimate_month") or "—"],
        ["Estimated harvest", sh.get("harvest_estimate_month") or "—"],
        ["Prediction source", sh.get("source") or "—"],
    ]
    story.append(_table([["Indicator", "Result"]] + [[_p(a, ss["Cell"]), _p(b, ss["Cell"])] for a, b in rows], [55*mm, 101*mm]))
    if ident.get("method"):
        story.append(Paragraph("Method: " + _esc(ident["method"]), ss["Small2"]))
    if ident.get("note"):
        story.append(Paragraph(_esc(ident["note"]), ss["Small2"]))
    if rotation.get("years"):
        story += [Spacer(1, 5), Paragraph("Crop Rotation (3-year)", ss["Heading3"])]
        rr = [["Year", "Kharif", "Rabi"]] + [[_p(y.get("year"), ss["Cell"]), _p(y.get("kharif"), ss["Cell"]), _p(y.get("rabi"), ss["Cell"])] for y in rotation["years"]]
        story.append(_table(rr, [30*mm, 60*mm, 60*mm]))
        if rotation.get("summary"): story.append(Paragraph(_esc(rotation["summary"]), ss["Small2"]))
    if calendar:
        story += [Spacer(1, 5), Paragraph("Crop Calendar Reference", ss["Heading3"])]
        cr = [["Season", "Typical sowing", "Typical harvest", "Duration"]]
        for season, info in calendar.items():
            cr.append([_p(season, ss["Cell"]), _p(info.get("sow"), ss["Cell"]), _p(info.get("harvest"), ss["Cell"]), _p(f"{info.get('duration_days')} days", ss["Cell"])])
        story.append(_table(cr, [30*mm, 47*mm, 47*mm, 32*mm]))
    return story


def _farm_profile_section(data, ss):
    e = data.get("enrichment") or {}
    yp = data.get("yield_prediction") or {}
    rows = [["Attribute", "Value"]]
    values = [
        ("Agro-Ecological Zone", (e.get("agro_ecological_zone") or {}).get("zone")),
        ("Adjacent land cover", ", ".join(f"{x.get('class')} {x.get('percent')}%" for x in ((e.get("adjacent_land_cover") or {}).get("breakdown") or [])[:3])),
        ("Estimated yield", f"{yp.get('estimated_yield_kg_per_ha')} kg/ha" if yp.get("estimated_yield_kg_per_ha") is not None else None),
        ("Estimated total yield", f"{yp.get('estimated_total_yield_quintal')} quintal on {yp.get('area_ha')} ha" if yp.get('estimated_total_yield_quintal') is not None else None),
        ("Yield model", "Formula proxy; not measured / not trained ML" if yp else None),
    ]
    for a, b in values: rows.append([_p(a, ss["Cell"]), _p(b or "—", ss["Cell"])])
    return [_section("4. Farm Profile & Yield Context", ss), _table(rows, [55*mm, 101*mm])]


def _cropping_history(data, ss):
    hist = (data.get("enrichment") or {}).get("cropping_history") or {}
    if not hist.get("years"): return []
    rows = [["Year", "Kharif NDVI", "Kharif", "Rabi NDVI", "Rabi"]]
    for y in hist["years"]:
        k, r = y.get("kharif") or {}, y.get("rabi") or {}
        rows.append([_p(y.get("year"), ss["Cell"]), _p(k.get("ndvi") if k.get("ndvi") is not None else "—", ss["Cell"]), _p("Cropped" if k.get("cropped") else "Fallow / no signal", ss["Cell"]), _p(r.get("ndvi") if r.get("ndvi") is not None else "—", ss["Cell"]), _p("Cropped" if r.get("cropped") else "Fallow / no signal", ss["Cell"])])
    return [_section("5. Cropping History (Satellite-derived, 3-year)", ss), _table(rows, [20*mm, 28*mm, 43*mm, 28*mm, 43*mm]), Paragraph("Season-level cropped/fallow signal from NDVI; it does not identify crop species.", ss["Small2"])]


def _water_section(data, ss, tmp):
    story = [_section("6. Water Conditions", ss)]
    rain = data.get("rainfall_monthly") or []
    gw = data.get("groundwater_trend") or []
    p = _bars(rain, "mm_per_day", "month", "Rainfall profile", "mm/day", tmp, "rainfall.png")
    if p: story.append(Image(p, width=155*mm, height=55*mm))
    g = _bars(gw, "groundwater", "year", "Groundwater trend", "kg/m²", tmp, "groundwater.png")
    if g: story.append(Image(g, width=155*mm, height=55*mm))
    if not p and not g: story.append(Paragraph("No water trend series was available in the report payload.", ss["Body2"]))
    return story


def _regional_section(data, ss):
    e = data.get("enrichment") or {}
    t = e.get("temperature_annual_range") or {}
    p = e.get("regional_prosperity") or {}
    w = e.get("nearest_water_body") or {}
    topo = e.get("topography") or {}
    pop = e.get("village_population") or {}
    drought = e.get("drought_instances") or {}
    rows = [["Regional parameter", "Observed / proxy"]]
    values = [
        ("Annual temperature range", f"{t.get('min_c')}°C – {t.get('max_c')}°C (mean {t.get('mean_c')}°C)" if t.get("min_c") is not None else None),
        ("Regional prosperity", p.get("tier")),
        ("Nearest water body", "Present" if w.get("water_present") else ("Not detected" if w.get("water_present") is False else None)),
        ("Topography", f"{topo.get('terrain')} · {topo.get('elevation_m')} m · slope {topo.get('slope_degrees')}°" if topo.get("terrain") else None),
        ("Nearby population proxy", f"~{pop.get('estimated_population')} within {pop.get('radius_m')} m" if pop.get("estimated_population") is not None else None),
        ("Drought years", ", ".join(map(str, drought.get("drought_years", []))) if drought.get("drought_years") else "None detected"),
    ]
    for a, b in values: rows.append([_p(a, ss["Cell"]), _p(b or "—", ss["Cell"])])
    return [_section("7. Regional Parameters", ss), _table(rows, [55*mm, 101*mm])]


def _risk_sources(data, ss):
    climate = data.get("climate_risk") or {}
    components = data.get("components") or {}
    sources = sorted({c.get("source") for c in components.values() if c.get("source")})
    rows = [["Decision context", "Result"],
            [_p("Climate risk", ss["Cell"]), _p(climate.get("level") or "—", ss["Cell"])]]
    flags = climate.get("flags") or []
    if flags: rows.append([_p("Risk flags", ss["Cell"]), _p("; ".join(flags), ss["Cell"])])
    rows.append([_p("Satellite / data sources", ss["Cell"]), _p("; ".join(sources) or "—", ss["Cell"])])
    rows.append([_p("Score interpretation", ss["Cell"]), _p("Suitability / condition index. It is not a validated yield model or standalone credit decision.", ss["Cell"])])
    return [_section("8. Risk Context, Data Provenance & Decision Notes", ss), _table(rows, [55*mm, 101*mm])]


def _bands(ss):
    rows = [["Score", "Band"], ["781–900", "Excellent"], ["661–780", "Good"], ["541–660", "Average"], ["421–540", "Fair"], ["300–420", "Poor"]]
    return [_section("9. Score Bands", ss), _table(rows, [55*mm, 101*mm]), Paragraph("The score is reported on the Bhumi AI 300–900 scale used by the application.", ss["Small2"])]


def _glossary(ss):
    rows = [["Term", "Meaning"]]
    for term, definition in list(GLOSSARY_TERMS.items())[:30]:
        rows.append([_p(term, ss["Cell"]), _p(definition, ss["CellSmall"])])
    return [_section("10. Glossary", ss), _table(rows, [45*mm, 111*mm])]


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 6.8)
    canvas.setFillColor(GREY)
    canvas.drawString(18 * mm, 9 * mm, "Bhumi AI · Satellite-powered farm intelligence")
    canvas.drawRightString(192 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def generate_pdf_report(data: Dict[str, Any]) -> bytes:
    """Generate the report from an existing /calculate result payload."""
    if not data or "score" not in data:
        raise ValueError("Report payload must include score")

    ss = _styles()
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm,
                                topMargin=16*mm, bottomMargin=16*mm, title="Bhumi AI Farm Intelligence Report")
        story = []
        coords = data.get("coordinates") or {}
        story += [Paragraph("Bhumi AI", ss["Title2"]),
                  Paragraph("Extended Farm Intelligence Report", ss["Subtitle2"]),
                  Spacer(1, 4),
                  Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y, %H:%M')} · Location: {_esc(coords.get('lat'))}° N, {_esc(coords.get('lng'))}° E", ss["Small2"]),
                  Spacer(1, 8)]
        story += _score_section(data, ss, tmp)
        story += _location_section(data, ss, tmp)
        story += _crop_section(data, ss)
        story += _farm_profile_section(data, ss)
        story += _cropping_history(data, ss)
        story += _water_section(data, ss, tmp)
        story += _regional_section(data, ss)
        story += _risk_sources(data, ss)
        story += _bands(ss)
        story.append(PageBreak())
        story += _glossary(ss)
        story += [_section("11. Disclaimer", ss),
                  Paragraph("Bhumi AI uses satellite-derived indicators, environmental datasets and transparent heuristic rules. Crop identification, yield estimates and regional proxies are indicative unless explicitly validated with field observations or labelled ground-truth data. The FarmScore is a suitability/condition index and should not be treated as a standalone lending, insurance, agronomic or legal decision. Field verification and applicable institutional policy remain necessary.", ss["Body2"]),
                  Spacer(1, 8),
                  Paragraph("Data note: missing satellite observations are not silently treated as zero; the scoring service redistributes available weights. Thresholds are provisional and should be calibrated against local ground-truth outcomes before production credit decisions.", ss["Small2"])]
        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
