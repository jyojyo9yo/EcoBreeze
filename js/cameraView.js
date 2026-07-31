(function cameraViewController() {
    // 카메라 위치를 잡을 때 쓰는 실시간 미리보기.
    //
    // 보드는 /capture 로 JPEG 한 장씩만 내려준다 -- MJPEG 스트림 엔드포인트가
    // 없다 (firmware/xiao_esp32s3_person_led 의 server.on 목록 참고). 그래서
    // "실시간"은 그 스냅샷을 타이머로 다시 받아오는 방식이다.
    //
    // 모달이 열려 있는 동안에만 돈다. person_detect.py 가 같은 단일 스레드
    // 웹서버를 이미 폴링하고 있어서, 닫힌 뒤에도 계속 받아오면 요청이 겹쳐
    // capture 타임아웃을 유발한다.
    const HOST_KEY = "ecobreeze_camera_host";
    const DEFAULT_HOST = "ecobreeze-cam.local";
    const FRAME_INTERVAL_MS = 800;
    // 보드가 WiFi에서 떨어지면 요청이 응답도 에러도 없이 매달린다. 그대로 두면
    // pending 이 영원히 안 풀려서 폴링이 멈춘 것처럼 보이므로 직접 끊는다.
    const FRAME_TIMEOUT_MS = 4000;

    const modeBtn = document.getElementById("cameraModeBtn");
    const modal = document.getElementById("cameraModal");
    const closeBtn = document.getElementById("cameraModalClose");
    const frameEl = document.getElementById("cameraFrame");
    const placeholderEl = document.getElementById("cameraPlaceholder");
    const statusEl = document.getElementById("cameraStatus");
    const hostInput = document.getElementById("cameraHostInput");
    const hostApplyBtn = document.getElementById("cameraHostApplyBtn");

    // mDNS 이름이 안 풀리는 경우가 실제로 있고(그럴 때 보드는 살아있는데 이름만
    // 안 잡힌다) DHCP 주소도 가끔 바뀌므로, 주소를 직접 입력할 수 있게 해두고
    // 브라우저에 기억시킨다.
    let host = localStorage.getItem(HOST_KEY) || DEFAULT_HOST;
    let timerId = null;
    let pending = null; // 현재 로딩 중인 detached Image
    let pendingTimeoutId = null;
    let failures = 0;

    function setStatus(text, kind) {
        statusEl.textContent = text;
        statusEl.className = "camera-status" + (kind ? " camera-status-" + kind : "");
    }

    function cancelPending() {
        if (pendingTimeoutId !== null) {
            clearTimeout(pendingTimeoutId);
            pendingTimeoutId = null;
        }
        if (pending) {
            pending.onload = null;
            pending.onerror = null;
            pending.src = ""; // 진행 중인 요청을 브라우저가 버리게 한다
            pending = null;
        }
    }

    function onFailure() {
        failures += 1;
        setStatus(
            host + " 에 연결할 수 없습니다 (실패 " + failures + "회). 주소를 확인하세요.",
            "error"
        );
    }

    function loadFrame() {
        if (pending) return; // 이전 프레임이 아직 안 왔으면 쌓지 않는다

        // 보드가 캐시 헤더를 안 보내서, 캐시 버스터가 없으면 브라우저가 첫
        // 스냅샷을 계속 재사용한다.
        const url = "http://" + host + "/capture?t=" + Date.now();
        const img = new Image();
        pending = img;

        pendingTimeoutId = setTimeout(function () {
            cancelPending();
            onFailure();
        }, FRAME_TIMEOUT_MS);

        img.onload = function () {
            const loaded = img.src;
            cancelPending();
            // 새 프레임이 디코딩까지 끝난 뒤에 갈아끼운다. 보이는 img 에 직접
            // src 를 넣으면 로딩 중 화면이 비어서 깜빡인다.
            frameEl.src = loaded;
            frameEl.classList.remove("hidden");
            placeholderEl.classList.add("hidden");
            failures = 0;
            setStatus("연결됨 · " + new Date().toLocaleTimeString("ko-KR"), "ok");
        };

        img.onerror = function () {
            cancelPending();
            onFailure();
        };

        img.src = url;
    }

    // reset=true 는 "새로 여는" 경우(모달 열기, 주소 변경)이고, false 는 탭
    // 전환에서 돌아와 이어서 받는 경우다. 둘을 구분하지 않으면 탭을 잠깐
    // 다녀올 때마다 에러 메시지가 "연결 중..." 으로 덮이고 실패 횟수도 0으로
    // 돌아가서, 카메라가 계속 죽어 있는데도 매번 처음 시도하는 것처럼 보인다.
    function start(reset) {
        stop();

        // HTTPS 페이지에서 http:// 이미지는 mixed content 로 차단된다. 실패
        // 사유가 "보드가 죽었다"와 구분이 안 되므로 시도 전에 걸러낸다.
        if (location.protocol === "https:") {
            setStatus(
                "HTTPS로 열린 페이지에서는 카메라(HTTP)에 직접 연결할 수 없습니다. " +
                    "카메라와 같은 네트워크에서 http로 대시보드를 열어주세요.",
                "error"
            );
            return;
        }

        if (reset) {
            failures = 0;
            setStatus("연결 중...", null);
        }
        loadFrame();
        timerId = setInterval(loadFrame, FRAME_INTERVAL_MS);
    }

    function stop() {
        if (timerId !== null) {
            clearInterval(timerId);
            timerId = null;
        }
        cancelPending();
    }

    function open() {
        hostInput.value = host;
        modal.classList.remove("hidden");
        start(true);
    }

    function close() {
        modal.classList.add("hidden");
        stop();
    }

    function applyHost() {
        // "http://10.0.0.5/" 처럼 붙여넣어도 받아준다.
        const raw = hostInput.value.trim().replace(/^https?:\/\//i, "").replace(/\/+$/, "");
        if (!raw) return;
        host = raw;
        localStorage.setItem(HOST_KEY, host);
        hostInput.value = host;
        frameEl.classList.add("hidden");
        placeholderEl.classList.remove("hidden");
        start(true);
    }

    modeBtn.addEventListener("click", open);
    closeBtn.addEventListener("click", close);
    modal.addEventListener("click", function (e) {
        if (e.target === modal) close();
    });

    hostApplyBtn.addEventListener("click", applyHost);
    hostInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") applyHost();
    });

    // 탭을 숨기면 브라우저가 타이머를 늦추긴 하지만 요청 자체는 계속 나간다.
    // 보드 부담을 줄이려고 아예 멈춘다.
    document.addEventListener("visibilitychange", function () {
        if (modal.classList.contains("hidden")) return;
        if (document.hidden) {
            stop();
        } else {
            start(false);
        }
    });
})();
