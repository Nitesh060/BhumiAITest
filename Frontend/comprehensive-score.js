const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://bhumiaitest.onrender.com";

function escapeHTML(value) {
    return String(value ?? "—").replace(/[&<>'"]/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    }[ch]));
}

let farmData = null;
const VEG_KEYS = ["ndvi", "evi", "savi", "msavi", "ndre", "ndmi", "ndwi", "ci_green", "ci_rededge"];
const RADAR_KEYS = ["vv", "vh", "vh_vv", "rvi"];
const WEATHER_KEYS = ["rainfall", "air_temp", "solar_radiation", "spi", "spei", "gdd", "lst"];

function paramTable(components, keys) {
    const rows = keys
        .filter(k => components && components[k])
        .map(k => {
            const c = components[k];
            const raw = c.raw_value != null ? c.raw_value : "—";
            const sub = c.sub_score != null ? c.sub_score : "N/A";
            const weight = c.effective_weight_pct != null ? c.effective_weight_pct : c.weight_pct;
            const contrib = c.contribution != null ? c.contribution : "—";
            return `<tr><td>${escapeHTML(c.label)}</td><td>${escapeHTML(raw)}</td><td>${escapeHTML(sub)}</td><td>${escapeHTML(weight)}%</td><td>${escapeHTML(contrib)}</td></tr>`;
        }).join("");

    if (!rows) return `<p class="empty-hint">No data.</p>`;
    return `
        <table class="report-table">
            <thead><tr><th>Parameter</th><th>Raw</th><th>Sub-score</th><th>Effective Weight</th><th>Contribution</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

document.getElementById("compute-cs-btn").addEventListener("click", async () => {
    if (!farmData?.coordinates) return;
    const btn = document.getElementById("compute-cs-btn");
    btn.disabled = true;
    btn.textContent = "Computing… (satellite sources in parallel)";

    try {
        const res = await fetch(`${API_BASE_URL}/comprehensive-score`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok || data.score_0_100 == null) {
            document.getElementById("score-big").textContent = "N/A";
            document.getElementById("score-grade").textContent = data.reason || "Could not compute score.";
            return;
        }

        document.getElementById("score-big").textContent = `${escapeHTML(data.score_0_100)} / 100`;
        document.getElementById("score-grade").textContent = `${escapeHTML(data.grade)} — (${escapeHTML(data.score_300_900)}/900 on FarmScore scale)`;
        document.getElementById("score-params-used").textContent = `${escapeHTML(data.parameters_used)} of ${escapeHTML(data.parameters_total)} parameters available · ${escapeHTML(data.confidence || "provisional")} confidence`;
        document.getElementById("method-note").textContent = data.method || "Provisional transparent suitability score.";

        document.getElementById("veg-table").innerHTML = paramTable(data.components, VEG_KEYS);
        document.getElementById("radar-table").innerHTML = paramTable(data.components, RADAR_KEYS);
        document.getElementById("weather-table").innerHTML = paramTable(data.components, WEATHER_KEYS);
    } catch (err) {
        console.error(err);
        document.getElementById("error-box").textContent = "Could not compute comprehensive score. Please try again.";
        document.getElementById("error-box").style.display = "block";
    } finally {
        btn.disabled = false;
        btn.textContent = "Recompute";
    }
});

function init() {
    try {
        const raw = sessionStorage.getItem("farmscore_last_result");
        if (raw) farmData = JSON.parse(raw);
    } catch (err) {
        console.warn("Could not restore last farm result", err);
    }
    if (!farmData?.coordinates) return;
    document.getElementById("cs-empty-state").style.display = "none";
    document.getElementById("cs-content").style.display = "block";
    document.getElementById("cs-subtitle").textContent = `${farmData.coordinates.lat}° N, ${farmData.coordinates.lng}° E`;
}

init();
