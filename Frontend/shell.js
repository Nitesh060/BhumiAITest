/* ===================================================================
   shell.js — included on every protected page BEFORE the page's own
   script. Handles:
     1. Theme (day/night) — applied immediately to avoid a flash
     2. Auth guard — redirects to login.html if no valid token
     3. Injects user info + logout + theme-toggle into the sidebar
   =================================================================== */

(function applyTheme() {
    const saved = localStorage.getItem("bhumi_theme") || "dark";
    if (saved === "light") document.documentElement.setAttribute("data-theme", "light");
})();

const BHUMI_API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://bhumiaitest.onrender.com";

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
    localStorage.removeItem("bhumi_token");
    localStorage.removeItem("bhumi_user");
    window.location.href = "login.html";
}

/** Fetch wrapper that attaches the Bearer token — use for any call to
 * an auth-protected endpoint (Farm Management, Credit Intelligence,
 * Insurance Claims). Falls back to a plain fetch if no token (the
 * backend will correctly respond 401, and the guard below will have
 * already redirected before this is ever called in practice). */
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
            info.innerHTML = `<strong>${user.name}</strong>${user.role === "admin" ? "Admin" : "Field Officer"}`;
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
