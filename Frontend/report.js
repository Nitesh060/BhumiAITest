/* ===================================================================
   Extended Report page
   Reads the exact result object index.html saved to sessionStorage
   after a successful FarmScore calculation — never recomputes or
   invents anything, just lays the same numbers out in more detail.
   =================================================================== */

const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://farmprototype.onrender.com";

function row(icon, label, value) {
    return `
        <div class="enrichment-row">
            <span class="er-icon">${icon}</span>
            <span class="er-label">${label}</span>
            <span class="er-value">${value}</span>
        </div>`;
}

function simpleBarChart(container, title, points, valueKey, labelKey, unit, color) {
    const values = points.map(p => p[valueKey]).filter(v => v != null);
    if (!values.length) {
        container.innerHTML = `<p class="empty-hint">${title}: no data.</p>`;
        return;
    }
    const max = Math.max(...values);
    const avg = values.reduce((a, b) => a + b, 0) / values.length;

    container.innerHTML = `
        <div class="sbc-title">${title} <span class="sbc-avg">avg ${avg.toFixed(1)}${unit}</span></div>
        <div class="sbc-bars">
            ${points.map(p => {
                const v = p[valueKey];
                const pct = v != null ? Math.max(4, (v / max) * 100) : 0;
                return `
                    <div class="sbc-bar-col">
                        <div class="sbc-bar-track">
                            <div class="sbc-bar-fill" style="height:${pct}%;background:${color}"></div>
                        </div>
                        <div class="sbc-bar-label">${p[labelKey]}</div>
                        <div class="sbc-bar-value">${v != null ? v.toFixed(1) : "—"}</div>
                    </div>`;
            }).join("")}
        </div>`;
}

function renderFarmDetails(data) {
    const list = document.getElementById("farm-details-list");
    const coords = data.coordinates || {};
    const enrichment = data.enrichment || {};
    const irrigation = enrichment.irrigation || {};
    const intensity = enrichment.cropping_intensity || {};
    const yieldPred = data.yield_prediction;

    const rows = [
        row("📍", "Farm Centroid", `${coords.lat}° N, ${coords.lng}° E`),
        row("🏞️", "Land Use Type", "Agricultural"),
        row("💧", "Irrigation Condition", irrigation.likely_irrigated == null ? "—" : (irrigation.likely_irrigated ? "Irrigated" : "Not Irrigated")),
        row("🌿", "Cropping Intensity", intensity.label || "—"),
        row("📊", "Bhumi AI Score", `${data.score}/900 (${data.grade})`),
    ];

    if (yieldPred) {
        const totalPart = yieldPred.estimated_total_yield_quintal != null
            ? ` (~${yieldPred.estimated_total_yield_quintal} quintal on ${yieldPred.area_ha} ha)`
            : "";
        rows.push(row("🌾", `Est. Yield (${yieldPred.crop})`, `${yieldPred.estimated_yield_kg_per_ha} kg/ha${totalPart}`));
    }

    list.innerHTML = rows.join("");
}

function renderEnrichment(data) {
    const list = document.getElementById("enrichment-list");
    const e = data.enrichment || {};
    const rows = [];

    if (e.soil_type && e.soil_type.label) rows.push(row("🪨", "Soil Type", e.soil_type.label));
    if (e.agro_ecological_zone && e.agro_ecological_zone.zone) rows.push(row("🌍", "Agro-Ecological Zone", e.agro_ecological_zone.zone));
    if (e.cropping_intensity && e.cropping_intensity.label) {
        rows.push(row("🌿", "Cropping Intensity", `${e.cropping_intensity.label} (${e.cropping_intensity.estimated_cycles} cycle/yr est.)`));
    }
    if (e.irrigation && e.irrigation.likely_irrigated != null) {
        rows.push(row("💧", "Irrigation Signal", e.irrigation.likely_irrigated ? "Likely irrigated" : "Likely rainfed"));
    }
    if (e.adjacent_land_cover && e.adjacent_land_cover.breakdown && e.adjacent_land_cover.breakdown.length) {
        const top = e.adjacent_land_cover.breakdown.slice(0, 3).map(b => `${b.class} ${b.percent}%`).join(", ");
        rows.push(row("🏞️", "Adjacent Land (1km ring)", top));
    }

    list.innerHTML = rows.length ? rows.join("") : `<p class="empty-hint">No enrichment data in this result.</p>`;
}

function renderCroppingHistory(data) {
    const wrap = document.getElementById("cropping-history-table");
    const history = data.enrichment && data.enrichment.cropping_history;
    if (!history || !history.years || !history.years.length) {
        wrap.innerHTML = `<p class="empty-hint">No cropping history data in this result.</p>`;
        return;
    }

    const rows = history.years.map(y => {
        const k = y.kharif || {}, r = y.rabi || {};
        return `
            <tr>
                <td>${y.year}</td>
                <td>${k.ndvi != null ? k.ndvi : "—"}</td>
                <td>${k.cropped ? "✅ Cropped" : "⚠️ Fallow/no signal"}</td>
                <td>${r.ndvi != null ? r.ndvi : "—"}</td>
                <td>${r.cropped ? "✅ Cropped" : "⚠️ Fallow/no signal"}</td>
            </tr>`;
    }).join("");

    wrap.innerHTML = `
        <table class="report-table">
            <thead><tr><th>Year</th><th>Kharif NDVI</th><th>Kharif Status</th><th>Rabi NDVI</th><th>Rabi Status</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        <p class="empty-hint" style="margin-top:6px;">Season-level cropped/fallow signal from NDVI — not crop-species identification.</p>`;
}

function renderRegional(data) {
    const list = document.getElementById("regional-list");
    const e = data.enrichment || {};
    const t = e.temperature_annual_range || {};
    const prosperity = e.regional_prosperity || {};
    const water = e.nearest_water_body || {};
    const topo = e.topography || {};
    const pop = e.village_population || {};
    const drought = e.drought_instances || {};

    const rows = [];
    if (t.min_c != null) rows.push(row("🌡️", "Annual Temp Range", `${t.min_c}°C – ${t.max_c}°C (avg ${t.mean_c}°C)`));
    if (water.water_present != null) rows.push(row("🌊", "Water Body (2km)", water.water_present ? "Present" : "Not detected"));
    if (prosperity.tier) rows.push(row("📈", "Regional Prosperity (proxy)", prosperity.tier));
    if (topo.terrain) rows.push(row("⛰️", "Topography", `${topo.terrain} (${topo.elevation_m}m elevation, ${topo.slope_degrees}° slope)`));
    if (pop.estimated_population != null) rows.push(row("🏘️", "Population (nearby, proxy)", `~${pop.estimated_population.toLocaleString()} within ${pop.radius_m}m`));
    if (drought.drought_years) {
        const years = drought.drought_years.length ? drought.drought_years.join(", ") : "None detected";
        rows.push(row("🏜️", "Drought Years (district-scale)", years));
    }

    list.innerHTML = rows.length ? rows.join("") : `<p class="empty-hint">No regional data in this result.</p>`;
}

function renderWaterCharts(data) {
    const rainfallEl = document.getElementById("rainfall-chart");
    const gwEl = document.getElementById("groundwater-chart");
    simpleBarChart(rainfallEl, "🌧️ Rainfall (mm/day)", data.rainfall_monthly || [], "mm_per_day", "month", " mm/day", "#60a5fa");
    simpleBarChart(gwEl, "💧 Groundwater Trend (kg/m²)", data.groundwater_trend || [], "groundwater", "year", " kg/m²", "#38bdf8");
}

function init() {
    let data = null;
    try {
        const raw = sessionStorage.getItem("farmscore_last_result");
        if (raw) data = JSON.parse(raw);
    } catch (err) {
        console.warn("Could not read cached result:", err);
    }

    if (!data) return;

    document.getElementById("report-empty-state").style.display = "none";
    document.getElementById("report-content").style.display = "block";
    document.getElementById("report-subtitle").textContent =
        `${data.coordinates.lat}° N, ${data.coordinates.lng}° E · Score ${data.score}/900 (${data.grade})`;

    renderFarmDetails(data);
    renderEnrichment(data);
    renderCroppingHistory(data);
    renderWaterCharts(data);
    renderRegional(data);

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
            if (!res.ok) throw new Error("Report generation failed");
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "BhumiAI_Report.pdf";
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (err) {
            const errBox = document.getElementById("error-box");
            errBox.textContent = "Could not generate PDF report. Please try again.";
            errBox.style.display = "block";
        } finally {
            btn.textContent = original;
            btn.disabled = false;
        }
    });
}

init();
