"""
ecobreeze_main.py

메인 상태머신 + "② 변동폭 최대화" 제어 루프.
IDLE -> SLEEP_ACTIVE(밴드 제어) 또는 CALIBRATION_BASELINE(무개입 관찰) -> IDLE

취침 시작/종료는 파일 트리거로 처리한다(터치 파일 생성 시 세션 시작/종료).
dashboard.py가 버튼 클릭 시 같은 파일을 만들어주므로, 이후 실제 앱/물리버튼으로
바꾸더라도 이 파일의 로직은 그대로 둘 수 있다.

config.SIMULATION_MODE=True(기본값, 대회 시연용)이면 실제 SHT31/IR 대신
ac_simulator의 가상 방 열모델을 사용하고, config.SIM_TIME_SCALE배 가속된
시계로 하룻밤을 몇 분 안에 재현한다.
"""

import csv
import json
import os
import time
from collections import deque
from enum import Enum, auto

import config
from ac_simulator import SimClock, SimulatedRoom, SimulatedSensor
from comfort_band import ComfortBand
from ir_control import IRControl
from power_monitor import PZEM004T
from sensors import SHT31

SESSION_COUNTER_FILE = "session_count.json"
MAX_RECENT_EVENTS = 30

# 리모컨 override 감지용 — EcoBreeze가 직접 보내는 명령이 아니라
# "사용자가 리모컨을 만졌다"를 인식하기 위해 학습해두는 버튼들 (실기기 모드).
OVERRIDE_DELTA_MAP = {
    "temp_down": -1.0,   # 사용자가 온도를 내림 = 더 시원하게 원함
    "temp_up": 1.0,
}


class State(Enum):
    IDLE = auto()
    SLEEP_ACTIVE = auto()
    CALIBRATION_BASELINE = auto()


class TempHistory:
    """최근 config.TEMP_HISTORY_LEN개 (clock_sec, temp) 샘플로 선형 기울기를 계산.
    tau_ac 자동측정(냉방 시작 후 실내온도가 실제로 꺾이는 시점 포착)에 사용."""

    def __init__(self, maxlen: int = config.TEMP_HISTORY_LEN):
        self.buf = deque(maxlen=maxlen)

    def push(self, t_sec: float, temp_c: float):
        self.buf.append((t_sec, temp_c))

    def slope_c_per_min(self):
        if len(self.buf) < 3:
            return None
        n = len(self.buf)
        t0 = self.buf[0][0]
        xs = [(t - t0) / 60.0 for t, _ in self.buf]
        ys = [temp for _, temp in self.buf]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        if den == 0:
            return None
        return num / den


class EcoBreeze:
    def __init__(self):
        self.comfort_band = ComfortBand()
        self.ir = IRControl()
        self.power = PZEM004T()

        self.room = None
        if config.SIMULATION_MODE:
            self.room = SimulatedRoom()
            self.clock = SimClock(scale=config.SIM_TIME_SCALE)
            self.sensor = SimulatedSensor(self.room, self.clock)
            self.control_interval_sec = config.SIM_CONTROL_INTERVAL_SEC
        else:
            self.clock = SimClock(scale=1.0)
            self.sensor = SHT31()
            self.control_interval_sec = config.CONTROL_INTERVAL_SEC

        self.state = State.IDLE
        self.temp_hist = TempHistory()

        self.sleep_start_real_time = None
        self.had_override_tonight = False
        self.compressor_on = False
        self.compressor_since = 0.0

        self.tau_measuring = False
        self.tau_start_time = None
        self.tau_ema = None

        self.session_count = self._load_session_count()
        self.recent_events = deque(maxlen=MAX_RECENT_EVENTS)
        self.last_reading = (None, None)
        self.last_band = (None, None)

    # ── 세션 카운터 (캘리브레이션 야간 스케줄용) ─────────
    def _load_session_count(self):
        if os.path.exists(SESSION_COUNTER_FILE):
            with open(SESSION_COUNTER_FILE, encoding="utf-8") as f:
                return json.load(f).get("count", 0)
        return 0

    def _save_session_count(self):
        with open(SESSION_COUNTER_FILE, "w", encoding="utf-8") as f:
            json.dump({"count": self.session_count}, f)

    # ── 취침 시작/종료 트리거 ─────────────────────────
    def _check_triggers(self):
        if self.state == State.IDLE and os.path.exists(config.TRIGGER_SLEEP_START_FILE):
            os.remove(config.TRIGGER_SLEEP_START_FILE)
            self._start_sleep_session()
        elif self.state != State.IDLE and os.path.exists(config.TRIGGER_SLEEP_END_FILE):
            os.remove(config.TRIGGER_SLEEP_END_FILE)
            self._end_sleep_session()

    def _check_override_sim(self, elapsed_min):
        """대시보드에서 '사용자가 리모컨을 만졌다'를 시뮬레이션하는 버튼 클릭 처리."""
        if not os.path.exists(config.CMD_OVERRIDE_SIM_FILE):
            return
        try:
            with open(config.CMD_OVERRIDE_SIM_FILE, encoding="utf-8") as f:
                req = json.load(f)
            delta = float(req.get("delta", -1.0))
        except (OSError, ValueError, json.JSONDecodeError):
            delta = -1.0
        finally:
            os.remove(config.CMD_OVERRIDE_SIM_FILE)

        if self.state == State.SLEEP_ACTIVE:
            self.comfort_band.on_override_detected(delta, elapsed_min)
            self.had_override_tonight = True
            self._log_event("override_sim", f"delta={delta} elapsed_min={elapsed_min:.1f}")

    def _check_set_sensitivity(self):
        """대시보드 설문(민감/보통/둔감)에서 밴드폭 등급(Class A/B/C)을 바꾸는 요청 처리.
        취침 중이 아니어도 언제든 바꿀 수 있게 상태와 무관하게 매 사이클 확인한다."""
        if not os.path.exists(config.CMD_SET_SENSITIVITY_FILE):
            return
        try:
            with open(config.CMD_SET_SENSITIVITY_FILE, encoding="utf-8") as f:
                req = json.load(f)
            level = req.get("level", config.DEFAULT_SENSITIVITY)
        except (OSError, ValueError, json.JSONDecodeError):
            level = config.DEFAULT_SENSITIVITY
        finally:
            os.remove(config.CMD_SET_SENSITIVITY_FILE)

        if self.comfort_band.set_sensitivity(level):
            self._log_event("sensitivity_changed", f"level={level}")

    def _start_sleep_session(self):
        self.session_count += 1
        is_baseline = (self.session_count % config.CALIBRATION_BASELINE_INTERVAL_NIGHTS == 0)
        self.state = State.CALIBRATION_BASELINE if is_baseline else State.SLEEP_ACTIVE
        self.sleep_start_real_time = time.time()
        self.clock.reset(sim_start=0.0)
        self.had_override_tonight = False
        self.compressor_on = False
        self.compressor_since = self.clock.now()
        self.temp_hist = TempHistory()
        if self.room is not None:
            self.room = SimulatedRoom()
            self.sensor.room = self.room
        self.power.reset_energy()
        self.ir.start_listening()
        self._save_session_count()
        msg = f"취침 세션 시작: {self.state.name} (session #{self.session_count})"
        print(f"[EcoBreeze] {msg}")
        self._log_event("session_start", msg)

    def _end_sleep_session(self):
        self.ir.stop_listening()
        self.ir.send_compressor(False)  # 기상 시 LED/컴프레서 확실히 끄기 (매 사이클 명령과 별개)
        m = self.power.read_measurements()
        energy_wh = m["energy_wh"] if m else None
        self._log_night_summary(energy_wh)
        msg = f"취침 세션 종료 ({self.state.name}), 누적 전력량: {energy_wh} Wh"
        print(f"[EcoBreeze] {msg}")
        self._log_event("session_end", msg)
        self.state = State.IDLE

    # ── 로그 ──────────────────────────────────────────
    def _log_night_summary(self, energy_wh):
        write_header = not os.path.exists(config.NIGHT_LOG_CSV)
        with open(config.NIGHT_LOG_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["session", "mode", "start_ts", "duration_min",
                            "energy_wh", "had_override", "sensitivity", "personal_offset"])
            duration_min = (time.time() - self.sleep_start_real_time) / 60.0
            w.writerow([self.session_count, self.state.name, self.sleep_start_real_time,
                        round(duration_min, 1), energy_wh, self.had_override_tonight,
                        self.comfort_band.sensitivity,
                        round(self.comfort_band.personal_offset, 3)])

    def _log_event(self, kind, detail):
        write_header = not os.path.exists(config.EVENT_LOG_CSV)
        with open(config.EVENT_LOG_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["ts", "session", "kind", "detail"])
            w.writerow([time.time(), self.session_count, kind, detail])
        self.recent_events.append({"ts": time.time(), "kind": kind, "detail": detail})

    # ── 제어 사이클 ("② 변동폭 최대화") ────────────────
    def _control_cycle(self, t_room, rh, elapsed_min):
        lo, hi = self.comfort_band.get_band(rh, elapsed_min)
        self.last_band = (lo, hi)
        now = self.clock.now()

        if self.compressor_on:
            min_on_elapsed = (now - self.compressor_since) >= config.MIN_COMPRESSOR_ON_SEC
            if t_room <= lo + config.HYST_IN_C and min_on_elapsed:
                self._set_compressor(False, now)
                self._log_event("compressor_off", f"t_room={t_room:.2f} lo={lo:.2f} hi={hi:.2f}")
        else:
            min_off_elapsed = (now - self.compressor_since) >= config.MIN_COMPRESSOR_OFF_SEC
            if t_room >= hi - config.HYST_OUT_C and min_off_elapsed:
                self._set_compressor(True, now)
                self._log_event("compressor_on", f"t_room={t_room:.2f} lo={lo:.2f} hi={hi:.2f}")
                self._begin_tau_measurement(now)
        # 밴드 내부에 머무는 동안은 아무 것도 하지 않는다 — 이 "무개입 구간"이
        # 절감의 실체다 (평균온도상승 + 무효냉방구간감소가 여기서 함께 발생,
        # 설계문서 Ⅲ장이 경고하는 이중계산 없이 하나의 조작으로 통합됨).

    def _set_compressor(self, on: bool, now: float):
        """실제 압축기 on/off 명령의 유일한 발신 지점.
        IR로 아두이노(LED 데모) 또는 향후 실제 에어컨에 상태를 알리고,
        시뮬레이션 중이면 가상 방 모델에도 같은 상태를 반영한다."""
        self.compressor_on = on
        self.compressor_since = now
        self.ir.send_compressor(on)
        if self.room is not None:
            self.room.set_compressor(on, now)

    # ── tau_ac 자동 측정 ───────────────────────────────
    def _begin_tau_measurement(self, start_time):
        self.tau_measuring = True
        self.tau_start_time = start_time

    def _check_tau_measurement(self, now):
        if not self.tau_measuring:
            return
        slope = self.temp_hist.slope_c_per_min()
        if slope is not None and slope < -config.TAU_SLOPE_THRESHOLD_C_PER_MIN:
            tau_sec = now - self.tau_start_time
            self.tau_ema = tau_sec if self.tau_ema is None else (0.7 * self.tau_ema + 0.3 * tau_sec)
            self._log_event("tau_ac_measured", f"tau_sec={tau_sec:.1f} ema={self.tau_ema:.1f}")
            self.tau_measuring = False
        elif now - self.tau_start_time > 10 * 60:  # 10분 넘게 못 잡으면 포기 (냉방효과 미미한 케이스)
            self.tau_measuring = False

    # ── 대시보드용 실시간 상태 기록 ────────────────────
    def _write_live_state(self, t_room, rh, elapsed_min):
        lo, hi = self.last_band
        center = (lo + hi) / 2.0 if lo is not None else None
        half_width = (hi - lo) / 2.0 if lo is not None else None
        state = {
            "ts": time.time(),
            "state": self.state.name,
            "session_count": self.session_count,
            "simulation_mode": config.SIMULATION_MODE,
            "elapsed_min": round(elapsed_min, 2) if elapsed_min is not None else None,
            "t_room": round(t_room, 2) if t_room is not None else None,
            "rh": round(rh, 1) if rh is not None else None,
            "band_lo": round(lo, 2) if lo is not None else None,
            "band_hi": round(hi, 2) if hi is not None else None,
            "band_center": round(center, 2) if center is not None else None,
            "compressor_on": self.compressor_on,
            "half_width": round(half_width, 3) if half_width is not None else None,
            "sensitivity": self.comfort_band.sensitivity,
            "personal_offset": round(self.comfort_band.personal_offset, 3),
            "tau_ema_sec": round(self.tau_ema, 1) if self.tau_ema is not None else None,
            "had_override_tonight": self.had_override_tonight,
            "recent_events": list(self.recent_events)[-10:],
        }
        tmp = config.LIVE_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        # Windows에서는 대시보드가 마침 파일을 읽는 순간과 겹치면 os.replace가
        # 간헐적으로 PermissionError를 낼 수 있고(라즈베리파이/Linux에서는 원자적
        # rename이라 발생하지 않음), 같은 작업 디렉터리에서 EcoBreeze 프로세스를
        # 두 개 이상 띄운 경우(개발/테스트 중 흔함) 서로 tmp 파일을 가로채
        # FileNotFoundError가 날 수도 있다. 데모 중 이 한 번의 실패로 전체 루프가
        # 죽으면 안 되므로 짧게 재시도하고, 그래도 안 되면 다음 사이클에 다시 쓰게 넘어간다.
        for attempt in range(5):
            try:
                os.replace(tmp, config.LIVE_STATE_FILE)
                break
            except (PermissionError, FileNotFoundError):
                time.sleep(0.02)

    # ── 메인 루프 ─────────────────────────────────────
    def _run_cycle(self):
        self._check_triggers()
        self._check_set_sensitivity()

        reading = self.sensor.read()
        if reading is None:
            return

        t_room, rh = reading
        self.last_reading = (t_room, rh)
        now = self.clock.now()
        self.temp_hist.push(now, t_room)

        elapsed_min = None
        if self.state in (State.SLEEP_ACTIVE, State.CALIBRATION_BASELINE):
            elapsed_min = now / 60.0

            if self.state == State.SLEEP_ACTIVE:
                self._check_override_sim(elapsed_min)

                override = self.ir.poll_override()
                if override in OVERRIDE_DELTA_MAP:
                    self.comfort_band.on_override_detected(
                        OVERRIDE_DELTA_MAP[override], elapsed_min)
                    self.had_override_tonight = True
                    self._log_event("override", f"{override} elapsed_min={elapsed_min:.1f}")

                self._control_cycle(t_room, rh, elapsed_min)
                self._check_tau_measurement(now)
            # CALIBRATION_BASELINE: 제어 개입 없음, 센서 이력만 기록
            # (에어컨은 사용자가 취침 전 맞춰둔 고정 설정온도로 자체 동작 중)

        self._write_live_state(t_room, rh, elapsed_min)

    def run_forever(self):
        print(f"[EcoBreeze] 시작 (SIMULATION_MODE={config.SIMULATION_MODE}). Ctrl+C로 종료.")
        while True:
            try:
                self._run_cycle()
            except KeyboardInterrupt:
                print("\n[EcoBreeze] 종료.")
                return
            except Exception as e:
                # 시연 도중 한 사이클의 예외 때문에 루프 전체가 죽으면 안 된다 —
                # 로그만 남기고 다음 사이클에서 계속 진행.
                print(f"[EcoBreeze] 사이클 오류 (계속 진행): {e!r}")
            time.sleep(self.control_interval_sec)


if __name__ == "__main__":
    EcoBreeze().run_forever()
