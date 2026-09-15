/* Odisha admin hierarchy fallback.
   The official NIC ArcGIS service does not reliably allow browser CORS, so
   the production UI uses the Bhumi backend proxy at /odisha-admin/<layer>. */
(function () {
    const isDashboard = window.location.pathname === "/" || window.location.pathname.endsWith("index.html") || window.location.pathname.endsWith("/Frontend/");
    if (!isDashboard) return;
    const API = window.FARMSCORE_API_URL || "https://bhumiaitest.onrender.com";

    function esc(v) { return String(v ?? "").replace(/[&<>'"]/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch])); }
    function select(id) { return document.getElementById(id); }
    function fill(id, items, placeholder) {
        const el = select(id); if (!el) return;
        el.innerHTML = `<option value="">${esc(placeholder)}</option>` + items.map(x => `<option value="${esc(x.value)}">${esc(x.label)}</option>`).join("");
        el.disabled = items.length === 0;
    }
    function status(text, bad) {
        const el = select("blv-status");
        if (!el) return;
        el.className = `blv-status ${bad ? "bad" : "wait"}`;
        el.textContent = text;
    }
    async function query(layer, where, fields) {
        const p = new URLSearchParams({where: where || "1=1", outFields: fields || "*", returnGeometry: "false", outSR: "4326", f: "json", resultRecordCount: "2000"});
        const r = await fetch(`${API}/odisha-admin/${layer}?${p}`);
        const data = await r.json();
        if (!r.ok || data.error) throw new Error(data.error?.message || data.error || "Odisha administrative service failed");
        return data.features || [];
    }
    function unique(features, valueKey, labelKey) {
        const seen = new Map();
        for (const f of features) {
            const a = f.attributes || {};
            const value = a[valueKey], label = a[labelKey];
            if (value != null && label != null && !seen.has(String(value))) seen.set(String(value), {value: String(value), label: String(label)});
        }
        return [...seen.values()].sort((a,b) => a.label.localeCompare(b.label));
    }
    async function loadDistricts() {
        const fs = await query(1, "1=1", "dtname,dtcode11,dist_lgd");
        fill("blv-district", unique(fs, "dtcode11", "dtname"), "Select District");
    }
    async function loadBlocks(districtCode) {
        const d = String(districtCode).replace(/'/g, "''");
        const fs = await query(2, `stcode11='21' AND dtcode11='${d}'`, "block_name,blkcode11,block_lgd,dtcode11");
        fill("blv-block", unique(fs, "block_name", "block_name"), "Select Tehsil / Block");
    }
    async function loadGPs(districtCode, blockName) {
        const d = String(districtCode).replace(/'/g, "''"), b = String(blockName).replace(/'/g, "''");
        const fs = await query(3, `stcode11='21' AND dtcode11='${d}' AND block_name='${b}'`, "gp_code,gp_name,block_name,dtcode11");
        fill("blv-gp", unique(fs, "gp_code", "gp_name"), "Select Gram Panchayat");
    }
    async function loadVillages(districtCode, gpCode) {
        const d = String(districtCode).replace(/'/g, "''"), g = String(gpCode).replace(/'/g, "''");
        const fs = await query(4, `stcode11='21' AND dtcode11='${d}' AND gp_code='${g}'`, "vilcode11,vilname11,gp_code,gp_name,sdtname,dtname");
        fill("blv-village", unique(fs, "vilcode11", "vilname11"), "Select Village");
    }
    function resetBelow(level) {
        const order = ["district","block","gp","village"], idx = order.indexOf(level);
        order.slice(idx + 1).forEach(k => fill(`blv-${k}`, [], k === "block" ? "Select Tehsil / Block" : k === "gp" ? "Select Gram Panchayat" : "Select Village"));
        const plot = select("blv-plot"); if (plot) plot.value = "";
    }
    async function boot() {
        const district = select("blv-district");
        if (!district || district.dataset.adminProxyReady === "1") return;
        district.dataset.adminProxyReady = "1";
        try {
            await loadDistricts();
            status("Select District → Tehsil / Block → GP → Village → Plot.", false);
        } catch (e) {
            console.error("Odisha admin proxy:", e);
            district.dataset.adminProxyReady = "";
            status("Could not load Odisha administrative data from the backend.", true);
            return;
        }
        const block = select("blv-block"), gp = select("blv-gp"), village = select("blv-village");
        district.addEventListener("change", async e => {
            e.stopImmediatePropagation(); resetBelow("district"); if (!e.target.value) return;
            try { status("Loading Tehsil / Block…", false); await loadBlocks(e.target.value); status("Select Gram Panchayat.", false); }
            catch (err) { console.error(err); status("Could not load Tehsil / Block data.", true); }
        }, true);
        block.addEventListener("change", async e => {
            e.stopImmediatePropagation(); resetBelow("block"); if (!e.target.value) return;
            try { status("Loading Gram Panchayats…", false); await loadGPs(district.value, e.target.value); status("Select Village.", false); }
            catch (err) { console.error(err); status("Could not load Gram Panchayat data.", true); }
        }, true);
        gp.addEventListener("change", async e => {
            e.stopImmediatePropagation(); resetBelow("gp"); if (!e.target.value) return;
            try { status("Loading villages…", false); await loadVillages(district.value, e.target.value); status("Select Village, then select/click a plot.", false); }
            catch (err) { console.error(err); status("Could not load Village data.", true); }
        }, true);
        village.addEventListener("change", e => { e.stopImmediatePropagation(); status("Village selected. Zoom into the cadastral map and select the required plot.", false); }, true);
    }

    function start() {
        boot();
        const observer = new MutationObserver(() => { if (select("blv-district")) { boot(); if (select("blv-district")?.dataset.adminProxyReady === "1") observer.disconnect(); } });
        observer.observe(document.body, {childList: true, subtree: true});
        setTimeout(() => observer.disconnect(), 30000);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
