#include <WiFi.h>
#include <WebServer.h>

// AP (access point) credentials
const char *AP_SSID = "jyo";
const char *AP_PASSWORD = "10201020";

// XIAO ESP32S3 pin map: D0 = GPIO1 (touch-capable, T1)
const int TOUCH_PIN = 1;

// XIAO ESP32S3 onboard user LED is on GPIO21 and is active-LOW
const int LED_PIN = LED_BUILTIN;

WebServer server(80);
bool ledOn = false;

void setLed(bool on) {
  ledOn = on;
  digitalWrite(LED_PIN, on ? LOW : HIGH);
}

const char INDEX_HTML[] PROGMEM = R"HTML(
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>XIAO ESP32S3 Control</title>
<style>
  body { font-family: sans-serif; text-align: center; background:#111; color:#eee; padding-top: 40px; }
  h1 { font-size: 1.3rem; }
  #touchVal { font-size: 3rem; margin: 20px 0; }
  button {
    font-size: 1.5rem; padding: 20px 50px; border-radius: 12px; border: none;
    cursor: pointer; margin-top: 30px;
  }
  #ledBtn.off { background:#555; color:#fff; }
  #ledBtn.on { background:#ffcc00; color:#000; }
</style>
</head>
<body>
  <h1>XIAO ESP32S3</h1>
  <div>Touch Sensor (D0 / GPIO1)</div>
  <div id="touchVal">--</div>
  <button id="ledBtn" class="off" onclick="toggleLed()">LED OFF</button>

<script>
async function refreshTouch() {
  try {
    const res = await fetch('/touch');
    const val = await res.text();
    document.getElementById('touchVal').textContent = val;
  } catch (e) {}
}

async function refreshLed() {
  try {
    const res = await fetch('/led');
    const state = (await res.text()).trim();
    setLedButton(state === '1');
  } catch (e) {}
}

function setLedButton(on) {
  const btn = document.getElementById('ledBtn');
  btn.textContent = on ? 'LED ON' : 'LED OFF';
  btn.className = on ? 'on' : 'off';
}

async function toggleLed() {
  const btn = document.getElementById('ledBtn');
  const turningOn = btn.className === 'off';
  const res = await fetch(turningOn ? '/led/on' : '/led/off');
  const state = (await res.text()).trim();
  setLedButton(state === '1');
}

refreshLed();
setInterval(refreshTouch, 300);
</script>
</body>
</html>
)HTML";

void handleRoot() {
  server.send_P(200, "text/html", INDEX_HTML);
}

void handleTouch() {
  uint32_t val = touchRead(TOUCH_PIN);
  server.send(200, "text/plain", String(val));
}

void handleLedGet() {
  server.send(200, "text/plain", ledOn ? "1" : "0");
}

void handleLedOn() {
  setLed(true);
  server.send(200, "text/plain", "1");
}

void handleLedOff() {
  setLed(false);
  server.send(200, "text/plain", "0");
}

void setup() {
  Serial.begin(115200);

  pinMode(LED_PIN, OUTPUT);
  setLed(false);

  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASSWORD);

  Serial.print("AP started. IP address: ");
  Serial.println(WiFi.softAPIP()); // default 192.168.4.1

  server.on("/", handleRoot);
  server.on("/touch", handleTouch);
  server.on("/led", handleLedGet);
  server.on("/led/on", handleLedOn);
  server.on("/led/off", handleLedOff);
  server.begin();

  Serial.println("HTTP server started");
}

void loop() {
  server.handleClient();
}
