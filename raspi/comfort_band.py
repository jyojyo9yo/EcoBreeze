"""
comfort_band.py

"① 쾌적대 파악" 담당 모듈.
쾌적대 "중심"은 PMV(config.PMV_CENTER_TARGET)로 습도를 반영해 계산하고,
"폭"은 config.SENSITIVITY_PRESETS에서 고른 값(°C, 직접 설정)을 그대로 쓴다.
여기에 개인 override 학습(중심 이동 personal_offset)과 취침 리듬 보정을
얹어 최종 [lo, hi] 밴드를 산출한다.

폭을 PMV로 역산하지 않는 이유: PMV는 "-3~+3" 사이의 무차원 체감지수라
PMV 지수 1단위가 실제 몇 °C인지는 습도·옷차림에 따라 달라진다 (예:
ISO 7730 Class B "±0.5"는 "±0.5°C"가 아니라, 조건에 따라 ±1°C가 넘기도
한다). 사용자 입장에서 "밴드폭 ±1°C"가 훨씬 직관적이라 폭은 °C로 직접
설정하게 했다.

밴드 "폭"은 override로도 자동 학습하지 않는다 — override 몇 번으로 "이
사람이 온도에 얼마나 민감한지"를 추정하는 건 수렴이 느리고 불안정했다.
대신 사용자가 대시보드 설문(민감/보통/둔감)으로 직접 폭을 고른다
(set_sensitivity). 밴드 "중심"만 override로 학습한다 — override는
"지금 이 순간 어느 쪽으로 불편했는지"를 바로 알려주는 신호라 학습이
빠르고 안정적이다.

personal_offset/sensitivity 등은 JSON 파일(config.STATE_FILE)에 영구 저장되어
재시작 후에도 유지된다 (ESP32 버전의 NVS에 해당).
"""

import json
import os

import config
from pmv import solve_ta_for_target_pmv


class ComfortBand:
    def __init__(self, state_path: str = config.STATE_FILE):
        self.state_path = state_path
        self.personal_offset = 0.0
        self.sensitivity = config.DEFAULT_SENSITIVITY
        self._load()

    # ── 영구 저장 ─────────────────────────────────────
    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.personal_offset = data.get("personal_offset", 0.0)
            self.sensitivity = data.get("sensitivity", config.DEFAULT_SENSITIVITY)

    def _save(self):
        data = {
            "personal_offset": self.personal_offset,
            "sensitivity": self.sensitivity,
        }
        tmp_path = self.state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.state_path)  # 원자적 교체 (중간에 정전 등 대비)

    # ── 민감도 설문 (대시보드에서 호출) ────────────────
    def set_sensitivity(self, level: str) -> bool:
        if level not in config.SENSITIVITY_PRESETS:
            return False
        self.sensitivity = level
        self._save()
        return True

    @property
    def half_width_c(self) -> float:
        """설문으로 고른 반폭(°C). PMV 역산이 아니라 config에 직접 적힌 값."""
        return config.SENSITIVITY_PRESETS[self.sensitivity]

    # ── 취침 리듬 보정 ─────────────────────────────────
    def _rhythm_correction(self, elapsed_min: float) -> float:
        """
        0~90분(입면기): -0.3C
        90분~5시간(심부수면): -0.3 -> +0.5C 로 선형 증가 (심부체온 하강 반영)
        5시간~기상: +0.5C 유지
        """
        if elapsed_min <= 90:
            return -0.3
        elif elapsed_min <= 300:
            t = (elapsed_min - 90) / (300 - 90)
            return -0.3 + t * (0.5 - (-0.3))
        else:
            return 0.5

    # ── 쾌적대 중심 (PMV로 습도 반영해 매 사이클 재계산) ──
    def _baseline_center(self, rh: float) -> float:
        return solve_ta_for_target_pmv(
            config.PMV_CENTER_TARGET, rh, config.PMV_AIR_VEL, config.PMV_MET, config.PMV_CLO,
            config.PMV_SOLVE_MIN_TA, config.PMV_SOLVE_MAX_TA, config.PMV_SOLVE_EPS,
        )

    # ── 밴드 산출 ─────────────────────────────────────
    def get_band(self, rh: float, elapsed_min: float):
        baseline_center = self._baseline_center(rh)

        center = baseline_center + self.personal_offset + self._rhythm_correction(elapsed_min)
        center = min(max(center, config.SAFE_CENTER_MIN_C), config.SAFE_CENTER_MAX_C)

        half_width = max(self.half_width_c, config.MIN_HALFWIDTH_C)

        lo = center - half_width
        hi = center + half_width
        lo = max(lo, config.ABS_MIN_TEMP_C)
        hi = min(hi, config.ABS_MAX_TEMP_C)
        return lo, hi

    # ── override 학습 (중심 이동만) ────────────────────
    def on_override_detected(self, delta_c: float, elapsed_min: float):
        """
        delta_c: 사용자가 원하는 방향/크기 (예: -1.0 = 1도 더 시원하게 원함)
        override는 "지금 이 순간 불편했다"는 신호이므로 쾌적대 중심을
        그 방향으로 이동시킨다. 밴드폭은 건드리지 않는다 (설문으로만 조정).
        """
        self.personal_offset += config.OVERRIDE_LEARNING_RATE * delta_c
        self._save()
