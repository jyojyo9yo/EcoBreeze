"""
dashboard.py

ecobreeze_main.py와는 별도 프로세스로 띄우는 실시간 웹 대시보드 + 수동 제어판.
대회 시연용: 실제 에어컨이 없어도 라즈베리파이가 계산 중인 쾌적대/제어 판단을
브라우저 화면으로 보여주고, 버튼으로 취침 시작/종료·override 시뮬레이션을
트리거할 수 있게 한다.

의존성 없음(표준 라이브러리 http.server만 사용) — 대회장 인터넷이 없어도 동작.
실행: python dashboard.py  ->  http://<라즈베리파이IP>:8080
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EcoBreeze 실시간 대시보드</title>
<style>
  :root {
    color-scheme: light;
    --surface-1:      #fcfcfb;
    --page:           #f9f9f7;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --grid:           #e1e0d9;
    --baseline:       #c3c2b7;
    --border:         rgba(11,11,11,0.10);
    --series-temp:    #2a78d6;
    --band-fill:      rgba(137,135,129,0.16);
    --status-good:    #0ca30c;
    --status-off:     #898781;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --surface-1:      #1a1a19;
      --page:           #0d0d0d;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --grid:           #2c2c2a;
      --baseline:       #383835;
      --border:         rgba(255,255,255,0.10);
      --series-temp:    #3987e5;
      --band-fill:      rgba(137,135,129,0.22);
      --status-good:    #0ca30c;
      --status-off:     #6b6a65;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface-1:      #1a1a19;
    --page:           #0d0d0d;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #898781;
    --grid:           #2c2c2a;
    --baseline:       #383835;
    --border:         rgba(255,255,255,0.10);
    --series-temp:    #3987e5;
    --band-fill:      rgba(137,135,129,0.22);
    --status-good:    #0ca30c;
    --status-off:     #6b6a65;
  }

  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; overflow-x: hidden; }
  body {
    background: var(--page);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    padding: 20px;
  }
  h1 { font-size: 1.25rem; margin: 0 0 4px; }
  .subtitle { color: var(--text-secondary); font-size: 0.85rem; margin: 0 0 20px; }

  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 10px;
    margin-bottom: 18px;
  }
  .tile {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
  }
  .tile .label { font-size: 0.72rem; color: var(--text-muted); }
  .tile .value {
    font-size: 1.4rem;
    font-weight: 600;
    margin-top: 2px;
    font-variant-numeric: tabular-nums;
  }
  .tile .value.status-good { color: var(--status-good); }
  .tile .value.status-off { color: var(--status-off); }

  .panel {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    margin-bottom: 18px;
    max-width: 100%;
    overflow-x: auto;
  }
  .panel h2 { font-size: 0.9rem; margin: 0 0 10px; color: var(--text-secondary); }

  svg { display: block; max-width: 100%; }
  .grid-line { stroke: var(--grid); stroke-width: 1; }
  .baseline { stroke: var(--baseline); stroke-width: 1; }
  .band-fill { fill: var(--band-fill); }
  .temp-line { fill: none; stroke: var(--series-temp); stroke-width: 2; stroke-linecap: round; }
  .axis-label { fill: var(--text-muted); font-size: 10px; }
  .end-label { fill: var(--text-primary); font-size: 11px; font-weight: 600; }

  .controls { display: flex; gap: 10px; flex-wrap: wrap; }
  button {
    font: inherit;
    font-size: 0.85rem;
    padding: 9px 14px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface-1);
    color: var(--text-primary);
    cursor: pointer;
  }
  button:hover { background: var(--band-fill); }
  button.primary { background: var(--series-temp); color: #fff; border-color: transparent; }
  button.selected { background: var(--series-temp); color: #fff; border-color: transparent; }
  .panel p.hint { color: var(--text-muted); font-size: 0.8rem; margin: 8px 0 0; }

  ul.events { list-style: none; margin: 0; padding: 0; font-size: 0.82rem; }
  ul.events li {
    padding: 6px 0;
    border-bottom: 1px solid var(--grid);
    color: var(--text-secondary);
    display: flex; gap: 8px;
  }
  ul.events li:last-child { border-bottom: none; }
  ul.events .kind { color: var(--text-primary); font-weight: 600; min-width: 140px; }
</style>
</head>
<body>
  <h1>EcoBreeze 실시간 대시보드</h1>
  <p class="subtitle" id="subtitle">불러오는 중...</p>

  <div class="tiles" id="tiles"></div>

  <div class="panel">
    <h2>실내온도 &amp; 쾌적대 밴드</h2>
    <svg id="chart" width="720" height="220" viewBox="0 0 720 220"></svg>
  </div>

  <div class="panel">
    <h2>수동 제어 (시연용)</h2>
    <div class="controls">
      <button class="primary" onclick="post('/api/sleep/start')">취침 시작</button>
      <button onclick="post('/api/sleep/end')">취침 종료</button>
      <button onclick="post('/api/override', {delta: -1.0})">override: 더 시원하게(-1°C)</button>
      <button onclick="post('/api/override', {delta: 1.0})">override: 더 따뜻하게(+1°C)</button>
    </div>
  </div>

  <div class="panel">
    <h2>온도 민감도 설문</h2>
    <div class="controls" id="sensitivity-controls">
      <button data-level="sensitive" onclick="setSensitivity('sensitive')">민감함 (±0.5°C)</button>
      <button data-level="normal" onclick="setSensitivity('normal')">보통 (±1.0°C)</button>
      <button data-level="insensitive" onclick="setSensitivity('insensitive')">둔감함 (±1.5°C)</button>
    </div>
    <p class="hint">쾌적대 "폭"은 override로 자동 학습하지 않고, 이 설문으로 직접 정한다.
    override는 쾌적대 "중심"(더 시원하게/따뜻하게)만 이동시킨다.</p>
  </div>

  <div class="panel">
    <h2>최근 이벤트</h2>
    <ul class="events" id="events"></ul>
  </div>

<script>
const history = []; // {t, temp, lo, hi}
const MAX_POINTS = 240;

async function post(path, body) {
  await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
}

async function setSensitivity(level) {
  await post('/api/sensitivity', {level});
}

function renderSensitivityButtons(level) {
  document.querySelectorAll('#sensitivity-controls button').forEach(btn => {
    btn.classList.toggle('selected', btn.dataset.level === level);
  });
}

const SENSITIVITY_LABEL = {sensitive: '민감함 (±0.5°C)', normal: '보통 (±1.0°C)', insensitive: '둔감함 (±1.5°C)'};

function tile(label, value, cls) {
  return `<div class="tile"><div class="label">${label}</div>` +
         `<div class="value ${cls||''}">${value}</div></div>`;
}

function renderTiles(s) {
  const comp = s.compressor_on
    ? tile('컴프레서', 'ON', 'status-good')
    : tile('컴프레서', 'OFF', 'status-off');
  document.getElementById('tiles').innerHTML = [
    tile('상태', s.state ?? '-'),
    tile('실내온도', s.t_room != null ? s.t_room.toFixed(1) + '°C' : '-'),
    tile('습도', s.rh != null ? s.rh.toFixed(0) + '%' : '-'),
    tile('밴드', (s.band_lo != null) ? `${s.band_lo.toFixed(1)}~${s.band_hi.toFixed(1)}°C` : '-'),
    comp,
    tile('halfWidth', s.half_width != null ? s.half_width.toFixed(2) + '°C' : '-'),
    tile('민감도', SENSITIVITY_LABEL[s.sensitivity] ?? (s.sensitivity ?? '-')),
    tile('개인보정(중심)', s.personal_offset != null ? s.personal_offset.toFixed(2) + '°C' : '-'),
    tile('경과시간', s.elapsed_min != null ? s.elapsed_min.toFixed(1) + '분' : '-'),
    tile('세션', s.session_count ?? '-'),
  ].join('');
}

function renderEvents(events) {
  if (!events) return;
  const items = events.slice().reverse().map(e => {
    const t = new Date(e.ts * 1000).toLocaleTimeString('ko-KR');
    return `<li><span class="kind">${e.kind}</span><span>${t} — ${e.detail}</span></li>`;
  });
  document.getElementById('events').innerHTML = items.join('');
}

function renderChart() {
  const svg = document.getElementById('chart');
  const W = 720, H = 220, PAD = 34;
  if (history.length < 2) { svg.innerHTML = ''; return; }

  const temps = history.map(p => p.temp);
  const los = history.map(p => p.lo).filter(v => v != null);
  const his = history.map(p => p.hi).filter(v => v != null);
  const all = temps.concat(los, his);
  const yMin = Math.min(...all) - 0.5;
  const yMax = Math.max(...all) + 0.5;

  const x = i => PAD + (i / (MAX_POINTS - 1)) * (W - PAD * 2);
  const y = v => H - PAD - ((v - yMin) / (yMax - yMin)) * (H - PAD * 2);

  let parts = [];
  // gridlines (4 horizontal)
  for (let g = 0; g <= 4; g++) {
    const gy = PAD + g * (H - PAD * 2) / 4;
    parts.push(`<line class="grid-line" x1="${PAD}" y1="${gy}" x2="${W-PAD}" y2="${gy}"/>`);
  }

  // band area (lo/hi) — offset history to right-align (latest point at W-PAD)
  const offset = MAX_POINTS - history.length;
  const bandPts = history.map((p, i) => [x(i + offset), p.lo, p.hi])
                          .filter(p => p[1] != null && p[2] != null);
  if (bandPts.length > 1) {
    const top = bandPts.map(p => `${p[0]},${y(p[2])}`).join(' ');
    const bot = bandPts.slice().reverse().map(p => `${p[0]},${y(p[1])}`).join(' ');
    parts.push(`<polygon class="band-fill" points="${top} ${bot}"/>`);
  }

  const linePts = history.map((p, i) => `${x(i + offset)},${y(p.temp)}`).join(' ');
  parts.push(`<polyline class="temp-line" points="${linePts}"/>`);

  const last = history[history.length - 1];
  parts.push(`<text class="end-label" x="${W-PAD+4}" y="${y(last.temp)+4}">${last.temp.toFixed(1)}</text>`);
  parts.push(`<line class="baseline" x1="${PAD}" y1="${H-PAD}" x2="${W-PAD}" y2="${H-PAD}"/>`);

  svg.innerHTML = parts.join('');
}

async function tick() {
  try {
    const res = await fetch('/api/state');
    const s = await res.json();
    if (s && s.ts) {
      document.getElementById('subtitle').textContent =
        `${s.simulation_mode ? '[시뮬레이션 모드] ' : ''}마지막 갱신: ${new Date(s.ts*1000).toLocaleTimeString('ko-KR')}`;
      renderTiles(s);
      renderEvents(s.recent_events);
      renderSensitivityButtons(s.sensitivity);
      if (s.t_room != null) {
        history.push({t: s.ts, temp: s.t_room, lo: s.band_lo, hi: s.band_hi});
        if (history.length > MAX_POINTS) history.shift();
      }
      renderChart();
    }
  } catch (e) {
    document.getElementById('subtitle').textContent = '서버에 연결할 수 없습니다.';
  }
}

tick();
setInterval(tick, 1000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 콘솔 스팸 방지 (필요시 주석 해제)

    def _send(self, code, body: bytes, content_type="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_live_state(self) -> str:
        """
        ecobreeze_main.py가 매 사이클 live_state.json을 임시파일+os.replace로
        덮어쓰는데, 그 순간과 이 read가 겹치면(특히 Windows) 간헐적으로
        PermissionError가 날 수 있다. 요청 스레드 하나가 죽는 걸 막기 위해
        짧게 재시도하고, 그래도 안 되면 빈 상태를 반환한다 (다음 폴링에서 복구됨).
        """
        for _ in range(3):
            try:
                if not os.path.exists(config.LIVE_STATE_FILE):
                    return "{}"
                with open(config.LIVE_STATE_FILE, "r", encoding="utf-8") as f:
                    return f.read()
            except (PermissionError, OSError):
                time.sleep(0.02)
        return "{}"

    def do_GET(self):
        if self.path == "/":
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            body = self._read_live_state()
            self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")
        else:
            self._send(404, b"not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}

        if self.path == "/api/sleep/start":
            open(config.TRIGGER_SLEEP_START_FILE, "w").close()
            self._send(200, b'{"ok": true}', "application/json")
        elif self.path == "/api/sleep/end":
            open(config.TRIGGER_SLEEP_END_FILE, "w").close()
            self._send(200, b'{"ok": true}', "application/json")
        elif self.path == "/api/override":
            with open(config.CMD_OVERRIDE_SIM_FILE, "w", encoding="utf-8") as f:
                json.dump({"delta": payload.get("delta", -1.0)}, f)
            self._send(200, b'{"ok": true}', "application/json")
        elif self.path == "/api/sensitivity":
            level = payload.get("level", config.DEFAULT_SENSITIVITY)
            if level not in config.SENSITIVITY_PRESETS:
                self._send(400, b'{"ok": false, "error": "unknown level"}', "application/json")
                return
            with open(config.CMD_SET_SENSITIVITY_FILE, "w", encoding="utf-8") as f:
                json.dump({"level": level}, f)
            self._send(200, b'{"ok": true}', "application/json")
        else:
            self._send(404, b"not found")


def run_in_background() -> ThreadingHTTPServer:
    """다른 스크립트(예: 통합 실행용 launcher)에서 임포트해 백그라운드 스레드로
    띄우고 싶을 때 사용. 서버 인스턴스를 반환하며, serve_forever()는 이미
    별도 스레드에서 돌고 있으므로 호출측은 그냥 계속 자기 할 일을 하면 된다."""
    server = ThreadingHTTPServer((config.DASHBOARD_HOST, config.DASHBOARD_PORT), Handler)
    print(f"[Dashboard] http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT} 에서 서비스 중")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == "__main__":
    server = ThreadingHTTPServer((config.DASHBOARD_HOST, config.DASHBOARD_PORT), Handler)
    print(f"[Dashboard] http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT} 에서 서비스 중")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Dashboard] 종료.")
        server.shutdown()
