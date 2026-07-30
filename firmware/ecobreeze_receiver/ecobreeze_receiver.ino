/*
 * ecobreeze_receiver.ino
 *
 * 대상 보드: ESP32-S3 (수신기 쪽 보드)
 *
 * 대회 시연용: 대회장에 실제 에어컨이 없으므로, 이 보드를 "에어컨 대역"으로
 * 세운다. 송신 측(카메라 보드 / 라즈베리파이)이 IR로 보내는 명령을 받아
 * 컴프레서 상태 LED를 켜고/끈다.
 *
 * ── 왜 IRremote NEC 디코딩을 쓰지 않는가 (2026-07-30, 실측으로 확정) ──
 * 이 프로젝트의 수신 부품은 3핀 복조 모듈(VS1838B/TSOP 계열)이 아니라 2핀 적외선
 * 포토다이오드(검은 5mm, 940nm)다. 복조 모듈은 내부에 38kHz 밴드패스 + AGC +
 * 복조기가 있어 깨끗한 디지털 HIGH/LOW를 내주지만, 생 포토다이오드는 마이크로암페어급
 * 아날로그 광전류만 낸다. 즉 IRremote가 디코딩할 신호가 하드웨어에서 애초에 만들어지지
 * 않는다 — 송신이 완벽해도 NEC 디코딩은 원리상 동작할 수 없다.
 *
 * 그래서 디코딩 대신 ADC로 광량을 직접 읽고, "IR 있음/없음"만 쓸 수 있는 채널로
 * 보고 명령을 **펄스 개수**로 표현하는 프로토콜을 쓴다. 38kHz 변조도 쓰지 않는다
 * (복조기가 없으니 변조는 신호를 절반으로 깎을 뿐이다 — 송신은 그냥 DC on/off).
 *
 * ── EcoBreeze 광 펄스 프로토콜 v1 ──
 *   펄스 1개 = IR ON 60ms + OFF 60ms
 *   프레임   = 펄스 N개 + 침묵 500ms
 *   명령 값이 그대로 펄스 개수다:
 *     1 = 전원 ON   2 = 전원 OFF   3 = 온도▲   4 = 온도▼
 *     5 = 수면모드 ON   6 = 수면모드 OFF
 *   송신 측 상수(xiao_esp32s3_person_led.ino, ir_tx_test.ino,
 *   raspi/ir_control.py)와 반드시 같은 값을 써야 한다.
 *
 * ── 배선 (실측) ──
 *   3.3V --[100kΩ]--+-- 포토다이오드 (보드로 가는 다리)
 *                   |
 *                  D5 = GPIO6   (ADC 있는 핀이어야 한다)
 *   포토다이오드 반대쪽 다리 -- GND
 *
 *   컴프레서 상태 LED (파란색, +저항 220~330Ω) -> D1 = GPIO2
 *   수면모드 LED (초록색, +저항 220~330Ω)      -> D0 = GPIO1
 *   (선택) 하트비트 LED(+저항) -> D2 = GPIO3 — 보드가 살아있음을 보여주는 용도
 *   (핀 번호는 실측 배선에 맞춘 값이다. 이 프로젝트는 코드의 핀과 실제 배선이
 *    어긋나 여러 번 헤맸으므로, 배선을 바꾸면 반드시 이 상수부터 확인할 것.)
 *
 * 100kΩ이 포토다이오드를 역방향 바이어스하는 것이 핵심이다: 어두울 때 노드가 3.3V로
 * 올라가 있고(ADC 4095), 빛이 오면 광전류가 노드를 끌어내려 값이 떨어진다(실측 ~290).
 * 신호 크기 = 광전류 x 이 저항이므로 저항이 곧 이득이다 — 10kΩ은 너무 작아 안 보였다.
 * 저항을 빼면 바이어스가 없어져 빛이 노드를 0 아래로만 밀고, ADC는 영원히 0을 읽는다.
 *
 * 주의: ESP32에서 analogRead()는 해당 패드를 아날로그로 재설정하면서 내부 풀 저항을
 * 떼어버린다. 그래서 pinMode(pin, INPUT_PULLUP)으로 바이어스를 대신할 수 없고,
 * 위 외부 저항이 반드시 필요하다.
 *
 * Arduino IDE 준비: 보드 매니저에서 esp32(Espressif) 설치, USB CDC On Boot 활성화.
 * 외부 라이브러리는 필요 없다(IRremote 의존성 제거).
 */

const uint8_t IR_RECV_PIN = 6;          // 실크 D5, ADC1_CH5
const uint8_t LED_COMPRESSOR_PIN = 2;   // 실크 D1, 파란색
const uint8_t LED_SLEEP_PIN = 1;        // 실크 D0, 초록색 (실측 배선에 맞춤)
const uint8_t LED_HEARTBEAT_PIN = 3;    // 실크 D2, 미배선이어도 무해

// 프로토콜 상수 — 송신 측과 같아야 함
const uint16_t FRAME_END_MS = 250;      // 이 시간 이상 빛이 없으면 프레임 종료
const uint8_t MAX_PULSES = 8;           // 이보다 많으면 노이즈로 보고 버린다

const uint8_t CMD_POWER_ON = 1;
const uint8_t CMD_POWER_OFF = 2;
const uint8_t CMD_TEMP_UP = 3;
const uint8_t CMD_TEMP_DOWN = 4;
const uint8_t CMD_SLEEP_SET = 5;
const uint8_t CMD_SLEEP_CLEAR = 6;

const unsigned long HEARTBEAT_INTERVAL_MS = 500;

// 어두울 때의 기준값을 부팅 시 실측해 임계값을 상대적으로 잡는다. 조명 환경이
// 달라져도 따라가고, 배선이 잘못돼 기준값이 안 올라오면 바로 경고할 수 있다.
uint16_t darkBaseline = 4095;
uint16_t threshOn = 2047;               // 이 값보다 낮아지면 "빛 있음"
uint16_t threshOff = 3071;              // 다시 이 값보다 높아지면 "빛 없음" (히스테리시스)

bool compressorOn = false;
bool sleepOn = false;
bool irPresent = false;
uint8_t pulseCount = 0;
unsigned long lastEdgeMs = 0;
uint16_t frameMinAdc = 4095;   // 프레임 동안의 최저값 — 임계값까지의 여유를 로그로 보기 위함

// 기준값을 유휴 구간에서 계속 다시 잡는다. 부팅 때 한 번만 재면, 하필 그 순간
// IR이 오고 있었을 때(예: 송신 보드가 부팅하며 초기 상태를 쏘는 중) 기준값이 낮게
// 박혀 이후 감지가 전부 실패한다. 노트북 없이 배터리로만 돌리는 시연에서는 'b'로
// 고칠 수도 없으므로, 스스로 회복하게 만든다. 조명 환경이 바뀌어도 따라간다.
const unsigned long BASELINE_REFRESH_MS = 5000;
const uint16_t BASELINE_MIN_SANE = 500;   // 이보다 낮은 값은 기준값으로 받지 않는다
uint16_t idleMax = 0;
unsigned long lastBaselineRefreshMs = 0;

bool heartbeatState = false;
unsigned long lastHeartbeatMs = 0;

void applyBaseline(uint16_t value) {
  darkBaseline = value;
  threshOn = (uint16_t)((uint32_t)darkBaseline * 80 / 100);
  threshOff = (uint16_t)((uint32_t)darkBaseline * 90 / 100);
}

// 유휴(빛 없음) 구간의 최대값으로 기준값을 5초마다 갱신한다. 빛이 들어와 있는
// 동안은 idleMax에 아무것도 쌓이지 않으므로 신호가 기준값을 오염시키지 않는다.
void refreshBaselineIfIdle() {
  if (millis() - lastBaselineRefreshMs < BASELINE_REFRESH_MS) {
    return;
  }
  lastBaselineRefreshMs = millis();

  if (idleMax >= BASELINE_MIN_SANE && idleMax != darkBaseline) {
    // 5% 이상 움직일 때만 로그를 남긴다 (매 5초 스팸 방지)
    bool notable = (idleMax > darkBaseline ? idleMax - darkBaseline
                                           : darkBaseline - idleMax) > darkBaseline / 20;
    applyBaseline(idleMax);
    if (notable) {
      Serial.printf("[EcoBreeze] 기준값 갱신 -> %u, 임계값 on<%u / off>%u\n",
                    darkBaseline, threshOn, threshOff);
    }
  }
  idleMax = 0;
}

void measureBaseline() {
  // 빛이 값을 끌어내리므로 어두울 때의 기준값은 "최대값"이다.
  uint16_t maxV = 0;
  unsigned long start = millis();
  while (millis() - start < 300) {
    uint16_t v = analogRead(IR_RECV_PIN);
    if (v > maxV) maxV = v;
  }
  // 기준값에서 20% 떨어지면 "빛 있음", 10% 이내로 돌아오면 "빛 없음"으로 본다.
  // 처음엔 50%/75%로 잡았는데 정렬이 약할 때 실측 최저가 2327까지만 내려가
  // (기준값 4095의 57%) 문턱을 못 넘고 전부 놓쳤다. 어두울 때의 노이즈는
  // 4035~4095, 즉 ±1.5% 수준이므로 20%면 노이즈 대비 13배 마진이 남는다 —
  // 감도를 6배 올리면서도 오검출 위험은 사실상 없다 (2026-07-30 실측).
  applyBaseline(maxV);
  idleMax = 0;
  lastBaselineRefreshMs = millis();

  Serial.printf("[EcoBreeze] 기준값(어두울 때)=%u, 임계값 on<%u / off>%u\n",
                darkBaseline, threshOn, threshOff);
  if (darkBaseline < 1000) {
    Serial.println(F("[EcoBreeze] 경고: 기준값이 너무 낮다. 100kΩ 바이어스 저항이"
                     " 3.3V와 센스 노드 사이에 연결됐는지, 센스 핀이 맞는지 확인할 것."));
  }
}

void setup() {
  Serial.begin(115200);
  // 네이티브 USB(CDC) 보드라 시리얼 모니터를 안 열면 Serial이 계속 false다.
  // 무한 대기하면 노트북 없이 배터리로만 돌리는 실제 시연 중 이 지점에서
  // 영원히 멈춰 LED가 하나도 안 켜지므로, 최대 3초만 기다리고 넘어간다.
  unsigned long serialWaitStart = millis();
  while (!Serial && millis() - serialWaitStart < 3000) { /* 대기 */ }

  pinMode(LED_COMPRESSOR_PIN, OUTPUT);
  pinMode(LED_SLEEP_PIN, OUTPUT);
  pinMode(LED_HEARTBEAT_PIN, OUTPUT);
  digitalWrite(LED_COMPRESSOR_PIN, LOW);
  digitalWrite(LED_SLEEP_PIN, LOW);

  measureBaseline();
  Serial.println(F("[EcoBreeze] 수신 대기 중... (b=기준값 재측정, r=원시값 덤프, ?=도움말)"));
}

void loop() {
  handleSerial();
  updateIr();
  updateHeartbeat();
}

void updateIr() {
  uint16_t v = analogRead(IR_RECV_PIN);
  if (v < frameMinAdc) frameMinAdc = v;
  if (!irPresent && pulseCount == 0 && v > idleMax) {
    idleMax = v;      // 프레임 중이 아닐 때만 기준값 후보로 쌓는다
  }
  // 빛이 오면 값이 내려간다. 히스테리시스로 경계에서의 떨림을 막는다.
  bool nowPresent = irPresent ? (v < threshOff) : (v < threshOn);

  if (nowPresent != irPresent) {
    irPresent = nowPresent;
    lastEdgeMs = millis();
    if (!irPresent && pulseCount < 255) {
      pulseCount++;               // 빛이 꺼진 순간 = 펄스 하나가 끝난 것
    }
  }

  if (pulseCount > 0 && !irPresent && millis() - lastEdgeMs > FRAME_END_MS) {
    handleFrame(pulseCount);
    pulseCount = 0;
    frameMinAdc = 4095;
  }

  refreshBaselineIfIdle();
}

void handleFrame(uint8_t pulses) {
  // 수신 세기를 같이 남긴다. 정렬이 약해지면 이 값이 임계값에 붙기 시작하므로,
  // "왜 어떤 프레임만 놓치는가"를 로그만 보고 판단할 수 있다.
  Serial.printf("[EcoBreeze] 프레임 수신: 펄스 %u개, 최저 ADC %u (임계 %u)\n",
                pulses, frameMinAdc, threshOn);

  if (pulses > MAX_PULSES) {
    Serial.printf("[EcoBreeze] 펄스 %u개 — 범위를 넘어 무시(노이즈로 판단)\n", pulses);
    return;
  }

  switch (pulses) {
    case CMD_POWER_ON:
      compressorOn = true;
      digitalWrite(LED_COMPRESSOR_PIN, HIGH);
      Serial.println(F("[EcoBreeze] 전원 ON 수신 -> LED 켬"));
      break;
    case CMD_POWER_OFF:
      compressorOn = false;
      digitalWrite(LED_COMPRESSOR_PIN, LOW);
      Serial.println(F("[EcoBreeze] 전원 OFF 수신 -> LED 끔"));
      break;
    case CMD_TEMP_UP:
      Serial.println(F("[EcoBreeze] 온도 1도 상승 신호 수신"));
      break;
    case CMD_TEMP_DOWN:
      Serial.println(F("[EcoBreeze] 온도 1도 하강 신호 수신"));
      break;
    case CMD_SLEEP_SET:
      sleepOn = true;
      digitalWrite(LED_SLEEP_PIN, HIGH);
      Serial.println(F("[EcoBreeze] 수면모드 ON 수신 -> 초록 LED 켬"));
      break;
    case CMD_SLEEP_CLEAR:
      sleepOn = false;
      digitalWrite(LED_SLEEP_PIN, LOW);
      Serial.println(F("[EcoBreeze] 수면모드 OFF 수신 -> 초록 LED 끔"));
      break;
    default:
      Serial.printf("[EcoBreeze] 알 수 없는 명령: 펄스 %u개\n", pulses);
      break;
  }
}

// 임계값 튜닝/배선 확인용. 500ms마다 최소~최대/평균을 찍어 신호 폭을 눈으로 본다.
void dumpRaw() {
  Serial.println(F("[EcoBreeze] 원시값 덤프 8초 (min~max/avg)"));
  unsigned long until = millis() + 8000;
  while (millis() < until) {
    uint16_t lo = 4095, hi = 0;
    uint32_t sum = 0, n = 0;
    unsigned long start = millis();
    while (millis() - start < 500) {
      uint16_t v = analogRead(IR_RECV_PIN);
      if (v < lo) lo = v;
      if (v > hi) hi = v;
      sum += v;
      n++;
    }
    Serial.printf("  %u~%u/%u (n=%lu)\n", lo, hi, (uint16_t)(sum / n), n);
  }
  Serial.println(F("[EcoBreeze] 덤프 끝"));
}

void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'b' || c == 'B') {
      measureBaseline();
    } else if (c == 'r' || c == 'R') {
      dumpRaw();
    } else if (c == '?') {
      Serial.println(F("[EcoBreeze] b=기준값 재측정, r=원시값 덤프, ?=도움말"));
      Serial.printf("[EcoBreeze] 기준값=%u, on<%u/off>%u, 컴프레서=%s, 수면모드=%s\n",
                    darkBaseline, threshOn, threshOff,
                    compressorOn ? "ON" : "OFF", sleepOn ? "ON" : "OFF");
    }
  }
}

// delay() 없이 millis()로 논블로킹 점멸 — IR 샘플링을 방해하지 않기 위함
void updateHeartbeat() {
  unsigned long now = millis();
  if (now - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = now;
    heartbeatState = !heartbeatState;
    digitalWrite(LED_HEARTBEAT_PIN, heartbeatState ? HIGH : LOW);
  }
}
