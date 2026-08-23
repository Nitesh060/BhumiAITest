"""Robust Bhumi AI FarmScore PDF report generator.

The endpoint receives an already-computed /calculate payload and only lays
out that data.  The implementation deliberately keeps every table inside the
A4 printable frame (174 mm wide) and avoids network/image dependencies so a
PDF export cannot fail because of a remote thumbnail or an oversized table.
"""
from __future__ import annotations

import hashlib
import html
import io
import logging
from typing import Any, Dict, List, Optional, Sequence

from urllib.parse import urlparse

import requests
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    Image,
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.linecharts import HorizontalLineChart

from glossary import GLOSSARY_TERMS

logger = logging.getLogger(__name__)

PAGE_W = 174 * mm
BLUE = colors.HexColor("#2F6FDB")
BLUE_DARK = colors.HexColor("#173A73")
BLUE_LIGHT = colors.HexColor("#EAF1FF")
GREEN = colors.HexColor("#2F9E63")
GREEN_DARK = colors.HexColor("#17633E")
RED = colors.HexColor("#D64545")
ORANGE = colors.HexColor("#E8912D")
YELLOW = colors.HexColor("#E8C02D")
GREY = colors.HexColor("#666666")
LIGHT_GREY = colors.HexColor("#F6F7F9")
BORDER = colors.HexColor("#D8DCE3")
BLACK = colors.HexColor("#222222")

GRADE_RISK = {
    "Poor": ("Highest Risk", RED),
    "Fair": ("High Risk", ORANGE),
    "Good": ("Medium Risk", YELLOW),
    "Very Good": ("Low Risk", GREEN),
    "Excellent": ("Lowest Risk", GREEN_DARK),
}


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("BhumiTitle", parent=s["Title"], fontName="Helvetica-Bold", fontSize=18, leading=21, textColor=BLUE_DARK, spaceAfter=4))
    s.add(ParagraphStyle("Section", parent=s["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=BLACK, spaceBefore=2, spaceAfter=6))
    s.add(ParagraphStyle("Body8", parent=s["Normal"], fontSize=7.5, leading=9.5, textColor=BLACK))
    s.add(ParagraphStyle("Body7", parent=s["Normal"], fontSize=6.7, leading=8.2, textColor=BLACK))
    s.add(ParagraphStyle("Small", parent=s["Normal"], fontSize=6.8, leading=8.2, textColor=GREY))
    s.add(ParagraphStyle("TableHead", parent=s["Normal"], fontName="Helvetica-Bold", fontSize=6.8, leading=8, textColor=BLUE))
    s.add(ParagraphStyle("TableBody", parent=s["Normal"], fontSize=6.7, leading=8.2, textColor=BLACK))
    s.add(ParagraphStyle("Tiny", parent=s["Normal"], fontSize=5.8, leading=7, textColor=GREY))
    s.add(ParagraphStyle("Score", parent=s["Normal"], fontName="Helvetica-Bold", fontSize=25, leading=27, alignment=TA_CENTER, textColor=BLUE_DARK))
    return s


def _esc(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return html.escape(str(value))


def _p(value: Any, style):
    return Paragraph(_esc(value), style)


def _score(value: Any) -> int:
    try:
        return max(400, min(1000, int(float(value))))
    except Exception:
        return 400


def _ref_id(data: Dict[str, Any]) -> str:
    c = data.get("coordinates") or {}
    raw = f"{c.get('lat', '')},{c.get('lng', '')}".encode("utf-8")
    return "BH-" + hashlib.sha1(raw).hexdigest()[:10].upper()


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 285 * mm, 192 * mm, 285 * mm)
    canvas.setFont("Helvetica-Bold", 8.5)
    canvas.setFillColor(BLUE)
    canvas.drawString(18 * mm, 289 * mm, "BHUMI AI")
    canvas.setFont("Helvetica-Bold", 7.5)
    canvas.drawString(38 * mm, 289 * mm, "FARMSCORE REPORT")
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(GREY)
    canvas.drawRightString(192 * mm, 289 * mm, f"Reference ID : {_header_footer.ref_id}")
    canvas.drawString(18 * mm, 8 * mm, "Bhumi AI - Satellite-powered farm intelligence")
    canvas.drawRightString(192 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


_header_footer.ref_id = "—"


def _section(title: str, s):
    return [
        Paragraph(_esc(title), s["Section"]),
        Table([[""]], colWidths=[PAGE_W], rowHeights=[0.7 * mm], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), BLUE)])),
        Spacer(1, 3),
    ]


def _table(rows, widths, s, header=True):
    if abs(sum(widths) - PAGE_W) > 0.1 * mm:
        raise ValueError(f"PDF table width must be {PAGE_W/mm:.1f}mm; got {sum(widths)/mm:.1f}mm")
    converted = []
    for r_i, row in enumerate(rows):
        converted.append([_p(v, s["TableHead"] if header and r_i == 0 else s["TableBody"]) for v in row])
    t = Table(converted, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), BLUE_LIGHT),
            ("TEXTCOLOR", (0, 0), (-1, 0), BLUE),
        ]
        if len(rows) > 1:
            commands.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]))
    t.setStyle(TableStyle(commands))
    return t


def _line_chart(points: Sequence[float], labels: Sequence[str], width_mm_val: float, height_mm_val: float, line_color) -> Optional[Drawing]:
    """A minimal reportlab-native line chart — no matplotlib, no temp
    files, same "no external rendering dependency" constraint as the
    ASCII score bar elsewhere in this module. Returns None (never
    raises) if there are fewer than 2 usable points to plot.
    """
    try:
        width, height = width_mm_val * mm, height_mm_val * mm
        d = Drawing(width, height)
        chart = HorizontalLineChart()
        chart.x = 26
        chart.y = 16
        chart.width = width - 34
        chart.height = height - 28
        chart.data = [list(points)]
        chart.categoryAxis.categoryNames = list(labels)
        chart.categoryAxis.labels.fontSize = 5.2
        lo, hi = min(points), max(points)
        pad = (hi - lo) * 0.15 or max(abs(hi), 1) * 0.1
        chart.valueAxis.valueMin = lo - pad
        chart.valueAxis.valueMax = hi + pad
        chart.valueAxis.labels.fontSize = 5.2
        chart.lines[0].strokeColor = line_color
        chart.lines[0].strokeWidth = 1.3
        d.add(chart)
        return d
    except Exception:
        logger.exception("Line chart rendering failed (non-fatal)")
        return None


# /report/pdf's caller (app.py) only regenerates satellite_thumbnail
# server-side when the client didn't already supply one — meaning a
# client CAN supply their own `{"available": true, "url": "..."}` and
# have this function fetch whatever URL they name (cloud metadata
# endpoints, internal services — blind SSRF). The thumbnail is only ever
# meant to be a Google Earth Engine getThumbURL() link, which always
# resolves under a *.googleapis.com host — anything else is rejected
# before a request is ever made, closing the SSRF path regardless of
# what the client sends.
_ALLOWED_IMAGE_HOST_SUFFIX = ".googleapis.com"


def _safe_image(url: Optional[str], width_mm_val: float, timeout: float = 8.0) -> Optional[Image]:
    """Downloads a remote image (the satellite thumbnail URL) and
    returns a reportlab Image flowable sized to `width_mm_val` wide,
    preserving aspect ratio — or None on ANY failure (missing url,
    disallowed host, network error, bad content, timeout). The PDF must
    render exactly the same with or without this succeeding.
    """
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "googleapis.com" or host.endswith(_ALLOWED_IMAGE_HOST_SUFFIX)):
        logger.warning("Rejected satellite thumbnail URL with disallowed host: %r", host)
        return None
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        buf = io.BytesIO(resp.content)
        img = Image(buf)
        width = width_mm_val * mm
        aspect = img.imageHeight / img.imageWidth if img.imageWidth else 1
        img.drawWidth = width
        img.drawHeight = width * aspect
        return img
    except Exception:
        logger.exception("Satellite thumbnail download/embed failed (non-fatal)")
        return None


def _water_body_label(water: Dict[str, Any]) -> str:
    """nearest_water_body only ever returns water_present / water_pixels_within_2km
    (a satellite presence/extent signal, by design — see enrichment_service.py's
    fetch_nearest_water_body_signal docstring) — it never returns a road-network
    "distance". The report used to look for distance_km/distance keys that don't
    exist and always fell back to "Not available", hiding real data. Read the
    fields that are actually returned instead.
    """
    if not water:
        return "Not available"
    if water.get("water_present") is True:
        px = water.get("water_pixels_within_2km")
        return f"Surface water detected within 2 km ({px} px)" if px is not None else "Surface water detected within 2 km"
    if water.get("water_present") is False:
        return "No surface water detected within 2 km"
    return "Not available"


def _score_breakdown_section(breakdown: Optional[Dict[str, Any]], s) -> list:
    """Renders the FarmScore's own Base + Kharif + Rabi breakdown —
    Bhumi's own version of SatSource's "Factors Contributing Towards
    SatScore" panel. Not the same computation or thresholds as any
    other product; see seasonal_score_service.py's module docstring.
    Degrades to a clear "not available" note rather than hiding the
    whole section if the underlying signals are missing.
    """
    story = _section("FarmScore Breakdown (Base + Kharif + Rabi)", s)
    if not breakdown or not breakdown.get("available"):
        reason = (breakdown or {}).get("reason", "Insufficient irrigation, cropping-intensity, or seasonal satellite/weather signal for this location.")
        story.append(Paragraph(f"Not available — {_esc(reason)}", s["Small"]))
        story.append(Spacer(1, 6))
        return story

    overall = breakdown["overall_score"]
    category = breakdown["category"]
    risk = breakdown["risk_rating"]
    filled = max(1, min(20, round((overall - 400) / 600 * 20)))
    bar = Table([["■" * filled + "□" * (20 - filled)]], colWidths=[75 * mm], rowHeights=[9 * mm])
    bar.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 13),
        ("TEXTCOLOR", (0, 0), (-1, -1), GREEN if category in ("Good", "Very Good", "Excellent") else (ORANGE if category == "Fair" else RED)),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    summary = [
        [bar, Paragraph(f"<b>{overall}/1000</b><br/>{_esc(category)} - {_esc(risk)} risk", s["Body8"])],
        [Paragraph("Base + Kharif + Rabi composite (see method note below)", s["Small"]), ""],
    ]
    st = Table(summary, colWidths=[100 * mm, 74 * mm])
    st.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("SPAN", (0, 1), (1, 1)),
    ]))
    story += [st, Spacer(1, 6)]

    def _factor_row(label, comp):
        if not comp or not comp.get("data_available"):
            return [label, "Not available", "—"]
        return [label, f"{comp['grade']} ({comp['score']}/{comp['max_score']})", ""]

    base, kharif, rabi = breakdown["base"], breakdown["kharif"], breakdown["rabi"]
    factor_rows = [
        ["FACTOR", "SCORE", "DETAIL"],
        [*_factor_row("Base Score", base)[:2], f"{base.get('irrigation_condition', '—')}, {base.get('cropping_intensity', '—')}" if base.get("data_available") else "—"],
        [*_factor_row("Average Kharif Score", kharif)[:2], f"{kharif.get('parameters_used', 0)} of {kharif.get('parameters_total', 20)} parameters used" if kharif.get("data_available") else "—"],
        [*_factor_row("Average Rabi Score", rabi)[:2], f"{rabi.get('parameters_used', 0)} of {rabi.get('parameters_total', 20)} parameters used" if rabi.get("data_available") else "—"],
    ]
    story += [_table(factor_rows, [45 * mm, 55 * mm, 74 * mm], s), Spacer(1, 4)]
    story.append(Paragraph(
        "The Bhumi AI FarmScore above is this Base + Average Kharif Score + Average Rabi Score composite, rescaled to "
        "a 400-1000 final score. Base comes from irrigation + cropping intensity; Kharif/Rabi each run the same "
        "transparent 20-parameter suitability formula (see \"FarmScore & Parameter Evidence\" above) scoped to that "
        "season's own satellite/weather data. It is a formula-based proxy, NOT validated against real harvested-yield "
        "ground truth.",
        s["Tiny"]))
    story.append(Spacer(1, 6))
    return story


def _score_page(data, s):
    score = _score(data.get("score"))
    grade = data.get("grade") or "Poor"
    risk, risk_color = GRADE_RISK.get(grade, ("—", BLUE))
    coords = data.get("coordinates") or {}
    enrichment = data.get("enrichment") or {}
    components = data.get("components") or {}

    story = _section("Overall FarmScore", s)

    # Visual score bar: no matplotlib, no temporary files, and therefore no
    # external rendering dependency.
    filled = max(1, min(20, round((score - 400) / 600 * 20)))
    score_bar = Table([["■" * filled + "□" * (20 - filled)]], colWidths=[75 * mm], rowHeights=[9 * mm])
    score_bar.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 13),
        ("TEXTCOLOR", (0, 0), (-1, -1), risk_color),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
    ]))

    summary = [
        [score_bar, Paragraph(f"<b>{score}/1000</b><br/><font color='{risk_color.hexval()}'>{_esc(grade)} - {_esc(risk)}</font>", s["Body8"])],
        [Paragraph("Bhumi AI suitability / condition index", s["Small"]), Paragraph("400-1000 scale", s["Small"])],
    ]
    st = Table(summary, colWidths=[100 * mm, 74 * mm])
    st.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [st, Spacer(1, 7)]

    story += _section("Farm Details", s)
    irr = enrichment.get("irrigation") or {}
    ci = enrichment.get("cropping_intensity") or {}
    soil = enrichment.get("soil_type") or {}
    details = [
        ["S.NO.", "REGION / LOCATION", "SURVEY NO.", "IRRIGATION", "CROPPING INTENSITY", "FARM CENTROID", "LAND USE"],
        [
            "1",
            "Location from selected coordinates",
            "Not available",
            "Irrigated" if irr.get("likely_irrigated") else ("Rainfed" if irr.get("likely_irrigated") is False else "Not available"),
            ci.get("label") or "Not available",
            f"{coords.get('lat', '—')} N / {coords.get('lng', '—')} E",
            "Agricultural",
        ],
    ]
    # IMPORTANT: total is exactly 174 mm. The previous implementation used
    # 184 mm here, wider than the A4 frame, which can raise ReportLab
    # LayoutError and produced the HTTP 500 seen by the frontend.
    story += [_table(details, [9 * mm, 38 * mm, 25 * mm, 26 * mm, 28 * mm, 28 * mm, 20 * mm], s), Spacer(1, 7)]

    story += _score_breakdown_section(enrichment.get("farmscore_breakdown"), s)

    story += _section("FarmScore & Parameter Evidence", s)
    sources = sorted({str(c.get("source")) for c in components.values() if isinstance(c, dict) and c.get("source")})
    used = sum(1 for c in components.values() if isinstance(c, dict) and c.get("sub_score") is not None)
    evidence = [
        ["ITEM", "VALUE"],
        ["Bhumi AI Score", f"{score}/1000 ({grade})"],
        ["Parameters Used", f"{used} of {data.get('parameters_total', 20)}"],
        ["Data Sources", " - ".join(sources) if sources else "Not available"],
        ["Interpretation", "Suitability / condition index; not a standalone credit or yield decision"],
    ]
    story += [_table(evidence, [48 * mm, 126 * mm], s), Spacer(1, 6)]

    param_rows = [["PARAMETER", "OBSERVED", "SUB-SCORE", "WEIGHT", "SOURCE"]]
    for key, comp in components.items():
        if not isinstance(comp, dict):
            continue
        raw = comp.get("raw_value")
        unit = comp.get("unit") or ""
        observed = f"{raw}{unit}" if raw is not None else "N/A"
        sub = f"{comp.get('sub_score')}/100" if comp.get("sub_score") is not None else "N/A"
        param_rows.append([comp.get("label") or key, observed, sub, f"{comp.get('weight', '—')}%", comp.get("source") or "—"])
    if len(param_rows) == 1:
        param_rows.append(["No parameter data", "—", "—", "—", "—"])
    story += [_table(param_rows, [42 * mm, 37 * mm, 25 * mm, 20 * mm, 50 * mm], s), Spacer(1, 5)]

    story += _section("Recommended Crops & Yield", s)
    crops = data.get("recommended_crops") or {}
    primary = crops.get("primary") or {}
    yp = data.get("yield_prediction") or {}
    crop_rows = [
        ["ITEM", "VALUE"],
        ["Top Recommended Crop", f"{primary.get('crop', '—')} ({primary.get('score', '—')}%)"],
        ["Estimated Yield", f"{yp.get('estimated_yield_kg_per_ha', '—')} kg/ha"],
        ["Climate Risk", (data.get("climate_risk") or {}).get("level", "—")],
    ]
    story += [_table(crop_rows, [55 * mm, 119 * mm], s), Spacer(1, 6)]

    story += _section("Cropping History", s)
    history = enrichment.get("cropping_history") or {}
    identified = enrichment.get("crop_history_identified") or {}
    identified_by_year = {y.get("year"): y for y in identified.get("years") or []}
    hrows = [["SEASON", "CROP (SATELLITE ESTIMATE)", "NDVI", "STATUS"]]
    for year in history.get("years") or []:
        year_num = year.get("year")
        id_year = identified_by_year.get(year_num, {})
        for key, label in (("kharif", "Kharif"), ("rabi", "Rabi")):
            item = year.get(key) or {}
            if item.get("ndvi") is None and not item.get("cropped"):
                continue
            id_season = id_year.get(key) or {}
            crop_name = id_season.get("identified_crop")
            confidence = id_season.get("confidence")
            crop_display = f"{crop_name} ({confidence} confidence)" if crop_name else "Not identified"
            hrows.append([f"{label} ({year_num or '—'})", crop_display, item.get("ndvi", "—"), "Cropped signal" if item.get("cropped") else "Fallow / no signal"])
    if len(hrows) == 1:
        hrows.append(["No historical signal", "—", "—", "—"])
    story += [_table(hrows, [38 * mm, 58 * mm, 26 * mm, 52 * mm], s), Spacer(1, 3)]
    story.append(Paragraph("Crop names are a satellite-signal heuristic (NDVI shape + flood signature), not a trained crop-species classifier or ground-truth record — see the Glossary. Historical price and measured yield are not fabricated when unavailable.", s["Tiny"]))
    story.append(PageBreak())
    return story


def _location_page(data, s):
    e = data.get("enrichment") or {}
    coords = data.get("coordinates") or {}
    story = _section("Farm Location", s)

    thumb = data.get("satellite_thumbnail") or {}
    if thumb.get("available") and thumb.get("url"):
        img = _safe_image(thumb["url"], 174)
        if img:
            story += [img, Spacer(1, 3)]
            caption = f"Sentinel-2 imagery" + (f", {thumb['scene_date']}" if thumb.get("scene_date") else "") + " — farm boundary shown in red."
            story += [Paragraph(caption, s["Tiny"]), Spacer(1, 6)]
        else:
            story += [Paragraph("Satellite image could not be loaded for this report.", s["Small"]), Spacer(1, 6)]
    elif thumb.get("reason"):
        story += [Paragraph(f"Satellite image unavailable: {_esc(thumb['reason'])}", s["Small"]), Spacer(1, 6)]

    rows = [
        ["ITEM", "VALUE"],
        ["Farm Centroid", f"{coords.get('lat', '—')} N, {coords.get('lng', '—')} E"],
        ["Farm Label", "Selected farm / location"],
        ["Survey Details", "Not available"],
        ["Land Use Type", "Agricultural"],
    ]
    story += [_table(rows, [55 * mm, 119 * mm], s), Spacer(1, 7)]

    story += [Paragraph("Adjacent Land Details", s["Section"])]
    adjacent = (e.get("adjacent_land_cover") or {}).get("breakdown") or []
    ar = [["LAND COVER", "PERCENT", "DIRECTION"]]
    for item in adjacent[:8]:
        ar.append([item.get("class", "Not available"), item.get("percent", "—"), "Not available"])
    if len(ar) == 1:
        ar.append(["Not available", "—", "—"])
    story += [_table(ar, [75 * mm, 35 * mm, 64 * mm], s), Spacer(1, 5), PageBreak()]
    return story


def _water_page(data, s):
    e = data.get("enrichment") or {}
    story = _section("Water Conditions", s)
    rain = data.get("rainfall_monthly") or e.get("rainfall_monthly") or []
    if isinstance(rain, dict):
        rain = rain.get("monthly") or rain.get("data") or []
    rows = [["PERIOD", "RAINFALL", "UNIT"]]
    if isinstance(rain, list):
        for item in rain[-12:]:
            if isinstance(item, dict):
                rows.append([item.get("month") or item.get("label") or item.get("year") or "—", item.get("mm_per_day") or item.get("rainfall") or item.get("rainfall_mm") or item.get("value") or "—", "mm/day"])
    if len(rows) == 1:
        rows.append(["No trend data", "—", "—"])
    story += [Paragraph("In-Season Monthly Rainfall", s["Section"]), _table(rows, [55 * mm, 59 * mm, 60 * mm], s), Spacer(1, 7)]

    water = e.get("nearest_water_body") or {}
    gw = data.get("groundwater_trend") or e.get("groundwater_trend") or []
    gw_points = [(g.get("year"), g.get("deep_soil_moisture")) for g in gw if isinstance(g, dict) and g.get("deep_soil_moisture") is not None]
    story.append(Paragraph("Groundwater Trend (Deep Soil Moisture Proxy, mm equivalent)", s["Section"]))
    if len(gw_points) >= 2:
        chart = _line_chart([v for _, v in gw_points], [str(y) for y, _ in gw_points], 174, 48, BLUE)
        if chart:
            story += [chart, Spacer(1, 4)]
        else:
            story.append(Paragraph("Chart could not be rendered — see raw values below.", s["Small"]))
    else:
        story.append(Paragraph("Not enough completed years of data to plot a trend for this location.", s["Small"]))
    story.append(Spacer(1, 6))

    rainfall_trend = data.get("rainfall_trend") or {}
    yearly = rainfall_trend.get("yearly") or []
    rain_points = [(y.get("year"), y.get("total_rainfall_mm")) for y in yearly if isinstance(y, dict) and y.get("total_rainfall_mm") is not None]
    story.append(Paragraph("Rainfall Trend (mm/year)", s["Section"]))
    if len(rain_points) >= 2:
        chart = _line_chart([v for _, v in rain_points], [str(y) for y, _ in rain_points], 174, 48, GREEN)
        if chart:
            story += [chart, Spacer(1, 4)]
        else:
            story.append(Paragraph("Chart could not be rendered — see raw values below.", s["Small"]))
    else:
        story.append(Paragraph("Not enough completed years of data to plot a trend for this location.", s["Small"]))
    story.append(Spacer(1, 6))

    summary = [
        ["ITEM", "VALUE"],
        ["Nearest Water Body", _water_body_label(water)],
        ["Groundwater Data Points", f"{len(gw_points)} year(s)" if gw_points else "Not available"],
        ["Rainfall Data Points", f"{len(rain_points)} year(s)" if rain_points else "Not available"],
    ]
    story += [_table(summary, [60 * mm, 114 * mm], s), Spacer(1, 4)]
    story.append(Paragraph("Water-related values are derived from the datasets already used by the FarmScore pipeline. Groundwater is a deep-soil-moisture satellite PROXY, not a measured water-table depth.", s["Tiny"]))
    story.append(PageBreak())
    return story


def _regional_page(data, s):
    e = data.get("enrichment") or {}
    soil = e.get("soil_type") or {}
    temp = e.get("temperature_annual_range") or {}
    drought = e.get("drought_instances") or {}
    pop = e.get("village_population") or {}
    aez = e.get("agro_ecological_zone") or {}
    prop = e.get("regional_prosperity") or {}
    water = e.get("nearest_water_body") or {}

    rows = [
        ["PARAMETER", "VALUE"],
        ["Nearest Water Body", _water_body_label(water)],
        ["Drought Years", ", ".join(map(str, drought.get("drought_years", [])[:12])) or "None detected / not available"],
        ["Nearby Population Proxy", pop.get("estimated_population") or "Not available"],
        ["Ambient Temperature", f"{temp.get('min_c')} C - {temp.get('max_c')} C" if temp.get("min_c") is not None else "Not available"],
        ["Type of Soil", soil.get("label") or soil.get("type") or "Not available"],
        ["Agro-Ecological Zone", aez.get("zone") or aez.get("label") or "Not available"],
        ["Regional Prosperity", prop.get("tier") or prop.get("score") or "Not available"],
    ]
    story = _section("Regional Parameters", s)
    story += [_table(rows, [68 * mm, 106 * mm], s), Spacer(1, 7)]

    crops = data.get("recommended_crops") or {}
    allc = crops.get("all") or []
    cr = [["MODEL", "CROP", "SCORE"]]
    for crop in allc[:6]:
        cr.append(["Current FarmScore model", crop.get("crop") or "—", f"{crop.get('score', '—')}%"])
    if len(cr) == 1:
        cr.append(["—", "Not available", "—"])
    story += [Paragraph("Major Crops / Model Recommendations", s["Section"]), _table(cr, [75 * mm, 55 * mm, 44 * mm], s), Spacer(1, 5)]
    story.append(Paragraph("These are Bhumi AI model recommendations, not a regional crop-area census.", s["Tiny"]))
    story.append(PageBreak())
    return story


def _risk_page(data, s):
    climate = data.get("climate_risk") or {}
    flags = climate.get("flags") or []
    story = _section("Risk & Interpretation", s)
    rows = [
        ["ITEM", "VALUE"],
        ["Climate Risk", climate.get("level") or "—"],
        ["Risk Flags", "; ".join(map(str, flags)) if flags else "None detected"],
        ["Scoring Method", "Transparent weighted suitability sub-scores; missing data are redistributed"],
        ["Decision Limitation", "Use field verification and institutional policy for lending / insurance decisions"],
    ]
    story += [_table(rows, [50 * mm, 124 * mm], s), Spacer(1, 7)]
    story.append(Paragraph("FarmScore is an analytical suitability/condition index and should not be used as the sole basis for lending, insurance, agronomic or legal decisions.", s["Body7"]))
    story.append(PageBreak())
    return story


def _glossary_page(s):
    preferred = ["FarmScore", "Crop Health", "Crop Performance", "Cropping Intensity", "Geotag", "Groundwater Thickness", "Hectare (ha)", "Irrigation Condition", "Kharif Season", "Land use type", "Overall FarmScore", "Price", "Potential Yield", "Rabi Season", "Risk Rating", "Seasonal Score", "Topography"]
    terms = [(k, GLOSSARY_TERMS[k]) for k in preferred if k in GLOSSARY_TERMS]
    if not terms:
        terms = list(GLOSSARY_TERMS.items())[:17]
    rows = [["TERM", "DESCRIPTION"]] + [[k, v] for k, v in terms]
    story = _section("Glossary", s)
    story += [_table(rows, [42 * mm, 132 * mm], s), PageBreak()]
    return story


def _colour_ranges(s):
    story = _section("Colour Ranges", s)
    rows = [
        ["CATEGORY", "RISK RATING", "BHUMI FARMSCORE"],
        ["Poor", "Highest", "400-625"],
        ["Fair", "High", "626-725"],
        ["Good", "Medium", "726-790"],
        ["Very Good", "Low", "791-870"],
        ["Excellent", "Lowest", "871-1000"],
    ]
    story += [_table(rows, [58 * mm, 48 * mm, 68 * mm], s), Spacer(1, 7)]
    story.append(Paragraph("Disclaimer: This system-generated report contains parameters processed using remote-sensing and environmental datasets. Crop identification, yield estimates and regional proxies are subject to model and data limitations. Field verification and applicable institutional policy remain necessary.", s["Body7"]))
    return story


def generate_pdf_report(data: Dict[str, Any]) -> bytes:
    """Build a PDF from an existing /calculate response."""
    if not isinstance(data, dict) or "score" not in data:
        raise ValueError("Report payload must include score")

    s = _styles()
    out = io.BytesIO()
    _header_footer.ref_id = _ref_id(data)

    doc = SimpleDocTemplate(
        out,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=15 * mm,
        title="Bhumi AI FarmScore Report",
        author="Bhumi AI",
    )

    story = []
    story += _score_page(data, s)
    story += _location_page(data, s)
    story += _water_page(data, s)
    story += _regional_page(data, s)
    story += _risk_page(data, s)
    story += _glossary_page(s)
    story += _colour_ranges(s)
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return out.getvalue()
