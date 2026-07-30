/*
 * ecobreeze_receiver.ino
 *
 * 대상 보드: Seeed Studio XIAO ESP32S3
 *
 * 대회 시연용: 대회장에 실제 에어컨이 없으므로, 이 보드를 "에어컨 대역"으로
 * 세운다. 라즈베리파이(ecobreeze_main.py)가 IR LED로 보내는 고정 NEC 프레임을
 * 그대로 받아서, 컴프레서 상태 LED를 지속적으로 켜고/끈다.
 *
 * Arduino IDE 준비:
 *   1. 파일 > 환경설정 > 추가 보드 매니저 URLs에
 *      https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
 *      추가 후, 보드 매니저에서 "esp32"(Espressif Systems) 설치
 *   2. 툴 > 보드에서 "XIAO_ESP32S3" 선택
 *   3. 툴 > USB CDC On Boot: "Enabled" (꺼져 있으면 시리얼 모니터가 안 붙음)
 *   4. 라이브러리 매니저에서 "IRremote"(Armin Joachimsmeyer, v4.x) 설치 —
 *      ESP32 계열은 RMT 페리페럴 기반이라 핀 제약이 적어 그대로 동작
 *
 * 배선 (XIAO ESP32S3 실크스크린 D-핀 기준, GPIO는 3.3V 로직):
 *   IR 수신모듈(VS1838B 등, 3.3V 호환 확인) OUT -> D0
 *   컴프레서 상태 LED (+저항 220~330Ω) -> D1
 *   (선택) 하트비트 LED(+저항) -> D2 — 보드가 멈추지 않고 살아있다는 걸
 *   심사위원에게 보여주는 용도, 없어도 동작에 지장 없음
 *   (XIAO ESP32S3는 사용자가 켤 수 있는 온보드 LED가 없어서 외부 LED 필수)
 *
 * 라즈베리파이 쪽 프로토콜 정의: raspi/ir_control.py의 NEC_ADDRESS/
 * NEC_CMD_*(_DEMO_CMD_MAP)와 반드시 같은 값을 써야 한다. 실제 에어컨이 없어도
 * 5개 신호(전원on/off, 온도▲/▼, 수면모드) 전부를 이 고정 프로토콜로 구분해서
 * 받는다 — LED는 전원(on/off)만 반영하고, 나머지는 시리얼 로그로만 확인한다
 * (추가 LED가 배선되어 있지 않으므로).
 */

#include <IRremote.hpp>

const uint8_t IR_RECV_PIN = D0;
const uint8_t LED_COMPRESSOR_PIN = D1;
const uint8_t LED_HEARTBEAT_PIN = D2;   // 안 쓰려면 배선 생략해도 무방

const uint16_t ECOBREEZE_ADDRESS = 0xEB;      // raspi/ir_control.py의 NEC_ADDRESS와 동일해야 함
const uint8_t CMD_POWER_ON = 0x01;
const uint8_t CMD_POWER_OFF = 0x02;
const uint8_t CMD_TEMP_UP = 0x03;
const uint8_t CMD_TEMP_DOWN = 0x04;
const uint8_t CMD_SLEEP_SET = 0x05;

const unsigned long HEARTBEAT_INTERVAL_MS = 500;

bool compressorOn = false;
bool heartbeatState = false;
unsigned long lastHeartbeatMs = 0;

void setup() {
  Serial.begin(115200);
  // 네이티브 USB(CDC) 보드라 시리얼 모니터를 안 열면 Serial이 계속 false다.
  // 무한 대기하면 노트북 없이 배터리로만 돌리는 실제 시연 중 이 지점에서
  // 영원히 멈춰 LED가 하나도 안 켜지므로, 최대 3초만 기다리고 넘어간다.
  unsigned long serialWaitStart = millis();
  while (!Serial && millis() - serialWaitStart < 3000) { /* 대기 */ }

  // IRremote 자체 피드백 LED(기본 LED_BUILTIN)는 하트비트와 핀이 겹쳐 헷갈리므로 끔
  IrReceiver.begin(IR_RECV_PIN, DISABLE_LED_FEEDBACK);

  pinMode(LED_COMPRESSOR_PIN, OUTPUT);
  pinMode(LED_HEARTBEAT_PIN, OUTPUT);
  digitalWrite(LED_COMPRESSOR_PIN, LOW);

  Serial.println(F("[EcoBreeze] 수신 대기 중..."));
}

void loop() {
  if (IrReceiver.decode()) {
    handleFrame();
    IrReceiver.resume();
  }
  updateHeartbeat();
}

void handleFrame() {
  auto &data = IrReceiver.decodedIRData;

  if (data.protocol != NEC) {
    return;  // 노이즈/다른 리모컨 신호는 무시
  }

  if (data.address != ECOBREEZE_ADDRESS) {
    Serial.print(F("[EcoBreeze] 다른 주소 신호 무시: 0x"));
    Serial.println(data.address, HEX);
    return;
  }

  switch (data.command) {
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
      Serial.println(F("[EcoBreeze] 수면모드(목표온도 설정) 신호 수신"));
      break;
    default:
      Serial.print(F("[EcoBreeze] 알 수 없는 명령: 0x"));
      Serial.println(data.command, HEX);
      break;
  }
}

// delay() 없이 millis()로 논블로킹 점멸 — IR 수신을 방해하지 않기 위함
void updateHeartbeat() {
  unsigned long now = millis();
  if (now - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = now;
    heartbeatState = !heartbeatState;
    digitalWrite(LED_HEARTBEAT_PIN, heartbeatState ? HIGH : LOW);
  }
}
