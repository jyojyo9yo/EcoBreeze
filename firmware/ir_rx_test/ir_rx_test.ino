/*
 * ir_rx_test.ino
 *
 * 대상: 수신기 쪽 ESP32-S3 보드 (COM7)
 *
 * 이 프로젝트의 "수신기"는 3핀 복조 모듈(VS1838B/TSOP 계열)이 아니라 2핀 적외선
 * 포토다이오드(검은 5mm, 940nm)다. 하드웨어 복조가 전혀 없어서 IRremote의 NEC
 * 디코딩은 원리상 동작할 수 없다 — 복조된 디지털 신호가 애초에 만들어지지 않는다.
 * 그래서 디코딩을 포기하고 ADC로 광량을 직접 읽어 "IR이 오고 있는지"만 본다.
 *
 * 실측 배선 (2026-07-30, 사용자 확인):
 *   3.3V --[10kΩ]--+-- 포토다이오드 긴 다리(+, 아노드)
 *                  |
 *              (센스 노드 = GPIO ?)   포토다이오드 짧은 다리(-, 캐소드) -- GND
 *
 * 핀 이름을 D0~D10(XIAO 실크스크린) 기준으로 쓰다가 헤맸다. 사용자 보드는 실크에
 * GPIO 번호가 찍혀 있어 XIAO가 아닐 가능성이 크고(XIAO는 GPIO15가 헤더로 안 나온다),
 * 그러면 D 핀 매크로가 전혀 다른 GPIO로 컴파일된다. 그래서 이 스케치는 **D 매크로를
 * 쓰지 않고 raw GPIO 번호로만** 다룬다 — 보드 종류와 무관하게 동작한다.
 * GPIO19/20은 네이티브 USB(D-/D+)라 건드리면 시리얼이 끊기므로 제외하고,
 * GPIO0은 부팅 스트래핑 핀이라 제외한다.
 *
 * setup()의 핀 진단이 센스 노드를 해석의 여지 없이 찾아낸다. 핵심은 10kΩ 풀업을
 * **디지털로** 잡는 것이다: 내부 풀다운(~45kΩ)을 걸어도 외부 10kΩ이 이기므로
 * `digitalRead`가 HIGH로 읽힌다. 아무것도 안 붙은 핀은 LOW다.
 * (analogRead는 패드를 아날로그로 재설정하면서 내부 풀 저항을 떨어뜨릴 수 있으므로,
 *  각 모드에서 digitalRead를 먼저 읽는다.)
 *
 * 연속 스캔은 내부 풀 저항을 끈 순수 INPUT으로 한다. 처음엔 판독 안정화 목적으로
 * INPUT_PULLDOWN을 썼는데 그게 신호를 깎고 있었다: 신호 = 광전류 x 부하저항인데,
 * 내부 풀다운이 외부 바이어스 저항과 병렬로 붙어 부하를 45kΩ으로 끌어내렸다.
 */

// GPIO19/20(USB), GPIO0(스트래핑) 제외
const uint8_t SCAN_PINS[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
                             11, 12, 13, 14, 15, 16, 17, 18};
const uint8_t PIN_COUNT = sizeof(SCAN_PINS) / sizeof(SCAN_PINS[0]);

const unsigned long REPORT_MS = 500;

// 연속 스캔에 쓸 입력 모드. 포토다이오드 방향에 따라 신호가 위로 가는지 아래로
// 가는지가 뒤집히므로 시리얼로 바꿀 수 있게 둔다:
//   PULLUP  : 내부 45kΩ이 역방향 바이어스 + 부하저항 역할 (photoconductive 모드).
//             캐소드가 핀에 붙은 배선이면 이게 정답 — 어두울 때 높고, 빛이 오면 떨어진다.
//   INPUT   : 무풀 고임피던스. 아노드가 핀에 붙은 배선(광전압 모드)에서 빛이 오면 올라간다.
//   PULLDOWN: 부하를 45kΩ으로 낮춰 신호를 깎으므로 보통 쓸 이유가 없다(비교용).
uint8_t scanMode = INPUT_PULLUP;
const char *scanModeName = "PULLUP";

uint16_t readAvg(uint8_t pin, uint8_t n = 32) {
  uint32_t sum = 0;
  for (uint8_t i = 0; i < n; i++) {
    sum += analogRead(pin);
  }
  return (uint16_t)(sum / n);
}

void pinDiagnostic() {
  Serial.println();
  Serial.println(F("=== 핀 진단: 센스 노드(10kΩ -> 3.3V) 찾기 ==="));
  Serial.println(F("  판독법:"));
  Serial.println(F("    풀다운 상태에서 digital=HIGH  -> 외부에서 3.3V로 강하게 당겨짐 = 센스 노드"));
  Serial.println(F("    풀업 상태에서 digital=LOW     -> 외부에서 GND로 강하게 당겨짐"));
  Serial.println(F("    풀다운=LOW & 풀업=HIGH        -> 아무것도 안 붙음(플로팅)"));
  Serial.println();
  Serial.println(F("  GPIO  풀다운(d/a)   풀업(d/a)    무풀(a)   판정"));

  for (uint8_t i = 0; i < PIN_COUNT; i++) {
    uint8_t pin = SCAN_PINS[i];

    pinMode(pin, INPUT_PULLDOWN);
    delay(10);
    int dPD = digitalRead(pin);       // analogRead보다 먼저 (풀 설정이 유지된 상태에서)
    uint16_t aPD = readAvg(pin);

    pinMode(pin, INPUT_PULLUP);
    delay(10);
    int dPU = digitalRead(pin);
    uint16_t aPU = readAvg(pin);

    pinMode(pin, INPUT);
    delay(10);
    uint16_t aFL = readAvg(pin);

    const char *verdict;
    if (dPD == HIGH) {
      verdict = "<<<<< 3.3V로 당겨짐 = 센스 노드 후보!";
    } else if (dPU == LOW) {
      verdict = "<<<<< GND로 당겨짐";
    } else {
      verdict = "플로팅";
    }

    Serial.printf("  %-4u  %d/%-4u      %d/%-4u     %-5u    %s\n",
                  pin, dPD, aPD, dPU, aPU, aFL, verdict);
  }
  Serial.println(F("=== 진단 끝 — 이제 연속 스캔(무풀 INPUT) ==="));
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  // 네이티브 USB(CDC)라 모니터를 열기 전엔 Serial이 false다. 최대 3초만 기다린다.
  unsigned long waitStart = millis();
  while (!Serial && millis() - waitStart < 3000) { /* 대기 */ }

  pinDiagnostic();
  applyScanMode();
  Serial.println(F("[RX] u=풀업 n=무풀 p=풀다운 d=핀진단 ?=도움말"));
}

void applyScanMode() {
  for (uint8_t i = 0; i < PIN_COUNT; i++) {
    pinMode(SCAN_PINS[i], scanMode);
  }
  Serial.printf("[RX] 스캔 모드 = %s\n", scanModeName);
}

// 이 보드는 포트를 열어도 리셋되지 않아서 부팅 시 출력한 진단 표를 놓치기 쉽다.
// 시리얼로 'd'를 보내면 언제든 다시 찍게 해 둔다 ('?'는 도움말).
void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'd' || c == 'D') {
      pinDiagnostic();
      applyScanMode();               // 진단이 바꿔놓은 핀 모드를 되돌린다
    } else if (c == 'u' || c == 'U') {
      scanMode = INPUT_PULLUP;
      scanModeName = "PULLUP";
      applyScanMode();
    } else if (c == 'n' || c == 'N') {
      scanMode = INPUT;
      scanModeName = "무풀";
      applyScanMode();
    } else if (c == 'p' || c == 'P') {
      scanMode = INPUT_PULLDOWN;
      scanModeName = "PULLDOWN";
      applyScanMode();
    } else if (c == '?') {
      Serial.println(F("[RX] u=풀업 n=무풀 p=풀다운 d=핀진단 ?=도움말"));
      Serial.printf("[RX] 현재 스캔 모드 = %s\n", scanModeName);
    }
  }
}

void loop() {
  handleSerial();

  uint16_t peak[PIN_COUNT] = {0};
  uint16_t valley[PIN_COUNT];
  uint32_t sum[PIN_COUNT] = {0};
  uint32_t samples = 0;

  for (uint8_t i = 0; i < PIN_COUNT; i++) {
    valley[i] = 4095;
  }

  unsigned long start = millis();
  while (millis() - start < REPORT_MS) {
    for (uint8_t i = 0; i < PIN_COUNT; i++) {
      uint16_t v = analogRead(SCAN_PINS[i]);
      if (v > peak[i]) peak[i] = v;
      if (v < valley[i]) valley[i] = v;
      sum[i] += v;
    }
    samples++;
  }

  // min도 같이 낸다: 풀업 모드에서는 빛이 값을 *끌어내리므로* 최소값이 신호다.
  Serial.printf("[RX:%s]", scanModeName);
  for (uint8_t i = 0; i < PIN_COUNT; i++) {
    Serial.printf(" %u=%u~%u/%u", SCAN_PINS[i], valley[i], peak[i],
                  (uint16_t)(sum[i] / samples));
  }
  Serial.printf("  (n=%lu)\n", samples);
}
