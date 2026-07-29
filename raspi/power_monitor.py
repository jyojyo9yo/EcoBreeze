"""
power_monitor.py

PZEM-004T v3.0 (Modbus RTU, USB-TTL 변환기로 연결) 읽기 래퍼.
전압/전류/전력/누적전력량/주파수/역률을 그대로 읽어오고, 에너지 카운터 리셋과
돌입전류 근사 포착(연속 폴링) 기능을 제공한다.

이 모듈이 EcoBreeze_Logic_Design.md 6절("캘리브레이션")의 실측 인프라다:
tau_ac는 ecobreeze_main.py에서 이 클래스의 read_measurements()와 온도이력을
같이 보며 판단하고, 여기서는 순수 전력계 I/O만 담당한다.
"""

import time

try:
    import serial
except ImportError:
    serial = None

import config


class PZEM004T:
    """
    PZEM/USB-시리얼이 연결되어 있지 않아도(예: 대회 시연 중 실제 에어컨 없이
    시뮬레이션만 돌릴 때) 프로그램이 죽지 않도록, 포트 오픈 실패 시
    self.available=False로 남기고 이후 호출은 전부 None/False를 반환한다.
    """

    def __init__(self, port: str = config.PZEM_PORT, baud: int = config.PZEM_BAUD,
                 addr: int = config.PZEM_SLAVE_ADDR, timeout: float = 1.0):
        self.addr = addr
        self.ser = None
        self.available = False
        if serial is None:
            return
        try:
            self.ser = serial.Serial(port, baud, timeout=timeout)
            self.available = True
        except (OSError, serial.SerialException):
            print(f"[PZEM] {port} 연결 실패 — 전력 측정 없이 계속 진행 (시뮬레이션/데모 모드)")

    def close(self):
        if self.ser:
            self.ser.close()

    @staticmethod
    def _crc16(data: bytes) -> bytes:
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return bytes([crc & 0xFF, (crc >> 8) & 0xFF])

    def _send_frame(self, payload: bytes) -> bytes:
        frame = payload + self._crc16(payload)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        return frame

    def read_measurements(self):
        """전압(V)/전류(A)/전력(W)/누적전력량(Wh)/주파수(Hz)/역률을 dict로 반환.
        통신 실패/CRC 불일치/미연결 시 None."""
        if not self.available:
            return None
        req = bytes([self.addr, 0x04, 0x00, 0x00, 0x00, 0x0A])  # 레지스터 0~9, 10개
        self._send_frame(req)
        resp = self.ser.read(25)  # addr+func+bytecount+20byte data+2byte crc
        if len(resp) < 25:
            return None
        if resp[0] != self.addr or resp[1] != 0x04 or resp[2] != 20:
            return None

        body, crc_recv = resp[:-2], resp[-2:]
        if self._crc16(body) != crc_recv:
            return None

        data = resp[3:23]
        voltage = int.from_bytes(data[0:2], "big") / 10.0
        current = (int.from_bytes(data[2:4], "big")
                   + (int.from_bytes(data[4:6], "big") << 16)) / 1000.0
        power = (int.from_bytes(data[6:8], "big")
                 + (int.from_bytes(data[8:10], "big") << 16)) / 10.0
        energy_wh = (int.from_bytes(data[10:12], "big")
                     + (int.from_bytes(data[12:14], "big") << 16))
        freq = int.from_bytes(data[14:16], "big") / 10.0
        pf = int.from_bytes(data[16:18], "big") / 100.0
        alarm = int.from_bytes(data[18:20], "big")

        return {
            "voltage_v": voltage,
            "current_a": current,
            "power_w": power,
            "energy_wh": energy_wh,
            "freq_hz": freq,
            "power_factor": pf,
            "alarm": alarm != 0,
        }

    def reset_energy(self) -> bool:
        """누적 전력량 카운터를 0으로 리셋 (야간 kWh 로깅 시작 전 호출)."""
        if not self.available:
            return False
        frame = self._send_frame(bytes([self.addr, 0x42]))
        resp = self.ser.read(4)
        return resp == frame

    def sample_inrush(self, duration_sec: float = 2.0, interval_sec: float = 0.15):
        """
        호출 직후부터 duration_sec 동안 순시전류를 반복 폴링해 최댓값을 근사 포착.

        주의: PZEM-004T는 UART 왕복 통신이라 1~2초 사이의 돌입전류 파형을
        정밀하게 잡기 어렵다 (설계문서 6절 참고). 여기서는 "근사치"만 제공하며,
        진짜 서브초 단위 파형이 필요하면 별도 CT+비교기 피크검출 회로가 필요하다.
        """
        samples = []
        t0 = time.time()
        while time.time() - t0 < duration_sec:
            m = self.read_measurements()
            if m:
                samples.append((time.time() - t0, m["current_a"]))
            time.sleep(interval_sec)
        if not samples:
            return None, []
        peak = max(c for _, c in samples)
        return peak, samples
