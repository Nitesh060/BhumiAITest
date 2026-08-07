const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://farmprototype.onrender.com";

function row(icon, label, value) {
    return `
        <div class="enrichment-row">
            <span class="er-icon">${icon}</span>
            <span class="er-label">${label}</span>
            <span class="er-value">${value}</span>
        </div>`;
}

function breakdownTable(counts, colLabel) {
    const entries = Object.entries(counts || {});
    if (!entries.length) return `<p class="empty-hint">No data yet.</p>`;
    return `
        <table class="report-table">
            <thead><tr><th>${colLabel}</th><th>Count</th></tr></thead>
            <tbody>${entries.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</tbody>
        </table>`;
}

async function loadPortfolio() {
    const list = document.getElementById("portfolio-list");
    try {
        const res = await bhumiAuthFetch(`${API_BASE_URL}/portfolio/summary`);
        if (res.status === 503) {
            list.innerHTML = `<p class="empty-hint">Database not configured — Farm Management data unavailable.</p>`;
            return;
        }
        const data = await res.json();
        if (!res.ok) throw new Error("failed");

        list.innerHTML = [
            row("👥", "Total Farmers", data.total_farmers),
            row("🌾", "Total Farms", data.total_farms),
            row("📐", "Total Area", `${data.total_area_ha} ha`),
            row("🛰️", "Farms with Measured Area", data.farms_with_measured_area),
        ].join("");

        document.getElementById("survey-breakdown-table").innerHTML = breakdownTable(data.survey_method_breakdown, "Survey Method");
        document.getElementById("district-breakdown-table").innerHTML = breakdownTable(data.farmers_by_district, "District");
    } catch (err) {
        list.innerHTML = `<p class="empty-hint">Could not load portfolio summary.</p>`;
    }
}

async function loadUsers() {
    const list = document.getElementById("users-list");
    try {
        const res = await bhumiAuthFetch(`${API_BASE_URL}/admin/users`);
        const data = await res.json();
        if (!res.ok) return;

        const currentUser = bhumiGetUser();
        list.innerHTML = data.users.map(u => `
            <div class="fm-list-item">
                <div class="fm-list-item-title">
                    ${u.name} <span style="font-weight:400;color:var(--text-muted);">(${u.role})</span>
                    ${u.id !== currentUser.id ? `<span style="float:right;cursor:pointer;" data-id="${u.id}">🗑️</span>` : ""}
                </div>
                <div class="fm-list-item-sub">@${u.username} · joined ${new Date(u.created_at).toLocaleDateString()}</div>
            </div>`).join("");

        list.querySelectorAll("span[data-id]").forEach(el => {
            el.addEventListener("click", async () => {
                if (!confirm("Delete this user?")) return;
                await bhumiAuthFetch(`${API_BASE_URL}/admin/users/${el.dataset.id}`, { method: "DELETE" });
                loadUsers();
            });
        });
    } catch (err) {
        list.innerHTML = `<p class="empty-hint">Could not load users.</p>`;
    }
}

document.getElementById("add-user-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
        name: document.getElementById("new-user-name").value.trim(),
        username: document.getElementById("new-user-username").value.trim(),
        password: document.getElementById("new-user-password").value,
        role: document.getElementById("new-user-role").value,
    };
    const errBox = document.getElementById("error-box");
    try {
        const res = await bhumiAuthFetch(`${API_BASE_URL}/admin/users`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) {
            errBox.textContent = data.error || "Could not add user.";
            errBox.style.display = "block";
            return;
        }
        errBox.style.display = "none";
        e.target.reset();
        loadUsers();
    } catch (err) {
        errBox.textContent = "Could not reach the server.";
        errBox.style.display = "block";
    }
});

function init() {
    loadPortfolio();
    const user = bhumiGetUser();
    if (user && user.role === "admin") {
        document.getElementById("admin-section").style.display = "block";
        loadUsers();
    }
}

init();
