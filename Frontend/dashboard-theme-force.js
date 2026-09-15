/* Force the approved light reference layout on the main FarmScore dashboard. */
(function () {
    const isDashboard = window.location.pathname === "/" ||
        window.location.pathname.endsWith("index.html") ||
        window.location.pathname.endsWith("/Frontend/");
    if (!isDashboard) return;
    document.documentElement.setAttribute("data-theme", "light");

    // Load the administrative fallback with a new cache-busting version.
    if (!document.querySelector('script[data-bhumi-odisha-admin-fix]')) {
        const script = document.createElement("script");
        script.src = "odisha-admin-fix.js?v=3";
        script.dataset.bhumiOdishaAdminFix = "true";
        document.head.appendChild(script);
    }
})();
