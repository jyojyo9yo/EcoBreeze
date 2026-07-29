// XIAO ESP32S3 Sense: serves camera snapshots and BME280 temperature/humidity
// readings over a local HTTP server, and lights LEDs when told to -- the
// onboard one for person detection, plus two discrete ones wired to D1
// (blue, AC-on indicator) and D0 (green, sleep-mode indicator). The
// inference server (running YOLO12n, on the same Wi-Fi network) polls
// /capture and /sensor and calls the /led/* endpoints based on what it sees
// -- see server/person_detect.py.
//
// Requires secrets.h (copy secrets.h.example -> secrets.h and fill in real values).
// Requires the "SparkFun BME280" library installed via Arduino IDE's Library
// Manager. (Not "Adafruit BME280 Library": its Adafruit_Sensor dependency
// redeclares `sensor_t`, which conflicts with the esp32-camera driver's own
// `sensor_t` typedef and fails to compile.)

#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <SparkFunBME280.h>
#include "esp_camera.h"
#include "camera_pins.h"
#include "secrets.h"

// XIAO ESP32S3 onboard user LED: GPIO21, active-LOW
const int LED_PIN = LED_BUILTIN;

// Discrete LEDs on the XIAO's D0/D1 pins. Assumes standard wiring (GPIO ->
// resistor -> LED anode -> cathode -> GND), i.e. active-HIGH -- if wired the
// other way around, swap the HIGH/LOW in setBlueLed/setGreenLed below.
// (Empirically GPIO2 lights the green LED and GPIO1 lights the blue one --
// opposite of the nominal D1/D0 assumption -- so wired to match reality.)
const int LED_BLUE_PIN = 1;
const int LED_GREEN_PIN = 2;

// BME280 over I2C on the XIAO's default SDA/SCL pins. This has to be a
// second I2C bus (not the shared `Wire` instance) because the camera's SCCB
// control bus already occupies the chip's first I2C peripheral -- reusing it
// here causes Wire.begin() to fail against the camera driver.
const int BME_SDA_PIN = 5;
const int BME_SCL_PIN = 6;
TwoWire bmeWire = TwoWire(1);
BME280 bme;
bool bmeReady = false;

WebServer server(80);
bool ledOn = false;
bool blueLedOn = false;
bool greenLedOn = false;

void setLed(bool on) {
  ledOn = on;
  digitalWrite(LED_PIN, on ? LOW : HIGH);
}

void setBlueLed(bool on) {
  blueLedOn = on;
  digitalWrite(LED_BLUE_PIN, on ? HIGH : LOW);
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
  setBlueLed(true);
  server.send(200, "text/plain", "1");
}

void handleBlueLedOff() {
  setBlueLed(false);
  server.send(200, "text/plain", "0");
}

void handleGreenLedOn() {
  setGreenLed(true);
  server.send(200, "text/plain", "1");
}

void handleGreenLedOff() {
  setGreenLed(false);
  server.send(200, "text/plain", "0");
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

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("Connecting to WiFi \"%s\"", WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected, IP: %s\n", WiFi.localIP().toString().c_str());
}

void setup() {
  Serial.begin(115200);

  pinMode(LED_PIN, OUTPUT);
  setLed(false);

  pinMode(LED_BLUE_PIN, OUTPUT);
  pinMode(LED_GREEN_PIN, OUTPUT);
  setBlueLed(false);
  setGreenLed(false);

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
