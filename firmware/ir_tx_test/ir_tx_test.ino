/*
 * ir_tx_test.ino
 *
 * 대상 보드: Seeed Studio XIAO ESP32S3 (FQBN esp32:esp32:XIAO_ESP32S3)
 *
 * IR 송신부(적외선 LED 다이오드)만 단독으로 점검하는 스케치. 카메라/WiFi/BME280을
 * 다 걷어내고 IR 송신만 남겨서 "LED가 실제로 빛을 내는가"와 "NEC 프레임이
 * 나가는가"를 따로 확인한다. 주소/커맨드 값은 xiao_esp32s3_person_led.ino,
 * ecobreeze_receiver.ino, raspi/ir_control.py와 같은 값을 그대로 쓴다.
 *
 * 배선 (실측, 2026-07-30): IR LED를 GPIO에 직결하지 않고 NPN 트랜지스터
 * 로우사이드 스위치로 구동한다.
 *
 *   VBUS(5V) --[저항]-- LED 아노드(긴 다리) / LED 캐소드(짧은 다리) -- 트랜지스터 C
 *   D3(GPIO4) --[저항]-- 트랜지스터 B
 *   트랜지스터 E -- GND
 *
 * 즉 D3을 HIGH로 올리면 LED가 켜지는 active-HIGH 구조라 IRsend의 기본
 * (inverted=false)이 그대로 맞다. LED 전류는 5V쪽 직렬 저항이 정하므로 GPIO에는
 * 베이스 전류만 흐른다.
 *
 * 적외선은 눈에 안 보이므로 휴대폰 카메라로 LED를 들여다보면서 확인한다. 요즘
 * 후면 카메라는 IR 차단 필터가 강해서 안 보일 수 있으니, 안 보이면 전면(셀피)
 * 카메라로 볼 것 — 전면은 필터가 약해서 보라/흰색 깜빡임이 잘 잡힌다.
 *
 * 시리얼 모니터(115200) 명령:
 *   1~5 : NEC 프레임 1회 송신 (1=전원ON 2=전원OFF 3=온도▲ 4=온도▼ 5=수면모드)
 *   a   : 1~5를 1초 간격으로 전부 송신 (수신기 쪽 로그 확인용)
 *   b   : 2초간 NEC 프레임 연타 — 폰 카메라로 깜빡임 확인
 *   c   : 2초간 38kHz 캐리어 연속 송출 — 폰 카메라로 가장 확실하게 보임
 *   d   : 1.5초간 변조 없이 DC로 점등 — 배선/극성 확인용 (저항 없이 길게 켜지 말 것)
 *   w   : D0~D10을 1초씩 훑으며 점등 — IR LED가 실제로 꽂힌 핀 찾기
 *   p   : 광전압 스캔 — 폰 카메라 없이 배선/극성/생존 확인 (아래 설명)
 *   h   : 자동 송신(3초마다 전원 ON/OFF 번갈아) 켜기/끄기
 *   ?   : 도움말 다시 출력
 */

#include <IRsend.h>

// D3(GPIO4) — 트랜지스터 베이스로 들어가는 핀. 실배선을 확인해 보니 IR LED가
// GPIO 직결이 아니라 NPN 로우사이드 스위치로 구동되고 있었고 베이스가 D3에 물려
// 있었다. 이전 값 D2(GPIO3)로는 트랜지스터가 아예 켜지지 않아 LED에 전류가 흐르지
// 않았다 (2026-07-30).
const uint16_t IR_TX_PIN = 4;
IRsend irsend(IR_TX_PIN);

const uint16_t NEC_ADDRESS = 0xEB;      // "EcoBreeze"

struct IrCommand {
  uint8_t code;
  const char *label;
};

const IrCommand COMMANDS[] = {
  {0x01, "전원 ON"},
  {0x02, "전원 OFF"},
  {0x03, "온도 1도 상승"},
  {0x04, "온도 1도 하강"},
  {0x05, "수면모드 ON"},
  {0x06, "수면모드 OFF"},
};
const uint8_t COMMAND_COUNT = sizeof(COMMANDS) / sizeof(COMMANDS[0]);

// 실배선이 설계와 달랐던 적이 있어(초록 LED는 D1이 아니라 GPIO2, 수신 핀은 D0이
// 아니라 D2였다) 핀을 훑어보는 명령을 따로 둔다. D6/D7은 UART0라 제외.
const uint8_t SWEEP_PINS[] = {D0, D1, D2, D3, D4, D5, D8, D9, D10};
const char *SWEEP_NAMES[] = {"D0", "D1", "D2", "D3", "D4", "D5", "D8", "D9", "D10"};
const uint8_t SWEEP_COUNT = sizeof(SWEEP_PINS) / sizeof(SWEEP_PINS[0]);

// EcoBreeze 광 펄스 프로토콜 v1 — 명령 값이 그대로 펄스 개수다. 수신기가 생
// 포토다이오드라 복조가 없어서 NEC 대신 이걸 쓴다(ecobreeze_receiver.ino 주석 참고).
// 상수는 ecobreeze_receiver.ino / xiao_esp32s3_person_led.ino / raspi/ir_control.py와
// 반드시 같아야 한다.
const uint16_t PULSE_ON_MS = 60;
const uint16_t PULSE_GAP_MS = 60;
const uint16_t FRAME_GAP_MS = 500;

const unsigned long AUTO_INTERVAL_MS = 3000;
bool autoSend = true;
bool autoNextIsOn = true;
unsigned long lastAutoMs = 0;

void printHelp() {
  Serial.println();
  Serial.printf("[IR TX 테스트] 송신 핀 D3(트랜지스터 베이스) = GPIO%u, NEC 주소 0x%02X\n",
                IR_TX_PIN, NEC_ADDRESS);
  Serial.println(F("  1~6 : 펄스 프로토콜 1프레임"));
  Serial.println(F("        1=전원ON 2=전원OFF 3=온도▲ 4=온도▼ 5=수면모드ON 6=수면모드OFF"));
  Serial.println(F("  a   : 펄스 프로토콜 1~6 순차"));
  Serial.println(F("  N/A : 레거시 NEC 1프레임 / 순차 (복조 모듈 쓸 때만 의미 있음)"));
  Serial.println(F("  b   : 2초간 NEC 연타 (폰 카메라 확인)"));
  Serial.println(F("  c   : 2초간 38kHz 캐리어 (폰 카메라 확인, 가장 잘 보임)"));
  Serial.println(F("  d   : 1.5초 DC 점등 (배선/극성 확인)"));
  Serial.println(F("  w   : D0~D10 순차 점등 (LED가 실제로 꽂힌 핀 찾기)"));
  Serial.println(F("  p   : 광전압 스캔 (폰 카메라 없이 배선/극성 확인)"));
  Serial.println(F("  h   : 자동 송신 토글"));
  Serial.println(F("  ?   : 이 도움말"));
  Serial.printf("  자동 송신: %s (%lu초 간격)\n", autoSend ? "ON" : "OFF",
                AUTO_INTERVAL_MS / 1000);
  Serial.println();
}

// 펄스 개수 프로토콜로 송신. 38kHz 변조 없이 DC로 켠다 — 수신기에 복조기가
// 없으므로 변조는 수신 신호를 절반으로 깎을 뿐이다.
void sendPulses(uint8_t count, const char *label) {
  for (uint8_t i = 0; i < count; i++) {
    digitalWrite(IR_TX_PIN, HIGH);
    delay(PULSE_ON_MS);
    digitalWrite(IR_TX_PIN, LOW);
    delay(PULSE_GAP_MS);
  }
  delay(FRAME_GAP_MS);
  Serial.printf("[TX] 펄스 %u개 송신 (%s)\n", count, label);
}

void sendPulseCommand(uint8_t index) {
  sendPulses(COMMANDS[index].code, COMMANDS[index].label);
}

void sendAllPulses() {
  Serial.println(F("[TX] 펄스 프로토콜로 1~5 순차 송신"));
  for (uint8_t i = 0; i < COMMAND_COUNT; i++) {
    sendPulseCommand(i);
    delay(700);   // 프레임 사이를 넉넉히 벌려 수신기가 확실히 나눠 읽게 한다
  }
  Serial.println(F("[TX] 순차 송신 끝"));
}

void sendCommand(uint8_t index) {
  const IrCommand &cmd = COMMANDS[index];
  irsend.sendNEC(irsend.encodeNEC(NEC_ADDRESS, cmd.code));
  Serial.printf("[TX] NEC addr=0x%02X cmd=0x%02X (%s)\n",
                NEC_ADDRESS, cmd.code, cmd.label);
}

void sendAll() {
  Serial.println(F("[TX] 1~5 순차 송신 시작"));
  for (uint8_t i = 0; i < COMMAND_COUNT; i++) {
    sendCommand(i);
    delay(1000);
  }
  Serial.println(F("[TX] 순차 송신 끝"));
}

// NEC 프레임 하나는 68ms 정도라 폰 카메라로 잡기 어렵다. 2초간 연타해서
// 눈(카메라)에 보이게 만든다. 수신기가 붙어 있으면 전원 ON을 반복 수신할 뿐이라 무해.
void burstFrames() {
  Serial.println(F("[TX] NEC 프레임 2초간 연타 -- 폰 카메라로 LED 확인"));
  unsigned long start = millis();
  uint16_t frames = 0;
  while (millis() - start < 2000) {
    irsend.sendNEC(irsend.encodeNEC(NEC_ADDRESS, COMMANDS[0].code));
    frames++;
  }
  Serial.printf("[TX] %u 프레임 송신 완료\n", frames);
}

// 38kHz 변조를 계속 물려 두는 것 — 실제 IR 수신모듈이 기대하는 파형이면서
// 폰 카메라에도 가장 밝게 잡힌다. mark()는 uint16_t(최대 65ms)라 이어 붙인다.
void carrierBurst() {
  Serial.println(F("[TX] 38kHz 캐리어 2초 송출 -- 폰 카메라로 LED 확인"));
  irsend.enableIROut(38);
  unsigned long start = millis();
  while (millis() - start < 2000) {
    irsend.mark(20000);
  }
  irsend.space(0);
  Serial.println(F("[TX] 캐리어 종료"));
}

// 변조 없이 그냥 켜기. 배선/납땜 확인용이고 폰 카메라에 가장 밝게 잡힌다.
// 트랜지스터 구동이라 LED 전류는 5V쪽 저항이 정하므로 오래 켜도 GPIO엔 부담이 없다.
void dcOn() {
  Serial.println(F("[TX] DC 1.5초 점등"));
  digitalWrite(IR_TX_PIN, HIGH);
  delay(1500);
  digitalWrite(IR_TX_PIN, LOW);
  Serial.println(F("[TX] 소등"));
}

void sweepPins() {
  Serial.println(F("[TX] D0~D10 순차 점등 -- 폰 카메라 보면서 어느 핀에서 켜지는지 확인"));
  for (uint8_t i = 0; i < SWEEP_COUNT; i++) {
    Serial.printf("  %s (GPIO%u) ...\n", SWEEP_NAMES[i], SWEEP_PINS[i]);
    pinMode(SWEEP_PINS[i], OUTPUT);
    digitalWrite(SWEEP_PINS[i], HIGH);
    delay(1000);
    digitalWrite(SWEEP_PINS[i], LOW);
    delay(300);
  }
  Serial.println(F("[TX] 스윕 끝"));
}

// IR LED는 발광 다이오드이면서 동시에 포토다이오드라서, 빛을 받으면 아노드에
// 미세한 광전압이 생긴다. 이 성질로 폰 카메라/수신모듈 없이 배선을 검증한다.
// D0~D10은 전부 ADC1 채널(GPIO1~9)이라 후보 핀 전체를 한 번에 훑을 수 있다.
//   빛 쬘 때 값이 오르는 핀 = LED 아노드가 실제로 붙어 있는 핀 (+ 극성 정상 + 살아있음)
//   아무 핀도 안 오르면 = 극성 반대 / 접촉 불량 / 죽은 LED
// 풀다운을 걸어 두는 이유: 플로팅 입력은 노이즈로 아무 값이나 읽혀서 판독이 안 된다.
// 주의: LED 아노드가 GPIO에 직결된 배선에서만 의미가 있다. 지금처럼 아노드가 5V
// 레일에 붙은 트랜지스터 구동에서는 GPIO에 광전압이 나타나지 않아 전부 0으로 읽힌다.
void photoScan() {
  Serial.println(F("[TEST] 광전압 스캔 20초 -- LED 머리에 빛을 바짝 대세요"));
  Serial.println(F("        1순위: TV/에어컨 리모컨을 LED에 붙이고 버튼 연타 (같은 940nm라 반응이 가장 큼)"));
  Serial.println(F("        2순위: 폰 손전등을 LED 렌즈에 밀착 / 창가 직사광선"));

  for (uint8_t i = 0; i < SWEEP_COUNT; i++) {
    pinMode(SWEEP_PINS[i], INPUT_PULLDOWN);
  }

  uint16_t peak[SWEEP_COUNT] = {0};
  unsigned long start = millis();
  unsigned long lastReport = millis();
  while (millis() - start < 20000) {
    for (uint8_t i = 0; i < SWEEP_COUNT; i++) {
      uint16_t v = analogRead(SWEEP_PINS[i]);
      if (v > peak[i]) peak[i] = v;
    }
    if (millis() - lastReport >= 2000) {
      lastReport = millis();
      Serial.print(F("  peak"));
      for (uint8_t i = 0; i < SWEEP_COUNT; i++) {
        Serial.printf(" %s=%u", SWEEP_NAMES[i], peak[i]);
        peak[i] = 0;   // 구간마다 초기화해서 "지금" 반응하는 핀이 보이게 한다
      }
      Serial.println();
    }
  }

  for (uint8_t i = 0; i < SWEEP_COUNT; i++) {
    pinMode(SWEEP_PINS[i], INPUT);
  }
  irsend.begin();               // 송신 핀을 다시 OUTPUT으로 되돌린다
  digitalWrite(IR_TX_PIN, LOW);
  lastAutoMs = millis();        // 스캔 동안 밀린 자동 송신이 몰려 나가지 않게
  Serial.println(F("[TEST] 스캔 끝"));
}

void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c >= '1' && c <= '6') {
      sendPulseCommand(c - '1');      // 현재 실제 프로토콜(펄스 개수)
    } else if (c == 'a') {
      sendAllPulses();
    } else if (c == 'N') {
      sendCommand(0);                 // 레거시 NEC 1프레임 — 복조 모듈을 쓸 때만 의미 있음
    } else if (c == 'A') {
      sendAll();                      // 레거시 NEC 1~5 순차
    } else if (c == 'b' || c == 'B') {
      burstFrames();
    } else if (c == 'c' || c == 'C') {
      carrierBurst();
    } else if (c == 'd' || c == 'D') {
      dcOn();
    } else if (c == 'w' || c == 'W') {
      sweepPins();
    } else if (c == 'p' || c == 'P') {
      photoScan();
    } else if (c == 'h' || c == 'H') {
      autoSend = !autoSend;
      lastAutoMs = millis();
      Serial.printf("[TX] 자동 송신 %s\n", autoSend ? "ON" : "OFF");
    } else if (c == '?') {
      printHelp();
    }
    // 그 외(개행 등)는 무시
  }
}

void setup() {
  Serial.begin(115200);
  // 네이티브 USB(CDC)라 시리얼 모니터를 열기 전엔 Serial이 false다. 모니터 없이
  // 돌릴 때 여기서 멈추지 않도록 최대 3초만 기다린다(ecobreeze_receiver.ino와 동일).
  unsigned long waitStart = millis();
  while (!Serial && millis() - waitStart < 3000) { /* 대기 */ }

  irsend.begin();
  pinMode(IR_TX_PIN, OUTPUT);
  digitalWrite(IR_TX_PIN, LOW);

  printHelp();
}

void loop() {
  handleSerial();

  if (autoSend && millis() - lastAutoMs >= AUTO_INTERVAL_MS) {
    sendPulseCommand(autoNextIsOn ? 0 : 1);
    lastAutoMs = millis();   // 송신이 1초 가까이 걸리므로 끝난 시점을 기준으로 잡는다
    autoNextIsOn = !autoNextIsOn;
  }
}
