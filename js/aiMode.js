(function aiModeModal() {
    const STORAGE_KEY = "ecobreeze_ai_sensitivity";
    const LABELS = { low: "낮음", normal: "보통", high: "민감" };

    const btn = document.getElementById("aiModeBtn");
    const modal = document.getElementById("aiModeModal");
    const cancelBtn = document.getElementById("aiModeCancel");
    const saveBtn = document.getElementById("aiModeSave");
    const options = document.querySelectorAll(".sensitivity-option");

    let saved = localStorage.getItem(STORAGE_KEY) || "normal";
    let selected = saved;

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

    saveBtn.addEventListener("click", () => {
        saved = selected;
        localStorage.setItem(STORAGE_KEY, saved);
        updateButtonLabel();
        closeModal();
    });

    modal.addEventListener("click", (e) => {
        if (e.target === modal) closeModal();
    });

    updateButtonLabel();
})();
