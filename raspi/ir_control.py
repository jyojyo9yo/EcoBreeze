"""
ir_control.py

pigpio 기반 IR 리모컨 학습/재생 모듈 (라즈베리파이).
"엣지 콜백으로 원시 펄스를 캡처하고, 캐리어 변조된 pigpio 웨이브로 재생한다"는
공개적으로 잘 알려진 방식(예: joan2937의 irrp.py)을 따라 직접 구현했다.

학습된 코드는 config.IR_CODES_FILE(JSON)에 저장되며, 취침 중 사용자가 리모컨을
직접 조작(override)하는지 감지하는 데도 동일 코드북을 재사용한다.

사전 준비: `sudo pigpiod` 데몬이 실행 중이어야 한다.
"""

import json
import os
import re
import time

try:
    import pigpio
except ImportError:
    pigpio = None  # 대회 현장에 에어컨/IR 하드웨어가 없을 때도 임포트 자체는 되게 함

import config

CARRIER_FREQ_HZ = 38000
DUTY_CYCLE = 0.33            # IR LED 소비전력/사거리 절충
FRAME_GAP_TIMEOUT_US = 15000  # 이 시간 이상 신호 없으면 한 프레임이 끝난 것으로 판단
GLITCH_FILTER_US = 100
MATCH_TOLERANCE = 0.35        # override 판별용 펄스 길이 허용 오차 비율

# ── 대회 시연용 NEC IR 프로토콜 (Pi -> 아두이노+LED) ──────
# 대회장에 실제 에어컨이 없어 "에어컨 대역"으로 아두이노를 세워 LED로 컴프레서
# 상태를 보여준다. 아두이노는 IRremote 라이브러리로 표준 NEC 프레임을 그대로
# 디코딩하면 되므로, 리모컨을 학습할 필요 없이 이 고정 주소/커맨드만 맞추면 된다.
# (실제 에어컨을 학습해 제어할 땐 기존 learn_button()/send()/send_setpoint()
#  경로를 쓴다 — 이건 그와 별개의, 데모 전용 경로다.)
NEC_ADDRESS = 0xEB            # "EcoBreeze"
NEC_CMD_COMPRESSOR_ON = 0x01
NEC_CMD_COMPRESSOR_OFF = 0x02
NEC_LEADER_MARK_US = 9000
NEC_LEADER_SPACE_US = 4500
NEC_BIT_MARK_US = 562
NEC_ONE_SPACE_US = 1687
NEC_ZERO_SPACE_US = 562


def _nec_pulses(address: int, command: int) -> list:
    """표준 NEC 프레임(리더 + 주소 + 반전주소 + 커맨드 + 반전커맨드, LSB 우선)을
    [on_us, off_us, ...] 펄스 목록으로 만든다. IRremote의 NEC 디코더가 그대로 읽는다."""
    inv_address = (~address) & 0xFF
    inv_command = (~command) & 0xFF

    bits = []
    for byte in (address, inv_address, command, inv_command):
        for i in range(8):
            bits.append((byte >> i) & 1)  # LSB first

    pulses = [NEC_LEADER_MARK_US, NEC_LEADER_SPACE_US]
    for bit in bits:
        pulses.append(NEC_BIT_MARK_US)
        pulses.append(NEC_ONE_SPACE_US if bit else NEC_ZERO_SPACE_US)
    pulses.append(NEC_BIT_MARK_US)  # 마지막 비트의 종료를 알리는 트레일링 마크
    return pulses


class IRControl:
    """
    dry_run=True(기본값은 config.DRY_RUN_IR)이면 pigpio/실제 IR 하드웨어를
    전혀 건드리지 않고, 무엇을 보냈을지 로그만 남긴다. 실제 에어컨/IR 모듈이
    없는 대회 시연에서 제어 로직만 검증할 때 쓴다. pigpio가 설치되지 않은
    환경(예: 개발 PC)에서도 이 모드면 그대로 동작한다.
    """

    def __init__(self, pi: "pigpio.pi" = None,
                 gpio_recv: int = config.PIN_IR_RECV,
                 gpio_send: int = config.PIN_IR_SEND,
                 codes_path: str = config.IR_CODES_FILE,
                 dry_run: bool = None):
        self.dry_run = config.DRY_RUN_IR if dry_run is None else dry_run
        if pigpio is None:
            self.dry_run = True  # pigpio 자체가 없으면 강제로 dry-run

        self.gpio_recv = gpio_recv
        self.gpio_send = gpio_send
        self.codes_path = codes_path
        self.codes = self._load_codes()

        self._capture_buf = []
        self._cb = None  # start_listening()에서만 등록 (평소엔 꺼둠)

        if self.dry_run:
            self.pi = None
            return

        self.pi = pi or pigpio.pi()
        if not self.pi.connected:
            # pigpio 모듈은 설치돼 있지만 pigpiod 데몬이 안 떠 있는 경우
            # (예: 이 배포판에 pigpiod 패키지가 없거나 아직 안 띄운 상태) —
            # 모듈 임포트 실패와 마찬가지로 강제 dry-run으로 내려서 죽지 않게 한다.
            print("[IR] pigpiod에 연결 실패 — dry-run으로 전환 (sudo pigpiod 실행 여부 확인)")
            self.dry_run = True
            self.pi = None
            return
        self.pi.set_mode(self.gpio_recv, pigpio.INPUT)
        self.pi.set_glitch_filter(self.gpio_recv, GLITCH_FILTER_US)
        self.pi.set_mode(self.gpio_send, pigpio.OUTPUT)
        self.pi.write(self.gpio_send, 0)

    # ── 저장/로드 ─────────────────────────────────────
    def _load_codes(self):
        if os.path.exists(self.codes_path):
            with open(self.codes_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_codes(self):
        tmp = self.codes_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.codes, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.codes_path)

    # ── 학습(recording) ───────────────────────────────
    def learn_button(self, name: str, timeout_sec: float = 5.0) -> bool:
        """
        사용자가 원본 리모컨의 버튼(name, 예: "temp_up"/"temp_down")을
        timeout_sec 안에 IR 수신모듈을 향해 누르면 원시 신호를 캡처해
        self.codes[name]에 저장한다. 성공 시 True.
        """
        if self.dry_run:
            print(f"[IR][dry-run] learn_button('{name}') 무시됨 (하드웨어 없음)")
            return False

        edges = []

        def _cb(gpio, level, tick):
            edges.append((level, tick))

        cb = self.pi.callback(self.gpio_recv, pigpio.EITHER_EDGE, _cb)
        start = time.time()
        try:
            while time.time() - start < timeout_sec:
                time.sleep(0.05)
                if edges and self._frame_complete(edges):
                    break
        finally:
            cb.cancel()

        pulses = self._edges_to_pulses(edges)
        if not pulses:
            return False

        self.codes[name] = pulses
        self._save_codes()
        return True

    def _frame_complete(self, edges):
        if len(edges) < 2:
            return False
        last_tick = edges[-1][1]
        return pigpio.tickDiff(last_tick, self.pi.get_current_tick()) > FRAME_GAP_TIMEOUT_US

    def _edges_to_pulses(self, edges):
        """(level, tick) 엣지 목록을 [on_us, off_us, on_us, ...] 펄스 길이 목록으로 변환."""
        if len(edges) < 2:
            return []
        return [pigpio.tickDiff(edges[i - 1][1], edges[i][1]) for i in range(1, len(edges))]

    # ── 재생(playback) ────────────────────────────────
    def send(self, name: str) -> bool:
        if self.dry_run:
            print(f"[IR][dry-run] send('{name}')")
            return True
        pulses = self.codes.get(name)
        if not pulses:
            return False
        self._transmit(pulses)
        return True

    def send_setpoint(self, target_c: float) -> bool:
        """
        학습 시 "cool_at_23", "cool_at_24" ... 형식으로 몇 개의 고정 설정온도
        매크로를 미리 등록해두면, 그중 target_c에 가장 가까운 것을 재생한다.

        실제 에어컨 IR 프로토콜을 디코딩해 임의 온도를 직접 조립하는 것이 아니라
        "몇 개의 학습된 버튼 매크로 중 가장 가까운 것을 고른다"는 v1 근사치다.
        세밀한 제어가 필요하면 추후 IRremoteESP8266류 라이브러리로 기종별 프로토콜을
        직접 인코딩하는 방식으로 교체하는 것을 권장한다 (설계문서 참고).
        """
        if self.dry_run:
            print(f"[IR][dry-run] send_setpoint({target_c:.2f}°C)")
            return True

        candidates = []
        for name in self.codes:
            m = re.match(r"cool_at_(\d+(?:\.\d+)?)$", name)
            if m:
                candidates.append((float(m.group(1)), name))
        if not candidates:
            return False
        candidates.sort(key=lambda x: abs(x[0] - target_c))
        _, name = candidates[0]
        return self.send(name)

    # ── 대회 시연용: NEC 고정 프로토콜로 아두이노+LED에 직접 송신 ──
    def send_nec(self, address: int, command: int) -> bool:
        if self.dry_run:
            print(f"[IR][dry-run] send_nec(addr=0x{address:02X}, cmd=0x{command:02X})")
            return True
        self._transmit(_nec_pulses(address, command))
        return True

    def send_compressor(self, on: bool) -> bool:
        """컴프레서 on/off를 고정 NEC 커맨드로 송신 — 아두이노가 이걸 받아
        LED 상태를 지속적으로 갱신한다 (arduino/ecobreeze_receiver.ino 참고)."""
        cmd = NEC_CMD_COMPRESSOR_ON if on else NEC_CMD_COMPRESSOR_OFF
        return self.send_nec(NEC_ADDRESS, cmd)

    def _transmit(self, pulses):
        """pulses: [on_us, off_us, on_us, ...]. 짝수 인덱스=IR LED on 구간(캐리어 변조),
        홀수 인덱스=off 구간(공백)."""
        wf = []
        on_period_us = 1_000_000.0 / CARRIER_FREQ_HZ
        on_time = on_period_us * DUTY_CYCLE
        off_time = on_period_us - on_time

        for i, dur in enumerate(pulses):
            if i % 2 == 0:
                remaining = dur
                while remaining > 0:
                    t_on = min(on_time, remaining)
                    wf.append(pigpio.pulse(1 << self.gpio_send, 0, int(t_on)))
                    remaining -= t_on
                    if remaining <= 0:
                        break
                    t_off = min(off_time, remaining)
                    wf.append(pigpio.pulse(0, 1 << self.gpio_send, int(t_off)))
                    remaining -= t_off
            else:
                wf.append(pigpio.pulse(0, 1 << self.gpio_send, int(dur)))

        self.pi.wave_add_generic(wf)
        wave_id = self.pi.wave_create()
        self.pi.wave_send_once(wave_id)
        while self.pi.wave_tx_busy():
            time.sleep(0.001)
        self.pi.wave_delete(wave_id)

    # ── override 감지 (SLEEP_ACTIVE 중 폴링) ──────────
    def start_listening(self):
        """백그라운드에서 들어오는 IR 프레임을 계속 캡처해 self._capture_buf에 쌓는다."""
        if self.dry_run:
            return
        if self._cb is None:
            def _cb(gpio, level, tick):
                self._capture_buf.append((level, tick))
            self._cb = self.pi.callback(self.gpio_recv, pigpio.EITHER_EDGE, _cb)

    def stop_listening(self):
        if self.dry_run:
            return
        if self._cb is not None:
            self._cb.cancel()
            self._cb = None

    def poll_override(self):
        """
        누적된 캡처 버퍼에서 완성된 프레임을 꺼내 학습된 코드와 비교.
        일치하는 버튼 이름을 반환하고, 없으면 None. 버퍼는 소비 후 비운다.
        메인 루프에서 매 제어 사이클마다 호출한다.

        dry_run 모드에서는 실제 리모컨 신호가 없으므로 항상 None —
        시연 중 override는 대시보드 버튼으로 별도 트리거한다 (ecobreeze_main 참고).
        """
        if self.dry_run:
            return None
        if not self._capture_buf or not self._frame_complete(self._capture_buf):
            return None

        pulses = self._edges_to_pulses(self._capture_buf)
        self._capture_buf = []

        for name, ref in self.codes.items():
            if self._pulses_match(pulses, ref):
                return name
        return None

    @staticmethod
    def _pulses_match(a, b) -> bool:
        if abs(len(a) - len(b)) > 2:
            return False
        n = min(len(a), len(b))
        for i in range(n):
            if b[i] == 0:
                continue
            if abs(a[i] - b[i]) / b[i] > MATCH_TOLERANCE:
                return False
        return True
