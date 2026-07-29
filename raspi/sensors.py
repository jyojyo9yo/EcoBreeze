"""
sensors.py

SHT31 온습도 센서 I2C 읽기. smbus2 기반으로 직접 구현해
(adafruit-blinka 스택 없이) 순정 라즈베리파이 OS에서 바로 동작하게 했다.

배선: SDA -> GPIO2(SDA1), SCL -> GPIO3(SCL1), VCC 3.3V, GND
"""

import time

try:
    from smbus2 import SMBus, i2c_msg
except ImportError:
    SMBus = i2c_msg = None  # SIMULATION_MODE에서는 실제 I2C 센서를 쓰지 않으므로 임포트 실패를 허용

import config

_CMD_MEASURE_HIGH_REP = [0x2C, 0x06]  # clock stretching, high repeatability


class SHT31:
    def __init__(self, bus_num: int = config.I2C_BUS, addr: int = config.SHT31_ADDR):
        self.bus_num = bus_num
        self.addr = addr

    @staticmethod
    def _crc8(data: bytes) -> int:
        crc = 0xFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x31) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    def read(self):
        """반환: (temperature_C, relative_humidity_%). 실패 시 None."""
        if SMBus is None:
            return None
        try:
            with SMBus(self.bus_num) as bus:
                write = i2c_msg.write(self.addr, _CMD_MEASURE_HIGH_REP)
                bus.i2c_rdwr(write)
                time.sleep(0.02)  # 고정밀 측정 변환시간 (최대 ~15ms) 여유
                read = i2c_msg.read(self.addr, 6)
                bus.i2c_rdwr(read)
                data = list(read)

            t_raw = (data[0] << 8) | data[1]
            t_crc = data[2]
            rh_raw = (data[3] << 8) | data[4]
            rh_crc = data[5]

            if self._crc8(bytes(data[0:2])) != t_crc or self._crc8(bytes(data[3:5])) != rh_crc:
                return None  # CRC 불일치 — 노이즈/배선 문제, 이번 샘플 버림

            temp_c = -45.0 + 175.0 * (t_raw / 65535.0)
            rh_pct = 100.0 * (rh_raw / 65535.0)
            return temp_c, rh_pct
        except OSError:
            return None  # I2C 통신 실패 (센서 미연결 등) — 호출측에서 재시도/보류 처리
