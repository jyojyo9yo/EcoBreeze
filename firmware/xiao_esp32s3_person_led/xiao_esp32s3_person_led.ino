// XIAO ESP32S3 Sense: publishes camera snapshots over MQTT and lights the
// onboard LED when the inference server (running YOLO12n) reports a person.
//
// Data flow:
//   this board --publish JPEG--> MQTT broker --subscribe--> server/person_detect.py (YOLO12n)
//   server/person_detect.py --publish ON/OFF--> MQTT broker --subscribe--> this board (LED)
//   server/person_detect.py also writes the result to Supabase for the web dashboard.
//
// Requires secrets.h (copy secrets.h.example -> secrets.h and fill in real values).

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include "esp_camera.h"
#include "camera_pins.h"
#include "secrets.h"

// XIAO ESP32S3 onboard user LED: GPIO21, active-LOW
const int LED_PIN = LED_BUILTIN;

const char *TOPIC_FRAME = "ecobreeze/" DEVICE_ID "/frame";
const char *TOPIC_LED = "ecobreeze/" DEVICE_ID "/led";

const unsigned long CAPTURE_INTERVAL_MS = 1500;
unsigned long lastCaptureMs = 0;

WiFiClientSecure secureClient;
PubSubClient mqttClient(secureClient);

void setLed(bool on) {
  digitalWrite(LED_PIN, on ? LOW : HIGH);
}

void onMqttMessage(char *topic, byte *payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();

  if (String(topic) == TOPIC_LED) {
    if (msg == "ON") {
      setLed(true);
    } else if (msg == "OFF") {
      setLed(false);
    }
  }
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

  // Kept small (QQVGA) so a JPEG frame comfortably fits one MQTT publish over
  // TLS. fb_count=1 + GRAB_WHEN_EMPTY captures on demand instead of streaming
  // continuously -- streaming with fb_count=2/GRAB_LATEST flooded the serial
  // log with "cam_hal: FB-OVF" and starved the WiFi/MQTT task, killing the
  // MQTT connection every cycle.
  config.frame_size = FRAMESIZE_QQVGA; // 160x120
  config.jpeg_quality = psramFound() ? 14 : 15;
  config.fb_count = 1;
  config.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
  }
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

void connectMqtt() {
  while (!mqttClient.connected()) {
    Serial.print("Connecting to MQTT broker...");
    String clientId = String(DEVICE_ID) + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    if (mqttClient.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD)) {
      Serial.println(" connected");
      mqttClient.subscribe(TOPIC_LED);
    } else {
      Serial.printf(" failed, rc=%d, retrying in 2s\n", mqttClient.state());
      delay(2000);
    }
  }
}

void publishFrame() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Camera capture failed");
    return;
  }
  if (mqttClient.publish(TOPIC_FRAME, fb->buf, fb->len)) {
    Serial.printf("Published frame (%u bytes)\n", fb->len);
  } else {
    Serial.println("Frame publish failed");
  }
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);

  pinMode(LED_PIN, OUTPUT);
  setLed(false);

  setupCamera();
  connectWifi();

  secureClient.setInsecure(); // skip broker cert validation (fine for a hackathon demo)
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCallback(onMqttMessage);
  mqttClient.setBufferSize(24576); // fit a QVGA JPEG frame
  mqttClient.setKeepAlive(30);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  if (!mqttClient.connected()) {
    connectMqtt();
  }
  mqttClient.loop();

  unsigned long now = millis();
  if (now - lastCaptureMs >= CAPTURE_INTERVAL_MS) {
    lastCaptureMs = now;
    publishFrame();
  }

  delay(20); // avoid hammering the TLS socket with a tight busy-loop
}
