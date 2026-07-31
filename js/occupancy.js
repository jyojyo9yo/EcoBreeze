(function occupancyController() {
    // 사람이 없을 때 에어컨을 끄는 기능. 수면 모드와 달리 토글이 출력 상태가
    // 아니라 "기능을 쓸지 말지"라서, Supabase 컬럼도 absence_enabled 다.
    // 실제 판단(부재 시간 누적, 에어컨 off)은 person_detect.py 가 한다.
    const DEVICE_ID = "cam1";
    const UNIT_KEY = "ecobreeze_absence_unit"; // 표시 전용, Supabase 에 안 보낸다
    const MIN_SEC = 5;
    const MAX_SEC = 7200;
    const STEP_MIN_UNIT = 60; // +/- 1분
    const STEP_SEC_UNIT = 5; // +/- 5초

    const modeBtn = document.getElementById("absenceModeBtn");
    const modal = document.getElementById("absenceModal");
    const closeBtn = document.getElementById("absenceModalClose");
    const statusLabel = document.getElementById("absenceStatusLabel");
    const toggleBtn = document.getElementById("absenceToggleBtn");
    const delayValueEl = document.getElementById("absenceDelayValue");
    const delayDownBtn = document.getElementById("absenceDelayDownBtn");
    const delayUpBtn = document.getElementById("absenceDelayUpBtn");
    const unitMinBtn = document.getElementById("absenceUnitMinBtn");
    const unitSecBtn = document.getElementById("absenceUnitSecBtn");
    const noteEl = document.getElementById("absenceNote");

    let enabled = false;
    let delaySeconds = 1800;
    let personDetected = false;
    let unit = localStorage.getItem(UNIT_KEY) === "sec" ? "sec" : "min";

    let client = null;
    getSupabaseClient().then((c) => {
        client = c;
    });

    function formatDelay() {
        return unit === "sec" ? delaySeconds + "초" : Math.round(delaySeconds / 60) + "분";
    }

    function renderModeBtn() {
        modeBtn.textContent = "재실 감지 (" + (enabled ? "켜짐" : "꺼짐") + ")";
    }

    function renderToggle() {
        statusLabel.textContent = enabled ? "켜짐" : "꺼짐";
        toggleBtn.textContent = enabled ? "ON" : "OFF";
        toggleBtn.classList.toggle("on", enabled);
        toggleBtn.classList.toggle("off", !enabled);

        if (!enabled) {
            noteEl.textContent = "";
        } else if (personDetected) {
            noteEl.textContent = "사람이 감지되고 있습니다 - 대기 중";
        } else {
            noteEl.textContent = "사람 없음 " + formatDelay() + " 경과 시 에어컨이 꺼집니다";
        }
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
        enabled = !enabled;
        renderToggle();
        if (client) {
            await client
                .from("device_status")
                .update({ absence_enabled: enabled })
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
                .update({ absence_delay_sec: delaySeconds })
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
        enabled = !!row.absence_enabled;
        personDetected = !!row.person_detected;
        if (Number.isFinite(row.absence_delay_sec)) {
            delaySeconds = row.absence_delay_sec;
            renderDelay();
        }
        renderToggle();
    });

    renderToggle();
    renderUnit();
})();
