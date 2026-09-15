/* Force the approved light reference layout on the main FarmScore dashboard. */
(function () {
    const isDashboard = window.location.pathname === "/" ||
        window.location.pathname.endsWith("index.html") ||
        window.location.pathname.endsWith("/Frontend/");
    if (!isDashboard) return;
    document.documentElement.setAttribute("data-theme", "light");

    // shell.js injects this file before the dashboard DOM is ready. Load the
    // administrative fallback now so it can take over the selectors once the
    // land-verification card is created.
    if (!document.querySelector('script[data-bhumi-odisha-admin-fix]')) {
        const script = document.createElement("script");
        script.src = "odisha-admin-fix.js?v=2";
        script.dataset.bhumiOdishaAdminFix = "true";
        document.head.appendChild(script);
    }
})();
