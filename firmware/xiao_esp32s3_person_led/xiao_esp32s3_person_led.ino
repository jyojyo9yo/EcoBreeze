// XIAO ESP32S3 Sense: serves camera snapshots and BME280 temperature/humidity
// readings over a local HTTP server, lights an LED for person detection and
// sleep-mode indication, and transmits an IR signal for the AC-on/off state.
// The inference server (running YOLO12n, on the same Wi-Fi network) polls
// /capture and /sensor and calls the /led/* endpoints based on what it sees
// -- see server/person_detect.py.
//
// Requires secrets.h (copy secrets.h.example -> secrets.h and fill in real values).
// Requires the "SparkFun BME280" library installed via Arduino IDE's Library
// Manager. (Not "Adafruit BME280 Library": its Adafruit_Sensor dependency
// redeclares `sensor_t`, which conflicts with the esp32-camera driver's own
// `sensor_t` typedef and fails to compile.)
// No IR library is needed: the receiver is a bare photodiode with no
// demodulator, so we key the IR LED on and off directly (see the protocol note
// on sendPulses below). "IRremoteESP8266" is no longer required.

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Wire.h>
#include <SparkFunBME280.h>
#include "esp_camera.h"
#include "camera_pins.h"
#include "secrets.h"

// XIAO ESP32S3 onboard user LED: GPIO21, active-LOW
const int LED_PIN = LED_BUILTIN;

// Green sleep-mode-indicator LED on the XIAO's D1 pin. Assumes standard
// wiring (GPIO -> resistor -> LED anode -> cathode -> GND), i.e. active-HIGH
// -- if wired the other way around, swap the HIGH/LOW in setGreenLed below.
// (Empirically GPIO2 lights the green LED -- opposite of the nominal D1/D0
// assumption -- so wired to match reality.)
const int LED_GREEN_PIN = 2;

// IR LED transmitter driven from D3 (GPIO4) -- replaces the old blue "AC-on"
// LED. The LED is not wired straight to a GPIO: its anode hangs off the 5V rail
// through a series resistor and its cathode goes to the collector of an NPN
// low-side switch, whose base D3 feeds through another resistor (emitter to
// GND). That is active-HIGH, so IRsend's default (inverted=false) is right.
// Bench-verified 2026-07-30 with firmware/ir_tx_test: the pin was D2 (GPIO3)
// before, which is not wired to anything, so the transistor never switched and
// no IR left the board at all -- the sketch looked fine and emitted nothing.
const uint8_t IR_TX_PIN = 4;

// EcoBreeze optical pulse protocol v1. NEC framing is gone: the receiver is a
// bare 2-pin photodiode with no demodulator, so it can only report "IR present
// or not" -- there is no hardware-demodulated signal for IRremote to decode, no
// matter how correct the transmitter is. So a command is simply that many
// pulses, and the command value *is* the pulse count. No 38kHz carrier either:
// with no demodulator, modulating would just halve the received signal.
//   one pulse = IR on for PULSE_ON_MS, then off for PULSE_GAP_MS
//   one frame = N pulses, then FRAME_GAP_MS of silence to close the frame
// These must match firmware/ecobreeze_receiver and raspi/ir_control.py.
const uint16_t PULSE_ON_MS = 60;
const uint16_t PULSE_GAP_MS = 60;
const uint16_t FRAME_GAP_MS = 500;

const uint8_t CMD_AC_ON = 1;
const uint8_t CMD_AC_OFF = 2;
const uint8_t CMD_SLEEP_SET = 5;
const uint8_t CMD_SLEEP_CLEAR = 6;

// Blocks for up to 5*(60+60)+500 = 1.1s. Callers are HTTP handlers, whose
// client (server/person_detect.py) uses a 5s timeout, so this is within budget.
void sendPulses(uint8_t count) {
  for (uint8_t i = 0; i < count; i++) {
    digitalWrite(IR_TX_PIN, HIGH);
    delay(PULSE_ON_MS);
    digitalWrite(IR_TX_PIN, LOW);
    delay(PULSE_GAP_MS);
  }
  delay(FRAME_GAP_MS);
}

// BME280 over I2C on the XIAO's default SDA/SCL pins. Uses the default
// `Wire` (I2C peripheral 0) -- confirmed via isolated testing that the
// camera's SCCB control bus occupies peripheral 1, not 0 (the opposite of
// what an earlier version of this comment assumed). Using TwoWire(1) here
// silently breaks BME280 detection once the camera is initialized.
const int BME_SDA_PIN = 5;
const int BME_SCL_PIN = 6;
TwoWire &bmeWire = Wire;
BME280 bme;
bool bmeReady = false;

WebServer server(80);
bool ledOn = false;
bool acOn = false;
bool greenLedOn = false;

void setLed(bool on) {
  ledOn = on;
  digitalWrite(LED_PIN, on ? LOW : HIGH);
}

void setAc(bool on) {
  acOn = on;
  sendPulses(on ? CMD_AC_ON : CMD_AC_OFF);
}

void setGreenLed(bool on) {
  greenLedOn = on;
  digitalWrite(LED_GREEN_PIN, on ? HIGH : LOW);
}

void setupCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // fb_count=1 + GRAB_WHEN_EMPTY captures on demand instead of streaming
  // continuously -- continuous capture (fb_count=2/GRAB_LATEST) flooded the
  // serial log with "cam_hal: FB-OVF" and starved outgoing traffic badly
  // enough to stall HTTP responses partway through (same failure mode we
  // hit with MQTT earlier).
  if (psramFound()) {
    config.frame_size = FRAMESIZE_QQVGA; // 160x120 -- small, known to transfer reliably
    config.jpeg_quality = 14;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  } else {
    config.frame_size = FRAMESIZE_QVGA;
    config.jpeg_quality = 14;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
  }
}

void handleCapture() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    server.send(503, "text/plain", "capture failed");
    return;
  }
  WiFiClient client = server.client();
  client.printf("HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\nConnection: close\r\n\r\n", fb->len);
  client.write(fb->buf, fb->len);
  esp_camera_fb_return(fb);
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

void handleBlueLedOn() {
  setAc(true);
  server.send(200, "text/plain", "1");
}

void handleBlueLedOff() {
  setAc(false);
  server.send(200, "text/plain", "0");
}

void handleGreenLedOn() {
  setGreenLed(true);
  sendPulses(CMD_SLEEP_SET);
  server.send(200, "text/plain", "1");
}

void handleGreenLedOff() {
  setGreenLed(false);
  sendPulses(CMD_SLEEP_CLEAR);   // so the receiver board can turn its green LED off too
  server.send(200, "text/plain", "0");
}

void handleI2cScan() {
  String body = "";
  for (uint8_t addr = 1; addr < 127; addr++) {
    bmeWire.beginTransmission(addr);
    if (bmeWire.endTransmission() == 0) {
      char line[16];
      snprintf(line, sizeof(line), "0x%02X\n", addr);
      body += line;
    }
  }
  if (body.length() == 0) {
    body = "(no devices found)\n";
  }
  server.send(200, "text/plain", body);
}

void handleSensor() {
  if (!bmeReady) {
    server.send(503, "application/json", "{\"error\":\"bme280 not found\"}");
    return;
  }
  float tempC = bme.readTempC();
  float humidity = bme.readFloatHumidity();
  char body[64];
  snprintf(body, sizeof(body), "{\"temperature\":%.2f,\"humidity\":%.2f}", tempC, humidity);
  server.send(200, "application/json", body);
}

// Advertise ecobreeze-cam.local so server/person_detect.py can find this board
// by name. The DHCP address drifted between sessions and every drift meant
// editing ESP32_IP in two separate .env files (dev PC and Pi) before anything
// worked again. A hand-picked static IP was rejected instead: this network is a
// /24 whose gateway sits at .173 (a phone hotspot or portable AP), so its DHCP
// pool is unknown and a fixed address could collide with another device. mDNS
// already resolves on this network -- the Pi is reachable as jyo.local and has
// avahi-daemon installed by pi-boot-config.
const char *MDNS_HOSTNAME = "ecobreeze-cam";

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("Connecting to WiFi \"%s\"", WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected, IP: %s\n", WiFi.localIP().toString().c_str());

  // connectWifi() also runs on reconnect, and MDNS.begin() fails if a previous
  // responder is still registered -- so tear the old one down first.
  MDNS.end();
  if (MDNS.begin(MDNS_HOSTNAME)) {
    MDNS.addService("http", "tcp", 80);
    Serial.printf("mDNS responder up: http://%s.local\n", MDNS_HOSTNAME);
  } else {
    Serial.println("mDNS responder failed to start -- fall back to the raw IP");
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(LED_PIN, OUTPUT);
  setLed(false);

  pinMode(LED_GREEN_PIN, OUTPUT);
  setGreenLed(false);

  pinMode(IR_TX_PIN, OUTPUT);
  digitalWrite(IR_TX_PIN, LOW);
  setAc(false);   // put the receiver's compressor state in a known place at boot

  setupCamera();

  bmeWire.begin(BME_SDA_PIN, BME_SCL_PIN);
  bme.setI2CAddress(0x76);
  bmeReady = bme.beginI2C(bmeWire);
  if (!bmeReady) {
    bme.setI2CAddress(0x77);
    bmeReady = bme.beginI2C(bmeWire);
  }
  Serial.println(bmeReady ? "BME280 ready" : "BME280 not found");

  connectWifi();

  server.on("/capture", handleCapture);
  server.on("/sensor", handleSensor);
  server.on("/i2cscan", handleI2cScan);
  server.on("/led", handleLedGet);
  server.on("/led/on", handleLedOn);
  server.on("/led/off", handleLedOff);
  server.on("/led/blue/on", handleBlueLedOn);
  server.on("/led/blue/off", handleBlueLedOff);
  server.on("/led/green/on", handleGreenLedOn);
  server.on("/led/green/off", handleGreenLedOff);
  server.begin();
  Serial.println("HTTP server started");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  server.handleClient();
}
