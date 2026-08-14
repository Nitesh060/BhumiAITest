/* Bhumi AI dashboard visual layer
   Adds the screenshot-inspired dashboard composition without replacing
   the existing FarmScore calculation logic. */
(function () {
    if (!window.location.pathname.endsWith("index.html") && window.location.pathname !== "/") return;

    function el(tag, cls, html) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (html !== undefined) node.innerHTML = html;
        return node;
    }

    function syncVisibleFields() {
        const lat = document.getElementById("calc-lat");
        const lng = document.getElementById("calc-lng");
        const area = document.getElementById("calc-area");
        if (lat) document.getElementById("lat-input").value = lat.value;
        if (lng) document.getElementById("lng-input").value = lng.value;
        if (area && area.value) document.getElementById("farm-area").value = `${area.value} ${document.getElementById("calc-area-unit").value}`;
    }

    function syncFromHiddenFields() {
        const lat = document.getElementById("calc-lat");
        const lng = document.getElementById("calc-lng");
        const area = document.getElementById("calc-area");
        if (lat) lat.value = document.getElementById("lat-input").value || "";
        if (lng) lng.value = document.getElementById("lng-input").value || "";
        if (area) {
            const raw = document.getElementById("farm-area").value || "";
            const match = raw.match(/([0-9.]+)/);
            if (match) area.value = match[1];
        }
    }

    function buildHero() {
        if (document.querySelector(".hero-intro")) return;
        const top = document.querySelector(".top-bar");
        const strip = document.querySelector(".info-strip");
        if (!top || !strip) return;

        const hero = el("section", "hero-intro");
        hero.innerHTML = `
            <div class="hero-copy">
                <div class="hero-kicker">Bhumi AI · Land Intelligence</div>
                <h2 class="hero-title">Know Your Land.<br><span>Grow Your Future.</span></h2>
                <p class="hero-subtitle">AI-powered agricultural suitability and credit intelligence platform.</p>
            </div>
            <div class="feature-strip">
                <div class="feature-chip"><div class="fc-icon">◉</div><div><strong>AI-Powered</strong><span>Analysis</span></div></div>
                <div class="feature-chip"><div class="fc-icon">▥</div><div><strong>Accurate</strong><span>Insights</span></div></div>
                <div class="feature-chip"><div class="fc-icon">▣</div><div><strong>Credit</strong><span>Intelligence</span></div></div>
                <div class="feature-chip"><div class="fc-icon">◎</div><div><strong>Better</strong><span>Decisions</span></div></div>
            </div>`;
        top.insertAdjacentElement("afterend", hero);
    }

    function buildHeaderWidgets() {
        const top = document.querySelector(".top-bar");
        if (!top || top.querySelector(".header-satellite")) return;
        const spacer = top.querySelector(".topbar-spacer");
        const satellite = el("div", "header-satellite", "Satellite: Active");
        const user = el("div", "header-user", `<div class="header-avatar">●</div><div><strong>${(window.BHUMI_USER_NAME || "Bhumi User")}</strong><span>Analyst</span></div><span>⌄</span>`);
        if (spacer) {
            spacer.insertAdjacentElement("afterend", satellite);
            satellite.insertAdjacentElement("afterend", user);
        } else {
            top.append(satellite, user);
        }
    }

    function buildCalculator() {
        const right = document.querySelector(".right-column");
        if (!right || right.querySelector(".farm-calc-card")) return;

        const card = el("section", "farm-calc-card");
        card.innerHTML = `
            <h2>Calculate FarmScore</h2>
            <div class="calc-mode-tabs">
                <button type="button" class="active">By Location</button>
                <button type="button" id="calc-map-mode">By Map</button>
            </div>
            <div class="calc-field"><label for="calc-lat">Latitude</label><input id="calc-lat" type="number" step="0.000001" placeholder="Enter latitude"></div>
            <div class="calc-field"><label for="calc-lng">Longitude</label><input id="calc-lng" type="number" step="0.000001" placeholder="Enter longitude"></div>
            <div class="calc-field"><label for="calc-area">Area (Optional)</label><div class="calc-row"><input id="calc-area" type="number" min="0" step="0.01" placeholder="2.50"><select id="calc-area-unit"><option value="Hectares">Hectares</option><option value="Acres">Acres</option></select></div></div>
            <button type="button" class="calc-primary" id="dashboard-calc-btn">✦ Calculate FarmScore</button>
            <p class="calc-note">Get an AI-powered farm suitability score, crop recommendation, and loan eligibility.</p>`;

        right.prepend(card);

        ["calc-lat", "calc-lng", "calc-area"].forEach(id => {
            const input = document.getElementById(id);
            if (input) input.addEventListener("input", syncVisibleFields);
        });

        document.getElementById("dashboard-calc-btn").addEventListener("click", function () {
            syncVisibleFields();
            const realBtn = document.getElementById("calc-btn");
            if (realBtn) realBtn.click();
        });

        document.getElementById("calc-map-mode").addEventListener("click", function () {
            document.getElementById("location-search").focus();
            document.getElementById("map").scrollIntoView({ behavior: "smooth", block: "center" });
        });

        syncFromHiddenFields();
    }

    function buildQuickInsights() {
        const right = document.querySelector(".right-column");
        if (!right || right.querySelector(".quick-insights")) return;
        const card = el("section", "quick-insights");
        card.innerHTML = `
            <h3>Quick Insights</h3>
            <div class="qi-row"><span class="qi-icon">🌿</span><div class="qi-label">Best Crop Suggestion</div><div class="qi-value" id="qi-crop">—</div></div>
            <div class="qi-row"><span class="qi-icon">▥</span><div class="qi-label">Farm Score</div><div class="qi-value" id="qi-score">—</div></div>
            <div class="qi-row"><span class="qi-icon">💧</span><div class="qi-label">Surface Water</div><div class="qi-value" id="qi-water">—</div></div>
            <div class="qi-row"><span class="qi-icon">⚠</span><div class="qi-label">Risk Level</div><div class="qi-value" id="qi-risk">—</div></div>
            <div class="qi-row"><span class="qi-icon">🏦</span><div class="qi-label">Loan Eligibility</div><div class="qi-value success" id="qi-loan">—</div></div>`;
        const calc = right.querySelector(".farm-calc-card");
        if (calc) calc.insertAdjacentElement("afterend", card);
    }

    function refreshInsights() {
        const crop = document.getElementById("ls-crop");
        const score = document.getElementById("ls-score");
        const water = document.getElementById("ls-ndwi");
        const risk = document.getElementById("ls-risk");
        const loan = document.getElementById("loan-status");
        const set = (id, source) => { const target = document.getElementById(id); if (target && source) target.textContent = source.textContent || "—"; };
        set("qi-crop", crop); set("qi-score", score); set("qi-water", water); set("qi-risk", risk); set("qi-loan", loan);
    }

    function addDataFooter() {
        if (document.querySelector(".data-footer-grid")) return;
        const footer = document.querySelector(".app-footer");
        if (!footer) return;
        const grid = el("div", "data-footer-grid");
        grid.innerHTML = `
            <div class="data-pill"><strong>Data Source</strong><span>Sentinel-2 · NASA · CHIRPS · MODIS</span></div>
            <div class="data-pill"><strong>Analysis</strong><span>Satellite + weather + terrain</span></div>
            <div class="data-pill"><strong>Coverage</strong><span>India · State to Village</span></div>
            <div class="data-pill"><strong>Decision Support</strong><span>Farming &amp; credit intelligence</span></div>`;
        footer.parentNode.insertBefore(grid, footer);
    }

    function applyLayout() {
        const comprehensive = document.getElementById("comprehensive-score-card");
        if (comprehensive) comprehensive.remove();
        const floatingCalc = document.getElementById("calc-btn");
        if (floatingCalc) floatingCalc.style.display = "none";
        const scoreInfo = document.querySelector(".score-info-card");
        if (scoreInfo) scoreInfo.style.display = "none";
        buildHero();
        buildHeaderWidgets();
        buildCalculator();
        buildQuickInsights();
        addDataFooter();
        refreshInsights();
        syncFromHiddenFields();
    }

    function start() {
        applyLayout();
        const observer = new MutationObserver(refreshInsights);
        ["ls-score", "ls-crop", "ls-ndwi", "ls-risk", "loan-status"].forEach(id => {
            const node = document.getElementById(id);
            if (node) observer.observe(node, { childList: true, characterData: true, subtree: true });
        });
        setInterval(syncFromHiddenFields, 700);
        window.addEventListener("resize", applyLayout);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
