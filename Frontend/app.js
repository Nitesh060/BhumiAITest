/**
 * app.js
 * ======
 * FarmScore frontend application logic.
 *
 * - Initialises the Leaflet map.
 * - Reads lat/lng from inputs or map click.
 * - Calls the backend via calculateFarmScore().
 * - Renders score ring, grade badge, parameter cards, historical trend,
 *   satellite metadata, nearby resources (real OSM data via Overpass),
 *   and weather (real data via Open-Meteo).
 *
 * No mock data. No AI-generated text. Every number shown either comes
 * from the FarmScore backend (Earth Engine) or a free public API
 * (Nominatim / Overpass / Open-Meteo), computed live.
 */

/* ===================================================================
   API Client
   =================================================================== */

const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://bhumiaitest.onrender.com";

async function calculateFarmScore(lat, lng) {
    const url = `${API_BASE_URL}/calculate`;

    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat, lng, polygon: farmPolygon }),
    });

    const data = await response.json();

    if (!response.ok) {
        const message = data.error || data.detail || `Server error ${response.status}`;
        throw new Error(message);
    }

    return data;
}

/* ===================================================================
   Map Initialisation
   =================================================================== */

let marker = null;

const map = L.map("map", { zoomControl: false }).setView([20.5, 78.9], 5);

const streetLayer = L.tileLayer(
    "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap contributors", maxZoom: 19 }
);

const satelliteLayer = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "Tiles © Esri", maxZoom: 19 }
);

const terrainLayer = L.tileLayer(
    "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenTopoMap contributors (CC-BY-SA)", maxZoom: 17 }
);

satelliteLayer.addTo(map);
L.control.zoom({ position: "topleft" }).addTo(map);
const baseLayersControl = L.control.layers(
    { "Road Map": streetLayer, "Satellite": satelliteLayer, "Terrain": terrainLayer },
    {},
    { position: "topright" }
).addTo(map);

// Leaflet measures its container on init; if the surrounding CSS layout
// finishes sizing after that (fonts loading, flex/grid settling), the map
// renders at the wrong zoom/pan. Nudge it once layout has settled, and
// again on any resize.
window.addEventListener("load", () => setTimeout(() => map.invalidateSize(), 200));
window.addEventListener("resize", () => map.invalidateSize());

L.Control.geocoder({ defaultMarkGeocode: false })
    .on("markgeocode", function (e) {
        const center = e.geocode.center;
        map.setView(center, 16);
        selectLocation(center.lat, center.lng);
    })
    .addTo(map);

/* ===================================================================
   Polygon Layer
   =================================================================== */

let drawnItems = new L.FeatureGroup();
map.addLayer(drawnItems);

let farmPolygon = null;

const drawControl = new L.Control.Draw({
    edit: { featureGroup: drawnItems },
    draw: {
        polygon: {
            allowIntersection: false,
            showArea: true,
            shapeOptions: { color: "#34d399", weight: 3 },
        },
        rectangle: true,
        polyline: false,
        circle: false,
        circlemarker: false,
        marker: false,
    },
});

map.addControl(drawControl);

const farmIcon = L.divIcon({
    className: "",
    html: `<div class="farm-pin"></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
});

map.on(L.Draw.Event.CREATED, function (e) {
    drawnItems.clearLayers();
    const layer = e.layer;
    drawnItems.addLayer(layer);

    farmPolygon = layer.toGeoJSON();
    const areaSqM = turf.area(farmPolygon);
    const areaAcres = areaSqM / 4046.85642;
    const areaHectare = areaSqM / 10000;

    document.getElementById("farm-area").value =
        `${areaAcres.toFixed(2)} Acres (${areaHectare.toFixed(2)} ha)`;
});

function placeMarker(lat, lng) {
    if (marker) map.removeLayer(marker);
    marker = L.marker([lat, lng], { icon: farmIcon }).addTo(map);
}

/* ===================================================================
   Map Click / Search / Geocoder → shared "select a location" flow
   =================================================================== */

function formatCoords(lat, lng) {
    const ns = lat >= 0 ? "N" : "S";
    const ew = lng >= 0 ? "E" : "W";
    return `${Math.abs(lat).toFixed(4)}° ${ns}, ${Math.abs(lng).toFixed(4)}° ${ew}`;
}

function selectLocation(lat, lng) {
    document.getElementById("lat-input").value = lat.toFixed(6);
    document.getElementById("lng-input").value = lng.toFixed(6);
    placeMarker(lat, lng);

    const coordsEl = document.getElementById("selected-location-coords");
    if (coordsEl) coordsEl.textContent = formatCoords(lat, lng);

    const card = document.getElementById("selected-location-card");
    if (card) card.style.display = "block";

    fetchLocationDetails(lat, lng);
    fetchWeather(lat, lng);
    fetchNearbyResources(lat, lng);
}

map.on("click", function (e) {
    selectLocation(e.latlng.lat, e.latlng.lng);
});

async function fetchLocationDetails(lat, lng) {
    try {
        const response = await fetch(
            `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lng}`
        );
        const data = await response.json();
        const addr = data.address || {};

        const village = addr.village || addr.town || addr.city || addr.hamlet || "";
        const district = addr.county || addr.state_district || "";
        const state = addr.state || "";

        document.getElementById("village-input").value = village;
        document.getElementById("district-input").value = district;
        document.getElementById("state-input").value = state;
        document.getElementById("pincode-input").value = addr.postcode || "";

        const placeEl = document.getElementById("selected-location-place");
        if (placeEl) {
            placeEl.textContent = [village, state].filter(Boolean).join(", ") || "Unknown location";
        }
    } catch (err) {
        console.error("Reverse Geocoding Error:", err);
    }
}

/* ===================================================================
   Location Search (Nominatim — free, no API key)
   =================================================================== */

async function searchLocation() {
    const input = document.getElementById("location-search");
    const query = input.value.trim();
    if (!query) return;

    const btn = document.getElementById("search-btn");
    btn.disabled = true;

    try {
        const res = await fetch(
            `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=in&q=${encodeURIComponent(query)}`
        );
        const results = await res.json();

        if (!results.length) {
            alert("Location not found. Try a different search.");
            return;
        }

        const lat = parseFloat(results[0].lat);
        const lng = parseFloat(results[0].lon);

        map.setView([lat, lng], 15);
        selectLocation(lat, lng);
    } catch (err) {
        console.error("Search error:", err);
        alert("Search failed. Please check your connection and try again.");
    } finally {
        btn.disabled = false;
    }
}

document.getElementById("search-btn").addEventListener("click", searchLocation);
document.getElementById("location-search").addEventListener("keydown", function (e) {
    if (e.key === "Enter") searchLocation();
});

/* ===================================================================
   Current Location Button
   =================================================================== */

function getCurrentLocation() {
    if (!navigator.geolocation) {
        alert("Geolocation is not supported by your browser.");
        return;
    }

    const btn = document.getElementById("location-btn");
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="btn-icon">📍</span> Locating…`;

    navigator.geolocation.getCurrentPosition(
        function (position) {
            const lat = position.coords.latitude;
            const lng = position.coords.longitude;
            map.setView([lat, lng], 15);
            selectLocation(lat, lng);
            btn.disabled = false;
            btn.innerHTML = originalText;
        },
        function (error) {
            btn.disabled = false;
            btn.innerHTML = originalText;
            switch (error.code) {
                case error.PERMISSION_DENIED:
                    alert("Location permission denied.");
                    break;
                case error.POSITION_UNAVAILABLE:
                    alert("Location unavailable.");
                    break;
                case error.TIMEOUT:
                    alert("Location request timed out.");
                    break;
                default:
                    alert("Unable to get current location.");
            }
            console.error(error);
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    );
}

document.getElementById("location-btn").addEventListener("click", getCurrentLocation);

/* ===================================================================
   Weather Forecast (Open-Meteo — free, no API key required)
   =================================================================== */

const WMO_WEATHER = {
    0:  { icon: "☀️", label: "Clear sky" },
    1:  { icon: "🌤️", label: "Mainly clear" },
    2:  { icon: "⛅", label: "Partly cloudy" },
    3:  { icon: "☁️", label: "Overcast" },
    45: { icon: "🌫️", label: "Fog" },
    48: { icon: "🌫️", label: "Depositing fog" },
    51: { icon: "🌦️", label: "Light drizzle" },
    53: { icon: "🌦️", label: "Drizzle" },
    55: { icon: "🌦️", label: "Dense drizzle" },
    61: { icon: "🌧️", label: "Light rain" },
    63: { icon: "🌧️", label: "Rain" },
    65: { icon: "🌧️", label: "Heavy rain" },
    71: { icon: "❄️", label: "Light snow" },
    73: { icon: "❄️", label: "Snow" },
    75: { icon: "❄️", label: "Heavy snow" },
    80: { icon: "🌦️", label: "Rain showers" },
    81: { icon: "🌦️", label: "Rain showers" },
    82: { icon: "🌧️", label: "Violent showers" },
    95: { icon: "⛈️", label: "Thunderstorm" },
    96: { icon: "⛈️", label: "Thunderstorm, hail" },
    99: { icon: "⛈️", label: "Thunderstorm, hail" },
};

function weatherInfo(code) {
    return WMO_WEATHER[code] || { icon: "🌡️", label: "Unknown" };
}

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

async function fetchWeather(lat, lng) {
    const card = document.getElementById("weather-card");

    try {
        const url =
            `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lng}` +
            `&current_weather=true` +
            `&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum` +
            `&timezone=auto`;

        const response = await fetch(url);
        const data = await response.json();
        if (!data.current_weather) throw new Error("No weather data");

        const current = data.current_weather;
        const info = weatherInfo(current.weathercode);

        document.getElementById("weather-icon").textContent = info.icon;
        document.getElementById("weather-temp").textContent = `${Math.round(current.temperature)}°C`;
        document.getElementById("weather-desc").textContent = info.label;
        document.getElementById("weather-wind").textContent = `💨 ${Math.round(current.windspeed)} km/h`;

        const daily = data.daily;
        const strip = document.getElementById("weather-forecast");
        strip.innerHTML = daily.time.slice(0, 5).map((dateStr, i) => {
            const d = new Date(dateStr);
            const dInfo = weatherInfo(daily.weathercode[i]);
            const max = Math.round(daily.temperature_2m_max[i]);
            const min = Math.round(daily.temperature_2m_min[i]);
            const rain = daily.precipitation_sum[i];
            return `
                <div class="weather-day">
                    <div class="wd-label">${i === 0 ? "Today" : DAY_NAMES[d.getDay()]}</div>
                    <div class="wd-icon">${dInfo.icon}</div>
                    <div class="wd-temp">${max}° <span>${min}°</span></div>
                    ${rain > 0 ? `<div class="wd-rain">💧${rain}mm</div>` : ""}
                </div>`;
        }).join("");

        card.style.display = "block";
    } catch (err) {
        console.error("Weather fetch error:", err);
        card.style.display = "none";
    }
}

/* ===================================================================
   Nearby Resources (real OSM data via the free Overpass API)
   =================================================================== */

/* Overpass's main public instance (overpass-api.de) is a free, shared,
   community-run service — it's had real outages (full connect timeouts,
   no HTTP response at all) and enforces per-IP rate limits. A bare,
   unguarded fetch() to it can hang or fail for reasons that have
   nothing to do with this app. Give every query a hard timeout and one
   fallback mirror before giving up. */
const OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    // Third, independently-hosted mirror — added after both of the
    // above were confirmed (via a user's live DevTools + a direct
    // browser navigation to the bare domain, no app involved)
    // unreachable with net::ERR_CONNECTION_TIMED_OUT from that user's
    // network, on both WiFi and mobile data. That rules out a bug in
    // this app's request handling — it's a connectivity/DNS/regional
    // routing problem between that network and these two specific
    // hosts. A third mirror on different infrastructure gives a real
    // chance of succeeding where the first two can't, without
    // resolving (or needing to resolve) whatever is blocking the
    // other two.
    "https://overpass.private.coffee/api/interpreter",
];
// Both Overpass QL queries below declare `[timeout:25]` — i.e. they
// tell the server it may take up to 25s to compute. This client-side
// abort used to fire at 15s, well before that budget was up, so any
// query that took Overpass more than 15s (confirmed live: real queries
// were landing in the 15.5s range) got canceled by our own code before
// the server ever had a chance to finish — not a real Overpass outage,
// just us hanging up too early on our own request. Set comfortably
// above the query's own 25s budget, with a few seconds of slack for
// network/queueing overhead.
const OVERPASS_TIMEOUT_MS = 28000;

async function overpassQuery(query, endpoint) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), OVERPASS_TIMEOUT_MS);
    try {
        // Content-Type must be explicit: without it, fetch() defaults a
        // plain-string body to `text/plain`, but Overpass's `data=`
        // parameter is a form field and expects
        // `application/x-www-form-urlencoded`. With the wrong
        // Content-Type, Overpass rejected the body outright with 406 Not
        // Acceptable on every single request (confirmed via DevTools —
        // Network tab showed 406 from overpass-api.de on 6/6 calls,
        // identical on the kumi.systems fallback). That 406 response
        // itself carries no CORS headers, which is why the browser
        // additionally reported it as a CORS violation instead of
        // surfacing the real 406 — a red herring that made this look
        // like a transient network/CORS issue rather than a malformed
        // request.
        const res = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: "data=" + encodeURIComponent(query),
            signal: controller.signal,
        });
        if (!res.ok) throw new Error(`Overpass responded ${res.status} (${endpoint})`);
        return await res.json();
    } finally {
        clearTimeout(timeoutId);
    }
}

/** Tries each Overpass mirror in turn (only 2 — this data is a "nice to
 * have" sidebar, not worth hammering every known mirror) until one
 * responds, instead of failing the whole card on the first endpoint's
 * bad day. */
async function overpassQueryWithFallback(query) {
    let lastErr;
    for (const endpoint of OVERPASS_ENDPOINTS) {
        try {
            return await overpassQuery(query, endpoint);
        } catch (err) {
            lastErr = err;
            console.warn(`Overpass endpoint failed (${endpoint}), trying next if any:`, err);
        }
    }
    throw lastErr;
}

const NODE_RESOURCE_TYPES = [
    { tag: `node["amenity"="bank"]`,        icon: "🏦", label: "Bank Branch" },
    { tag: `way["power"="substation"]`,     icon: "⚡", label: "Power Substation" },
    { tag: `node["power"="substation"]`,    icon: "⚡", label: "Power Substation" },
    { tag: `node["amenity"="marketplace"]`, icon: "🏪", label: "Market / Mandi" },
    { tag: `node["amenity"="hospital"]`,    icon: "🏥", label: "Hospital" },
    { tag: `node["amenity"="fuel"]`,        icon: "⛽", label: "Petrol Pump" },
    { tag: `node["railway"="station"]`,     icon: "🚉", label: "Railway Station" },
    { tag: `node["amenity"="school"]`,      icon: "🏫", label: "School" },
];

const WAY_RESOURCE_TYPES = [
    { tag: `way["waterway"="canal"]`, icon: "💧", label: "Canal" },
    { tag: `way["highway"~"^(trunk|primary|secondary)$"]`, icon: "🛣️", label: "Main Road" },
    { tag: `way["natural"="water"]`, icon: "🌊", label: "Water Body" },
    { tag: `way["waterway"="river"]`, icon: "🌊", label: "Water Body" },
];

const RESOURCE_LABELS = [
    "Bank Branch", "Power Substation", "Canal", "Main Road", "Water Body",
    "Market / Mandi", "Hospital", "Petrol Pump", "Railway Station", "School",
];
const RESOURCE_ICONS = {
    "Bank Branch": "🏦", "Power Substation": "⚡", "Canal": "💧", "Main Road": "🛣️",
    "Water Body": "🌊", "Market / Mandi": "🏪", "Hospital": "🏥", "Petrol Pump": "⛽",
    "Railway Station": "🚉", "School": "🏫",
};

function matchesLabel(label, tags) {
    return (
        (label === "Bank Branch" && tags.amenity === "bank") ||
        (label === "Power Substation" && tags.power === "substation") ||
        (label === "Market / Mandi" && tags.amenity === "marketplace") ||
        (label === "Hospital" && tags.amenity === "hospital") ||
        (label === "Petrol Pump" && tags.amenity === "fuel") ||
        (label === "Railway Station" && tags.railway === "station") ||
        (label === "School" && tags.amenity === "school")
    );
}

/** Nearest node-type POIs — fast, reliable, no way-geometry issues. */
async function fetchNearestNodes(lat, lng, radius) {
    const clauses = NODE_RESOURCE_TYPES.map(t => `${t.tag}(around:${radius},${lat},${lng});`).join("\n");
    const query = `[out:json][timeout:25];(${clauses});out center;`;

    const data = await overpassQueryWithFallback(query);
    const elements = data.elements || [];
    const origin = [lng, lat];

    const nearest = {};
    elements.forEach(el => {
        const plat = el.lat ?? el.center?.lat;
        const plon = el.lon ?? el.center?.lon;
        if (plat == null || plon == null) return;

        const tags = el.tags || {};
        const distanceKm = turf.distance(origin, [plon, plat], { units: "kilometers" });

        ["Bank Branch", "Power Substation", "Market / Mandi", "Hospital", "Petrol Pump", "Railway Station", "School"]
            .forEach(label => {
                if (matchesLabel(label, tags) && (nearest[label] == null || distanceKm < nearest[label])) {
                    nearest[label] = distanceKm;
                }
            });
    });

    return nearest;
}

/** Nearest road/canal — uses full way geometry (out geom) so the distance is
 * to the closest point actually inside the search radius, not the centroid
 * of the entire road/canal (which can be many km away). */
async function fetchNearestWays(lat, lng, radius) {
    const clauses = WAY_RESOURCE_TYPES.map(t => `${t.tag}(around:${radius},${lat},${lng});`).join("\n");
    const query = `[out:json][timeout:25];(${clauses});out geom;`;

    const data = await overpassQueryWithFallback(query);
    const elements = data.elements || [];
    const origin = [lng, lat];

    const nearest = {};
    elements.forEach(el => {
        if (!el.geometry) return;
        const tags = el.tags || {};

        let label = null;
        if (tags.waterway === "canal") label = "Canal";
        else if (["trunk", "primary", "secondary"].includes(tags.highway)) label = "Main Road";
        else if (tags.natural === "water" || tags.waterway === "river") label = "Water Body";
        if (!label) return;

        // Distance to the nearest vertex actually on this way — far more
        // accurate than the way's overall centroid for long roads/canals.
        let minDist = Infinity;
        el.geometry.forEach(pt => {
            const d = turf.distance(origin, [pt.lon, pt.lat], { units: "kilometers" });
            if (d < minDist) minDist = d;
        });

        if (minDist < Infinity && (nearest[label] == null || minDist < nearest[label])) {
            nearest[label] = minDist;
        }
    });

    return nearest;
}

let _nearbyResourcesRequestId = 0;

async function fetchNearbyResources(lat, lng) {
    const section = document.getElementById("nearby-resources-card");
    const list = document.getElementById("nearby-resources-list");
    const accessEl = document.getElementById("accessibility-value");

    const requestId = ++_nearbyResourcesRequestId;
    section.style.display = "block";
    list.innerHTML = `<p class="empty-hint">Checking nearby resources…</p>`;

    const nodeRadius = 25000; // 25 km — rural amenities are often sparse
    const wayRadius = 15000;  // 15 km — roads/canals are usually much closer

    // Run both independently: if one fails (e.g. Overpass timeout on a
    // heavy way query), the other's results still render instead of the
    // whole card going blank.
    const [nodeResult, wayResult] = await Promise.allSettled([
        fetchNearestNodes(lat, lng, nodeRadius),
        fetchNearestWays(lat, lng, wayRadius),
    ]);

    // A newer call for a different location finished first (or this one
    // just took a while) — don't clobber whatever it already rendered.
    if (requestId !== _nearbyResourcesRequestId) return;

    const distances = {
        ...(nodeResult.status === "fulfilled" ? nodeResult.value : {}),
        ...(wayResult.status === "fulfilled" ? wayResult.value : {}),
    };

    if (nodeResult.status === "rejected") console.error("Nearby nodes fetch failed:", nodeResult.reason);
    if (wayResult.status === "rejected") console.error("Nearby ways fetch failed:", wayResult.reason);

    if (nodeResult.status === "rejected" && wayResult.status === "rejected") {
        // Previously this hid the whole card (section.style.display =
        // "none"), which is indistinguishable from the feature not
        // existing at all. OpenStreetMap's free Overpass API is a
        // shared public service that does go down / rate-limit
        // (confirmed outages, not hypothetical) — when that happens the
        // right move is to say so and offer a retry, not disappear.
        list.innerHTML = `
            <p class="empty-hint">
                Couldn't reach OpenStreetMap's live map data service just now — it may be busy or briefly down.
                <a href="#" class="nr-retry" id="nearby-retry-link">Try again</a>
            </p>`;
        const retryLink = document.getElementById("nearby-retry-link");
        if (retryLink) {
            retryLink.addEventListener("click", (e) => {
                e.preventDefault();
                fetchNearbyResources(lat, lng);
            });
        }
        return;
    }

    list.innerHTML = RESOURCE_LABELS.map(label => {
        const d = distances[label];
        return `
            <div class="nr-row">
                <span class="nr-icon">${RESOURCE_ICONS[label]}</span>
                <span class="nr-label">${label}</span>
                <span class="nr-dist">${
                    d == null
                        ? `<span class="nr-notfound">not found nearby</span>`
                        : d < 1
                            ? `${Math.round(d * 1000)} m`
                            : `${d.toFixed(2)} km`
                }</span>
            </div>`;
    }).join("");

    // ---- Accessibility index — a transparent, real-distance-derived
    // score (NOT a model prediction), calibrated for rural India where a
    // 5-15km distance to the nearest road/market is normal. Categories
    // not found nearby are excluded from the average, not counted as 0. ----
    const scores = [];
    if (distances["Main Road"] != null) scores.push(Math.max(0, 100 - distances["Main Road"] * 5));
    if (distances["Market / Mandi"] != null) scores.push(Math.max(0, 100 - distances["Market / Mandi"] * 4));

    accessEl.textContent = scores.length
        ? `${Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)}%`
        : "N/A";

    section.style.display = "block";
}

/* ===================================================================
   Grade Assignment
   =================================================================== */

function gradeStyle(grade) {
    const map = {
        Excellent:  { bg: "rgba(52, 211, 153, 0.15)", color: "#34d399" },
        "Very Good": { bg: "rgba(74, 222, 128, 0.15)",  color: "#4ade80" },
        Good:       { bg: "rgba(250, 204, 21, 0.15)",  color: "#facc15" },
        Fair:       { bg: "rgba(251, 146, 60, 0.15)",  color: "#fb923c" },
        Poor:       { bg: "rgba(248, 113, 113, 0.15)", color: "#f87171" },
    };
    return map[grade] || map.Poor;
}

const GRADE_META = {
    Excellent:   { risk: "Low",      loan: "High Eligibility",     loanNote: "Strong land suitability across all measured factors." },
    "Very Good": { risk: "Low",      loan: "High Eligibility",     loanNote: "Suitable for multiple crops with proper planning." },
    Good:        { risk: "Moderate", loan: "Moderate Eligibility", loanNote: "Consider soil/irrigation support before financing." },
    Fair:        { risk: "Moderate", loan: "Limited Eligibility",  loanNote: "Higher risk profile — field verification recommended." },
    Poor:        { risk: "High",     loan: "Low Eligibility",      loanNote: "Field verification strongly recommended before financing." },
};

function updateRing(score) {
    const pct = Math.max(0, Math.min(1, (score - 400) / 600));
    const circumference = 339.3;
    const offset = circumference * (1 - pct);

    const arc = document.getElementById("ring-arc");
    arc.style.transition = "stroke-dashoffset 1s ease";
    arc.setAttribute("stroke-dashoffset", offset);

    const hue = Math.round(pct * 120);
    arc.setAttribute("stroke", `hsl(${hue}, 70%, 50%)`);
}

/* ===================================================================
   Satellite Metadata + Historical NDVI Trend
   =================================================================== */

function renderSatelliteMeta(meta) {
    const card = document.getElementById("sat-status-card");
    if (!card) return;

    if (!meta || !meta.scene_count) {
        card.style.display = "none";
        return;
    }

    document.getElementById("sat-status-scenes").textContent = `${meta.scene_count} scenes`;
    document.getElementById("sat-status-cloud").textContent =
        meta.mean_cloud_cover != null ? `${meta.mean_cloud_cover}% avg cloud` : "—";

    if (meta.latest_scene_date) {
        const days = Math.floor((Date.now() - new Date(meta.latest_scene_date)) / 86400000);
        document.getElementById("sat-status-freshness").textContent =
            `Latest scene: ${meta.latest_scene_date} (${days}d ago)`;
    } else {
        document.getElementById("sat-status-freshness").textContent = "—";
    }

    card.style.display = "block";
}

function renderTrendChart(trend) {
    const wrap = document.getElementById("trend-chart-wrap");
    if (!wrap) return;

    const points = (trend || []).filter(p => p.ndvi != null);
    if (points.length < 2) {
        wrap.innerHTML = `<p class="trend-empty">Not enough seasons with clear-sky imagery to plot a trend.</p>`;
        return;
    }

    const w = 100, h = 100, pad = 8;
    const values = points.map(p => p.ndvi);
    const min = Math.min(...values), max = Math.max(...values);
    const range = max - min || 0.01;

    const coords = points.map((p, i) => {
        const x = pad + (i / (points.length - 1)) * (w - pad * 2);
        const y = h - pad - ((p.ndvi - min) / range) * (h - pad * 2);
        return [x, y];
    });

    const pathD = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c[0].toFixed(1)},${c[1].toFixed(1)}`).join(" ");
    const dots = coords.map((c) =>
        `<circle cx="${c[0].toFixed(1)}" cy="${c[1].toFixed(1)}" r="2.4" fill="#34d399" />`
    ).join("");

    wrap.innerHTML = `
        <svg viewBox="0 0 ${w} ${h}" class="trend-svg" preserveAspectRatio="none">
            <path d="${pathD}" fill="none" stroke="#34d399" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
            ${dots}
        </svg>
        <div class="trend-labels">
            ${points.map(p => `<span>${p.year}</span>`).join("")}
        </div>`;
}

/* ===================================================================
   Render Result
   =================================================================== */

const PARAM_ORDER = [
    "ndvi", "evi", "savi", "msavi", "ndre", "ndmi", "ndwi", "ci_green", "ci_rededge",
    "vv", "vh", "vh_vv", "rvi",
    "rainfall", "air_temp", "solar_radiation", "spi", "spei", "gdd", "lst",
];

const PARAM_LABELS = {
    ndvi: "NDVI (Vegetation)", evi: "EVI", savi: "SAVI", msavi: "MSAVI",
    ndre: "NDRE (Nitrogen)", ndmi: "NDMI (Moisture)", ndwi: "NDWI (Water)",
    ci_green: "Chlorophyll (Green)", ci_rededge: "Chlorophyll (Red Edge)",
    vv: "Radar VV", vh: "Radar VH", vh_vv: "Radar VH/VV", rvi: "Radar Veg. Index",
    rainfall: "Rainfall", air_temp: "Air Temperature", solar_radiation: "Solar Radiation",
    spi: "SPI (Rainfall Anomaly)", spei: "SPEI (Water Balance)", gdd: "Growing Degree Days",
    lst: "Land Surface Temp.",
};

// What each of the 20 FarmScore parameters actually means — shown as a
// hover tooltip (ⓘ) on every parameter card so a value is never just a
// number with no context. Mirrors Backend/glossary.py's explanations.
const PARAM_MEANINGS = {
    ndvi: "Vegetation greenness/health from satellite bands. Higher = healthier, denser plant growth.",
    evi: "Like NDVI but corrected for haze and soil background, so it stays accurate in dense canopy. Higher = healthier vegetation.",
    savi: "A vegetation index that reduces the influence of visible soil in the background — useful for young/sparse crops. Higher = healthier vegetation.",
    msavi: "A self-adjusting version of SAVI for soil brightness. Higher = healthier vegetation.",
    ndre: "Red-edge based index, more sensitive to canopy nitrogen/chlorophyll than NDVI. Higher = better nitrogen status.",
    ndmi: "Moisture content in plant canopy. Higher = more water in the crop/vegetation.",
    ndwi: "Detects surface water presence (ponds, flooding, waterlogging).",
    ci_green: "Leaf chlorophyll content estimated from the green band. Higher = more chlorophyll.",
    ci_rededge: "Leaf chlorophyll content from the red-edge band — more sensitive at higher biomass. Higher = more chlorophyll.",
    vv: "Sentinel-1 radar signal strength (dB) reflected from the field. Sensitive to canopy structure/moisture; works through clouds.",
    vh: "Cross-polarized radar signal (dB), especially sensitive to crop volume/biomass. Works through clouds.",
    vh_vv: "Ratio of VH to VV radar signals — separates vegetation structure from soil/surface effects.",
    rvi: "Vegetation-density index computed purely from radar (VV/VH) — usable even under cloud cover.",
    rainfall: "Mean daily rainfall (mm/day) for the growing season. Too little stresses the crop; too much risks waterlogging.",
    air_temp: "Ambient air temperature — currently sourced from the same MODIS LST signal as Land Surface Temp, shown for reference (not separately weighted, to avoid double-counting).",
    solar_radiation: "Average daily incoming solar energy (MJ/m²/day) — a key driver of photosynthesis and growth rate.",
    spi: "Compares current rainfall to the historical average for this location — flags drought or unusually wet conditions.",
    spei: "Like SPI, but also accounts for temperature-driven water loss — catches heat-driven moisture stress.",
    gdd: "Accumulated heat units this season — gauges whether crop development is on track, behind, or heat-stressed.",
    lst: "Temperature of the land surface itself (not air), measured from thermal satellite bands.",
};

const PARAM_ICONS = {
    ndvi: "🌿", evi: "🌳", savi: "🌾", msavi: "🌾", ndre: "🍃", ndmi: "🌱", ndwi: "💧",
    ci_green: "🟢", ci_rededge: "🔴",
    vv: "📡", vh: "📡", vh_vv: "📡", rvi: "📡",
    rainfall: "🌧️", air_temp: "🌡️", solar_radiation: "☀️",
    spi: "🌧️", spei: "🌍", gdd: "🌱", lst: "🌡️",
};

const PARAM_COLORS = [
    "#34d399", "#22c55e", "#84cc16", "#65a30d", "#4d7c0f", "#4ade80", "#38bdf8",
    "#16a34a", "#15803d",
    "#8b5cf6", "#7c3aed", "#6d28d9", "#5b21b6",
    "#2563eb", "#f59e0b", "#b45309", "#0ea5e9", "#0e7490", "#65a30d", "#f97316",
];

function statusLabel(pct) {
    if (pct >= 80) return "Excellent";
    if (pct >= 60) return "Good";
    if (pct >= 40) return "Moderate";
    if (pct >= 20) return "Low";
    return "Poor";
}

function ndviTintColor(pct) {
    // 0 = poor (red/amber), 100 = healthy (green) — reflects the ACTUAL
    // computed NDVI sub-score, not a decorative fake heatmap.
    const hue = Math.max(0, Math.min(120, pct * 1.2));
    return `hsl(${hue}, 65%, 45%)`;
}

function renderResult(data) {
    const {
        score,
        grade,
        components,
        coordinates,
        recommended_crops,
        satellite_meta,
        ndvi_trend,
    } = data;

    // ---- Score ring ----
    document.getElementById("final-score").textContent = score;
    updateRing(score);

    // ---- FarmScore breakdown (Base + Kharif + Rabi composite) — same
    // score as the ring above, just showing how it's composed. ----
    renderFarmScoreBreakdown(document.getElementById("bhumi-seasonal-score-card"), data.enrichment?.farmscore_breakdown);

    // ---- Grade badge ----
    const gs = gradeStyle(grade);
    const gradeEl = document.getElementById("score-grade");
    gradeEl.textContent = grade;
    gradeEl.style.background = gs.bg;
    gradeEl.style.color = gs.color;

    // ---- Coordinates display ----
    document.getElementById("coord-display").textContent = formatCoords(coordinates.lat, coordinates.lng);

    // ---- Score Breakdown (real components) ----
    const grid = document.getElementById("params-grid");
    grid.innerHTML = PARAM_ORDER.map((key, i) => {
        const c = components[key];
        if (!c) return "";
        const pct = Math.max(0, Math.min(100, c.sub_score));
        const rawDisplay = typeof c.raw_value === "number" ? c.raw_value.toFixed(2) : c.raw_value;
        const unit = c.unit ? ` ${c.unit}` : "";
        // The backend already computes a specific reason for every
        // unavailable component (data_reasons / unavailable_reason in
        // app.py) — previously it was fetched but never rendered, so
        // "no data" gave no clue why. Surface it as a hover tooltip.
        const nodataReason = c.unavailable_reason ? ` title="${String(c.unavailable_reason).replace(/"/g, "&quot;")}"` : "";
        const availability = c.data_available ? "" : `<span class="p-nodata"${nodataReason}>⚠ no data</span>`;

        let extraTitle = PARAM_MEANINGS[key] || "";
        if (key === "rainfall" && data.rainfall_monthly && data.rainfall_monthly.length) {
            extraTitle += " Monthly breakdown: " + data.rainfall_monthly
                .map(m => `${m.month} ${m.mm_per_day != null ? m.mm_per_day.toFixed(1) : "—"} mm/day`)
                .join(", ");
        }
        const hasExtra = extraTitle ? ' <span class="sr-info" title="' + extraTitle.replace(/"/g, "&quot;") + '">ⓘ</span>' : "";

        return `
            <div class="score-row" data-param="${key}">
                <div class="sr-icon" style="background:${PARAM_COLORS[i]}22;color:${PARAM_COLORS[i]}">${PARAM_ICONS[key] || "📊"}</div>
                <div class="sr-body">
                    <div class="sr-top">
                        <span class="sr-label">${PARAM_LABELS[key] || key}${hasExtra}</span>
                        <span class="sr-status" style="color:${PARAM_COLORS[i]}">${statusLabel(pct)}</span>
                    </div>
                    <div class="mini-bar"><div class="mini-bar-fill" style="width:${pct}%;background:${PARAM_COLORS[i]}"></div></div>
                    <div class="sr-meta">${rawDisplay}${unit} · weight ${c.weight}% · ${c.source}${availability}</div>
                </div>
            </div>`;
    }).join("");

    // ---- NDVI Heatmap — real per-pixel vegetation image draped over the
    // farm boundary, not a flat tint. Falls back to a flat tint if the
    // heatmap image fails to generate (slow network, no imagery, etc). ----
    if (components.ndvi) {
        const col = ndviTintColor(components.ndvi.sub_score);
        drawnItems.eachLayer(function (layer) {
            if (layer.setStyle) layer.setStyle({ fillColor: col, fillOpacity: 0.15, color: col });
        });
        const legend = document.getElementById("ndvi-legend");
        if (legend) legend.style.display = "flex";

        if (window.__ndviHeatmapLayer) {
            map.removeLayer(window.__ndviHeatmapLayer);
            window.__ndviHeatmapLayer = null;
        }
        fetch(`${API_BASE_URL}/ndvi-heatmap`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat: coordinates.lat, lng: coordinates.lng, polygon: farmPolygon }),
        })
            .then(res => res.ok ? res.json() : null)
            .then(heatmap => {
                if (!heatmap || !heatmap.url || !heatmap.bounds) return;
                window.__ndviHeatmapLayer = L.imageOverlay(heatmap.url, heatmap.bounds, { opacity: 0.75 }).addTo(map);
            })
            .catch(err => console.warn("NDVI heatmap overlay failed (non-fatal):", err));
    }

    // ---- Top crop (folded into Land Summary — no separate crop card in this layout) ----
    let topCrop = "—";
    if (recommended_crops && recommended_crops.primary) {
        topCrop = `${recommended_crops.primary.crop} (${recommended_crops.primary.score}%)`;
    }

    // ---- Land Summary ----
    const meta = GRADE_META[grade] || GRADE_META.Poor;
    document.getElementById("ls-score").textContent = `${score}/1000`;
    document.getElementById("ls-risk").textContent = meta.risk;
    document.getElementById("ls-crop").textContent = topCrop;
    document.getElementById("ls-area").textContent = document.getElementById("farm-area").value || "Not drawn";
    document.getElementById("ls-date").textContent = new Date().toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });

    const climateEl = document.getElementById("ls-climate");
    if (climateEl && data.climate_risk) {
        const cr = data.climate_risk;
        climateEl.textContent = cr.flags.length ? `${cr.level} (${cr.flags[0]})` : cr.level;
        climateEl.title = cr.flags.join("; ");
    }

    const ndwiEl = document.getElementById("ls-ndwi");
    if (ndwiEl) {
        ndwiEl.textContent = data.ndwi != null ? data.ndwi.toFixed(3) : "—";
    }

    // ---- Loan Eligibility (qualitative only — no fabricated ₹ figures) ----
    const loanBadge = document.getElementById("loan-status");
    loanBadge.textContent = meta.loan;
    loanBadge.style.background = gs.bg;
    loanBadge.style.color = gs.color;
    document.getElementById("loan-desc").textContent = meta.loanNote;

    // ---- AI Insight (Gemini, grounded in the real data above — hidden
    // entirely if unavailable, never replaced with placeholder text) ----
    const aiCard = document.getElementById("ai-insight-card");
    if (data.ai_insight) {
        document.getElementById("ai-insight-text").textContent = data.ai_insight;
        aiCard.style.display = "block";
    } else {
        aiCard.style.display = "none";
    }

    // ---- AI Farm Advisor + AI Risk Analysis (on-demand, not auto-run,
    // to avoid an extra Gemini call on every single calculation) ----
    document.getElementById("ai-advisor-card").style.display = "block";
    document.getElementById("ai-advisor-text").textContent = "Not generated yet.";
    document.getElementById("ai-risk-card").style.display = "block";
    document.getElementById("ai-risk-text").textContent = "Not generated yet.";

    // ---- BCIS Credit Intelligence (Phase 6, on-demand) ----
    document.getElementById("bcis-card").style.display = "block";
    document.getElementById("bcis-content").innerHTML = `<p class="empty-hint">Not computed yet — needs a farm boundary (for total yield) for the loan ceiling.</p>`;

    // ---- Satellite metadata + historical trend (real Earth Engine data) ----
    renderSatelliteMeta(satellite_meta);
    renderTrendChart(ndvi_trend);

    // Keep the full result around so the chatbot can ground its answers
    // about "this farm" in the same real numbers shown on screen, and so
    // the Extended Report / PDF export pages can reuse it without
    // recomputing anything.
    lastFarmContext = data;
    try {
        sessionStorage.setItem("farmscore_last_result", JSON.stringify({ ...data, polygon: farmPolygon || null }));
    } catch (err) {
        console.warn("Could not cache result for report/PDF pages:", err);
    }
    const downloadBtn = document.getElementById("download-pdf-btn");
    if (downloadBtn) downloadBtn.disabled = false;
}

/* ===================================================================
   BCIS Credit Intelligence — on-demand (Phase 6)
   =================================================================== */
(function setupBcis() {
    const btn = document.getElementById("compute-bcis-btn");
    if (!btn) return;

    const tierColors = { GREEN: "#2f9e63", AMBER: "#e8912d", RED: "#d64545" };

    btn.addEventListener("click", async () => {
        if (!lastFarmContext) return;
        const content = document.getElementById("bcis-content");
        btn.disabled = true;
        content.innerHTML = `<p class="empty-hint">Computing… (includes a flood-risk check, can take 15-20s)</p>`;

        try {
            const res = await bhumiAuthFetch(`${API_BASE_URL}/credit-intelligence`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ...lastFarmContext, polygon: farmPolygon }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Failed");

            const bcis = data.bcis;
            const income = data.income_estimate;
            const ceiling = data.loan_ceiling;
            const color = tierColors[bcis.tier] || "#888";

            let html = `
                <div class="enrichment-row">
                    <span class="er-icon">💳</span>
                    <span class="er-label">BCIS Risk Score</span>
                    <span class="er-value" style="color:${color};font-weight:700;">${bcis.score}/100 — ${bcis.tier}</span>
                </div>`;

            if (income.available) {
                html += `
                    <div class="enrichment-row">
                        <span class="er-icon">💰</span>
                        <span class="er-label">Est. Income (${income.crop})</span>
                        <span class="er-value">₹${income.income_estimate_rs.toLocaleString("en-IN")}</span>
                    </div>`;
            } else {
                html += `<p class="empty-hint">${income.reason}</p>`;
            }

            if (ceiling.loan_ceiling_rs != null) {
                html += `
                    <div class="enrichment-row">
                        <span class="er-icon">🏦</span>
                        <span class="er-label">Loan Ceiling</span>
                        <span class="er-value" style="font-weight:700;">₹${ceiling.loan_ceiling_rs.toLocaleString("en-IN")}</span>
                    </div>`;
            }

            if (data.auto_freeze.frozen) {
                html += `<p style="color:#d64545;font-weight:700;font-size:0.76rem;margin-top:6px;">🚫 AUTO-FROZEN: ${data.auto_freeze.reason}</p>`;
            }

            html += `<p class="empty-hint" style="margin-top:8px;">Drivers: ${bcis.drivers.join("; ")}</p>`;
            html += `<p class="empty-hint">${bcis.method}</p>`;

            content.innerHTML = html;
        } catch (err) {
            content.innerHTML = `<p class="empty-hint">Could not compute BCIS. Please try again.</p>`;
        } finally {
            btn.disabled = false;
        }
    });
})();
(function setupAiAdvisorAndRisk() {
    const advisorBtn = document.getElementById("get-advisor-btn");
    const riskBtn = document.getElementById("get-risk-btn");
    if (!advisorBtn || !riskBtn) return;

    advisorBtn.addEventListener("click", async () => {
        if (!lastFarmContext) return;
        const textEl = document.getElementById("ai-advisor-text");
        advisorBtn.disabled = true;
        textEl.textContent = "Thinking…";
        try {
            const res = await fetch(`${API_BASE_URL}/farm-advisor`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(lastFarmContext),
            });
            const data = await res.json();
            textEl.textContent = data.advisory || data.reason || "No advisory available.";
        } catch (err) {
            textEl.textContent = "Could not generate advisory. Please try again.";
        } finally {
            advisorBtn.disabled = false;
        }
    });

    riskBtn.addEventListener("click", async () => {
        if (!lastFarmContext) return;
        const textEl = document.getElementById("ai-risk-text");
        riskBtn.disabled = true;
        textEl.textContent = "Analyzing…";
        try {
            const res = await fetch(`${API_BASE_URL}/risk-analysis`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    climate_risk: lastFarmContext.climate_risk,
                    score: lastFarmContext.score,
                    grade: lastFarmContext.grade,
                    enrichment: lastFarmContext.enrichment,
                }),
            });
            const data = await res.json();
            textEl.textContent = data.analysis || data.reason || "No risk analysis available.";
        } catch (err) {
            textEl.textContent = "Could not generate risk analysis. Please try again.";
        } finally {
            riskBtn.disabled = false;
        }
    });
})();
(function setupPdfDownload() {
    const btn = document.getElementById("download-pdf-btn");
    if (!btn) return;

    btn.addEventListener("click", async function () {
        if (!lastFarmContext) return;
        const original = btn.textContent;
        btn.textContent = "Generating…";
        btn.disabled = true;

        try {
            const res = await fetch(`${API_BASE_URL}/report/pdf`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(lastFarmContext),
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
            console.error("PDF download error:", err);
            const errBox = document.getElementById("error-box");
            if (errBox) {
                errBox.textContent = "Could not generate PDF report. Please try again.";
                errBox.style.display = "block";
            }
        } finally {
            btn.textContent = original;
            btn.disabled = false;
        }
    });
})();

/* ===================================================================
   Compute Score — single entry point called on button click
   =================================================================== */

async function computeScore() {
    const lat = parseFloat(document.getElementById("lat-input").value);
    const lng = parseFloat(document.getElementById("lng-input").value);

    const errBox = document.getElementById("error-box");
    errBox.style.display = "none";

    if (isNaN(lat) || isNaN(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
        errBox.textContent = "Please search, use current location, or click on the map first.";
        errBox.style.display = "block";
        return;
    }

    placeMarker(lat, lng);
    map.panTo([lat, lng]);

    const btn = document.getElementById("calc-btn");
    const btnText = document.getElementById("btn-text");
    btn.classList.add("loading");
    btnText.textContent = "Fetching satellite data…";

    try {
        btnText.textContent = "Querying Earth Engine…";
        const result = await calculateFarmScore(lat, lng);
        renderResult(result);
    } catch (err) {
        errBox.textContent = err.message || "An unexpected error occurred.";
        errBox.style.display = "block";
    } finally {
        btn.classList.remove("loading");
        btnText.textContent = "Calculate Bhumi AI Score";
    }
}

document.getElementById("calc-btn").addEventListener("click", computeScore);

/* ===================================================================
   Map toolbar tabs — each does something real, not decorative
   =================================================================== */

const TAB_TO_PARAM = { ndmi: "ndmi", rainfall: "rainfall", groundwater: "groundwater" };

document.querySelectorAll(".map-tab").forEach(tab => {
    tab.addEventListener("click", function () {
        document.querySelectorAll(".map-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");

        const key = tab.dataset.tab;

        if (key === "spectral") {
            openSpectralPanel();
            return;
        }

        if (key === "ndvi") {
            const legend = document.getElementById("ndvi-legend");
            if (legend) legend.style.display = legend.style.display === "flex" ? "none" : "flex";
            return;
        }

        if (key === "layers") {
            if (map.hasLayer(satelliteLayer)) {
                map.removeLayer(satelliteLayer);
                streetLayer.addTo(map);
            } else {
                map.removeLayer(streetLayer);
                satelliteLayer.addTo(map);
            }
            return;
        }

        const paramKey = TAB_TO_PARAM[key];
        if (paramKey) {
            const row = document.querySelector(`.score-row[data-param="${paramKey}"]`);
            if (row) {
                row.scrollIntoView({ behavior: "smooth", block: "center" });
                row.classList.add("flash");
                setTimeout(() => row.classList.remove("flash"), 900);
            }
        }
    });
});

/* ===================================================================
   Floating Chatbot — answers general questions from Gemini's own
   knowledge, and questions about the currently-calculated farm using
   the exact real numbers shown on screen (never invented client-side).
   =================================================================== */

let lastFarmContext = null;
let chatHistory = [];

const chatPanel = document.getElementById("chat-panel");
const chatMessages = document.getElementById("chat-messages");
const chatInput = document.getElementById("chat-input");

document.getElementById("chat-toggle-btn").addEventListener("click", () => {
    chatPanel.classList.toggle("open");
    if (chatPanel.classList.contains("open")) chatInput.focus();
});

document.getElementById("chat-close-btn").addEventListener("click", () => {
    chatPanel.classList.remove("open");
});

function appendChatMessage(text, who) {
    const el = document.createElement("div");
    el.className = `chat-msg chat-msg-${who}`;
    el.textContent = text;
    chatMessages.appendChild(el);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return el;
}

async function sendChatMessage() {
    const message = chatInput.value.trim();
    if (!message) return;

    chatInput.value = "";
    appendChatMessage(message, "user");
    chatHistory.push({ role: "user", text: message });

    const typingEl = appendChatMessage("…", "bot");

    try {
        const res = await fetch(`${API_BASE_URL}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message,
                history: chatHistory.slice(0, -1),
                farm_context: lastFarmContext,
            }),
        });
        const data = await res.json();

        if (!res.ok) throw new Error(data.error || "Assistant unavailable");

        typingEl.textContent = data.reply;
        chatHistory.push({ role: "assistant", text: data.reply });
    } catch (err) {
        typingEl.textContent = "Sorry, I couldn't reach the assistant. " + (err.message || "");
        typingEl.classList.add("chat-msg-error");
    }
}

document.getElementById("chat-send-btn").addEventListener("click", sendChatMessage);
chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendChatMessage();
});

/* ===================================================================
   Crop Disease Diagnosis — our own trained MobileNetV2 classifier
   (see Backend/plant_disease_model.py), uploaded photo only. No
   Gemini call. Always shows confidence + caveat exactly as returned
   by the backend; never invents extra certainty client-side.
   =================================================================== */

const diagnoseOverlay = document.getElementById("diagnose-overlay");
const diagnoseFileInput = document.getElementById("diagnose-file-input");
const diagnosePreview = document.getElementById("diagnose-preview");
const uploadDropText = document.getElementById("upload-drop-text");
const diagnoseSubmitBtn = document.getElementById("diagnose-submit-btn");
const diagnoseResult = document.getElementById("diagnose-result");

document.getElementById("nav-diagnose-btn").addEventListener("click", () => {
    diagnoseOverlay.classList.add("open");
});

document.getElementById("diagnose-close-btn").addEventListener("click", () => {
    diagnoseOverlay.classList.remove("open");
});

diagnoseOverlay.addEventListener("click", (e) => {
    if (e.target === diagnoseOverlay) diagnoseOverlay.classList.remove("open");
});

let selectedImageFile = null;

diagnoseFileInput.addEventListener("change", () => {
    const file = diagnoseFileInput.files[0];
    if (!file) return;

    if (file.size > 6 * 1024 * 1024) {
        alert("Image too large — please use a photo under 6MB.");
        diagnoseFileInput.value = "";
        return;
    }

    selectedImageFile = file;
    diagnoseSubmitBtn.disabled = false;
    diagnoseResult.style.display = "none";

    const reader = new FileReader();
    reader.onload = (e) => {
        diagnosePreview.src = e.target.result;
        diagnosePreview.style.display = "block";
        uploadDropText.style.display = "none";
    };
    reader.readAsDataURL(file);
});

function confidenceColor(level) {
    if (level === "High") return "var(--primary)";
    if (level === "Medium") return "var(--signal, var(--accent-amber))";
    return "var(--danger)";
}

// plant_disease_model.py's labels come straight from PlantVillage's
// folder names, e.g. "Tomato___Late_blight" — readable to a developer,
// not to a farmer. Purely cosmetic (___ -> " — ", _ -> " ").
function formatTrainedModelLabel(label) {
    return String(label || "").replaceAll("___", " — ").replaceAll("_", " ");
}

// The diagnosis/crop_guess/confidence fields above already ARE this
// same model's top prediction — this section only adds its runner-up
// guesses (top3[1:]), so there's nothing to show when there aren't any.
function renderTrainedModelSection(prediction) {
    const alternatives = (prediction?.top3 || []).slice(1) // first entry duplicates the diagnosis above
        .map(p => `<li>${escapeHTML(formatTrainedModelLabel(p.label))} — ${Number(p.confidence ?? 0).toFixed(1)}%</li>`)
        .join("");
    if (!alternatives) return "";
    return `
        <div class="diag-section diag-trained-model">
            <div class="diag-trained-model-alt">Other possibilities our model considered:<ul>${alternatives}</ul></div>
        </div>`;
}

async function submitDiagnosis() {
    if (!selectedImageFile) return;

    diagnoseSubmitBtn.disabled = true;
    diagnoseSubmitBtn.textContent = "Analyzing…";
    diagnoseResult.style.display = "none";

    const formData = new FormData();
    formData.append("image", selectedImageFile);

    try {
        const res = await fetch(`${API_BASE_URL}/diagnose`, { method: "POST", body: formData });
        const data = await res.json();

        if (!res.ok) throw new Error(data.error || "Diagnosis failed");

        // Every field below comes from the backend's AI-classified
        // response to an uploaded photo. None of it is trusted as safe
        // markup — escape everything before it touches innerHTML.
        if (data.is_plant === false) {
            diagnoseResult.innerHTML = `
                <p class="diag-not-plant">This doesn't look like a plant/crop photo. ${escapeHTML(data.diagnosis || "")}</p>`;
        } else {
            const symptoms = (data.symptoms_observed || []).map(s => `<li>${escapeHTML(s)}</li>`).join("");
            const remedies = (data.remedy_steps || []).map(s => `<li>${escapeHTML(s)}</li>`).join("");

            diagnoseResult.innerHTML = `
                <div class="diag-row">
                    <span class="diag-label">Crop (guess)</span>
                    <span>${escapeHTML(data.crop_guess || "Unclear")}</span>
                </div>
                <div class="diag-row">
                    <span class="diag-label">Diagnosis</span>
                    <span class="diag-diagnosis">${escapeHTML(data.diagnosis || "Unclear from photo")}</span>
                </div>
                <div class="diag-row">
                    <span class="diag-label">Confidence</span>
                    <span style="color:${confidenceColor(data.confidence)}">${escapeHTML(data.confidence || "Low")}</span>
                </div>
                ${symptoms ? `<div class="diag-section"><strong>Symptoms observed</strong><ul>${symptoms}</ul></div>` : ""}
                ${remedies ? `<div class="diag-section"><strong>Suggested next steps</strong><ul>${remedies}</ul></div>` : ""}
                ${renderTrainedModelSection(data.trained_model_prediction)}
                <p class="diag-caveat">⚠ ${escapeHTML(data.caveat || "This is an AI estimate, not a substitute for expert advice.")}</p>`;
        }

        diagnoseResult.style.display = "block";
    } catch (err) {
        diagnoseResult.innerHTML = `<p class="diag-not-plant">Couldn't diagnose the photo: ${escapeHTML(err.message)}</p>`;
        diagnoseResult.style.display = "block";
    } finally {
        diagnoseSubmitBtn.disabled = false;
        diagnoseSubmitBtn.textContent = "Diagnose Photo";
    }
}

diagnoseSubmitBtn.addEventListener("click", submitDiagnosis);

/* ===================================================================
   Spectral Intelligence — "hyperspectral-style" crop health engine.
   Calls the backend /spectral endpoint (real Sentinel-2 multispectral
   proxy indices: NDVI/NDRE/GNDVI/NDMI/MSI), renders a 0-100 score ring,
   per-index breakdown, flags, and AI (or rule-based fallback)
   recommendations. Never fabricates numbers client-side.
   =================================================================== */

const spectralOverlay = document.getElementById("spectral-overlay");
const spectralLoading = document.getElementById("spectral-loading");
const spectralContent = document.getElementById("spectral-content");
const spectralErrorBox = document.getElementById("spectral-error");

const SPECTRAL_INDEX_ORDER = ["chlorophyll", "nitrogen", "moisture_stress", "stress_risk"];
const SPECTRAL_COLORS = { chlorophyll: "#34d399", nitrogen: "#a3e635", moisture_stress: "#38bdf8", stress_risk: "#f59e0b" };
const SPECTRAL_ICONS = { chlorophyll: "🌿", nitrogen: "🧪", moisture_stress: "💧", stress_risk: "⚠️" };

function closeSpectralPanel() {
    spectralOverlay.classList.remove("open");
}

document.getElementById("spectral-close-btn").addEventListener("click", closeSpectralPanel);
spectralOverlay.addEventListener("click", (e) => {
    if (e.target === spectralOverlay) closeSpectralPanel();
});

function updateSpectralRing(score) {
    const pct = Math.max(0, Math.min(1, score / 100));
    const circumference = 339.3;
    const offset = circumference * (1 - pct);
    const arc = document.getElementById("spectral-ring-arc");
    arc.style.transition = "stroke-dashoffset 1s ease";
    arc.setAttribute("stroke-dashoffset", offset);
    const hue = Math.round(pct * 200); // blue(200)->green as it improves
    arc.setAttribute("stroke", `hsl(${hue}, 70%, 55%)`);
}

function renderSpectralResult(data) {
    document.getElementById("spectral-final-score").textContent = data.spectral_score;
    updateSpectralRing(data.spectral_score);

    // Spectral's own grade scale (Excellent/Good/Moderate/Fair/Poor, from
    // spectral_service.py — unrelated to FarmScore) doesn't line up
    // position-for-position with gradeStyle()'s FarmScore-flavored keys
    // (Excellent/Very Good/Good/Fair/Poor) — its 2nd tier is called
    // "Good" and its 3rd "Moderate", so both need remapping onto
    // gradeStyle's matching color position, not just "Moderate".
    const SPECTRAL_GRADE_TO_STYLE_KEY = { Good: "Very Good", Moderate: "Good" };
    const gs = gradeStyle(SPECTRAL_GRADE_TO_STYLE_KEY[data.grade] || data.grade);
    const gradeEl = document.getElementById("spectral-grade");
    gradeEl.textContent = data.grade;
    gradeEl.style.background = gs.bg;
    gradeEl.style.color = gs.color;

    const flagsWrap = document.getElementById("spectral-flags");
    flagsWrap.innerHTML = (data.flags || []).map(f => `<div class="spectral-flag">⚠ ${f}</div>`).join("");

    const grid = document.getElementById("spectral-params-grid");
    grid.innerHTML = SPECTRAL_INDEX_ORDER.map(key => {
        const c = data.indices[key];
        if (!c) return "";
        const pct = Math.max(0, Math.min(100, c.sub_score));
        const color = SPECTRAL_COLORS[key];
        const availability = c.data_available ? "" : `<span class="p-nodata">⚠ no data</span>`;
        return `
            <div class="score-row">
                <div class="sr-icon" style="background:${color}22;color:${color}">${SPECTRAL_ICONS[key]}</div>
                <div class="sr-body">
                    <div class="sr-top">
                        <span class="sr-label">${c.label}</span>
                        <span class="sr-status" style="color:${color}">${c.status}</span>
                    </div>
                    <div class="mini-bar"><div class="mini-bar-fill" style="width:${pct}%;background:${color}"></div></div>
                    <div class="sr-meta">${c.index} ${c.raw_value} · weight ${c.weight}% · ${c.source}${availability}</div>
                </div>
            </div>`;
    }).join("");

    const rec = data.recommendations || {};
    const recWrap = document.getElementById("spectral-recommendations");
    // These three are Gemini-generated advisory text (see
    // gemini_service.generate_spectral_insight) — same untrusted-output
    // class as the /diagnose fields above, escaped for the same reason.
    recWrap.innerHTML = `
        <div class="spectral-rec-row"><span class="spectral-rec-label">💧 Irrigation</span><p>${escapeHTML(rec.irrigation_advice || "—")}</p></div>
        <div class="spectral-rec-row"><span class="spectral-rec-label">🧪 Fertilization</span><p>${escapeHTML(rec.fertilization_advice || "—")}</p></div>
        <div class="spectral-rec-row"><span class="spectral-rec-label">🌾 Crop Management</span><p>${escapeHTML(rec.crop_management_advice || "—")}</p></div>`;
}

async function fetchSpectralIntelligence(lat, lng) {
    const res = await fetch(`${API_BASE_URL}/spectral`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat, lng, polygon: farmPolygon }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || data.detail || `Server error ${res.status}`);
    return data;
}

async function openSpectralPanel() {
    const lat = parseFloat(document.getElementById("lat-input").value);
    const lng = parseFloat(document.getElementById("lng-input").value);

    if (isNaN(lat) || isNaN(lng)) {
        const errBox = document.getElementById("error-box");
        errBox.textContent = "Select a location on the map first, then open Spectral Intelligence.";
        errBox.style.display = "block";
        return;
    }

    spectralOverlay.classList.add("open");
    spectralContent.style.display = "none";
    spectralErrorBox.style.display = "none";
    spectralLoading.style.display = "flex";

    try {
        const data = await fetchSpectralIntelligence(lat, lng);
        renderSpectralResult(data);
        spectralContent.style.display = "block";
    } catch (err) {
        spectralErrorBox.textContent = "Couldn't compute spectral intelligence: " + (err.message || "unknown error");
        spectralErrorBox.style.display = "block";
    } finally {
        spectralLoading.style.display = "none";
    }
}
