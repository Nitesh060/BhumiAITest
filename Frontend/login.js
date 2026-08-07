const API_BASE_URL = window.FARMSCORE_API_URL || "https://bhumiaitest.onrender.com";

function showError(msg) {
    const el = document.getElementById("login-error");
    el.textContent = msg;
    el.style.display = "block";
}

// Login
document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();

    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    const btn = document.getElementById("login-submit-btn");

    btn.disabled = true;
    btn.textContent = "Signing in...";

    try {
        const res = await fetch(`${API_BASE_URL}/auth/login`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                username,
                password
            })
        });

        const data = await res.json();

        if (!res.ok) {
            showError(data.error || "Login failed.");
            return;
        }

        localStorage.setItem("bhumi_token", data.token);
        localStorage.setItem("bhumi_user", JSON.stringify(data.user));

        window.location.href = "index.html";

    } catch (err) {
        console.error(err);
        showError("Could not reach the server. Please try again.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Sign In";
    }
});

// Show Register Form
document.getElementById("show-register-link").addEventListener("click", (e) => {
    e.preventDefault();
    document.getElementById("register-form").style.display = "block";
    document.getElementById("register-toggle").style.display = "none";
});

// Register
document.getElementById("register-form").addEventListener("submit", async (e) => {
    e.preventDefault();

    const name = document.getElementById("reg-name").value.trim();
    const username = document.getElementById("reg-username").value.trim();
    const password = document.getElementById("reg-password").value;

    try {
        const res = await fetch(`${API_BASE_URL}/auth/register`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                name,
                username,
                password
            })
        });

        const data = await res.json();

        if (!res.ok) {
            showError(data.error || "Registration failed.");
            return;
        }

        localStorage.setItem("bhumi_token", data.token);
        localStorage.setItem("bhumi_user", JSON.stringify(data.user));

        window.location.href = "index.html";

    } catch (err) {
        console.error(err);
        showError("Could not reach the server. Please try again.");
    }
});