"""
EcoBreeze config.py

라즈베리파이 기준 핀맵/파라미터. firmware/Config.h(ESP32 참고용)와 값은 동일하게
맞췄고, GPIO 번호 체계만 BCM 기준으로 바뀌었다.

clo/met/target_pmv, 데드밴드/tau_ac 초기값은 모두 "실측 전 가정치"이며
EcoBreeze_Logic_Design.md 8절의 현장 보정 대상이다.
"""

# ── GPIO (BCM 번호) ──────────────────────────────────
PIN_IR_RECV = 17        # IR 수신모듈 (VS1838B 등) OUT
PIN_IR_SEND = 18        # IR LED 구동 (PWM 가능 핀 권장)

# ── I2C (SHT31 온습도 센서) ───────────────────────────
I2C_BUS = 1
SHT31_ADDR = 0x44

# ── PZEM-004T (USB-TTL 변환기로 연결) ─────────────────
PZEM_PORT = "/dev/ttyUSB0"
PZEM_BAUD = 9600
PZEM_SLAVE_ADDR = 0xF8   # 공장 기본 주소. 여러 대 연결 시 개별 설정 필요

# ── 상태/학습값 저장 위치 ─────────────────────────────
STATE_FILE = "ecobreeze_state.json"
IR_CODES_FILE = "ir_codes.json"
NIGHT_LOG_CSV = "night_log.csv"          # 야간 요약(모드/kWh/외기온 등)
EVENT_LOG_CSV = "event_log.csv"          # override/컴프레서 on-off 이벤트 로그

# ── 제어 주기 ─────────────────────────────────────────
CONTROL_INTERVAL_SEC = 30
# 단주기 운전 방지 최소 유지시간. "실내 온도 취침.csv" 실측 사이클의 최소
# 지속시간(ON 5분, OFF 5분)에 맞춰 5분으로 잡았다 (기존 3분은 임의 가정치였음).
MIN_COMPRESSOR_ON_SEC = 5 * 60
MIN_COMPRESSOR_OFF_SEC = 5 * 60

# ── 히스테리시스 (밴드 경계 근처 떨림 방지용 여유) ───────
HYST_IN_C = 0.1
HYST_OUT_C = 0.1

# ── PMV 파라미터 (수면 조건 가정치) ───────────────────
PMV_MET = 0.75          # 수면 중 대사량 (met)
PMV_CLO = 1.0           # 여름철 얇은 잠옷+홑이불 근사 열저항 (clo) — 실측 보정 대상.
                        # (예전 2.0은 겨울용 이불 수준이라 과도하게 시원한 쪽으로 치우쳐 있었음)
PMV_AIR_VEL = 0.05      # 침실 정지 공기 (m/s)
PMV_SOLVE_MIN_TA = 15.0
PMV_SOLVE_MAX_TA = 32.0
PMV_SOLVE_EPS = 0.01

# 쾌적대 "중심"만 PMV로 계산한다 (습도를 반영해 "중립적으로 느껴지는 온도"를
# 찾는 용도). PMV_CENTER_TARGET=-0.2는 완전 중립(0)이 아니라 약간 서늘한
# 쪽 — 취침 시 서늘한 편이 입면에 유리하다는 통념 반영.
#
# 주의: PMV는 온도(°C)가 아니라 "-3~+3" 사이의 무차원 체감지수다. 예전엔
# 밴드 "폭"도 PMV 허용범위(예: ISO 7730 Class B ±0.5)로 정했었는데, PMV
# 지수 1단위가 실제 몇 °C에 해당하는지는 습도·옷차림에 따라 달라지고
# 직관적이지도 않아서(예: ±0.5는 "±0.5°C"가 아니라 rh70%·clo1.0 기준 약
# ±1.245°C), 혼란만 컸다. 그래서 폭은 아래처럼 °C로 직접 정한다.
PMV_CENTER_TARGET = -0.2

# 밴드 "폭"은 °C로 직접 설정한다 (더 이상 PMV에서 역산하지 않음).
# 밴드폭은 "override로 자동 학습"하지도 않는다. 그 방식은 개인마다 다른
# 온도 민감도(어디가 편한지 X, 변동을 얼마나 견디는지 O)를 override
# 몇 번만으로 추정해야 해서 수렴이 느리고 불안정했다. 대신 사용자가
# 대시보드 설문으로 자신의 민감도를 직접 고르면, 그게 그대로 반폭(°C)을
# 정한다. "보통" 기본값 1.0°C(전체 2.0°C)는 설계문서 예시("쾌적대 24~26°C")와
# 같은 폭이다. 쾌적대 "중심"만 override로 학습한다 (personal_offset).
SENSITIVITY_PRESETS = {
    "sensitive": 0.5,      # 온도 변화에 민감함 — 반폭 0.5°C (전체 1.0°C)
    "normal": 1.0,         # 보통 (기본값) — 반폭 1.0°C (전체 2.0°C)
    "insensitive": 1.5,    # 온도 변화에 둔감함 — 반폭 1.5°C (전체 3.0°C)
}
DEFAULT_SENSITIVITY = "normal"

# ── 쾌적대(밴드) 파라미터 ─────────────────────────────
MIN_HALFWIDTH_C = 0.2      # 어떤 설정을 골라도 이 아래로는 안 좁아짐 (안전 하한)
OVERRIDE_LEARNING_RATE = 0.3  # override 1회당 personal_offset(중심 이동) 반영 비율

# ── 안전 한계 (학습 결과와 무관하게 항상 적용) ───────────
SAFE_CENTER_MIN_C = 23.0
SAFE_CENTER_MAX_C = 27.0
ABS_MIN_TEMP_C = 22.0
ABS_MAX_TEMP_C = 29.0

# ── 캘리브레이션 ──────────────────────────────────────
TAU_SLOPE_THRESHOLD_C_PER_MIN = 0.05   # 이보다 가파르게 냉각되기 시작하면 tau_ac 확정
TEMP_HISTORY_LEN = 10                   # 슬로프 계산용 최근 샘플 개수
CALIBRATION_BASELINE_INTERVAL_NIGHTS = 7  # 1주에 1박은 개입 없이 순정 데드밴드 관찰

# ── 시연/시뮬레이션 모드 ───────────────────────────────
# 대회 현장에 실제 에어컨이 없다. 두 가지가 서로 다른 이유로 분리되어 있다:
#   SIMULATION_MODE — 실내온도를 잴 "실제 방+에어컨"이 없으므로 가상 열모델
#                      (ac_simulator.py)로 대체. 로직(①②) 검증용.
#   DRY_RUN_IR      — Pi->아두이노(LED) IR 송신 자체는 실제로 나가야 하므로
#                      기본 False. pigpio/IR LED가 없는 개발 PC에서 돌릴 때만
#                      True로 바꾸거나, ir_control.py가 pigpio 미설치를
#                      감지하면 자동으로 dry-run으로 전환된다.
SIMULATION_MODE = True
DRY_RUN_IR = False

SIM_TIME_SCALE = 60.0            # 1 실제초 = 60 시뮬레이션초 (8시간 밤을 약 8분에 시연)
SIM_CONTROL_INTERVAL_SEC = 1.0    # 시뮬레이션 중 대시보드 갱신 주기 (실제초)
SIM_INITIAL_TEMP_C = 29.0        # "실내 온도 취침.csv" 첫 샘플(02:04, 29.1°C)과 일치
SIM_INITIAL_RH = 70.0

# 냉각/승온 속도 — "실내 온도 취침.csv"(2026-07-25 취침 실측, 02:04~13:17,
# 673분)의 정상상태 구간(03:00 이후)을 피크/트로프로 나눠 직접 측정한 값.
# 예전엔 뉴턴냉각(목표온도로 수렴) 모델에 임의 계수(0.12/0.02, 목표16도)를
# 썼는데, 실측 구간이 좁아(0.5°C대) 곡률이 거의 안 보이는 걸 확인하고
# 상수 속도(constant-rate) 모델로 바꿨다 — 데이터가 실제로 보여주는 형태에
# 더 가깝고, 목표온도 같은 관측 안 된 값을 지어낼 필요도 없다.
#   냉방 중 하강률: 26개 구간 중앙값 0.072°C/min (범위 0.009~0.089)
#   대기 중 상승률: 23개 구간 중앙값 0.060°C/min (범위 0.007~0.076)
SIM_COOL_RATE_C_PER_MIN = 0.072
SIM_WARM_RATE_C_PER_MIN = 0.060
SIM_TAU_AC_SEC = 180             # 시뮬레이션 시간 기준 기동지연(초) — 별도 오후실험 실측치(약 3분) 반영

# ── 대시보드 (실시간 웹 화면 + 수동 제어) ────────────────
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 8080
LIVE_STATE_FILE = "live_state.json"       # main 루프가 매 사이클 기록, 대시보드가 읽음
CMD_OVERRIDE_SIM_FILE = "trigger_override_sim.json"  # 대시보드에서 override 시뮬레이션 요청
CMD_SET_SENSITIVITY_FILE = "trigger_set_sensitivity.json"  # 대시보드 설문에서 민감도 선택 요청

# 취침 시작/종료 트리거 — main 루프와 dashboard.py가 파일로 주고받는 공통 경로.
# v1은 파일 트리거지만, 나중에 앱/물리버튼으로 교체해도 main.py 쪽 로직은 그대로 둘 수 있다.
TRIGGER_SLEEP_START_FILE = "trigger_sleep_start"
TRIGGER_SLEEP_END_FILE = "trigger_sleep_end"
