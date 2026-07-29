(function sleepModeController() {
    const DEVICE_ID = "cam1";
    const DELAY_KEY = "ecobreeze_sleep_mode_delay_min";
    const MIN_DELAY = 1;
    const MAX_DELAY = 60;

    const modeBtn = document.getElementById("sleepModeBtn");
    const modal = document.getElementById("sleepModal");
    const closeBtn = document.getElementById("sleepModalClose");
    const statusLabel = document.getElementById("sleepStatusLabel");
    const toggleBtn = document.getElementById("sleepToggleBtn");
    const delayValueEl = document.getElementById("sleepDelayValue");
    const delayDownBtn = document.getElementById("sleepDelayDownBtn");
    const delayUpBtn = document.getElementById("sleepDelayUpBtn");
    const autoNoteEl = document.getElementById("sleepAutoNote");

    // on/manual are driven by person_detect.py via Supabase: it tracks how
    // long someone's been lying down and auto-triggers sleep_on after
    // sleep_delay_min, unless the dashboard has manually taken over (sleep_manual).
    let on = false;
    let manual = false;
    let delayMinutes = parseInt(localStorage.getItem(DELAY_KEY), 10);
    if (!Number.isFinite(delayMinutes)) delayMinutes = 10;

    let client = null;
    getSupabaseClient().then((c) => {
        client = c;
    });

    function renderModeBtn() {
        modeBtn.textContent = "수면 모드 (" + (on ? "켜짐" : "꺼짐") + ")";
    }

    function renderToggle() {
        statusLabel.textContent = on ? "켜짐" : "꺼짐";
        toggleBtn.textContent = on ? "ON" : "OFF";
        toggleBtn.classList.toggle("on", on);
        toggleBtn.classList.toggle("off", !on);
        autoNoteEl.textContent = on && !manual ? "누운 지 " + delayMinutes + "분 경과 - 자동으로 켜짐" : "";
        renderModeBtn();
    }

    function renderDelay() {
        delayValueEl.textContent = delayMinutes + "분";
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

    async function setDelay(next) {
        delayMinutes = next;
        localStorage.setItem(DELAY_KEY, String(delayMinutes));
        renderDelay();
        renderToggle();
        if (client) {
            await client
                .from("device_status")
                .update({ sleep_delay_min: delayMinutes })
                .eq("device_id", DEVICE_ID);
        }
    }

    delayDownBtn.addEventListener("click", () => setDelay(Math.max(MIN_DELAY, delayMinutes - 1)));
    delayUpBtn.addEventListener("click", () => setDelay(Math.min(MAX_DELAY, delayMinutes + 1)));

    document.addEventListener("ecobreeze:status", (e) => {
        const row = e.detail && e.detail.row;
        if (!row) return;
        on = !!row.sleep_on;
        manual = !!row.sleep_manual;
        if (Number.isFinite(row.sleep_delay_min)) {
            delayMinutes = row.sleep_delay_min;
            localStorage.setItem(DELAY_KEY, String(delayMinutes));
            renderDelay();
        }
        renderToggle();
    });

    renderToggle();
    renderDelay();
})();
