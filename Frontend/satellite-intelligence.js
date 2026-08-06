const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://bhumiaitest.onrender.com";

let farmData = null;

function row(icon, label, value) {
    return `
        <div class="enrichment-row">
            <span class="er-icon">${icon}</span>
            <span class="er-label">${label}</span>
            <span class="er-value">${value}</span>
        </div>`;
}

async function loadIndices() {
    const list = document.getElementById("indices-list");
    list.innerHTML = `<p class="empty-hint">Loading…</p>`;
    try {
        const res = await fetch(`${API_BASE_URL}/spectral-indices`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed");

        list.innerHTML = [
            row("🌳", "EVI (Enhanced Vegetation Index)", `${data.evi} — ${data.evi_label}`),
            row("🌾", "SAVI (Soil-Adjusted Vegetation Index)", `${data.savi} — ${data.savi_label}`),
            row("🏜️", "BSI (Bare Soil Index)", `${data.bsi} — ${data.bsi_label}`),
        ].join("");
    } catch (err) {
        list.innerHTML = `<p class="empty-hint">Could not load indices.</p>`;
    }
}

async function loadSAR() {
    const list = document.getElementById("sar-list");
    list.innerHTML = `<p class="empty-hint">Loading… (radar data, may take a moment)</p>`;
    try {
        const res = await fetch(`${API_BASE_URL}/sar-moisture`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok || data.available === false) {
            list.innerHTML = `<p class="empty-hint">${(data && data.reason) || "SAR data unavailable for this location."}</p>`;
            return;
        }
        list.innerHTML = [
            row("📡", "VV Backscatter", `${data.vv_db} dB`),
            row("📡", "VH Backscatter", `${data.vh_db} dB`),
            row("🌊", "Flood Signal", data.flood_signal ? "⚠️ Possible standing water" : "None detected"),
            row("🗓️", "Scenes in period", data.scenes_available_in_period),
        ].join("");
    } catch (err) {
        list.innerHTML = `<p class="empty-hint">Could not load SAR data.</p>`;
    }
}

async function loadHeatmap(index) {
    const img = document.getElementById("heatmap-img");
    const status = document.getElementById("heatmap-status");
    status.textContent = "Generating heatmap…";
    img.style.display = "none";

    try {
        const res = await fetch(`${API_BASE_URL}/vegetation-heatmap`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null, index }),
        });
        const data = await res.json();
        if (!res.ok || !data.url) throw new Error("failed");
        img.src = data.url;
        img.style.display = "block";
        status.textContent = "";
    } catch (err) {
        status.textContent = "Could not generate heatmap.";
    }
}

document.querySelectorAll("#si-content .fm-tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll("#si-content .fm-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        loadHeatmap(tab.dataset.index);
    });
});

document.getElementById("load-timeline-btn").addEventListener("click", async () => {
    const status = document.getElementById("timeline-status");
    const chartEl = document.getElementById("timeline-chart");
    status.textContent = "Loading multi-year timeline… this can take 20-30 seconds.";

    try {
        const res = await fetch(`${API_BASE_URL}/historical-timeline`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "failed");

        const points = (data.timeline || []).filter(p => p.ndvi != null);
        if (!points.length) {
            status.textContent = "No timeline data available.";
            return;
        }
        const max = Math.max(...points.map(p => p.ndvi));
        chartEl.innerHTML = `
            <div class="sbc-bars" style="height:160px;">
                ${points.map(p => `
                    <div class="sbc-bar-col">
                        <div class="sbc-bar-track">
                            <div class="sbc-bar-fill" style="height:${Math.max(4, (p.ndvi / max) * 100)}%;background:#66bd63"></div>
                        </div>
                        <div class="sbc-bar-label" style="writing-mode:vertical-rl;font-size:0.6rem;">${p.year} ${p.quarter}</div>
                    </div>`).join("")}
            </div>`;
        status.textContent = `Showing ${data.start_year}–${data.end_year}, quarterly NDVI.`;
    } catch (err) {
        status.textContent = "Could not load timeline.";
    }
});

document.getElementById("compare-btn").addEventListener("click", async () => {
    const date1 = document.getElementById("date1-input").value;
    const date2 = document.getElementById("date2-input").value;
    const status = document.getElementById("compare-status");
    const resultEl = document.getElementById("comparison-result");

    if (!date1 || !date2) {
        status.textContent = "Pick both dates.";
        return;
    }
    status.textContent = "Fetching imagery for both dates…";
    resultEl.innerHTML = "";

    try {
        const res = await fetch(`${API_BASE_URL}/before-after`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null, date1, date2 }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "failed");

        const cell = (label, img) => img
            ? `<div><div class="empty-hint">${label} (actual scene: ${img.actual_scene_date}, NDVI ${img.ndvi})</div><img src="${img.url}" style="width:100%;border-radius:var(--radius);" /></div>`
            : `<div><div class="empty-hint">${label}: no cloud-free scene found nearby.</div></div>`;

        resultEl.innerHTML = cell("Before", data.before) + cell("After", data.after);
        status.textContent = data.note || "";
    } catch (err) {
        status.textContent = "Could not fetch comparison.";
    }
});

async function loadSoilHealth() {
    const list = document.getElementById("soil-health-list");
    list.innerHTML = `<p class="empty-hint">Loading…</p>`;
    try {
        const res = await fetch(`${API_BASE_URL}/soil-health`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error("failed");
        list.innerHTML = [
            row("🧪", "pH", `${data.ph} — ${data.ph_label}`),
            row("🌿", "Organic Carbon", `${data.organic_carbon_g_per_kg} g/kg — ${data.organic_carbon_label}`),
            row("💠", "Nitrogen", `${data.nitrogen_g_per_kg} g/kg`),
        ].join("") + `<p class="empty-hint" style="margin-top:8px;">${data.npk_note}</p>`;
    } catch (err) {
        list.innerHTML = `<p class="empty-hint">Could not load soil health.</p>`;
    }
}

async function loadSoilMoisture() {
    const list = document.getElementById("soil-moisture-list");
    list.innerHTML = `<p class="empty-hint">Loading…</p>`;
    try {
        const res = await fetch(`${API_BASE_URL}/soil-moisture`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok || !data.available) {
            list.innerHTML = `<p class="empty-hint">${(data && data.reason) || "Soil moisture unavailable."}</p>`;
            return;
        }
        list.innerHTML = [
            row("💧", "Surface Soil Moisture", `${data.surface_soil_moisture_m3_m3} m³/m³ — ${data.label}`),
        ].join("") + `<p class="empty-hint" style="margin-top:8px;">${data.note}</p>`;
    } catch (err) {
        list.innerHTML = `<p class="empty-hint">Could not load soil moisture.</p>`;
    }
}

async function loadFloodRisk() {
    const list = document.getElementById("flood-risk-list");
    list.innerHTML = `<p class="empty-hint">Loading…</p>`;
    try {
        const res = await fetch(`${API_BASE_URL}/flood-risk`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error("failed");
        list.innerHTML = [
            row("🌊", "Flood Risk Level", data.risk_level),
            row("📋", "Contributing Factors", data.factors.length ? data.factors.join("; ") : "None identified"),
        ].join("") + `<p class="empty-hint" style="margin-top:8px;">${data.note}</p>`;
    } catch (err) {
        list.innerHTML = `<p class="empty-hint">Could not load flood risk.</p>`;
    }
}

document.getElementById("load-weather-btn").addEventListener("click", async () => {
    const status = document.getElementById("weather-status");
    const chartEl = document.getElementById("weather-chart");
    status.textContent = "Loading historical weather… this can take 20-30 seconds.";

    try {
        const res = await fetch(`${API_BASE_URL}/historical-weather`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "failed");

        const points = (data.yearly || []).filter(p => p.total_rainfall_mm != null);
        if (!points.length) {
            status.textContent = "No historical weather data available.";
            return;
        }
        const max = Math.max(...points.map(p => p.total_rainfall_mm));
        chartEl.innerHTML = `
            <div class="sbc-bars">
                ${points.map(p => `
                    <div class="sbc-bar-col">
                        <div class="sbc-bar-track">
                            <div class="sbc-bar-fill" style="height:${Math.max(4, (p.total_rainfall_mm / max) * 100)}%;background:#60a5fa"></div>
                        </div>
                        <div class="sbc-bar-label">${p.year}</div>
                        <div class="sbc-bar-value">${Math.round(p.total_rainfall_mm)}mm</div>
                    </div>`).join("")}
            </div>`;
        status.textContent = `Long-term average: ${data.long_term_avg_rainfall_mm} mm/year (${data.start_year}–${data.end_year}).`;
    } catch (err) {
        status.textContent = "Could not load historical weather.";
    }
});

function init() {
    try {
        const raw = sessionStorage.getItem("farmscore_last_result");
        if (raw) farmData = JSON.parse(raw);
    } catch (err) { /* ignore */ }

    if (!farmData) return;

    document.getElementById("si-empty-state").style.display = "none";
    document.getElementById("si-content").style.display = "block";
    document.getElementById("si-subtitle").textContent =
        `${farmData.coordinates.lat}° N, ${farmData.coordinates.lng}° E`;

    loadIndices();
    loadSAR();
    loadHeatmap("ndvi");
    loadSoilHealth();
    loadSoilMoisture();
    loadFloodRisk();
}

init();
