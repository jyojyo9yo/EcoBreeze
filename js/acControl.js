(function acController() {
    const DEVICE_ID = "cam1";
    const TEMP_KEY = "ecobreeze_ac_temp";
    const MIN_TEMP = 18.0;
    const MAX_TEMP = 30.0;
    const STEP = 0.1;

    const modeBtn = document.getElementById("acModeBtn");
    const modal = document.getElementById("acModal");
    const closeBtn = document.getElementById("acModalClose");
    const statusLabel = document.getElementById("acStatusLabel");
    const toggleBtn = document.getElementById("acToggleBtn");
    const tempValueEl = document.getElementById("tempSettingValue");
    const tempDownBtn = document.getElementById("tempDownBtn");
    const tempUpBtn = document.getElementById("tempUpBtn");

    // on/off is driven by person_detect.py via Supabase (auto PMV comfort-band
    // decision, or whatever the dashboard last set manually) -- not local state.
    let on = false;
    let targetTemp = parseFloat(localStorage.getItem(TEMP_KEY));
    if (!Number.isFinite(targetTemp)) targetTemp = 24.0;

    let client = null;
    getSupabaseClient().then((c) => {
        client = c;
    });

    function renderModeBtn() {
        modeBtn.textContent = "에어컨 (" + (on ? targetTemp.toFixed(1) + "°C" : "꺼짐") + ")";
    }

    function renderToggle() {
        statusLabel.textContent = on ? "켜짐" : "꺼짐";
        toggleBtn.textContent = on ? "ON" : "OFF";
        toggleBtn.classList.toggle("on", on);
        toggleBtn.classList.toggle("off", !on);
        renderModeBtn();
    }

    function renderTemp() {
        tempValueEl.textContent = targetTemp.toFixed(1) + "°C";
        renderModeBtn();
    }

    modeBtn.addEventListener("click", () => modal.classList.remove("hidden"));
    closeBtn.addEventListener("click", () => modal.classList.add("hidden"));
    modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.classList.add("hidden");
    });

    toggleBtn.addEventListener("click", async () => {
        on = !on;
        renderToggle();
        if (client) {
            // ac_manual: true tells person_detect.py to stop auto-deciding this
            // and just mirror whatever we set here, until toggled again.
            await client
                .from("device_status")
                .update({ ac_on: on, ac_manual: true })
                .eq("device_id", DEVICE_ID);
        }
    });

    tempDownBtn.addEventListener("click", () => {
        targetTemp = Math.max(MIN_TEMP, +(targetTemp - STEP).toFixed(1));
        localStorage.setItem(TEMP_KEY, String(targetTemp));
        renderTemp();
    });

    tempUpBtn.addEventListener("click", () => {
        targetTemp = Math.min(MAX_TEMP, +(targetTemp + STEP).toFixed(1));
        localStorage.setItem(TEMP_KEY, String(targetTemp));
        renderTemp();
    });

    document.addEventListener("ecobreeze:status", (e) => {
        const row = e.detail && e.detail.row;
        if (!row) return;
        on = !!row.ac_on;
        renderToggle();
    });

    renderToggle();
    renderTemp();
})();
