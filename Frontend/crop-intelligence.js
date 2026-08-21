const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://bhumiaitest.onrender.com";

// escapeHTML is defined once in shell.js (loaded before this file on
// every page) — no longer duplicated here.

function row(icon, label, value) {
    return `<div class="enrichment-row"><span class="er-icon">${escapeHTML(icon)}</span><span class="er-label">${escapeHTML(label)}</span><span class="er-value">${escapeHTML(value)}</span></div>`;
}

function renderSummary(data, farmData) {
    const el = document.getElementById("ci-summary-list");
    const id = data.identification || {};
    const gs = data.growth_stage || {};
    const primary = farmData?.recommended_crops?.primary || {};
    el.innerHTML = [
        row("🌾", "Current crop hypothesis", id.identified_crop || "No clear identification"),
        row("🎯", "Identification confidence", id.confidence || "none"),
        row("🌱", "Growth stage", gs.stage || "—"),
        row("🏆", "Top crop recommendation", primary.crop ? `${primary.crop} (${primary.score}%)` : "—"),
        row("📍", "Farm location", `${farmData.coordinates.lat}° N, ${farmData.coordinates.lng}° E`),
    ].join("");
}

function renderIdentification(id) {
    const list = document.getElementById("identification-list");
    if (!id || !id.identified_crop) {
        list.innerHTML = `<p class="empty-hint">Could not determine a likely crop from the available seasonal NDVI signal.</p>`;
        return;
    }
    list.innerHTML = [
        row("🌾", "Most Likely Crop", id.identified_crop),
        row("📊", "Confidence", id.confidence),
        row("📈", "Peak NDVI Month", `${id.peak_ndvi_month} (NDVI ${id.peak_ndvi})`),
        row("💧", "Early-season Flood Signature", id.flood_signature_detected ? "Detected (paddy-like)" : "Not detected"),
        row("🧪", "Method", id.method || "—"),
    ].join("") + (id.note ? `<p class="empty-hint" style="margin-top:8px;">${escapeHTML(id.note)}</p>` : "");
}

function renderGrowthStage(gs) {
    const list = document.getElementById("growth-stage-list");
    if (!gs || !gs.stage) {
        list.innerHTML = `<p class="empty-hint">Insufficient data to determine growth stage.</p>`;
        return;
    }
    list.innerHTML = [
        row("🌱", "Current Stage", gs.stage),
        row("📈", "Current NDVI", gs.current_ndvi),
        row("🔝", "Season Peak NDVI", gs.peak_ndvi),
        row("📐", "Current / Peak Ratio", gs.ratio_to_peak != null ? gs.ratio_to_peak : "—"),
        row("🧪", "Method", gs.method || "—"),
    ].join("");
}

function renderSowingHarvest(sh) {
    const list = document.getElementById("sowing-harvest-list");
    if (!sh) {
        list.innerHTML = `<p class="empty-hint">No prediction available.</p>`;
        return;
    }
    list.innerHTML = [
        row("🌱", "Estimated Sowing", sh.sowing_estimate_month || "—"),
        row("🌾", "Estimated Harvest", sh.harvest_estimate_month || "—"),
        row("📖", "Source", sh.source || "—"),
    ].join("") + (sh.note ? `<p class="empty-hint" style="margin-top:8px;">${escapeHTML(sh.note)}</p>` : "");
}

function renderRotation(rot) {
    document.getElementById("rotation-summary").textContent = rot?.summary || "No rotation data.";
    const tableEl = document.getElementById("rotation-table");
    if (!rot?.years?.length) {
        tableEl.innerHTML = `<p class="empty-hint">No 3-year Kharif/Rabi history available.</p>`;
        return;
    }
    tableEl.innerHTML = `<table class="report-table"><thead><tr><th>Year</th><th>Kharif</th><th>Rabi</th></tr></thead><tbody>${rot.years.map(y => `<tr><td>${escapeHTML(y.year)}</td><td>${escapeHTML(y.kharif)}</td><td>${escapeHTML(y.rabi)}</td></tr>`).join("")}</tbody></table>` +
        (rot.note ? `<p class="empty-hint" style="margin-top:6px;">${escapeHTML(rot.note)}</p>` : "");
}

function renderCalendar(cal) {
    const el = document.getElementById("calendar-table");
    if (!cal || !Object.keys(cal).length) {
        el.innerHTML = `<p class="empty-hint">No calendar reference for the identified crop.</p>`;
        return;
    }
    const rows = Object.entries(cal).map(([season, info]) => `<tr><td>${escapeHTML(season)}</td><td>${escapeHTML(info.sow)}</td><td>${escapeHTML(info.harvest)}</td><td>${escapeHTML(info.duration_days)} days</td></tr>`).join("");
    el.innerHTML = `<table class="report-table"><thead><tr><th>Season</th><th>Typical Sowing</th><th>Typical Harvest</th><th>Duration</th></tr></thead><tbody>${rows}</tbody></table><p class="empty-hint" style="margin-top:6px;">Indicative India-general reference — not calibrated to this specific district.</p>`;
}

async function loadCropIntelligence(farmData) {
    document.getElementById("ci-empty-state").style.display = "none";
    document.getElementById("ci-loading").style.display = "block";
    try {
        const res = await fetch(`${API_BASE_URL}/crop-intelligence`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed");
        document.getElementById("ci-loading").style.display = "none";
        document.getElementById("ci-content").style.display = "block";
        renderSummary(data, farmData);
        renderIdentification(data.identification);
        renderGrowthStage(data.growth_stage);
        renderSowingHarvest(data.sowing_harvest_prediction);
        renderRotation(data.crop_rotation);
        renderCalendar(data.crop_calendar);
        try {
            sessionStorage.setItem("farmscore_crop_intelligence", JSON.stringify(data));
        } catch (err) {
            console.warn("Could not cache crop intelligence", err);
        }
    } catch (err) {
        console.error(err);
        document.getElementById("ci-loading").style.display = "none";
        const errBox = document.getElementById("error-box");
        errBox.textContent = "Could not load crop intelligence. Please try again.";
        errBox.style.display = "block";
    }
}

function init() {
    let farmData = null;
    try {
        const raw = sessionStorage.getItem("farmscore_last_result");
        if (raw) farmData = JSON.parse(raw);
    } catch (err) {
        console.warn("Could not restore last farm result", err);
    }
    if (!farmData?.coordinates) return;
    document.getElementById("ci-subtitle").textContent = `${farmData.coordinates.lat}° N, ${farmData.coordinates.lng}° E`;
    loadCropIntelligence(farmData);
}

init();
