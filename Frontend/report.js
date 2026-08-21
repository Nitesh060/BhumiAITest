const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://bhumiaitest.onrender.com";

// escapeHTML is defined once in shell.js (loaded before this file on
// every page) — no longer duplicated here.

function row(icon, label, value) {
    return `<div class="enrichment-row"><span class="er-icon">${escapeHTML(icon)}</span><span class="er-label">${escapeHTML(label)}</span><span class="er-value">${escapeHTML(value)}</span></div>`;
}

function simpleBarChart(container, title, points, valueKey, labelKey, unit) {
    const values = (points || []).map(p => p[valueKey]).filter(v => v != null);
    if (!values.length) {
        container.innerHTML = `<p class="empty-hint">${escapeHTML(title)}: no data.</p>`;
        return;
    }
    const max = Math.max(...values) || 1;
    const avg = values.reduce((a, b) => a + b, 0) / values.length;
    container.innerHTML = `
        <div class="sbc-title">${escapeHTML(title)} <span class="sbc-avg">avg ${avg.toFixed(1)}${escapeHTML(unit)}</span></div>
        <div class="sbc-bars">${(points || []).map(p => {
            const v = p[valueKey];
            const pct = v != null ? Math.max(4, (v / max) * 100) : 0;
            return `<div class="sbc-bar-col"><div class="sbc-bar-track"><div class="sbc-bar-fill" style="height:${pct}%;"></div></div><div class="sbc-bar-label">${escapeHTML(p[labelKey])}</div><div class="sbc-bar-value">${v != null ? escapeHTML(Number(v).toFixed(1)) : "—"}</div></div>`;
        }).join("")}</div>`;
}

function renderFarmDetails(data) {
    const coords = data.coordinates || {};
    const e = data.enrichment || {};
    const irrigation = e.irrigation || {};
    const intensity = e.cropping_intensity || {};
    const soil = e.soil_type || {};
    const yp = data.yield_prediction || {};
    const primary = (data.recommended_crops || {}).primary || {};
    document.getElementById("farm-details-list").innerHTML = [
        row("📍", "Farm Centroid", `${coords.lat}° N, ${coords.lng}° E`),
        row("🏞️", "Land Use", "Agricultural"),
        row("🪨", "Soil Type", soil.label || "—"),
        row("💧", "Irrigation", irrigation.likely_irrigated == null ? "—" : (irrigation.likely_irrigated ? "Likely irrigated" : "Likely rainfed")),
        row("🌿", "Cropping Intensity", intensity.label || "—"),
        row("🏆", "Top Recommended Crop", primary.crop ? `${primary.crop} (${primary.score}%)` : "—"),
        row("📦", "Estimated Yield", yp.estimated_yield_kg_per_ha != null ? `${yp.estimated_yield_kg_per_ha} kg/ha` : "—"),
    ].join("");
}

function renderScoreEvidence(data) {
    const components = data.components || {};
    const params = Object.entries(components);
    const used = params.filter(([, c]) => c && c.sub_score != null).length;
    const sources = [...new Set(params.map(([, c]) => c.source).filter(Boolean))];
    document.getElementById("score-summary-list").innerHTML = [
        row("🎯", "Bhumi AI Score", `${data.score}/900 (${data.grade})`),
        row("📐", "Parameters Used", `${used} of ${data.parameters_total || 20}`),
        row("🛰️", "Data Sources", sources.join(" · ") || "—"),
        row("⚠️", "Interpretation", "Suitability / condition index; not a standalone credit or yield decision"),
    ].join("");

    const rows = params.map(([key, c]) => `<tr><td>${escapeHTML(c.label || key)}</td><td>${escapeHTML(c.raw_value)}${escapeHTML(c.unit || "")}</td><td>${c.sub_score != null ? escapeHTML(c.sub_score) + "/100" : "N/A"}</td><td>${escapeHTML(c.weight)}%</td><td>${escapeHTML(c.source || "—")}</td></tr>`).join("");
    document.getElementById("score-breakdown-table").innerHTML = `<table class="report-table"><thead><tr><th>Parameter</th><th>Observed</th><th>Sub-score</th><th>Weight</th><th>Source</th></tr></thead><tbody>${rows || "<tr><td colspan=5>No parameter data.</td></tr>"}</tbody></table>`;
}

function renderEnrichment(data) {
    const e = data.enrichment || {};
    const adjacent = (e.adjacent_land_cover || {}).breakdown || [];
    const rows = [];
    if (e.agro_ecological_zone?.zone) rows.push(row("🌍", "Agro-Ecological Zone", e.agro_ecological_zone.zone));
    if (e.soil_type?.label) rows.push(row("🪨", "Soil Type", e.soil_type.label));
    if (e.cropping_intensity?.label) rows.push(row("🌿", "Cropping Intensity", `${e.cropping_intensity.label} (${e.cropping_intensity.estimated_cycles || "—"} cycle/yr est.)`));
    if (e.irrigation?.likely_irrigated != null) rows.push(row("💧", "Irrigation Signal", e.irrigation.likely_irrigated ? "Likely irrigated" : "Likely rainfed"));
    if (adjacent.length) rows.push(row("🏞️", "Adjacent Land Cover", adjacent.slice(0, 3).map(x => `${x.class} ${x.percent}%`).join(", ")));
    document.getElementById("enrichment-list").innerHTML = rows.length ? rows.join("") : `<p class="empty-hint">No enrichment data in this result.</p>`;
}

function renderCropIntelligence(data) {
    const ci = data.crop_intelligence || {};
    const id = ci.identification || {};
    const gs = ci.growth_stage || {};
    const sh = ci.sowing_harvest_prediction || {};
    const primary = (data.recommended_crops || {}).primary || {};
    const list = document.getElementById("crop-intelligence-list");
    list.innerHTML = [
        row("🌾", "Current Crop Hypothesis", id.identified_crop || "No clear identification"),
        row("🎯", "Identification Confidence", id.confidence || "none"),
        row("🌱", "Growth Stage", gs.stage || "—"),
        row("📈", "Peak NDVI", id.peak_ndvi != null ? `${id.peak_ndvi} · month ${id.peak_ndvi_month}` : "—"),
        row("💧", "Early Flood Signature", id.flood_signature_detected ? "Detected (paddy-like)" : "Not detected"),
        row("📅", "Sowing / Harvest", `${sh.sowing_estimate_month || "—"} / ${sh.harvest_estimate_month || "—"}`),
        row("🏆", "Crop Recommendation", primary.crop ? `${primary.crop} (${primary.score}%)` : "—"),
    ].join("");

    const rot = ci.crop_rotation || {};
    if (rot.years?.length) {
        document.getElementById("crop-rotation-report").innerHTML = `<div style="margin-top:8px;font-size:.76rem;font-weight:600;">3-year rotation signal</div><table class="report-table"><thead><tr><th>Year</th><th>Kharif</th><th>Rabi</th></tr></thead><tbody>${rot.years.map(y => `<tr><td>${escapeHTML(y.year)}</td><td>${escapeHTML(y.kharif)}</td><td>${escapeHTML(y.rabi)}</td></tr>`).join("")}</tbody></table>`;
    } else {
        document.getElementById("crop-rotation-report").innerHTML = "";
    }
}

function renderCroppingHistory(data) {
    const history = data.enrichment && data.enrichment.cropping_history;
    const wrap = document.getElementById("cropping-history-table");
    if (!history?.years?.length) {
        wrap.innerHTML = `<p class="empty-hint">No cropping history data in this result.</p>`;
        return;
    }
    wrap.innerHTML = `<table class="report-table"><thead><tr><th>Year</th><th>Kharif NDVI</th><th>Kharif</th><th>Rabi NDVI</th><th>Rabi</th></tr></thead><tbody>${history.years.map(y => { const k = y.kharif || {}, r = y.rabi || {}; return `<tr><td>${escapeHTML(y.year)}</td><td>${k.ndvi != null ? escapeHTML(k.ndvi) : "—"}</td><td>${k.cropped ? "Cropped" : "Fallow / no signal"}</td><td>${r.ndvi != null ? escapeHTML(r.ndvi) : "—"}</td><td>${r.cropped ? "Cropped" : "Fallow / no signal"}</td></tr>`; }).join("")}</tbody></table><p class="empty-hint" style="margin-top:6px;">Season-level signal only — not crop-species identification.</p>`;
}

async function refreshLatestCroppingHistory(data) {
    const coords = data.coordinates || {};
    if (coords.lat == null || coords.lng == null) return;

    // Use the latest 3 completed crop years. The current Kharif season is
    // excluded while it is still in progress; for Aug 2026 this means
    // 2023, 2024 and 2025 (Rabi 2025 = Nov 2025–Mar 2026).
    const currentYear = new Date().getFullYear();
    const firstYear = currentYear - 3;
    const lastYear = currentYear;

    try {
        const res = await fetch(`${API_BASE_URL}/historical-timeline`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                lat: coords.lat,
                lng: coords.lng,
                polygon: data.polygon || null,
                start_year: firstYear,
                end_year: lastYear,
            }),
        });
        if (!res.ok) return;
        const payload = await res.json();
        const points = payload.timeline || [];
        const byYear = {};
        points.forEach(p => {
            if (p.ndvi == null) return;
            if (!byYear[p.year]) byYear[p.year] = {};
            byYear[p.year][p.quarter] = Number(p.ndvi);
        });

        const years = [];
        for (let year = firstYear; year < lastYear; year++) {
            const q = byYear[year] || {};
            const next = byYear[year + 1] || {};
            const kh = [q.Q2, q.Q3].filter(v => v != null);
            const rb = [q.Q4, next.Q1].filter(v => v != null);
            if (!kh.length && !rb.length) continue;
            const kndvi = kh.length ? kh.reduce((a, b) => a + b, 0) / kh.length : null;
            const rndvi = rb.length ? rb.reduce((a, b) => a + b, 0) / rb.length : null;
            years.push({
                year,
                kharif: { ndvi: kndvi != null ? Number(kndvi.toFixed(4)) : null, cropped: kndvi != null && kndvi > 0.3 },
                rabi: { ndvi: rndvi != null ? Number(rndvi.toFixed(4)) : null, cropped: rndvi != null && rndvi > 0.3 },
            });
        }

        if (years.length) {
            data.enrichment = data.enrichment || {};
            data.enrichment.cropping_history = {
                years,
                note: "Season-level cropped/fallow signal from NDVI — not crop-species identification.",
                source: "Sentinel-2 quarterly NDVI composites, latest 3 completed years",
            };
        }
    } catch (err) {
        console.warn("Latest cropping-history refresh skipped:", err);
    }
}

function renderRegional(data) {
    const e = data.enrichment || {};
    const t = e.temperature_annual_range || {};
    const prosperity = e.regional_prosperity || {};
    const water = e.nearest_water_body || {};
    const topo = e.topography || {};
    const pop = e.village_population || {};
    const drought = e.drought_instances || {};
    const rows = [];
    if (t.min_c != null) rows.push(row("🌡️", "Annual Temperature", `${t.min_c}°C – ${t.max_c}°C (avg ${t.mean_c}°C)`));
    if (water.water_present != null) rows.push(row("🌊", "Water Body (2 km)", water.water_present ? "Present" : "Not detected"));
    if (prosperity.tier) rows.push(row("📈", "Regional Prosperity", prosperity.tier));
    if (topo.terrain) rows.push(row("⛰️", "Topography", `${topo.terrain} · ${topo.elevation_m} m · slope ${topo.slope_degrees}°`));
    if (pop.estimated_population != null) rows.push(row("🏘️", "Nearby Population Proxy", `~${Number(pop.estimated_population).toLocaleString()} within ${pop.radius_m} m`));
    rows.push(row("🏜️", "Drought Years", drought.drought_years?.length ? drought.drought_years.join(", ") : "None detected"));
    document.getElementById("regional-list").innerHTML = rows.join("");
}

function renderRiskMethod(data) {
    const climate = data.climate_risk || {};
    const components = data.components || {};
    const sources = [...new Set(Object.values(components).map(c => c.source).filter(Boolean))];
    const flags = climate.flags || [];
    document.getElementById("risk-method-list").innerHTML = [
        row("⚠️", "Climate Risk", climate.level || "—"),
        row("🚩", "Risk Flags", flags.length ? flags.join("; ") : "None detected"),
        row("🛰️", "Primary Data Sources", sources.join(" · ") || "—"),
        row("🧮", "Scoring Method", "Transparent weighted suitability sub-scores; missing data are redistributed"),
        row("🛑", "Decision Limitation", "Use field verification and institutional policy for lending/insurance decisions"),
    ].join("");
}

async function enrichWithCropIntelligence(data) {
    try {
        const cached = sessionStorage.getItem("farmscore_crop_intelligence");
        if (cached) data.crop_intelligence = JSON.parse(cached);
    } catch (_) {}

    try {
        const res = await fetch(`${API_BASE_URL}/crop-intelligence`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: data.coordinates.lat, lng: data.coordinates.lng, polygon: data.polygon || null }),
        });
        const ci = await res.json();
        if (res.ok) {
            data.crop_intelligence = ci;
            sessionStorage.setItem("farmscore_crop_intelligence", JSON.stringify(ci));
        }
    } catch (err) {
        console.warn("Crop intelligence enrichment skipped:", err);
    }
    return data;
}

async function init() {
    let data = null;
    try {
        const raw = sessionStorage.getItem("farmscore_last_result");
        if (raw) data = JSON.parse(raw);
    } catch (err) {
        console.warn("Could not restore cached farm result", err);
    }
    if (!data?.coordinates) return;

    document.getElementById("report-empty-state").style.display = "none";
    document.getElementById("report-content").style.display = "block";
    document.getElementById("report-subtitle").textContent = `${data.coordinates.lat}° N, ${data.coordinates.lng}° E · Score ${data.score}/900 (${data.grade})`;

    // Refresh the fixed 2021–2023 history with the latest completed years
    // before rendering the report. If the refresh fails, the cached history
    // remains as a fallback.
    await refreshLatestCroppingHistory(data);

    renderFarmDetails(data);
    renderScoreEvidence(data);
    renderEnrichment(data);
    renderCroppingHistory(data);
    renderRegional(data);
    renderRiskMethod(data);
    simpleBarChart(document.getElementById("rainfall-chart"), "🌧️ Rainfall (mm/day)", data.rainfall_monthly || [], "mm_per_day", "month", " mm/day");
    simpleBarChart(document.getElementById("groundwater-chart"), "💧 Groundwater Trend (kg/m²)", data.groundwater_trend || [], "groundwater", "year", " kg/m²");

    data = await enrichWithCropIntelligence(data);
    renderCropIntelligence(data);
    renderRiskMethod(data);

    try { sessionStorage.setItem("farmscore_last_result", JSON.stringify(data)); } catch (_) {}

    const btn = document.getElementById("download-pdf-btn-report");
    btn.disabled = false;
    btn.addEventListener("click", async function () {
        const original = btn.textContent;
        btn.textContent = "Generating…";
        btn.disabled = true;
        try {
            const res = await fetch(`${API_BASE_URL}/report/pdf`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.error || "Report generation failed");
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "BhumiAI_Farm_Intelligence_Report.pdf";
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (err) {
            const box = document.getElementById("error-box");
            box.textContent = "Could not generate PDF report: " + (err.message || "unknown error");
            box.style.display = "block";
        } finally {
            btn.textContent = original;
            btn.disabled = false;
        }
    });
}

init();