/* ===================================================================
   shell.js — included on every protected page BEFORE the page's own
   script. Handles theme, auth, shared UI polish, API configuration,
   and user controls.
   =================================================================== */

(function applyTheme() {
    const saved = localStorage.getItem("bhumi_theme") || "dark";
    if (saved === "light") document.documentElement.setAttribute("data-theme", "light");
})();

function escapeHTML(value) {
    return String(value ?? "—").replace(/[&<>'"]/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    }[ch]));
}

window.FARMSCORE_API_URL = window.FARMSCORE_API_URL || "https://bhumiaitest.onrender.com";
const BHUMI_API_BASE_URL = window.FARMSCORE_API_URL;

const FARMSCORE_BANDS = [
    { from: 0, to: 625, color: "#ef4444", label: "Poor", risk: "Highest" },
    { from: 626, to: 725, color: "#f59e0b", label: "Fair", risk: "High" },
    { from: 726, to: 790, color: "#eab308", label: "Good", risk: "Medium" },
    { from: 791, to: 870, color: "#84cc16", label: "Very Good", risk: "Low" },
    { from: 871, to: 1000, color: "#22c55e", label: "Excellent", risk: "Lowest" },
];

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
    const boundaries = [bands[0].from, ...bands.map(b => b.to)];
    boundaries.forEach((v, i) => {
        const [tx, ty] = toXY(frac(v), r + 14);
        const anchor = i === 0 ? "start" : i === boundaries.length - 1 ? "end" : "middle";
        svg += `<text x="${tx}" y="${ty}" text-anchor="${anchor}" dominant-baseline="middle" class="bss-gauge-tick">${v}</text>`;
    });
    const [nx, ny] = toXY(frac(score), r - 6);
    svg += `<line x1="${cx}" y1="${cy}" x2="${nx}" y2="${ny}" stroke="currentColor" stroke-width="3" stroke-linecap="round" class="bss-gauge-needle"/>`;
    svg += `<circle cx="${cx}" cy="${cy}" r="7" fill="currentColor" class="bss-gauge-needle"/>`;
    svgEl.setAttribute("viewBox", "0 0 220 140");
    svgEl.innerHTML = svg;
}

function _renderBhumiSubScoreBar(container, label, sub) {
    if (!container) return;
    if (!sub || !sub.data_available) {
        container.innerHTML = `<div class="bss-subscore-label">${escapeHTML(label)}</div><p class="empty-hint">No data available for this component.</p>`;
        return;
    }
    const pct = Math.max(0, Math.min(100, (sub.score / sub.max_score) * 100));
    container.innerHTML = `<div class="bss-subscore-label">${escapeHTML(label)} <span class="bss-subscore-grade">${escapeHTML(sub.grade)} (${sub.score}/${sub.max_score})</span></div><div class="bss-subscore-track"><div class="bss-subscore-fill" style="width:${pct}%"></div></div><div class="bss-subscore-ends"><span>Low Score</span><span>High Score</span></div>`;
}

function renderFarmScoreBreakdown(rootEl, breakdown) {
    if (!rootEl) return;
    const emptyEl = rootEl.querySelector(".bss-empty");
    const contentEl = rootEl.querySelector(".bss-content");
    if (!breakdown || !breakdown.available) {
        if (emptyEl) {
            emptyEl.style.display = "block";
            emptyEl.textContent = breakdown?.reason ? `FarmScore breakdown unavailable — ${breakdown.reason}` : "FarmScore breakdown unavailable — not enough irrigation/seasonal signal for this location.";
        }
        if (contentEl) contentEl.style.display = "none";
        return;
    }
    if (emptyEl) emptyEl.style.display = "none";
    if (contentEl) contentEl.style.display = "block";
    renderSpeedometerGauge(rootEl.querySelector(".bss-gauge-svg"), breakdown.overall_score, 0, 1000, FARMSCORE_BANDS);
    const scoreEl = rootEl.querySelector(".bss-overall-score");
    if (scoreEl) scoreEl.textContent = breakdown.overall_score;
    const labelEl = rootEl.querySelector(".bss-overall-label");
    if (labelEl) labelEl.textContent = `${breakdown.category} · ${breakdown.risk_rating} Risk`;
    _renderBhumiSubScoreBar(rootEl.querySelector(".bss-base"), "Base Score", breakdown.base);
    _renderBhumiSubScoreBar(rootEl.querySelector(".bss-kharif"), "Average Kharif Score", breakdown.kharif);
    _renderBhumiSubScoreBar(rootEl.querySelector(".bss-rabi"), "Average Rabi Score", breakdown.rabi);
}

function renderFarmScoreLegend(container) {
    if (!container) return;
    container.innerHTML = `<table class="bss-legend-table"><thead><tr><th>Category</th><th>Risk Rating</th><th>Interval</th></tr></thead><tbody>${FARMSCORE_BANDS.map(b => `<tr><td><span class="bss-legend-swatch" style="background:${b.color}"></span>${escapeHTML(b.label)}</td><td>${escapeHTML(b.risk)}</td><td>${b.from} - ${b.to}</td></tr>`).join("")}</tbody></table>`;
}

(function loadUiPolish() {
    if (document.querySelector('link[data-bhumi-ui-polish]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "ui-polish.css?v=4";
    link.dataset.bhumiUiPolish = "true";
    document.head.appendChild(link);
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

function bhumiGetToken() { return localStorage.getItem("bhumi_token"); }
function bhumiGetUser() {
    try { return JSON.parse(localStorage.getItem("bhumi_user") || "null"); } catch (e) { return null; }
}
function bhumiLogout() {
    const token = bhumiGetToken();
    if (token) fetch(`${BHUMI_API_BASE_URL}/auth/logout`, { method: "POST", headers: { "Authorization": `Bearer ${token}` } }).catch(() => {});
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
    if (!isLoginPage && !token) { window.location.href = "login.html"; return; }
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
            if (currentlyLight) { document.documentElement.removeAttribute("data-theme"); localStorage.setItem("bhumi_theme", "dark"); themeBtn.textContent = "☀️"; }
            else { document.documentElement.setAttribute("data-theme", "light"); localStorage.setItem("bhumi_theme", "light"); themeBtn.textContent = "🌙"; }
        });
        const logoutBtn = document.createElement("button");
        logoutBtn.className = "shell-logout-btn";
        logoutBtn.type = "button";
        logoutBtn.textContent = "Logout";
        logoutBtn.addEventListener("click", bhumiLogout);
        btnGroup.appendChild(themeBtn); btnGroup.appendChild(logoutBtn); bar.appendChild(info); bar.appendChild(btnGroup); footer.appendChild(bar);
    });
})();

/* ===================================================================
   Odisha Land Verification — dashboard-only gate
   State -> District -> Tehsil/Block -> GP -> Village -> Plot -> verify -> FarmScore
   =================================================================== */
(function setupOdishaLandVerification() {
    const isDashboard = window.location.pathname.endsWith("index.html") || window.location.pathname === "/" || window.location.pathname.endsWith("/Frontend/");
    if (!isDashboard) return;

    const API = window.FARMSCORE_API_URL || "https://bhumiaitest.onrender.com";
    const ADMIN = "https://webgis1.nic.in/publishing/rest/services/odisha/odisha/MapServer";
    const CADASTRAL = "https://indianopenmaps.com/not-so-open/cadastrals/odisha/odisha4kgeo/";
    let verificationToken = null;
    let selectedParcel = null;
    let adminCache = {};
    let parcelLayerIds = [];
    let cadMap = null;
    let cadReady = false;

    window.__BHUMI_LAND_VERIFICATION_TOKEN = null;

    // Inject token into the existing app.js /calculate request without modifying
    // the large scoring client. The production WSGI middleware rejects requests
    // without this token, so this is not only a cosmetic button lock.
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async function(input, init = {}) {
        const url = typeof input === "string" ? input : input?.url || "";
        if (String(url).includes("/calculate") && verificationToken) {
            let opts = { ...init };
            let body = opts.body;
            if (typeof body === "string") {
                try {
                    const parsed = JSON.parse(body);
                    parsed.land_verification_token = verificationToken;
                    opts.body = JSON.stringify(parsed);
                } catch (_) {}
            }
            return nativeFetch(input, opts);
        }
        return nativeFetch(input, init);
    };

    function css() {
        if (document.getElementById("bhumi-land-verify-css")) return;
        const style = document.createElement("style");
        style.id = "bhumi-land-verify-css";
        style.textContent = `
            .blv-panel{margin:0 0 14px;padding:14px;border:1px solid #d7e6dc;border-radius:14px;background:#f8fcf9}
            .blv-title{font-weight:700;font-size:15px;margin-bottom:3px;color:#163322}
            .blv-sub{font-size:11px;color:#66776d;margin-bottom:10px}
            .blv-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
            .blv-field{display:flex;flex-direction:column;gap:4px}.blv-field.full{grid-column:1/-1}
            .blv-field label{font-size:10px;font-weight:700;color:#5c6d63;text-transform:uppercase;letter-spacing:.04em}
            .blv-field select,.blv-field input{width:100%;box-sizing:border-box;border:1px solid #d6e3da;border-radius:9px;padding:9px;background:white;color:#183027;font-size:12px}
            .blv-field select:disabled{background:#edf2ee;color:#89958d}.blv-map{height:210px;margin-top:10px;border-radius:10px;overflow:hidden;border:1px solid #d6e3da}
            .blv-status{margin-top:9px;padding:9px;border-radius:9px;font-size:11px;background:#eef4ef;color:#526158}.blv-status.ok{background:#e5f6eb;color:#11733c}.blv-status.bad{background:#fff0ee;color:#a83b30}.blv-status.wait{background:#f0f4f1;color:#5e6e65}
            .blv-row{display:flex;gap:8px;margin-top:8px}.blv-btn{border:0;border-radius:9px;padding:9px 11px;background:#168d4c;color:white;font-weight:700;cursor:pointer}.blv-btn:disabled{opacity:.45;cursor:not-allowed}.blv-btn.secondary{background:#e6eee8;color:#245336}
            .blv-parcel{margin-top:8px;padding:9px;border:1px dashed #b9cdbf;border-radius:9px;background:white;font-size:11px}.blv-parcel strong{color:#123d25}
            .blv-note{font-size:9px;color:#78857d;margin-top:7px;line-height:1.35}
            .blv-mini{font-size:10px;color:#168d4c;font-weight:700}
        `;
        document.head.appendChild(style);
    }

    function esc(v){return escapeHTML(v);}
    function setStatus(text, cls="wait") { const el=document.getElementById("blv-status"); if(el){el.className=`blv-status ${cls}`;el.textContent=text;} }
    function setOptions(id, items, placeholder, valueKey="value", labelKey="label") {
        const el=document.getElementById(id); if(!el)return;
        el.innerHTML=`<option value="">${placeholder}</option>`+(items||[]).map(x=>`<option value="${esc(x[valueKey])}">${esc(x[labelKey])}</option>`).join("");
        el.disabled=!(items&&items.length);
    }
    function resetFrom(level){
        const order=["district","block","gp","village"];
        const i=order.indexOf(level);
        order.slice(i+1).forEach(k=>setOptions(`blv-${k}`,[],`Select ${k==='block'?'Tehsil / Block':k==='gp'?'Gram Panchayat':k}`));
        document.getElementById("blv-plot").value="";
        verificationToken=null; window.__BHUMI_LAND_VERIFICATION_TOKEN=null; selectedParcel=null;
        const calc=document.getElementById("calc-btn"); if(calc){calc.disabled=true;calc.title="Select and verify an agricultural parcel first";}
        setStatus("Select the administrative hierarchy, then choose/click a plot.","wait");
    }
    async function arcQuery(layer, where, outFields, returnGeometry=false) {
        const params=new URLSearchParams({where:where||"1=1",outFields,returnGeometry:String(returnGeometry),outSR:"4326",f:"json",resultRecordCount:"2000"});
        const res=await nativeFetch(`${ADMIN}/${layer}/query?${params}`);
        const data=await res.json(); if(!res.ok||data.error) throw new Error(data.error?.message||"Administrative data service failed");
        return data.features||[];
    }
    async function loadDistricts(){
        const res=await nativeFetch(`${API}/api/odisha/districts`);
        const data=await res.json();
        if(!res.ok||!data.districts) throw new Error("Could not load districts from backend");
        const items=data.districts.map(d=>({value:d,label:d})).sort((a,b)=>a.label.localeCompare(b.label));
        adminCache.districts=items;setOptions("blv-district",items,"Select District");
    }
    async function loadBlocks(districtName){
        const res=await nativeFetch(`${API}/api/odisha/blocks/${encodeURIComponent(districtName)}`);
        const data=await res.json();
        if(!res.ok||!data.blocks) throw new Error("Could not load blocks from backend");
        const items=data.blocks.map(b=>({value:b,label:b,code:b})).sort((a,b)=>a.label.localeCompare(b.label));
        setOptions("blv-block",items,"Select Tehsil / Block");return items;
    }
    async function loadGPs(districtCode,blockName){
        const where=`stcode11='21' AND dtcode11='${String(districtCode).replace(/'/g,"''")}' AND block_name='${String(blockName).replace(/'/g,"''")}'`;
        const features=await arcQuery(3,where,"gp_code,gp_name,block_name,dtcode11",false);
        const seen=new Map();features.forEach(f=>{const a=f.attributes;if(a.gp_name&&!seen.has(String(a.gp_code)))seen.set(String(a.gp_code),{value:String(a.gp_code),label:a.gp_name});});
        const items=[...seen.values()].sort((a,b)=>a.label.localeCompare(b.label));setOptions("blv-gp",items,"Select Gram Panchayat");return items;
    }
    async function loadVillages(districtCode,gpCode){
        const where=`stcode11='21' AND dtcode11='${String(districtCode).replace(/'/g,"''")}' AND gp_code='${String(gpCode).replace(/'/g,"''")}'`;
        const features=await arcQuery(4,where,"vilcode11,vilname11,gp_code,gp_name,sdtname,dtname",false);
        const seen=new Map();features.forEach(f=>{const a=f.attributes;if(a.vilname11&&!seen.has(a.vilcode11))seen.set(a.vilcode11,{value:a.vilcode11,label:a.vilname11});});
        const items=[...seen.values()].sort((a,b)=>a.label.localeCompare(b.label));setOptions("blv-village",items,"Select Village");return items;
    }
    async function villageGeometry(vilCode){
        const features=await arcQuery(4,`vilcode11='${String(vilCode).replace(/'/g,"''")}'`,"vilcode11,vilname11,gp_code,gp_name,dtname,sdtname",true);
        const g=features[0]?.geometry; if(!g)return null;
        const rings=g.rings||[];let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
        rings.flat().forEach(p=>{if(p[0]<minX)minX=p[0];if(p[0]>maxX)maxX=p[0];if(p[1]<minY)minY=p[1];if(p[1]>maxY)maxY=p[1];});
        return Number.isFinite(minX)?[(minX+maxX)/2,(minY+maxY)/2]:null;
    }
    function extractLandUse(props){
        const keys=Object.keys(props||{});const preferred=keys.filter(k=>/(land.?use|land.?type|ltype|landclass|landcategory|use_type|lulc|class)/i.test(k));
        for(const k of preferred){if(props[k]!=null&&String(props[k]).trim())return String(props[k]).trim();}
        for(const k of keys){if(/(agri|cultiv|crop|paddy|kharif|rabi|orchard|garden|fallow|farm)/i.test(String(props[k])))return String(props[k]).trim();}
        return "";
    }
    function extractPlotNo(props){
        const keys=Object.keys(props||{});const p=keys.find(k=>/(plot|parcel|survey|khasra|plot_no|plotno)/i.test(k));return p&&props[p]!=null?String(props[p]).trim():"";
    }
    function addParcelToMap(){
        if(!window.maplibregl){setStatus("Map library is still loading…","wait");return;}
        const el=document.getElementById("blv-map");if(!el)return;
        cadMap=new maplibregl.Map({container:el,style:{version:8,sources:{base:{type:"raster",tiles:["https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"],tileSize:256,attribution:"© CARTO"}},layers:[{id:"base",type:"raster",source:"base"}]},center:[84.7,19.55],zoom:6,attributionControl:false});
        cadMap.on("load",async()=>{
            try{
                const r=await nativeFetch(`${CADASTRAL}tiles.json`);const tj=await r.json();
                cadMap.addSource("odisha-cad",{type:"vector",url:`${CADASTRAL}tiles.json`});
                (tj.vector_layers||[]).forEach((vl,i)=>{
                    const id=`blv-parcel-fill-${i}`;parcelLayerIds.push(id);
                    cadMap.addLayer({id,type:"fill",source:"odisha-cad","source-layer":vl.id,paint:{"fill-color":"#168d4c","fill-opacity":0.06}});
                    cadMap.addLayer({id:`${id}-line`,type:"line",source:"odisha-cad","source-layer":vl.id,paint:{"line-color":"#168d4c","line-width":1,"line-opacity":0.55}});
                    parcelLayerIds.push(`${id}-line`);
                });
                cadReady=true;setStatus("Parcel map ready. Select a Village and then click a plot.","wait");
            }catch(e){console.error(e);setStatus("Could not load Odisha cadastral map metadata. The administrative selectors still work.","bad");}
        });
        cadMap.on("click",e=>{
            if(!cadReady)return;
            const features=cadMap.queryRenderedFeatures(e.point,{layers:parcelLayerIds.filter(id=>id.includes("fill"))});
            if(features.length)selectParcel(features[0],e.lngLat.lng,e.lngLat.lat);
        });
    }
    function selectParcel(feature,lng,lat){
        const props=feature.properties||{};const plot=extractPlotNo(props)||document.getElementById("blv-plot").value.trim();const landUse=extractLandUse(props);
        if(!plot){setStatus("Plot number is not present in the loaded cadastral properties. Use the Plot No. box and click the parcel.","bad");return;}
        document.getElementById("blv-plot").value=plot;
        const d=document.getElementById("blv-district").selectedOptions[0]?.text||"";
        const b=document.getElementById("blv-block").selectedOptions[0]?.text||"";
        const g=document.getElementById("blv-gp").selectedOptions[0]?.text||"";
        const v=document.getElementById("blv-village").selectedOptions[0]?.text||"";
        selectedParcel={state:"Odisha",district:d,block:b,gp:g,village:v,plot_no:plot,land_use:landUse,source:"Odisha 4K GEO",lat,lng,properties:props};
        document.getElementById("blv-parcel").innerHTML=`<strong>Plot ${esc(plot)}</strong><br>Land-use: ${esc(landUse||"Not exposed in this tile")}`;
        verificationToken=null;window.__BHUMI_LAND_VERIFICATION_TOKEN=null;const calc=document.getElementById("calc-btn");if(calc)calc.disabled=true;
        if(landUse){verifyParcel();}else setStatus("Plot selected, but no explicit land-use field was exposed by this cadastral tile. FarmScore stays blocked.","bad");
    }
    async function verifyParcel(){
        if(!selectedParcel)return;
        setStatus("Verifying agricultural land-use classification…","wait");
        try{
            const res=await nativeFetch(`${API}/land-verification`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(selectedParcel)});const data=await res.json();
            if(!res.ok||!data.verified)throw new Error(data.error||"Verification failed");
            if(!data.farm_score_allowed){verificationToken=null;window.__BHUMI_LAND_VERIFICATION_TOKEN=null;setStatus(`Land verified as non-agricultural (${data.land_use}). FarmScore is blocked.`,"bad");return;}
            verificationToken=data.verification_token;window.__BHUMI_LAND_VERIFICATION_TOKEN=verificationToken;
            const lat=document.getElementById("lat-input"),lng=document.getElementById("lng-input");if(lat)lat.value=Number(selectedParcel.lat).toFixed(6);if(lng)lng.value=Number(selectedParcel.lng).toFixed(6);
            const calc=document.getElementById("calc-btn");if(calc){calc.disabled=false;calc.title="Agricultural parcel verified — calculate FarmScore";}
            setStatus("✓ Agricultural parcel verified. FarmScore is now enabled.","ok");
        }catch(e){console.error(e);setStatus(e.message||"Land verification failed. FarmScore remains blocked.","bad");}
    }
    async function loadMapLibre(){
        if(window.maplibregl){addParcelToMap();return;}
        if(document.getElementById("blv-maplibre-script")){return;}
        const link=document.createElement("link");link.rel="stylesheet";link.href="https://unpkg.com/maplibre-gl@5.23.0/dist/maplibre-gl.css";document.head.appendChild(link);
        const s=document.createElement("script");s.id="blv-maplibre-script";s.src="https://unpkg.com/maplibre-gl@5.23.0/dist/maplibre-gl.js";s.onload=addParcelToMap;s.onerror=()=>setStatus("Could not load the cadastral map library.","bad");document.head.appendChild(s);
    }
    function buildUI(){
        css();const card=document.querySelector(".farm-calc-card");if(!card||document.getElementById("blv-panel"))return;
        const panel=document.createElement("section");panel.id="blv-panel";panel.className="blv-panel";
        panel.innerHTML=`<div class="blv-title">1. Verify Your Land</div><div class="blv-sub">Odisha cadastral workflow — FarmScore stays locked until an agricultural plot is verified.</div><div class="blv-grid"><div class="blv-field full"><label>State</label><select id="blv-state"><option value="Odisha">Odisha</option></select></div><div class="blv-field"><label>District</label><select id="blv-district"><option>Loading…</option></select></div><div class="blv-field"><label>Tehsil / Block</label><select id="blv-block" disabled><option>Select Tehsil / Block</option></select></div><div class="blv-field"><label>Gram Panchayat</label><select id="blv-gp" disabled><option>Select Gram Panchayat</option></select></div><div class="blv-field"><label>Village</label><select id="blv-village" disabled><option>Select Village</option></select></div><div class="blv-field full"><label>Plot No.</label><input id="blv-plot" placeholder="Enter/select plot number"></div></div><div class="blv-map" id="blv-map"></div><div class="blv-row"><button type="button" class="blv-btn secondary" id="blv-find-plot">Find Loaded Plot</button><button type="button" class="blv-btn" id="blv-verify" disabled>Verify Plot</button></div><div class="blv-parcel" id="blv-parcel">No parcel selected.</div><div class="blv-status wait" id="blv-status">Loading Odisha administrative hierarchy…</div><div class="blv-note">Source: Odisha 4K GEO cadastral metadata. This verifies the land-use classification available in the selected cadastral data; it is not a legal title/ROR certification.</div>`;
        card.insertBefore(panel,card.querySelector(".calc-mode-tabs")||card.firstChild);
        const calc=document.getElementById("calc-btn");if(calc){calc.disabled=true;calc.title="Verify an agricultural parcel first";}
        document.getElementById("blv-district").addEventListener("change",async e=>{resetFrom("district");if(!e.target.value)return;try{setStatus("Loading Tehsil / Block…");await loadBlocks(e.target.value);}catch(err){setStatus("Could not load blocks.","bad");}});
        document.getElementById("blv-block").addEventListener("change",async e=>{resetFrom("block");const d=document.getElementById("blv-district").value;if(!e.target.value)return;try{setStatus("Loading Gram Panchayats…");await loadGPs(d,e.target.value);}catch(err){setStatus("Could not load Gram Panchayats.","bad");}});
        document.getElementById("blv-gp").addEventListener("change",async e=>{resetFrom("gp");const d=document.getElementById("blv-district").value;if(!e.target.value)return;try{setStatus("Loading villages…");await loadVillages(d,e.target.value);}catch(err){setStatus("Could not load villages.","bad");}});
        document.getElementById("blv-village").addEventListener("change",async e=>{verificationToken=null;window.__BHUMI_LAND_VERIFICATION_TOKEN=null;const calc=document.getElementById("calc-btn");if(calc)calc.disabled=true;if(!e.target.value)return;try{const c=await villageGeometry(e.target.value);if(c&&cadMap){cadMap.flyTo({center:c,zoom:15});}setStatus("Village selected. Zoom in and click the required cadastral plot.","wait");}catch(err){setStatus("Village selected, but map could not zoom to it.","bad");}});
        document.getElementById("blv-find-plot").addEventListener("click",()=>{const q=document.getElementById("blv-plot").value.trim().toLowerCase();if(!q||!cadMap){setStatus("Enter a Plot No. and make sure the cadastral map is loaded.","bad");return;}const layers=parcelLayerIds.filter(id=>id.includes("fill"));const fs=cadMap.queryRenderedFeatures(undefined,{layers});const f=fs.find(x=>extractPlotNo(x.properties||{}).toLowerCase()===q);if(f){const c=f.geometry?.coordinates?.[0]?.[0]?.[0];const ll=c&&Array.isArray(c)?c:null;selectParcel(f,ll?ll[0]:cadMap.getCenter().lng,ll?ll[1]:cadMap.getCenter().lat);}else setStatus("Plot not found in the currently loaded map view. Zoom into the village and click the parcel.","bad");});
        document.getElementById("blv-verify").addEventListener("click",verifyParcel);
        loadDistricts().then(()=>{setStatus("Select District → Tehsil/Block → GP → Village → Plot.","wait");}).catch(e=>setStatus("Could not load Odisha administrative data.","bad"));
        loadMapLibre();
    }
    if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",buildUI);else buildUI();
})();
