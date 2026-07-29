"""
ac_simulator.py

대회 현장에 실제 에어컨이 없을 때, 제어 로직(① 쾌적대 파악 → ② 변동폭 최대화)이
올바르게 동작하는지 보여주기 위한 가상 방 열모델.

상수 속도(constant-rate) 모델 — "실내 온도 취침.csv" 실측 데이터에서 직접
측정한 냉각/승온 속도를 그대로 쓴다:
  - 압축기 정지 중: SIM_WARM_RATE_C_PER_MIN 속도로 온도 상승
  - 압축기 가동 중, tau_ac(SIM_TAU_AC_SEC) 지난 뒤부터: SIM_COOL_RATE_C_PER_MIN
    속도로 온도 하강
예전엔 뉴턴 냉각(목표온도로 점근 수렴)을 썼는데, 실측 사이클의 진폭이 좁아서
(약 0.5°C) 곡률이 거의 안 보였다 — 그 범위 안에서는 상수 속도가 실측과 더
가깝고, 목표온도처럼 관측되지 않은 값을 지어낼 필요도 없다. 이 방을 폭주 없이
가둬주는 건 물리 모델이 아니라 위의 밴드 제어 로직(컴프레서 on/off)이다.

SensorLike 인터페이스(.read() -> (temp_c, rh) 또는 None)를 그대로 흉내 내서
ecobreeze_main.py가 실제 SHT31과 동일하게 다룰 수 있게 했다.
"""

import random
import time

import config


class SimulatedRoom:
    def __init__(self,
                 initial_temp: float = config.SIM_INITIAL_TEMP_C,
                 initial_rh: float = config.SIM_INITIAL_RH,
                 cool_rate_per_min: float = config.SIM_COOL_RATE_C_PER_MIN,
                 warm_rate_per_min: float = config.SIM_WARM_RATE_C_PER_MIN,
                 tau_ac_sec: float = config.SIM_TAU_AC_SEC):
        self.temp = initial_temp
        self.rh = initial_rh
        self.cool_rate = cool_rate_per_min
        self.warm_rate = warm_rate_per_min
        self.tau_ac_sec = tau_ac_sec

        self.compressor_on = False
        self.compressor_on_since_sim = None
        self._last_sim_time = None

    def set_compressor(self, on: bool, sim_now: float):
        if on and not self.compressor_on:
            self.compressor_on_since_sim = sim_now
        self.compressor_on = on

    def step(self, sim_now: float):
        """sim_now: 시뮬레이션 경과시각(초). 반환: (temp_c, rh)."""
        if self._last_sim_time is None:
            self._last_sim_time = sim_now
            return self.temp, self.rh

        dt_min = (sim_now - self._last_sim_time) / 60.0
        self._last_sim_time = sim_now
        if dt_min <= 0:
            return self.temp, self.rh

        if self.compressor_on:
            on_since = self.compressor_on_since_sim if self.compressor_on_since_sim is not None else sim_now
            elapsed_on_sec = sim_now - on_since
            if elapsed_on_sec >= self.tau_ac_sec:
                self.temp -= self.cool_rate * dt_min  # 기동지연 이후: 실측 냉각속도로 하강
            # 기동지연 구간: 전력은 소모되지만 냉방효과 미미 (문서 Ⅲ장) — 온도 변화 없음
        else:
            self.temp += self.warm_rate * dt_min  # 정지 중: 실측 상승속도로 데워짐

        self.rh += random.uniform(-0.3, 0.3)
        self.rh = min(max(self.rh, 40.0), 90.0)
        return self.temp, self.rh


class SimClock:
    """실제시간 1초당 config.SIM_TIME_SCALE 시뮬레이션초가 흐르는 가속 시계.
    SIMULATION_MODE=False면 scale=1.0으로 그냥 실제시간과 동일하게 동작한다."""

    def __init__(self, scale: float = 1.0):
        self.scale = scale
        self._real_start = time.time()
        self._sim_start = 0.0

    def now(self) -> float:
        return self._sim_start + (time.time() - self._real_start) * self.scale

    def reset(self, sim_start: float = 0.0):
        self._real_start = time.time()
        self._sim_start = sim_start


class SimulatedSensor:
    """SHT31과 동일한 .read() 인터페이스를 제공하는 가짜 센서.
    room.step()은 ecobreeze_main의 제어 루프가 compressor 상태를 반영한 뒤 호출한다."""

    def __init__(self, room: SimulatedRoom, clock: SimClock):
        self.room = room
        self.clock = clock

    def read(self):
        return self.room.step(self.clock.now())
