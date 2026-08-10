const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://farmprototype.onrender.com";

let farmData = null;

const VEG_KEYS = ["ndvi", "evi", "savi", "msavi", "ndre", "ndmi", "ndwi", "ci_green", "ci_rededge"];
const RADAR_KEYS = ["vv", "vh", "vh_vv", "rvi"];
const WEATHER_KEYS = ["rainfall", "air_temp", "solar_radiation", "spi", "spei", "gdd", "lst"];

function paramTable(components, keys) {
    const rows = keys
        .filter(k => components[k])
        .map(k => {
            const c = components[k];
            const raw = c.raw_value != null ? c.raw_value : "—";
            const sub = c.sub_score != null ? c.sub_score : "N/A";
            const contrib = c.contribution != null ? c.contribution : "—";
            return `<tr><td>${c.label}</td><td>${raw}</td><td>${sub}</td><td>${c.weight_pct}%</td><td>${contrib}</td></tr>`;
        }).join("");

    if (!rows) return `<p class="empty-hint">No data.</p>`;
    return `
        <table class="report-table">
            <thead><tr><th>Parameter</th><th>Raw</th><th>Sub-score</th><th>Weight</th><th>Contribution</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

document.getElementById("compute-cs-btn").addEventListener("click", async () => {
    if (!farmData) return;
    const btn = document.getElementById("compute-cs-btn");
    btn.disabled = true;
    btn.textContent = "Computing… (fetches 8 satellite sources in parallel, ~20-40s)";

    try {
        const res = await fetch(`${API_BASE_URL}/comprehensive-score`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: farmData.coordinates.lat, lng: farmData.coordinates.lng, polygon: farmData.polygon || null }),
        });
        const data = await res.json();
        if (!res.ok || data.score_0_100 == null) {
            document.getElementById("score-big").textContent = "N/A";
            document.getElementById("score-grade").textContent = data.reason || "Could not compute score.";
            return;
        }

        document.getElementById("score-big").textContent = `${data.score_0_100} / 100`;
        document.getElementById("score-grade").textContent = `${data.grade} — (${data.score_300_900}/900 on FarmScore scale)`;
        document.getElementById("score-params-used").textContent = `${data.parameters_used} of ${data.parameters_total} parameters available`;
        document.getElementById("method-note").textContent = data.method;

        document.getElementById("veg-table").innerHTML = paramTable(data.components, VEG_KEYS);
        document.getElementById("radar-table").innerHTML = paramTable(data.components, RADAR_KEYS);
        document.getElementById("weather-table").innerHTML = paramTable(data.components, WEATHER_KEYS);
    } catch (err) {
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
    } catch (err) { /* ignore */ }

    if (!farmData) return;

    document.getElementById("cs-empty-state").style.display = "none";
    document.getElementById("cs-content").style.display = "block";
    document.getElementById("cs-subtitle").textContent = `${farmData.coordinates.lat}° N, ${farmData.coordinates.lng}° E`;
}

init();
