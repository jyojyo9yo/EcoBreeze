(function personStatusPoller() {
    const DEVICE_ID = "cam1";
    const POLL_INTERVAL_MS = 3000;
    const OFFLINE_AFTER_MS = 15000;

    const statusEl = document.getElementById("personStatus");
    const updatedAtEl = document.getElementById("personUpdatedAt");
    const tempEl = document.getElementById("tempValue");
    const humidityEl = document.getElementById("humidityValue");

    function render(row) {
        if (!row) {
            statusEl.textContent = "연결 대기중";
            statusEl.className = "person-status status-offline";
            updatedAtEl.textContent = "";
            tempEl.textContent = "--°C";
            humidityEl.textContent = "습도 --%";
            document.dispatchEvent(new CustomEvent("ecobreeze:status", { detail: { row: null, lying: false } }));
            return;
        }

        const updatedAt = new Date(row.updated_at);
        const isStale = Date.now() - updatedAt.getTime() > OFFLINE_AFTER_MS;

        if (isStale) {
            statusEl.textContent = "연결 끊김";
            statusEl.className = "person-status status-offline";
        } else if (row.person_detected && row.lying_detected) {
            statusEl.textContent = "누워있음";
            statusEl.className = "person-status status-lying";
        } else if (row.person_detected) {
            statusEl.textContent = "사람 탐지";
            statusEl.className = "person-status status-detected";
        } else {
            statusEl.textContent = "사람 없음";
            statusEl.className = "person-status status-clear";
        }

        updatedAtEl.textContent = "마지막 업데이트: " + updatedAt.toLocaleTimeString("ko-KR");

        tempEl.textContent = row.temperature != null ? row.temperature.toFixed(1) + "°C" : "--°C";
        humidityEl.textContent = "습도 " + (row.humidity != null ? row.humidity.toFixed(0) + "%" : "--%");

        const lying = !isStale && !!(row.person_detected && row.lying_detected);
        document.dispatchEvent(new CustomEvent("ecobreeze:status", { detail: { row: row, lying: lying } }));
    }

    async function poll(client) {
        const { data, error } = await client
            .from("device_status")
            .select(
                "person_detected, lying_detected, temperature, humidity, updated_at," +
                    "ai_sensitivity, ac_on, ac_manual, sleep_on, sleep_manual, sleep_delay_sec"
            )
            .eq("device_id", DEVICE_ID)
            .maybeSingle();

        if (error) {
            render(null);
            return;
        }
        render(data);
    }

    (async function start() {
        const client = await getSupabaseClient();
        poll(client);
        setInterval(() => poll(client), POLL_INTERVAL_MS);
    })();
})();
