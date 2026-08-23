/* ===================================================================
   shell.js — included on every protected page BEFORE the page's own
   script. Handles theme, auth, shared UI polish, API configuration,
   and user controls.
   =================================================================== */

(function applyTheme() {
    const saved = localStorage.getItem("bhumi_theme") || "dark";
    if (saved === "light") document.documentElement.setAttribute("data-theme", "light");
})();

/* Shared HTML-escaping helper — report.js and crop-intelligence.js each
 * already defined their own identical copy of this; every OTHER page
 * that builds HTML strings from farmer-entered, admin-entered, or
 * AI-generated text via innerHTML (farm-management.js, dashboard-
 * overview.js, insurance-claim.js, app.js) had none at all, which is
 * how a farmer/farm/user name or an AI diagnosis response could carry
 * a stored XSS payload straight into another user's (including an
 * admin's) browser. Defined once here, in the file every page already
 * loads first, so there's one place to get this right instead of N. */
function escapeHTML(value) {
    return String(value ?? "—").replace(/[&<>'"]/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    }[ch]));
}

window.FARMSCORE_API_URL = window.FARMSCORE_API_URL || "https://bhumiaitest.onrender.com";
const BHUMI_API_BASE_URL = window.FARMSCORE_API_URL;

/* ===================================================================
   Bhumi Seasonal Score — shared gauge + rendering, used on both the
   main dashboard (app.js) and the Extended Report page (report.js).
   Renders enrichment.seasonal_score from /calculate — a Base (0-200) +
   Average Kharif (0-400) + Average Rabi (0-400) composite scaled to an
   Overall 0-1000, computed in Backend/seasonal_score_service.py. This
   is a distinct, complementary metric from the main FarmScore
   (0-900) — never conflated with it; see that module's docstring.
   =================================================================== */

// Matches Backend/seasonal_score_service.py's OVERALL_BANDS exactly —
// keep both in sync if those thresholds ever change.
const BHUMI_SEASONAL_SCORE_BANDS = [
    { from: 0, to: 399, color: "#ef4444", label: "Poor", risk: "Highest" },
    { from: 400, to: 549, color: "#f59e0b", label: "Fair", risk: "High" },
    { from: 550, to: 699, color: "#eab308", label: "Good", risk: "Medium" },
    { from: 700, to: 849, color: "#84cc16", label: "Very Good", risk: "Low" },
    { from: 850, to: 1000, color: "#22c55e", label: "Excellent", risk: "Lowest" },
];

// Half-circle "speedometer" gauge: colored bands + a needle at `score`.
// Pure SVG, no charting library. Angle convention: theta=180° is the
// leftmost point (score=minScale), theta=0° is the rightmost point
// (score=maxScale), sweeping up and over the top as the fraction
// increases — verified visually against sample scores before shipping.
function renderSpeedometerGauge(svgEl, score, minScale, maxScale, bands) {
    if (!svgEl) return;
    const cx = 110, cy = 115, r = 90, innerR = 62;
    const toXY = (frac, radius) => {
        const theta = Math.PI * (1 - frac);
        return [cx + radius * Math.cos(theta), cy - radius * Math.sin(theta)];
    };
    const frac = (v) => Math.max(0, Math.min(1, (v - minScale) / (maxScale - minScale)));

    let svg = "";
    bands.forEach(b => {
        const fFrom = frac(b.from), fTo = frac(b.to);
        const [x1, y1] = toXY(fFrom, r), [x2, y2] = toXY(fTo, r);
        const [x3, y3] = toXY(fTo, innerR), [x4, y4] = toXY(fFrom, innerR);
        const largeArc = (fTo - fFrom) > 0.5 ? 1 : 0;
        const path = `M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} L ${x3} ${y3} A ${innerR} ${innerR} 0 ${largeArc} 0 ${x4} ${y4} Z`;
        svg += `<path d="${path}" fill="${b.color}"/>`;
    });

    // Tick labels at band boundaries — anchor start/end (not middle) at
    // the two ends so a 4-digit number like "1000" doesn't clip past
    // the edge of the viewBox; interior boundaries stay center-anchored.
    const boundaries = [bands[0].from, ...bands.map(b => b.to)];
    boundaries.forEach((v, i) => {
        const f = frac(v);
        const [tx, ty] = toXY(f, r + 14);
        const anchor = i === 0 ? "start" : i === boundaries.length - 1 ? "end" : "middle";
        svg += `<text x="${tx}" y="${ty}" text-anchor="${anchor}" dominant-baseline="middle" class="bss-gauge-tick">${v}</text>`;
    });

    const f = frac(score);
    const [nx, ny] = toXY(f, r - 6);
    svg += `<line x1="${cx}" y1="${cy}" x2="${nx}" y2="${ny}" stroke="currentColor" stroke-width="3" stroke-linecap="round" class="bss-gauge-needle"/>`;
    svg += `<circle cx="${cx}" cy="${cy}" r="7" fill="currentColor" class="bss-gauge-needle"/>`;

    svgEl.setAttribute("viewBox", "0 0 220 140");
    svgEl.innerHTML = svg;
}

function _renderBhumiSubScoreBar(container, label, sub) {
    if (!container) return;
    if (!sub || !sub.data_available) {
        container.innerHTML = `
            <div class="bss-subscore-label">${escapeHTML(label)}</div>
            <p class="empty-hint">No data available for this component.</p>`;
        return;
    }
    const pct = Math.max(0, Math.min(100, (sub.score / sub.max_score) * 100));
    container.innerHTML = `
        <div class="bss-subscore-label">${escapeHTML(label)} <span class="bss-subscore-grade">${escapeHTML(sub.grade)} (${sub.score}/${sub.max_score})</span></div>
        <div class="bss-subscore-track"><div class="bss-subscore-fill" style="width:${pct}%"></div></div>
        <div class="bss-subscore-ends"><span>Low Score</span><span>High Score</span></div>`;
}

// rootEl must contain, as descendants: svg.bss-gauge-svg,
// .bss-overall-score, .bss-overall-label, .bss-base, .bss-kharif,
// .bss-rabi (report.html/index.html each define these once).
function renderBhumiSeasonalScore(rootEl, seasonalScore) {
    if (!rootEl) return;
    const emptyEl = rootEl.querySelector(".bss-empty");
    const contentEl = rootEl.querySelector(".bss-content");
    if (!seasonalScore || !seasonalScore.available) {
        if (emptyEl) {
            emptyEl.style.display = "block";
            emptyEl.textContent = seasonalScore?.reason
                ? `Bhumi Seasonal Score unavailable — ${seasonalScore.reason}`
                : "Bhumi Seasonal Score unavailable — not enough historical/irrigation signal for this location.";
        }
        if (contentEl) contentEl.style.display = "none";
        return;
    }
    if (emptyEl) emptyEl.style.display = "none";
    if (contentEl) contentEl.style.display = "block";

    renderSpeedometerGauge(rootEl.querySelector(".bss-gauge-svg"), seasonalScore.overall_score, 0, 1000, BHUMI_SEASONAL_SCORE_BANDS);
    const scoreEl = rootEl.querySelector(".bss-overall-score");
    if (scoreEl) scoreEl.textContent = seasonalScore.overall_score;
    const labelEl = rootEl.querySelector(".bss-overall-label");
    if (labelEl) labelEl.textContent = `${seasonalScore.category} · ${seasonalScore.risk_rating} Risk`;

    _renderBhumiSubScoreBar(rootEl.querySelector(".bss-base"), "Base Score", seasonalScore.base);
    _renderBhumiSubScoreBar(rootEl.querySelector(".bss-kharif"), "Average Kharif Score", seasonalScore.kharif);
    _renderBhumiSubScoreBar(rootEl.querySelector(".bss-rabi"), "Average Rabi Score", seasonalScore.rabi);
}

function renderBhumiScoreLegend(container) {
    if (!container) return;
    container.innerHTML = `
        <table class="bss-legend-table">
            <thead><tr><th>Category</th><th>Risk Rating</th><th>Interval</th></tr></thead>
            <tbody>${BHUMI_SEASONAL_SCORE_BANDS.map(b => `
                <tr>
                    <td><span class="bss-legend-swatch" style="background:${b.color}"></span>${escapeHTML(b.label)}</td>
                    <td>${escapeHTML(b.risk)}</td>
                    <td>${b.from} - ${b.to}</td>
                </tr>`).join("")}</tbody>
        </table>`;
}

(function loadUiPolish() {
    if (document.querySelector('link[data-bhumi-ui-polish]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "ui-polish.css?v=4";
    link.dataset.bhumiUiPolish = "true";
    document.head.appendChild(link);

    // The main dashboard is intentionally light and professional,
    // independent of the user's saved global day/night preference —
    // style.css's [data-theme="light"] block plus its dashboard-specific
    // component rules handle the whole look now (see style.css).
    const isDashboard = window.location.pathname.endsWith("index.html") || window.location.pathname === "/" || window.location.pathname.endsWith("/Frontend/");
    if (isDashboard) {
        document.documentElement.setAttribute("data-theme", "light");

        if (!document.querySelector('script[data-bhumi-dashboard-theme]')) {
            const dashboardTheme = document.createElement("script");
            dashboardTheme.src = "dashboard-theme-force.js?v=1";
            dashboardTheme.dataset.bhumiDashboardTheme = "true";
            document.head.appendChild(dashboardTheme);
        }
    }
})();

function bhumiGetToken() {
    return localStorage.getItem("bhumi_token");
}

function bhumiGetUser() {
    try {
        return JSON.parse(localStorage.getItem("bhumi_user") || "null");
    } catch (e) {
        return null;
    }
}

function bhumiLogout() {
    // Best-effort: invalidate this token server-side (see
    // auth_service.bump_token_version) so it can't keep authenticating
    // if it leaks after this device clears it — e.g. from a browser
    // history/cache snapshot, or a proxy log. Never blocks the actual
    // logout on this call succeeding; a network failure here just means
    // the token still expires normally on its own.
    const token = bhumiGetToken();
    if (token) {
        fetch(`${BHUMI_API_BASE_URL}/auth/logout`, {
            method: "POST",
            headers: { "Authorization": `Bearer ${token}` },
        }).catch(() => {});
    }
    localStorage.removeItem("bhumi_token");
    localStorage.removeItem("bhumi_user");
    window.location.href = "login.html";
}

function bhumiAuthFetch(url, options = {}) {
    const token = bhumiGetToken();
    const headers = { ...(options.headers || {}) };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return fetch(url, { ...options, headers });
}

(function guardAndInjectShell() {
    const isLoginPage = window.location.pathname.endsWith("login.html");
    const token = bhumiGetToken();
    const user = bhumiGetUser();

    if (!isLoginPage && !token) {
        window.location.href = "login.html";
        return;
    }

    document.addEventListener("DOMContentLoaded", () => {
        const footer = document.querySelector(".nav-footer-card");
        if (!footer || isLoginPage) return;

        const bar = document.createElement("div");
        bar.className = "shell-user-bar";

        const info = document.createElement("div");
        info.className = "shell-user-info";
        if (user) {
            const strong = document.createElement("strong");
            strong.textContent = user.name || "User";
            info.appendChild(strong);
            info.appendChild(document.createTextNode(user.role === "admin" ? "Admin" : "Field Officer"));
        }

        const btnGroup = document.createElement("div");
        btnGroup.style.display = "flex";
        btnGroup.style.gap = "6px";

        const themeBtn = document.createElement("button");
        themeBtn.className = "shell-theme-btn";
        themeBtn.type = "button";
        const isLight = document.documentElement.getAttribute("data-theme") === "light";
        themeBtn.textContent = isLight ? "🌙" : "☀️";
        themeBtn.title = "Toggle day/night mode";
        themeBtn.addEventListener("click", () => {
            const currentlyLight = document.documentElement.getAttribute("data-theme") === "light";
            if (currentlyLight) {
                document.documentElement.removeAttribute("data-theme");
                localStorage.setItem("bhumi_theme", "dark");
                themeBtn.textContent = "☀️";
            } else {
                document.documentElement.setAttribute("data-theme", "light");
                localStorage.setItem("bhumi_theme", "light");
                themeBtn.textContent = "🌙";
            }
        });

        const logoutBtn = document.createElement("button");
        logoutBtn.className = "shell-logout-btn";
        logoutBtn.type = "button";
        logoutBtn.textContent = "Logout";
        logoutBtn.addEventListener("click", bhumiLogout);

        btnGroup.appendChild(themeBtn);
        btnGroup.appendChild(logoutBtn);
        bar.appendChild(info);
        bar.appendChild(btnGroup);
        footer.appendChild(bar);
    });
})();
