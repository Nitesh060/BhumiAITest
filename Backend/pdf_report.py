"""Bhumi AI decision-oriented PDF report generator.

Uses only the /calculate payload plus optional crop_intelligence attached by
Frontend/report.js. It never recomputes the FarmScore.
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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak

from glossary import GLOSSARY_TERMS
from enrichment_service import fetch_farm_thumbnail_url

logger = logging.getLogger(__name__)
BLUE = colors.HexColor("#1a56c4")
DARK = colors.HexColor("#0f3a92")
GREEN = colors.HexColor("#2f9e63")
GREY = colors.HexColor("#666666")
LIGHT = colors.HexColor("#f2f2f2")
BORDER = colors.HexColor("#d9d9d9")
GRADE = {"Poor":"#d64545", "Fair":"#e8912d", "Average":"#e8c02d", "Good":"#7fbf3f", "Excellent":"#2f9e63"}


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("T2", parent=s["Title"], fontSize=19, leading=23, textColor=DARK, spaceAfter=3))
    s.add(ParagraphStyle("S2", parent=s["Normal"], fontSize=8, leading=10, textColor=GREY))
    s.add(ParagraphStyle("H2x", parent=s["Heading2"], fontSize=11.5, leading=14, textColor=DARK, spaceBefore=12, spaceAfter=7))
    s.add(ParagraphStyle("B2", parent=s["Normal"], fontSize=8.5, leading=11.2))
    s.add(ParagraphStyle("C2", parent=s["Normal"], fontSize=7, leading=8.5))
    s.add(ParagraphStyle("CS", parent=s["Normal"], fontSize=6.2, leading=7.4))
    return s


def _esc(v):
    return html.escape("—" if v is None else str(v))


def _p(v, style):
    return Paragraph(_esc(v), style)


def _section(title, s):
    bar = Table([[""]], colWidths=[3*mm], rowHeights=[5.5*mm])
    bar.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), GREEN), ("LEFTPADDING", (0,0), (-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0), ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    t = Table([[bar, Paragraph(_esc(title), s["H2x"])]], colWidths=[5*mm,155*mm])
    t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0), ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0), ("LEFTPADDING",(1,0),(1,0),7)]))
    return t


def _table(rows, widths):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),BLUE), ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"), ("FONTSIZE",(0,0),(-1,0),7),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHT]),
        ("GRID",(0,0),(-1,-1),0.4,BORDER), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("LEFTPADDING",(0,0),(-1,-1),4), ("RIGHTPADDING",(0,0),(-1,-1),4),
    ]))
    return t


def _gauge(score, tmp):
    fig, ax = plt.subplots(figsize=(3.3,1.8), subplot_kw={"projection":"polar"})
    bands=[(300,421,"#d64545"),(421,541,"#e8912d"),(541,661,"#e8c02d"),(661,781,"#7fbf3f"),(781,901,"#2f9e63")]
    for lo,hi,col in bands:
        a=3.14159265*(1-(lo-300)/600); b=3.14159265*(1-(hi-300)/600)
        ax.bar((a+b)/2,.28,width=a-b,bottom=.72,color=col,edgecolor="white")
    score=max(300,min(900,int(score or 300))); th=3.14159265*(1-(score-300)/600)
    ax.plot([th,th],[0,.72],color="black",linewidth=2)
    ax.set_theta_zero_location("W"); ax.set_theta_direction(-1); ax.set_thetamin(0); ax.set_thetamax(180); ax.set_ylim(0,1); ax.axis("off")
    p=str(Path(tmp)/"gauge.png"); fig.savefig(p,dpi=170,transparent=True,bbox_inches="tight"); plt.close(fig); return p


def _chart(points, value_key, label_key, title, ylabel, tmp, filename):
    pts=[p for p in (points or []) if p.get(value_key) is not None]
    if not pts: return None
    fig,ax=plt.subplots(figsize=(6.1,2.1)); ax.bar([str(p.get(label_key,"—")) for p in pts],[float(p[value_key]) for p in pts],width=.62)
    ax.set_title(title,fontsize=9,loc="left"); ax.set_ylabel(ylabel,fontsize=7); ax.tick_params(labelsize=6.5)
    for sp in ("top","right"): ax.spines[sp].set_visible(False)
    fig.tight_layout(); p=str(Path(tmp)/filename); fig.savefig(p,dpi=170,bbox_inches="tight"); plt.close(fig); return p


def _score(data,s,tmp):
    score=data.get("score",300); grade=data.get("grade","—"); comps=data.get("components") or {}
    rows=[["Parameter","Observed","Sub-score","Weight","Source"]]
    for key,c in comps.items():
        rows.append([_p(c.get("label") or key,s["C2"]),_p(f"{c.get('raw_value')}{c.get('unit','')}",s["C2"]),_p(f"{c.get('sub_score')}/100" if c.get("sub_score") is not None else "N/A",s["C2"]),_p(f"{c.get('weight',0)}%",s["C2"]),_p(c.get("source") or "—",s["CS"])])
    gc=GRADE.get(grade,"#222222")
    top=Table([[Image(_gauge(score,tmp),width=76*mm,height=40*mm),Paragraph(f'<font size="27" color="{gc}"><b>{_esc(score)}</b></font><br/><font size="12" color="{gc}"><b>{_esc(grade)}</b></font><br/><font size="7">300–900 FarmScore scale</font>',s["B2"])]],colWidths=[85*mm,45*mm])
    top.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0)]))
    return [_section("1. Overall FarmScore & Parameter Evidence",s),top,Spacer(1,4),_table(rows,[47*mm,28*mm,23*mm,20*mm,38*mm])]


def _snapshot(data,s,tmp):
    e=data.get("enrichment") or {}; c=data.get("climate_risk") or {}; rc=data.get("recommended_crops") or {}; primary=rc.get("primary") or {}; soil=e.get("soil_type") or {}; irr=e.get("irrigation") or {}; ci=e.get("cropping_intensity") or {}; coords=data.get("coordinates") or {}
    rows=[["Field","Value"],["Coordinates",f"{coords.get('lat')}° N, {coords.get('lng')}° E"],["Land use","Agricultural"],["Soil",soil.get("label") or "—"],["Irrigation","Likely irrigated" if irr.get("likely_irrigated") else ("Likely rainfed" if irr.get("likely_irrigated") is False else "—")],["Cropping intensity",ci.get("label") or "—"],["Top recommended crop",f"{primary.get('crop','—')} ({primary.get('score','—')}%)"],["Climate risk",c.get("level") or "—"]]
    story=[_section("2. Farm Location & Executive Snapshot",s),_table([[ _p(a,s["C2"]),_p(b,s["C2"]) ] if i else [a,b] for i,(a,b) in enumerate(rows)],[50*mm,106*mm])]
    try:
        if coords.get("lat") is not None and coords.get("lng") is not None:
            url=fetch_farm_thumbnail_url(coords["lat"],coords["lng"],data.get("farm_polygon") or data.get("polygon"))
            if url:
                r=requests.get(url,timeout=15); r.raise_for_status(); p=str(Path(tmp)/"farm_satellite.png"); Path(p).write_bytes(r.content)
                story += [Spacer(1,5),Image(p,width=92*mm,height=72*mm),Paragraph("Satellite true-colour context. Scoring is based on the measured parameters, not on this image alone.",s["S2"])]
    except Exception: logger.info("Satellite thumbnail unavailable",exc_info=True)
    return story


def _crop(data,s):
    ci=data.get("crop_intelligence") or {}; ident=ci.get("identification") or {}; gs=ci.get("growth_stage") or {}; sh=ci.get("sowing_harvest_prediction") or {}; rot=ci.get("crop_rotation") or {}; cal=ci.get("crop_calendar") or {}; primary=(data.get("recommended_crops") or {}).get("primary") or {}
    rows=[["Indicator","Result"],["Current crop hypothesis",ident.get("identified_crop") or "—"],["Identification confidence",ident.get("confidence") or "—"],["Peak NDVI",f"{ident.get('peak_ndvi')} · month {ident.get('peak_ndvi_month')}" if ident.get("peak_ndvi") is not None else "—"],["Early flood / paddy signature","Detected" if ident.get("flood_signature_detected") else "Not detected"],["Growth stage",gs.get("stage") or "—"],["Current / peak NDVI",f"{gs.get('current_ndvi')} / {gs.get('peak_ndvi')}" if gs.get("current_ndvi") is not None else "—"],["Estimated sowing",sh.get("sowing_estimate_month") or "—"],["Estimated harvest",sh.get("harvest_estimate_month") or "—"],["Top recommendation",f"{primary.get('crop','—')} ({primary.get('score','—')}%)"]]
    story=[_section("3. Crop Intelligence",s),_table([[ _p(a,s["C2"]),_p(b,s["C2"]) ] if i else [a,b] for i,(a,b) in enumerate(rows)],[55*mm,101*mm])]
    if not ci: story.append(Paragraph("Detailed crop-intelligence output was not attached. The report still shows the crop recommendation and satellite-derived cropping history available in the /calculate response.",s["S2"]))
    if ident.get("method"): story.append(Paragraph("Method: "+_esc(ident["method"]),s["S2"]))
    if ident.get("note"): story.append(Paragraph(_esc(ident["note"]),s["S2"]))
    if rot.get("years"):
        rr=[["Year","Kharif","Rabi"]]+[[_p(y.get("year"),s["C2"]),_p(y.get("kharif"),s["C2"]),_p(y.get("rabi"),s["C2"])] for y in rot["years"]]
        story += [Spacer(1,4),Paragraph("3-year rotation evidence",s["Heading3"]),_table(rr,[30*mm,63*mm,63*mm])]
        if rot.get("summary"): story.append(Paragraph(_esc(rot["summary"]),s["S2"]))
    if cal:
        cr=[["Season","Typical sowing","Typical harvest","Duration"]]
        for season,info in cal.items(): cr.append([_p(season,s["C2"]),_p(info.get("sow"),s["C2"]),_p(info.get("harvest"),s["C2"]),_p(f"{info.get('duration_days')} days",s["C2"])])
        story += [Spacer(1,4),Paragraph("Crop calendar reference",s["Heading3"]),_table(cr,[30*mm,47*mm,47*mm,32*mm])]
    return story


def _history(data,s):
    h=(data.get("enrichment") or {}).get("cropping_history") or {}
    if not h.get("years"): return []
    rows=[["Year","Kharif NDVI","Kharif","Rabi NDVI","Rabi"]]
    for y in h["years"]:
        k=y.get("kharif") or {}; r=y.get("rabi") or {}; rows.append([_p(y.get("year"),s["C2"]),_p(k.get("ndvi") if k.get("ndvi") is not None else "—",s["C2"]),_p("Cropped" if k.get("cropped") else "Fallow / no signal",s["C2"]),_p(r.get("ndvi") if r.get("ndvi") is not None else "—",s["C2"]),_p("Cropped" if r.get("cropped") else "Fallow / no signal",s["C2"])])
    return [_section("4. Cropping History (Satellite-derived, 3-year)",s),_table(rows,[20*mm,28*mm,43*mm,28*mm,43*mm]),Paragraph("Season-level signal only; this does not identify crop species.",s["S2"])]


def _profile(data,s):
    e=data.get("enrichment") or {}; yp=data.get("yield_prediction") or {}; rows=[["Farm / regional indicator","Value"]]
    vals=[("Agro-Ecological Zone",(e.get("agro_ecological_zone") or {}).get("zone")),("Adjacent land cover",", ".join(f"{x.get('class')} {x.get('percent')}%" for x in ((e.get("adjacent_land_cover") or {}).get("breakdown") or [])[:3])),("Estimated yield",f"{yp.get('estimated_yield_kg_per_ha')} kg/ha" if yp.get("estimated_yield_kg_per_ha") is not None else None),("Estimated total",f"{yp.get('estimated_total_yield_quintal')} quintal on {yp.get('area_ha')} ha" if yp.get("estimated_total_yield_quintal") is not None else None)]
    rows += [[_p(a,s["C2"]),_p(b or "—",s["C2"])] for a,b in vals]
    return [_section("5. Farm Profile & Yield Context",s),_table(rows,[55*mm,101*mm]),Paragraph("Yield is a formula-based proxy, not a measured harvest or trained ML prediction.",s["S2"])]


def _water(data,s,tmp):
    story=[_section("6. Water Conditions",s)]; r=_chart(data.get("rainfall_monthly"),"mm_per_day","month","Rainfall profile","mm/day",tmp,"rain.png"); g=_chart(data.get("groundwater_trend"),"groundwater","year","Groundwater trend","kg/m²",tmp,"gw.png")
    if r: story.append(Image(r,width=155*mm,height=53*mm))
    if g: story.append(Image(g,width=155*mm,height=53*mm))
    if not r and not g: story.append(Paragraph("No water trend series was available in this payload.",s["B2"]))
    return story


def _regional(data,s):
    e=data.get("enrichment") or {}; t=e.get("temperature_annual_range") or {}; p=e.get("regional_prosperity") or {}; w=e.get("nearest_water_body") or {}; top=e.get("topography") or {}; pop=e.get("village_population") or {}; dr=e.get("drought_instances") or {}
    vals=[("Annual temperature range",f"{t.get('min_c')}°C – {t.get('max_c')}°C (avg {t.get('mean_c')}°C)" if t.get("min_c") is not None else None),("Regional prosperity",p.get("tier")),("Water body within 2 km","Present" if w.get("water_present") else ("Not detected" if w.get("water_present") is False else None)),("Topography",f"{top.get('terrain')} · {top.get('elevation_m')} m · slope {top.get('slope_degrees')}°" if top.get("terrain") else None),("Nearby population proxy",f"~{pop.get('estimated_population')} within {pop.get('radius_m')} m" if pop.get("estimated_population") is not None else None),("Drought years",", ".join(map(str,dr.get("drought_years",[]))) if dr.get("drought_years") else "None detected")]
    rows=[["Regional parameter","Value"]]+[[_p(a,s["C2"]),_p(b or "—",s["C2"])] for a,b in vals]
    return [_section("7. Regional Parameters",s),_table(rows,[55*mm,101*mm])]


def _risk(data,s):
    climate=data.get("climate_risk") or {}; comps=data.get("components") or {}; sources=sorted({c.get("source") for c in comps.values() if c.get("source")}); flags=climate.get("flags") or []
    rows=[["Decision context","Result"],["Climate risk",climate.get("level") or "—"],["Risk flags","; ".join(flags) if flags else "None detected"],["Data sources","; ".join(sources) or "—"],["Interpretation","Suitability / condition index; not a standalone credit or yield decision"]]
    return [_section("8. Risk Context, Methodology & Data Provenance",s),_table([[ _p(a,s["C2"]),_p(b,s["C2"]) ] if i else [a,b] for i,(a,b) in enumerate(rows)],[55*mm,101*mm])]


def _bands(s):
    return [_section("9. Score Bands",s),_table([["Score","Band"],["781–900","Excellent"],["661–780","Good"],["541–660","Average"],["421–540","Fair"],["300–420","Poor"]],[55*mm,101*mm]),Paragraph("The application reports FarmScore on a 300–900 scale.",s["S2"])]


def _glossary(s):
    rows=[["Term","Meaning"]]
    for item in GLOSSARY_TERMS[:30]:
        term=item.get("term","—"); full=item.get("full_form"); expl=item.get("explanation",""); meaning=(f"{full}. " if full else "")+expl
        rows.append([_p(term,s["C2"]),_p(meaning,s["CS"])])
    return [_section("10. Glossary",s),_table(rows,[45*mm,111*mm])]


def _footer(canvas,doc):
    canvas.saveState(); canvas.setFont("Helvetica",6.7); canvas.setFillColor(GREY); canvas.drawString(18*mm,9*mm,"Bhumi AI · Satellite-powered farm intelligence"); canvas.drawRightString(192*mm,9*mm,f"Page {doc.page}"); canvas.restoreState()


def generate_pdf_report(data: Dict[str,Any]) -> bytes:
    if not data or "score" not in data: raise ValueError("Report payload must include score")
    s=_styles(); out=io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        doc=SimpleDocTemplate(out,pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=15*mm,bottomMargin=15*mm,title="Bhumi AI Farm Intelligence Report")
        coords=data.get("coordinates") or {}
        story=[Paragraph("Bhumi AI",s["T2"]),Paragraph("Extended Farm Intelligence Report",s["S2"]),Spacer(1,4),Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y, %H:%M')} · {coords.get('lat')}° N, {coords.get('lng')}° E",s["S2"]),Spacer(1,7)]
        story += _score(data,s,tmp)+_snapshot(data,s,tmp)+_crop(data,s)+_profile(data,s)+_history(data,s)+_water(data,s,tmp)+_regional(data,s)+_risk(data,s)+_bands(s)
        story += [PageBreak()]+_glossary(s)+[_section("11. Disclaimer",s),Paragraph("Bhumi AI combines satellite-derived indicators, environmental datasets and transparent heuristic rules. Crop identification, yield estimates and regional proxies are indicative unless validated against field observations or labelled ground-truth data. FarmScore is a suitability/condition index and should not be treated as a standalone lending, insurance, agronomic or legal decision. Field verification and applicable institutional policy remain necessary.",s["B2"]),Spacer(1,7),Paragraph("Missing observations are not silently treated as zero. The scoring service redistributes available weights. Thresholds remain provisional until calibrated against local ground-truth outcomes.",s["S2"])]
        doc.build(story,onFirstPage=_footer,onLaterPages=_footer)
    return out.getvalue()
