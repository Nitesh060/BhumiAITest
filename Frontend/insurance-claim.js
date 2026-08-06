const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://farmprototype.onrender.com";

document.getElementById("use-last-farm-btn").addEventListener("click", () => {
    try {
        const raw = sessionStorage.getItem("farmscore_last_result");
        if (!raw) return;
        const data = JSON.parse(raw);
        document.getElementById("lat-input").value = data.coordinates.lat;
        document.getElementById("lng-input").value = data.coordinates.lng;
    } catch (err) { /* ignore */ }
});

function row(icon, label, value) {
    return `
        <div class="enrichment-row">
            <span class="er-icon">${icon}</span>
            <span class="er-label">${label}</span>
            <span class="er-value">${value}</span>
        </div>`;
}

document.getElementById("assess-claim-btn").addEventListener("click", async () => {
    const lat = document.getElementById("lat-input").value;
    const lng = document.getElementById("lng-input").value;
    const declared_area_ha = document.getElementById("declared-area-input").value;
    const declared_crop = document.getElementById("declared-crop-input").value;
    const date1 = document.getElementById("date1-input").value;
    const date2 = document.getElementById("date2-input").value;
    const errBox = document.getElementById("error-box");

    if (!lat || !lng || !date1 || !date2) {
        errBox.textContent = "Latitude, longitude, and both dates are required.";
        errBox.style.display = "block";
        return;
    }
    errBox.style.display = "none";

    document.getElementById("claim-result").style.display = "none";
    document.getElementById("claim-loading").style.display = "block";

    try {
        const res = await bhumiAuthFetch(`${API_BASE_URL}/insurance-claim`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                lat: parseFloat(lat), lng: parseFloat(lng),
                declared_area_ha: declared_area_ha ? parseFloat(declared_area_ha) : null,
                declared_crop: declared_crop || null,
                date1, date2,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed");

        document.getElementById("claim-loading").style.display = "none";
        document.getElementById("claim-result").style.display = "block";

        const recColors = { APPROVE_FOR_REVIEW: "#2f9e63", LOW_PRIORITY: "#7fbf3f", INVESTIGATE: "#e8912d", NEEDS_DATA: "#888" };
        document.getElementById("recommendation-text").textContent = data.recommendation.replace(/_/g, " ");
        document.getElementById("recommendation-text").style.color = recColors[data.recommendation] || "#fff";
        document.getElementById("recommendation-reason").textContent = data.reason;

        const ac = data.acreage_check;
        document.getElementById("acreage-list").innerHTML = ac.available ? [
            row("📐", "Declared Area", `${ac.declared_area_ha} ha`),
            row("🛰️", "Satellite-Measured Area", `${ac.measured_area_ha} ha`),
            row("📊", "Discrepancy", `${ac.discrepancy_pct}%`),
            row(ac.match ? "✅" : "⚠️", "Match", ac.match ? "Yes" : (ac.flag || "No")),
        ].join("") : `<p class="empty-hint">${ac.reason}</p>`;

        const cc = data.crop_check;
        document.getElementById("crop-list").innerHTML = cc.available ? [
            row("📝", "Declared Crop", cc.declared_crop),
            row("🛰️", "Satellite-Identified Crop", cc.identified_crop),
            row(cc.match ? "✅" : "⚠️", "Match", cc.match ? "Yes" : (cc.flag || "No")),
        ].join("") : `<p class="empty-hint">${cc.reason}</p>`;

        const ls = data.loss_estimate;
        document.getElementById("loss-list").innerHTML = ls.available ? [
            row("📈", "Before NDVI", `${ls.before_ndvi} (${ls.before_scene_date})`),
            row("📉", "After NDVI", `${ls.after_ndvi} (${ls.after_scene_date})`),
            row("💥", "Estimated Loss", `${ls.estimated_loss_pct}% — ${ls.severity}`),
        ].join("") : `<p class="empty-hint">${ls.reason}</p>`;

        const fr = data.fraud_signals;
        document.getElementById("fraud-list").innerHTML = [
            row("🚩", "Fraud Risk Level", fr.fraud_risk_level),
            row("📊", "Fraud Risk Score", `${fr.fraud_risk_score}/100`),
        ].join("") + `<p class="empty-hint" style="margin-top:6px;">${fr.flags.join("; ")}</p>`;

        document.getElementById("disclaimer-text").textContent = data.disclaimer;
    } catch (err) {
        document.getElementById("claim-loading").style.display = "none";
        errBox.textContent = "Could not assess claim. Please try again.";
        errBox.style.display = "block";
    }
});
