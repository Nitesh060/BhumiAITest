/* Force the approved light reference layout on the main FarmScore dashboard. */
(function () {
    const isDashboard = window.location.pathname === "/" ||
        window.location.pathname.endsWith("index.html") ||
        window.location.pathname.endsWith("/Frontend/");
    if (!isDashboard) return;
    document.documentElement.setAttribute("data-theme", "light");
})();
