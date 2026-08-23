/* ===================================================================
   Farm Management — Phase 1
   Farmer CRUD, multi-farm support, boundary capture via draw / GPS
   walking survey / KML-GeoJSON import.
   =================================================================== */

const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://bhumiaitest.onrender.com";

let selectedFarmerId = null;
let selectedFarmId = null;
let pendingPolygon = null;   // GeoJSON Feature, set by draw/gps/import before saving
let pendingCentroid = null;  // {lat, lng}
let gpsWatchId = null;
let gpsPoints = [];

// ---- Map setup ----
const map = L.map("fm-map", { zoomControl: true }).setView([20.5, 78.9], 5);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

let drawnItems = new L.FeatureGroup();
map.addLayer(drawnItems);

const drawControl = new L.Control.Draw({
    edit: { featureGroup: drawnItems },
    draw: {
        polygon: { allowIntersection: false, showArea: true, shapeOptions: { color: "#34d399", weight: 3 } },
        rectangle: true, polyline: false, circle: false, circlemarker: false, marker: false,
    },
});
map.addControl(drawControl);

map.on(L.Draw.Event.CREATED, function (e) {
    drawnItems.clearLayers();
    drawnItems.addLayer(e.layer);
    pendingPolygon = e.layer.toGeoJSON();
    const centroid = turf.centroid(pendingPolygon).geometry.coordinates;
    pendingCentroid = { lat: centroid[1], lng: centroid[0] };
    document.getElementById("save-farm-btn").disabled = false;
});

// ---- Tabs ----
document.querySelectorAll(".fm-tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".fm-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        const mode = tab.dataset.mode;
        document.getElementById("draw-panel").style.display = mode === "draw" ? "block" : "none";
        document.getElementById("gps-panel").style.display = mode === "gps" ? "block" : "none";
        document.getElementById("import-panel").style.display = mode === "import" ? "block" : "none";
    });
});

// ---- GPS Walking Survey ----
document.getElementById("gps-start-btn").addEventListener("click", () => {
    if (!navigator.geolocation) {
        document.getElementById("gps-status").textContent = "GPS not available on this device/browser.";
        return;
    }
    gpsPoints = [];
    drawnItems.clearLayers();
    document.getElementById("gps-start-btn").style.display = "none";
    document.getElementById("gps-stop-btn").style.display = "inline-block";
    document.getElementById("gps-status").textContent = "Walking… 0 points recorded.";

    let trackLine = L.polyline([], { color: "#34d399", weight: 3 }).addTo(map);

    gpsWatchId = navigator.geolocation.watchPosition(
        (pos) => {
            const { latitude, longitude } = pos.coords;
            gpsPoints.push([longitude, latitude]);
            trackLine.addLatLng([latitude, longitude]);
            map.panTo([latitude, longitude]);
            document.getElementById("gps-status").textContent = `Walking… ${gpsPoints.length} points recorded.`;
        },
        (err) => {
            document.getElementById("gps-status").textContent = `GPS error: ${err.message}`;
        },
        { enableHighAccuracy: true, maximumAge: 2000, timeout: 10000 }
    );
});

document.getElementById("gps-stop-btn").addEventListener("click", () => {
    if (gpsWatchId !== null) navigator.geolocation.clearWatch(gpsWatchId);
    document.getElementById("gps-start-btn").style.display = "inline-block";
    document.getElementById("gps-stop-btn").style.display = "none";

    if (gpsPoints.length < 3) {
        document.getElementById("gps-status").textContent = "Need at least 3 points to make a boundary. Walk more and try again.";
        return;
    }

    const ring = [...gpsPoints];
    if (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1]) {
        ring.push(ring[0]); // close the ring
    }
    pendingPolygon = { type: "Feature", geometry: { type: "Polygon", coordinates: [ring] }, properties: {} };
    const centroid = turf.centroid(pendingPolygon).geometry.coordinates;
    pendingCentroid = { lat: centroid[1], lng: centroid[0] };

    drawnItems.clearLayers();
    L.geoJSON(pendingPolygon, { style: { color: "#34d399", weight: 3 } }).addTo(drawnItems);
    document.getElementById("gps-status").textContent = `Boundary closed — ${gpsPoints.length} points.`;
    document.getElementById("save-farm-btn").disabled = false;
});

// ---- KML / GeoJSON Import ----
document.getElementById("import-file-input").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const status = document.getElementById("import-status");
    status.textContent = "Uploading…";

    const formData = new FormData();
    formData.append("file", file);

    try {
        const res = await bhumiAuthFetch(`${API_BASE_URL}/farms/import`, { method: "POST", body: formData });
        const data = await res.json();
        if (!res.ok || !data.polygon) {
            status.textContent = data.error || "Could not extract a boundary from this file.";
            return;
        }
        pendingPolygon = { type: "Feature", geometry: data.polygon, properties: {} };
        const centroid = turf.centroid(pendingPolygon).geometry.coordinates;
        pendingCentroid = { lat: centroid[1], lng: centroid[0] };

        drawnItems.clearLayers();
        L.geoJSON(pendingPolygon, { style: { color: "#34d399", weight: 3 } }).addTo(drawnItems);
        map.fitBounds(L.geoJSON(pendingPolygon).getBounds());
        status.textContent = "Boundary imported successfully.";
        document.getElementById("save-farm-btn").disabled = false;
    } catch (err) {
        status.textContent = "Upload failed. Please try again.";
    }
});

// ---- Farmer CRUD ----
async function loadFarmers(search = "") {
    const url = search ? `${API_BASE_URL}/farmers?search=${encodeURIComponent(search)}` : `${API_BASE_URL}/farmers`;
    const errorBox = document.getElementById("error-box");

    let res;
    try {
        res = await bhumiAuthFetch(url);
    } catch (err) {
        errorBox.textContent = `Could not reach the server: ${err.message}`;
        errorBox.style.display = "block";
        return;
    }

    if (res.status === 503) {
        document.getElementById("db-unconfigured-notice").style.display = "block";
        document.getElementById("fm-content").style.display = "none";
        return;
    }
    document.getElementById("db-unconfigured-notice").style.display = "none";
    document.getElementById("fm-content").style.display = "block";

    if (!res.ok) {
        // Previously any other error (401 from an expired token, a 500
        // from a real DB problem, etc.) fell straight through to the
        // "no farmers" empty state below — indistinguishable from
        // actually having zero farmers, hiding the real problem.
        const err = await res.json().catch(() => ({}));
        errorBox.textContent = err.error || `Could not load farmers (server said: ${res.status}).`;
        errorBox.style.display = "block";
        return;
    }
    errorBox.style.display = "none";

    const data = await res.json();
    const farmers = data.farmers || [];
    document.getElementById("farmer-count").textContent = `(${farmers.length})`;

    const list = document.getElementById("farmer-list");
    if (!farmers.length) {
        list.innerHTML = `<p class="empty-hint">No farmers registered yet.</p>`;
        return;
    }
    list.innerHTML = farmers.map(f => `
        <div class="fm-list-item ${f.id === selectedFarmerId ? "selected" : ""}" data-id="${escapeHTML(f.id)}">
            <div class="fm-list-item-title">${escapeHTML(f.name)}</div>
            <div class="fm-list-item-sub">${escapeHTML([f.village, f.district, f.state].filter(Boolean).join(", ") || "—")} · ${f.farm_count} farm(s)</div>
        </div>`).join("");

    list.querySelectorAll(".fm-list-item").forEach(el => {
        el.addEventListener("click", () => selectFarmer(el.dataset.id, el.querySelector(".fm-list-item-title").textContent));
    });
}

document.getElementById("farmer-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorBox = document.getElementById("error-box");
    errorBox.style.display = "none";

    const body = {
        name: document.getElementById("farmer-name").value.trim(),
        phone: document.getElementById("farmer-phone").value.trim() || null,
        village: document.getElementById("farmer-village").value.trim() || null,
        district: document.getElementById("farmer-district").value.trim() || null,
        state: document.getElementById("farmer-state").value.trim() || null,
    };
    if (!body.name) return;

    // Previously any failure here — a non-OK response (bad request,
    // auth/CORS/DB error) or a network-level failure (fetch itself
    // throwing, e.g. CORS block) — left the form exactly as the user
    // left it with zero feedback, indistinguishable from "did nothing
    // happen yet". Both are now surfaced in error-box.
    try {
        const res = await bhumiAuthFetch(`${API_BASE_URL}/farmers`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        if (res.ok) {
            e.target.reset();
            loadFarmers();
        } else {
            const err = await res.json().catch(() => ({}));
            errorBox.textContent = err.error || `Could not register farmer (server said: ${res.status}).`;
            errorBox.style.display = "block";
        }
    } catch (err) {
        errorBox.textContent = `Could not reach the server: ${err.message}`;
        errorBox.style.display = "block";
    }
});

document.getElementById("farmer-search").addEventListener("input", (e) => loadFarmers(e.target.value));

function selectFarmer(id, name) {
    selectedFarmerId = id;
    selectedFarmId = null;
    document.getElementById("ground-truth-panel").style.display = "none";
    document.getElementById("selected-farmer-name").textContent = name;
    document.getElementById("farms-panel").style.display = "block";
    document.querySelectorAll(".fm-list-item").forEach(el => el.classList.toggle("selected", el.dataset.id === id));
    loadFarms(id);
}

// ---- Farm CRUD ----
async function loadFarms(farmerId) {
    const res = await bhumiAuthFetch(`${API_BASE_URL}/farmers/${farmerId}/farms`);
    const data = await res.json();
    const farms = data.farms || [];

    const list = document.getElementById("farm-list");
    if (!farms.length) {
        list.innerHTML = `<p class="empty-hint">No farms yet — add one below.</p>`;
        document.getElementById("ground-truth-panel").style.display = "none";
        selectedFarmId = null;
    } else {
        list.innerHTML = farms.map(f => `
            <div class="fm-list-item ${f.id === selectedFarmId ? "selected" : ""}" data-id="${escapeHTML(f.id)}">
                <div class="fm-list-item-title">${escapeHTML(f.label || "Unlabeled farm")} <span style="float:right;">🗑️</span></div>
                <div class="fm-list-item-sub">
                    ${f.lat.toFixed(4)}°, ${f.lng.toFixed(4)}° · ${f.area_ha ? f.area_ha.toFixed(2) + " ha" : "area unknown"} · ${escapeHTML(f.survey_method || "—")}
                </div>
            </div>`).join("");

        list.querySelectorAll(".fm-list-item").forEach((el, i) => {
            el.querySelector("span").addEventListener("click", async (ev) => {
                ev.stopPropagation();
                if (!confirm("Delete this farm?")) return;
                await bhumiAuthFetch(`${API_BASE_URL}/farms/${farms[i].id}`, { method: "DELETE" });
                if (selectedFarmId === farms[i].id) {
                    selectedFarmId = null;
                    document.getElementById("ground-truth-panel").style.display = "none";
                }
                loadFarms(farmerId);
            });
            el.addEventListener("click", () => selectFarm(farms[i].id, farms[i].label || "Unlabeled farm"));
        });
    }

    // Show all farm boundaries/pins on the map for context
    drawnItems.clearLayers();
    farms.forEach(f => {
        if (f.polygon) {
            L.geoJSON(f.polygon, { style: { color: "#60a5fa", weight: 2, fillOpacity: 0.1 } }).addTo(drawnItems);
        } else {
            L.marker([f.lat, f.lng]).addTo(drawnItems);
        }
    });
    if (farms.length) {
        try { map.fitBounds(drawnItems.getBounds(), { maxZoom: 15 }); } catch (e) { /* single point, ignore */ }
    }
}

document.getElementById("save-farm-btn").addEventListener("click", async () => {
    if (!selectedFarmerId || !pendingCentroid) return;

    const activeTab = document.querySelector(".fm-tab.active").dataset.mode;
    const surveyMethod = { draw: "drawn", gps: "gps_walk", import: pendingPolygon ? "geojson_import" : "point_only" }[activeTab] || "drawn";

    const body = {
        lat: pendingCentroid.lat,
        lng: pendingCentroid.lng,
        label: document.getElementById("farm-label").value.trim() || null,
        polygon: pendingPolygon,
        survey_method: surveyMethod,
    };

    const res = await bhumiAuthFetch(`${API_BASE_URL}/farmers/${selectedFarmerId}/farms`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });

    if (res.ok) {
        pendingPolygon = null;
        pendingCentroid = null;
        document.getElementById("farm-label").value = "";
        document.getElementById("save-farm-btn").disabled = true;
        loadFarms(selectedFarmerId);
        loadFarmers(); // refresh farm_count in the farmer list
    } else {
        const err = await res.json();
        document.getElementById("error-box").textContent = err.error || "Could not save farm.";
        document.getElementById("error-box").style.display = "block";
    }
});

// ---- Ground Truth (ROADMAP.md Phase 8 — labeled-data bootstrap) ----
function selectFarm(farmId, label) {
    selectedFarmId = farmId;
    document.querySelectorAll("#farm-list .fm-list-item").forEach(el => el.classList.toggle("selected", el.dataset.id === farmId));
    document.getElementById("gt-farm-label").textContent = label;
    document.getElementById("ground-truth-panel").style.display = "block";
    loadGroundTruth(farmId);
}

async function loadGroundTruth(farmId) {
    const res = await bhumiAuthFetch(`${API_BASE_URL}/farms/${farmId}/ground-truth`);
    const data = await res.json();
    const observations = data.observations || [];

    document.getElementById("gt-count").textContent = `(${observations.length})`;

    const list = document.getElementById("ground-truth-list");
    if (!observations.length) {
        list.innerHTML = `<p class="empty-hint">No observations recorded yet for this farm.</p>`;
        return;
    }
    list.innerHTML = observations.map(o => {
        const dates = [o.sowing_date, o.harvest_date].filter(Boolean).map(d => d.slice(0, 10)).join(" → ");
        const yieldText = o.observed_yield_kg_per_acre != null ? `${o.observed_yield_kg_per_acre} kg/acre` : null;
        return `
            <div class="fm-list-item">
                <div class="fm-list-item-title">${escapeHTML(o.crop)}${o.season ? ` · ${escapeHTML(o.season)}` : ""} ${o.has_photo ? "📷" : ""}</div>
                <div class="fm-list-item-sub">
                    ${[dates, yieldText].filter(Boolean).map(escapeHTML).join(" · ") || "—"}
                    ${o.notes ? `<br>${escapeHTML(o.notes)}` : ""}
                </div>
            </div>`;
    }).join("");
}

document.getElementById("ground-truth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selectedFarmId) return;

    const crop = document.getElementById("gt-crop").value.trim();
    if (!crop) return;

    const formData = new FormData();
    formData.append("crop", crop);
    const season = document.getElementById("gt-season").value;
    if (season) formData.append("season", season);
    const sowingDate = document.getElementById("gt-sowing-date").value;
    if (sowingDate) formData.append("sowing_date", sowingDate);
    const harvestDate = document.getElementById("gt-harvest-date").value;
    if (harvestDate) formData.append("harvest_date", harvestDate);
    const yieldValue = document.getElementById("gt-yield").value;
    if (yieldValue) formData.append("observed_yield_kg_per_acre", yieldValue);
    const notes = document.getElementById("gt-notes").value.trim();
    if (notes) formData.append("notes", notes);
    const photoFile = document.getElementById("gt-photo").files[0];
    if (photoFile) {
        if (photoFile.size > 2 * 1024 * 1024) {
            document.getElementById("error-box").textContent = "Photo too large — please use a photo under 2MB.";
            document.getElementById("error-box").style.display = "block";
            return;
        }
        formData.append("image", photoFile);
    }

    const res = await bhumiAuthFetch(`${API_BASE_URL}/farms/${selectedFarmId}/ground-truth`, {
        method: "POST", body: formData,
    });

    if (res.ok) {
        e.target.reset();
        loadGroundTruth(selectedFarmId);
    } else {
        const err = await res.json();
        document.getElementById("error-box").textContent = err.error || "Could not record observation.";
        document.getElementById("error-box").style.display = "block";
    }
});

// ---- Init ----
loadFarmers();
document.getElementById("fm-subtitle").textContent = "Register farmers, capture farm boundaries, manage multiple farms per farmer.";
