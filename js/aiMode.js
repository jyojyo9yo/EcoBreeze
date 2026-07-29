(function aiModeModal() {
    const DEVICE_ID = "cam1";
    const STORAGE_KEY = "ecobreeze_ai_sensitivity";
    const LABELS = { low: "낮음", normal: "보통", high: "민감" };

    const btn = document.getElementById("aiModeBtn");
    const modal = document.getElementById("aiModeModal");
    const cancelBtn = document.getElementById("aiModeCancel");
    const saveBtn = document.getElementById("aiModeSave");
    const options = document.querySelectorAll(".sensitivity-option");

    let saved = localStorage.getItem(STORAGE_KEY) || "normal";
    let selected = saved;
    let client = null;
    getSupabaseClient().then((c) => {
        client = c;
    });

    function updateButtonLabel() {
        btn.textContent = "AI 모드 (" + LABELS[saved] + ")";
    }

    function renderSelection() {
        options.forEach((opt) => {
            opt.classList.toggle("selected", opt.dataset.level === selected);
        });
    }

    function openModal() {
        selected = saved;
        renderSelection();
        modal.classList.remove("hidden");
    }

    function closeModal() {
        modal.classList.add("hidden");
    }

    options.forEach((opt) => {
        opt.addEventListener("click", () => {
            selected = opt.dataset.level;
            renderSelection();
        });
    });

    btn.addEventListener("click", openModal);
    cancelBtn.addEventListener("click", closeModal);

    saveBtn.addEventListener("click", async () => {
        saved = selected;
        localStorage.setItem(STORAGE_KEY, saved);
        updateButtonLabel();
        closeModal();
        if (client) {
            // Read by person_detect.py to size the PMV comfort band (low/normal/high -> 0.7/0.5/0.2C).
            await client.from("device_status").update({ ai_sensitivity: saved }).eq("device_id", DEVICE_ID);
        }
    });

    modal.addEventListener("click", (e) => {
        if (e.target === modal) closeModal();
    });

    // Stay in sync if the sensitivity was changed from elsewhere (e.g. another tab).
    document.addEventListener("ecobreeze:status", (e) => {
        const row = e.detail && e.detail.row;
        if (row && LABELS[row.ai_sensitivity] && row.ai_sensitivity !== saved) {
            saved = row.ai_sensitivity;
            localStorage.setItem(STORAGE_KEY, saved);
            updateButtonLabel();
        }
    });

    updateButtonLabel();
})();
