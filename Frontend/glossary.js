const API_BASE_URL =
    window.FARMSCORE_API_URL ||
    "https://farmprototype.onrender.com";

async function loadGlossary() {
    const content = document.getElementById("glossary-content");
    try {
        const res = await fetch(`${API_BASE_URL}/glossary`);
        if (!res.ok) throw new Error("Request failed");
        const data = await res.json();
        const terms = data.terms || [];

        if (!terms.length) {
            content.innerHTML = `<p class="empty-hint">No glossary terms available.</p>`;
            return;
        }

        content.innerHTML = terms.map(t => `
            <div class="glossary-term">
                <div class="glossary-term-name">${t.term}${t.full_form ? ` <span class="glossary-full-form">(${t.full_form})</span>` : ""}</div>
                <div class="glossary-term-desc">${t.explanation}</div>
            </div>`).join("");
    } catch (err) {
        content.innerHTML = `<p class="empty-hint">Could not load glossary. Is the backend running?</p>`;
        const errBox = document.getElementById("error-box");
        if (errBox) {
            errBox.textContent = "Could not reach the backend to load the glossary.";
            errBox.style.display = "block";
        }
    }
}

loadGlossary();
