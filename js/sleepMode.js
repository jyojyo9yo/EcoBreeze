(function sleepModeController() {
    const DEVICE_ID = "cam1";
    const UNIT_KEY = "ecobreeze_sleep_mode_unit"; // display-only preference, not sent to Supabase
    const MIN_SEC = 5;
    const MAX_SEC = 3600;
    const STEP_MIN_UNIT = 60; // +/- 1 minute
    const STEP_SEC_UNIT = 5; // +/- 5 seconds

    const modeBtn = document.getElementById("sleepModeBtn");
    const modal = document.getElementById("sleepModal");
    const closeBtn = document.getElementById("sleepModalClose");
    const statusLabel = document.getElementById("sleepStatusLabel");
    const toggleBtn = document.getElementById("sleepToggleBtn");
    const delayValueEl = document.getElementById("sleepDelayValue");
    const delayDownBtn = document.getElementById("sleepDelayDownBtn");
    const delayUpBtn = document.getElementById("sleepDelayUpBtn");
    const unitMinBtn = document.getElementById("sleepUnitMinBtn");
    const unitSecBtn = document.getElementById("sleepUnitSecBtn");
    const autoNoteEl = document.getElementById("sleepAutoNote");

    // on/manual/delaySeconds are driven by person_detect.py via Supabase: it
    // tracks how long someone's been lying down and auto-triggers sleep_on
    // after delaySeconds, unless the dashboard has manually taken over.
    let on = false;
    let manual = false;
    let delaySeconds = 600;
    let unit = localStorage.getItem(UNIT_KEY) === "sec" ? "sec" : "min";

    let client = null;
    getSupabaseClient().then((c) => {
        client = c;
    });

    function formatDelay() {
        return unit === "sec" ? delaySeconds + "초" : Math.round(delaySeconds / 60) + "분";
    }

    function renderModeBtn() {
        modeBtn.textContent = "수면 모드 (" + (on ? "켜짐" : "꺼짐") + ")";
    }

    function renderToggle() {
        statusLabel.textContent = on ? "켜짐" : "꺼짐";
        toggleBtn.textContent = on ? "ON" : "OFF";
        toggleBtn.classList.toggle("on", on);
        toggleBtn.classList.toggle("off", !on);
        autoNoteEl.textContent = on && !manual ? "누운 지 " + formatDelay() + " 경과 - 자동으로 켜짐" : "";
        renderModeBtn();
    }

    function renderDelay() {
        delayValueEl.textContent = formatDelay();
    }

    function renderUnit() {
        unitMinBtn.classList.toggle("selected", unit === "min");
        unitSecBtn.classList.toggle("selected", unit === "sec");
        renderDelay();
    }

    modeBtn.addEventListener("click", () => modal.classList.remove("hidden"));
    closeBtn.addEventListener("click", () => modal.classList.add("hidden"));
    modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.classList.add("hidden");
    });

    toggleBtn.addEventListener("click", async () => {
        on = !on;
        manual = true;
        renderToggle();
        if (client) {
            await client
                .from("device_status")
                .update({ sleep_on: on, sleep_manual: true })
                .eq("device_id", DEVICE_ID);
        }
    });

    async function setDelay(nextSeconds) {
        delaySeconds = Math.min(MAX_SEC, Math.max(MIN_SEC, nextSeconds));
        renderDelay();
        renderToggle();
        if (client) {
            await client
                .from("device_status")
                .update({ sleep_delay_sec: delaySeconds })
                .eq("device_id", DEVICE_ID);
        }
    }

    delayDownBtn.addEventListener("click", () => {
        setDelay(delaySeconds - (unit === "sec" ? STEP_SEC_UNIT : STEP_MIN_UNIT));
    });
    delayUpBtn.addEventListener("click", () => {
        setDelay(delaySeconds + (unit === "sec" ? STEP_SEC_UNIT : STEP_MIN_UNIT));
    });

    unitMinBtn.addEventListener("click", () => {
        unit = "min";
        localStorage.setItem(UNIT_KEY, unit);
        renderUnit();
    });
    unitSecBtn.addEventListener("click", () => {
        unit = "sec";
        localStorage.setItem(UNIT_KEY, unit);
        renderUnit();
    });

    document.addEventListener("ecobreeze:status", (e) => {
        const row = e.detail && e.detail.row;
        if (!row) return;
        on = !!row.sleep_on;
        manual = !!row.sleep_manual;
        if (Number.isFinite(row.sleep_delay_sec)) {
            delaySeconds = row.sleep_delay_sec;
            renderDelay();
        }
        renderToggle();
    });

    renderToggle();
    renderUnit();
})();
