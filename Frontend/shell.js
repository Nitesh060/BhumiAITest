/* ===================================================================
   shell.js — included on every protected page BEFORE the page's own
   script. Handles theme, auth, shared UI polish, API configuration,
   and user controls.
   =================================================================== */

(function applyTheme() {
    const saved = localStorage.getItem("bhumi_theme") || "dark";
    if (saved === "light") document.documentElement.setAttribute("data-theme", "light");
})();

// Shared API configuration — loaded before page-specific JavaScript.
window.FARMSCORE_API_URL = window.FARMSCORE_API_URL || "https://bhumiaitest.onrender.com";
const BHUMI_API_BASE_URL = window.FARMSCORE_API_URL;

// Shared typography/layout layer. Loading it here keeps all protected pages
// visually consistent without requiring every HTML page to duplicate the link.
(function loadUiPolish() {
    if (document.querySelector('link[data-bhumi-ui-polish]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "ui-polish.css?v=2";
    link.dataset.bhumiUiPolish = "true";
    document.head.appendChild(link);
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
            // Use text nodes rather than innerHTML for account-derived data.
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
