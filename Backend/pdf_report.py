"""Bhumi AI FarmScore PDF report.

The report follows the structure and information hierarchy of the supplied
SatSource sample while keeping Bhumi AI's existing 20-parameter FarmScore
and 300-900 scoring model. It does not introduce the SatSource 200/400/400
scoring split.
"""
from __future__ import annotations

import hashlib
import html
import io
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether

from glossary import GLOSSARY_TERMS
from enrichment_service import fetch_farm_thumbnail_url

logger = logging.getLogger(__name__)

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
    "Average": ("Medium Risk", YELLOW),
    "Good": ("Low Risk", GREEN),
    "Excellent": ("Lowest Risk", GREEN_DARK),
}

# Existing FarmScore factor weights. These are presentation groups only and
# do not change the scoring formula.
FACTOR_WEIGHTS = [
    ("VEGETATION", 45),
    ("RADAR", 20),
    ("WEATHER", 25),
    ("LST", 10),
]


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("TitleBhumi", parent=s["Title"], fontName="Helvetica-Bold", fontSize=18, leading=21, textColor=BLUE_DARK, spaceAfter=2))
    s.add(ParagraphStyle("Small", parent=s["Normal"], fontSize=6.8, leading=8.2, textColor=GREY))
    s.add(ParagraphStyle("Body8", parent=s["Normal"], fontSize=7.4, leading=9.4, textColor=BLACK))
    s.add(ParagraphStyle("Body7", parent=s["Normal"], fontSize=6.7, leading=8.2, textColor=BLACK))
    s.add(ParagraphStyle("Section", parent=s["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=BLACK, spaceBefore=2, spaceAfter=6))
    s.add(ParagraphStyle("BlueHead", parent=s["Heading3"], fontName="Helvetica-Bold", fontSize=8.2, leading=10, textColor=BLUE, spaceBefore=4, spaceAfter=3))
    s.add(ParagraphStyle("TableHead", parent=s["Normal"], fontName="Helvetica-Bold", fontSize=6.8, leading=8, textColor=BLUE))
    s.add(ParagraphStyle("TableBody", parent=s["Normal"], fontSize=6.7, leading=8.2, textColor=BLACK))
    s.add(ParagraphStyle("Tiny", parent=s["Normal"], fontSize=5.8, leading=7, textColor=GREY))
    s.add(ParagraphStyle("CenterTiny", parent=s["Normal"], fontSize=6.2, leading=7.2, textColor=BLACK, alignment=TA_CENTER))
    s.add(ParagraphStyle("Risk", parent=s["Normal"], fontName="Helvetica-Bold", fontSize=13, leading=15, textColor=BLUE))
    return s


def _esc(v):
    return html.escape("—" if v is None or v == "" else str(v))


def _p(v, style):
    return Paragraph(_esc(v), style)


def _fmt(v, digits=2):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return str(v)


def _reference_id(data):
    coords = data.get("coordinates") or {}
    raw = f"{coords.get('lat','')},{coords.get('lng','')}".encode()
    return "BH-" + hashlib.sha1(raw).hexdigest()[:10].upper()


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(18*mm, 285*mm, 192*mm, 285*mm)
    canvas.setFont("Helvetica-Bold", 8.5)
    canvas.setFillColor(BLUE)
    canvas.drawString(18*mm, 289*mm, "BHUMI AI")
    canvas.setFont("Helvetica-Bold", 7.5)
    canvas.drawString(38*mm, 289*mm, "FARMSCORE REPORT")
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(GREY)
    canvas.drawRightString(192*mm, 289*mm, f"Reference ID : {_header_footer.ref_id}")
    canvas.drawString(18*mm, 8*mm, "Bhumi AI · Satellite-powered farm intelligence")
    canvas.drawRightString(192*mm, 8*mm, f"Page {doc.page}")
    canvas.restoreState()

_header_footer.ref_id = "—"


def _section(title, s):
    return [Paragraph(title, s["Section"]), Table([[""]], colWidths=[174*mm], rowHeights=[0.7*mm], style=TableStyle([("BACKGROUND", (0,0), (-1,-1), BLUE)])), Spacer(1, 2)]


def _table(rows, widths, header=True, font=6.7):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("GRID", (0,0), (-1,-1), 0.35, BORDER),
        ("LEFTPADDING", (0,0), (-1,-1), 3), ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("FONTSIZE", (0,0), (-1,-1), font),
    ]
    if header:
        commands += [("BACKGROUND", (0,0), (-1,0), BLUE_LIGHT), ("TEXTCOLOR", (0,0), (-1,0), BLUE), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold")]
        if len(rows) > 1:
            commands.append(("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, LIGHT_GREY]))
    t.setStyle(TableStyle(commands))
    return t


def _gauge(score, tmp):
    score = max(300, min(900, int(score or 300)))
    fig, ax = plt.subplots(figsize=(3.35, 2.15), subplot_kw={"projection": "polar"})
    bands = [(300,421,RED), (421,541,ORANGE), (541,661,YELLOW), (661,781,colors.HexColor("#7FBF3F")), (781,901,GREEN_DARK)]
    for lo, hi, col in bands:
        a = 3.14159265 * (1 - (lo-300)/600)
        b = 3.14159265 * (1 - (hi-300)/600)
        ax.bar((a+b)/2, .30, width=a-b, bottom=.70, color=col, edgecolor="white", linewidth=.6)
    th = 3.14159265 * (1 - (score-300)/600)
    ax.plot([th, th], [0, .70], color="black", linewidth=2.2)
    ax.scatter([th], [0], s=22, color="black")
    ax.set_theta_zero_location("W")
    ax.set_theta_direction(-1)
    ax.set_thetamin(0); ax.set_thetamax(180)
    ax.set_ylim(0,1); ax.axis("off")
    path = str(Path(tmp) / "farmscore_gauge.png")
    fig.savefig(path, dpi=180, transparent=True, bbox_inches="tight")
    plt.close(fig)
    return path


def _bar_chart(values, labels, title, ylabel, tmp, filename):
    if not values:
        return None
    fig, ax = plt.subplots(figsize=(6.7, 2.25))
    ax.bar(range(len(values)), values, width=.58)
    ax.set_xticks(range(len(values)), labels, fontsize=6.5)
    ax.tick_params(axis="y", labelsize=6)
    ax.set_title(title, fontsize=9, loc="left")
    ax.set_ylabel(ylabel, fontsize=7)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y", alpha=.18)
    fig.tight_layout()
    path = str(Path(tmp) / filename)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return path


def _line_chart(values, labels, title, ylabel, tmp, filename):
    if not values:
        return None
    fig, ax = plt.subplots(figsize=(6.7, 2.25))
    ax.plot(range(len(values)), values, marker="o", linewidth=1.8)
    ax.set_xticks(range(len(values)), labels, fontsize=6.5)
    ax.tick_params(axis="y", labelsize=6)
    ax.set_title(title, fontsize=9, loc="left")
    ax.set_ylabel(ylabel, fontsize=7)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y", alpha=.18)
    fig.tight_layout()
    path = str(Path(tmp) / filename)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return path


def _normalise_series(obj, value_keys=("value", "rainfall", "rainfall_mm", "thickness")):
    if isinstance(obj, list):
        out=[]
        for x in obj:
            if isinstance(x, dict):
                label=x.get("year") or x.get("month") or x.get("label")
                val=None
                for k in value_keys:
                    if x.get(k) is not None:
                        val=x.get(k); break
                if label is not None and val is not None:
                    try: out.append((str(label), float(val)))
                    except Exception: pass
        return out
    if isinstance(obj, dict):
        for key in ("years", "data", "series", "monthly", "history"):
            if isinstance(obj.get(key), list):
                return _normalise_series(obj[key], value_keys)
        out=[]
        for k,v in obj.items():
            if isinstance(v,(int,float)):
                out.append((str(k), float(v)))
        return out
    return []


def _score_page(data, s, tmp):
    score=int(data.get("score") or 300)
    grade=data.get("grade") or "Poor"
    risk, risk_color = GRADE_RISK.get(grade, ("—", BLUE))
    comps=data.get("components") or {}
    story=[]
    story += _section("Overall FarmScore", s)
    left=Table([
        [Image(_gauge(score,tmp), width=72*mm, height=46*mm)],
        [Paragraph(f'<font size="24" color="{risk_color.hexval()}"><b>{score}</b></font>', s["Risk"])],
        [Paragraph(f'<font size="12" color="{risk_color.hexval()}"><b>{_esc(risk)}</b></font>', s["Body8"])],
    ], colWidths=[78*mm], style=TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("BOX",(0,0),(-1,-1),.5,BORDER)]))
    factors=[[Paragraph("FACTORS CONTRIBUTING TOWARDS FARMSCORE",s["BlueHead"]), "", ""], ["Factor","Weight","Model role"]]
    role={"VEGETATION":"9 Sentinel-2 indicators","RADAR":"4 Sentinel-1 indicators","WEATHER":"5 climate/weather indicators","LST":"Land-surface temperature"}
    for name,w in FACTOR_WEIGHTS:
        factors.append([_p(name,s["TableBody"]), _p(f"{w}%",s["TableBody"]), _p(role[name],s["TableBody"])])
    ft=_table(factors,[42*mm,20*mm,48*mm])
    summary=Table([[left,ft]], colWidths=[82*mm,92*mm], style=TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0)]))
    story += [summary, Spacer(1,7)]
    story += _section("Farm Details", s)
    e=data.get("enrichment") or {}; coords=data.get("coordinates") or {}
    irr=e.get("irrigation") or {}; ci=e.get("cropping_intensity") or {}; soil=e.get("soil_type") or {}
    details=[["S.NO.","REGION / LOCATION","SURVEY NO.","IRRIGATION CONDITION","CROPPING INTENSITY","FARM CENTROID","LAND USE TYPE"],
             ["1", "Location derived from selected coordinates", "Not available", "Irrigated" if irr.get("likely_irrigated") else ("Rainfed" if irr.get("likely_irrigated") is False else "Not available"), ci.get("label") or "Not available", f"{coords.get('lat','—')}° N / {coords.get('lng','—')}° E", "Agricultural"]]
    story += [_table(details,[12*mm,42*mm,28*mm,28*mm,27*mm,27*mm,20*mm]), Spacer(1,6)]
    story += _section("Cropping History", s)
    h=e.get("cropping_history") or {}; years=h.get("years") or []
    rows=[["SEASON","CROP NAME","PRICE (₹/100kg)","CROP PERFORMANCE","CROP YIELD (Kg/Ha)"]]
    # The structure follows the supplied SatSource report. Where Bhumi does
    # not have a measured historical crop/yield/price value, it explicitly
    # shows Not available rather than fabricating one.
    for y in years:
        for season_key, season_label in (("kharif","Kharif"),("rabi","Rabi")):
            item=y.get(season_key) or {}
            if not item.get("cropped") and item.get("ndvi") is None:
                continue
            crop="Not identified"
            perf="Not available"
            ndvi=item.get("ndvi")
            if ndvi is not None:
                perf="Good signal" if float(ndvi)>=.55 else ("Average signal" if float(ndvi)>=.35 else "Below average signal")
            rows.append([f"{season_label}\n({y.get('year')})",crop,"Not available",perf,"Not available"])
    if len(rows)==1:
        rows.append(["No historical season signal","—","—","—","—"])
    story += [_table(rows,[34*mm,32*mm,30*mm,38*mm,40*mm])]
    story.append(Paragraph("Historical crop species, market price and measured yield are shown only when supported by the available data. Satellite-derived season signals are not presented as crop-species ground truth.",s["Tiny"]))
    story.append(PageBreak())
    return story


def _location_page(data, s, tmp):
    e=data.get("enrichment") or {}; coords=data.get("coordinates") or {}
    story=[]
    story += _section("Farm Location", s)
    image=None
    try:
        url=fetch_farm_thumbnail_url(coords.get("lat"), coords.get("lng"), data.get("polygon") or data.get("farm_polygon"))
        if url:
            r=requests.get(url,timeout=15); r.raise_for_status()
            image=str(Path(tmp)/"farm_location.png"); Path(image).write_bytes(r.content)
    except Exception:
        logger.info("Farm location image unavailable", exc_info=True)
    if image:
        story += [Table([[Image(image,width=118*mm,height=68*mm)]], colWidths=[174*mm], style=TableStyle([("BOX",(0,0),(-1,-1),.5,BORDER),("ALIGN",(0,0),(-1,-1),"CENTER")])), Spacer(1,6)]
    loc=[["FARM LABEL","SURVEY DETAILS","FARM CENTROID"],["1","Not available",f"{coords.get('lat','—')}° N  {coords.get('lng','—')}° E"]]
    story += [_table(loc,[28*mm,72*mm,74*mm]), Spacer(1,7)]
    story += [Paragraph("Adjacent Land Details",s["BlueHead"])]
    adj=(e.get("adjacent_land_cover") or {}).get("breakdown") or []
    ar=[["ADJACENT LAND LABEL","ADJACENT LAND / COVER DETAILS","DIRECTION"]]
    for i,x in enumerate(adj[:6],1):
        ar.append([str(i), f"{x.get('class','Not available')} · {x.get('percent','—')}%", "Not available"])
    if len(ar)==1: ar.append(["—","Not available","—"])
    story += [_table(ar,[35*mm,92*mm,47*mm]), Spacer(1,4), Paragraph("Farm boundary and adjacent-land details are dependent on the data available for the selected location.",s["Tiny"]), PageBreak()]
    return story


def _water_page(data,s,tmp):
    e=data.get("enrichment") or {}
    story=[]
    story += _section("Water Conditions",s)
    story.append(Paragraph("Rainfall and water-related indicators are derived from the datasets used by the existing FarmScore pipeline.",s["Small"]))
    rain=_normalise_series(data.get("rainfall_monthly"), ("rainfall","rainfall_mm","value"))
    if not rain:
        rain=_normalise_series(e.get("rainfall_monthly"), ("rainfall","rainfall_mm","value"))
    labels=[x[0] for x in rain[-12:]]; vals=[x[1] for x in rain[-12:]]
    if vals:
        p=_bar_chart(vals,labels,"RAINFALL TREND","Rainfall",tmp,"rainfall.png")
        story += [Paragraph("RAINFALL TREND",s["BlueHead"]),Image(p,width=155*mm,height=52*mm),Spacer(1,5)]
    else:
        story += [Paragraph("RAINFALL TREND",s["BlueHead"]),Paragraph("Rainfall trend data not available in the current report payload.",s["Body8"]),Spacer(1,5)]
    gw=data.get("groundwater_trend") or e.get("groundwater_trend") or {}
    gw_series=_normalise_series(gw,("thickness","groundwater_thickness","value"))
    if gw_series:
        labels=[x[0] for x in gw_series[-12:]]; vals=[x[1] for x in gw_series[-12:]]
        p=_bar_chart(vals,labels,"GROUNDWATER TREND","Water / thickness signal",tmp,"groundwater.png")
        story += [Paragraph("GROUNDWATER TREND",s["BlueHead"]),Image(p,width=155*mm,height=52*mm)]
    else:
        water=e.get("nearest_water_body") or {}
        story += [Paragraph("GROUNDWATER TREND",s["BlueHead"]),Paragraph(f"Nearest water-body signal: {_esc(water.get('distance_km') or water.get('distance') or 'Not available')}",s["Body8"])]
    story += [Paragraph("*Values are calculated for the selected farm/boundary where the underlying dataset supports it.",s["Tiny"]),PageBreak()]
    return story


def _regional_page(data,s):
    e=data.get("enrichment") or {}
    story=[]
    story += _section("Regional Parameters",s)
    soil=e.get("soil_type") or {}; temp=e.get("temperature_annual_range") or {}; drought=e.get("drought_instances") or {}; pop=e.get("village_population") or {}; aez=e.get("agro_ecological_zone") or {}; prop=e.get("regional_prosperity") or {}; water=e.get("nearest_water_body") or {}
    rows=[["PARAMETER","VALUE"],
          ["NEAREST MANDI","Not available"],
          ["PROXIMITY TO NEAREST ROAD / RAIL","Not available"],
          ["PROXIMITY TO NEAREST MAJOR WATER BODY",water.get("distance_km") or water.get("distance") or "Not available"],
          ["DROUGHT INSTANCES (district/25km proxy, since 2000)",", ".join(map(str,drought.get("drought_years",[])[:12])) or "None reported / not available"],
          ["VILLAGE POPULATION",pop.get("estimated_population") or "Not available"],
          ["AMBIENT TEMPERATURE", temp.get("min_c") is not None and f"{temp.get('min_c')}°C–{temp.get('max_c')}°C" or "Not available"],
          ["TYPE OF SOIL",soil.get("label") or soil.get("type") or "Not available"],
          ["AGRO-ECOLOGICAL SUB-ZONE",aez.get("zone") or aez.get("label") or "Not available"],
          ["REGIONAL PROSPERITY INDEX",prop.get("score") if prop.get("score") is not None else "Not available"]]
    story += [_table([[ _p(a,s["TableBody"]),_p(b,s["TableBody"]) ] if i else [a,b] for i,(a,b) in enumerate(rows)],[68*mm,106*mm]),Spacer(1,7)]
    story += [Paragraph("Major Crops / Model Recommendations",s["Section"])]
    rc=data.get("recommended_crops") or {}; allc=rc.get("all") or []
    cr=[["SEASON / MODEL","CROP NAME","MODEL SCORE"]]
    if allc:
        for i,c in enumerate(allc[:4]):
            cr.append(["Current FarmScore crop model",c.get("crop") or "—",f"{c.get('score','—')}%"])
    else:
        cr.append(["—","Not available","—"])
    story += [_table(cr,[65*mm,65*mm,44*mm]),Paragraph("These are Bhumi AI model recommendations, not a district crop-area census. Regional major-crop statistics are shown only when available from a trusted regional dataset.",s["Tiny"]),PageBreak()]
    return story


def _glossary_page(s):
    story=[]
    story += _section("Glossary",s)
    preferred=["FarmScore","Crop Health","Crop Performance","Cropping Intensity","Geotag","Groundwater Thickness","Hectare (ha)","Irrigation Condition","Kharif Season","Land use type","Overall FarmScore","Price","Potential Yield","Rabi Season","Risk Rating","Seasonal Score","Topography"]
    terms=[]
    for k in preferred:
        if k in GLOSSARY_TERMS: terms.append((k,GLOSSARY_TERMS[k]))
    if not terms:
        terms=list(GLOSSARY_TERMS.items())[:17]
    rows=[["TERM","DESCRIPTION"]]+[[_p(k,s["TableBody"]),_p(v,s["TableBody"])] for k,v in terms]
    story += [_table(rows,[42*mm,132*mm]),PageBreak()]
    return story


def _colour_ranges(s):
    story=[]
    story += _section("Colour Ranges",s)
    story.append(Paragraph("The categories below are the current Bhumi AI FarmScore interpretation mapped to lending-risk language. The report presentation follows the supplied SatSource reference; the underlying Bhumi scoring scale remains 300–900.",s["Body8"]))
    bullets=[
        ("Poor farms / Highest Risk","Low productivity / weaker satellite and environmental evidence; higher risk signal.",RED),
        ("Fair farms / High Risk","Average-to-weaker evidence; lower priority for lending decisions.",ORANGE),
        ("Average farms / Medium Risk","Moderate evidence with meaningful positive and negative indicators.",YELLOW),
        ("Good farms / Low Risk","Strong evidence across the measured parameters.",colors.HexColor("#7FBF3F")),
        ("Excellent farms / Lowest Risk","Strongest overall evidence across the measured parameters.",GREEN_DARK),
    ]
    for title,desc,col in bullets:
        story.append(Table([["",Paragraph(f"<b>{_esc(title)}</b> — {_esc(desc)}",s["Body8"])]],colWidths=[5*mm,169*mm],style=TableStyle([("BACKGROUND",(0,0),(0,0),col),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),3),("RIGHTPADDING",(0,0),(-1,-1),3),("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)])))
    rows=[["CATEGORY","RISK RATING","BHUMI FARMSCORE"]]
    rows += [["Poor","Highest","300–420"],["Fair","High","421–540"],["Average","Medium","541–660"],["Good","Low","661–780"],["Excellent","Lowest","781–900"]]
    story += [Spacer(1,6),_table(rows,[58*mm,48*mm,68*mm]),Spacer(1,6)]
    story.append(Paragraph("Disclaimer: This is a system-generated report containing parameters processed using remote-sensing and environmental datasets. Crop identification, yield estimates and regional proxies are subject to model and data limitations. FarmScore is an analytical suitability/condition index and should not be used as the sole basis for lending, insurance, agronomic or legal decisions. Field verification and applicable institutional policy remain necessary.",s["Body7"]))
    return story


def generate_pdf_report(data: Dict[str, Any]) -> bytes:
    if not data or "score" not in data:
        raise ValueError("Report payload must include score")
    s=_styles(); out=io.BytesIO(); ref=_reference_id(data); _header_footer.ref_id=ref
    with tempfile.TemporaryDirectory() as tmp:
        doc=SimpleDocTemplate(out,pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=20*mm,bottomMargin=15*mm,title="Bhumi AI FarmScore Report",author="Bhumi AI")
        story=[]
        story += _score_page(data,s,tmp)
        story += _location_page(data,s,tmp)
        story += _water_page(data,s,tmp)
        story += _regional_page(data,s)
        story += _glossary_page(s)
        story += _colour_ranges(s)
        doc.build(story,onFirstPage=_header_footer,onLaterPages=_header_footer)
    return out.getvalue()
