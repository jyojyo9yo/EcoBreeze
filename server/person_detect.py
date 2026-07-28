"""
Subscribes to camera frames published by the XIAO ESP32S3 over MQTT, runs
YOLO12n person detection on each frame, and:
  - publishes ON/OFF to the board's LED command topic
  - writes the latest status to Supabase for the web dashboard to poll

Setup: pip install -r requirements.txt, then copy .env.example to .env and
fill in the MQTT + Supabase credentials, then `python person_detect.py`.
"""

import os
import queue

import cv2
import numpy as np
import requests
from dotenv import load_dotenv
from paho.mqtt import client as mqtt
from ultralytics import YOLO

load_dotenv()

DEVICE_ID = os.environ["DEVICE_ID"]
MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ["MQTT_PORT"])
MQTT_USERNAME = os.environ["MQTT_USERNAME"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

PERSON_CONF_THRESHOLD = float(os.environ.get("PERSON_CONF_THRESHOLD", 0.5))
ON_STREAK = int(os.environ.get("ON_STREAK", 2))
OFF_STREAK = int(os.environ.get("OFF_STREAK", 3))

TOPIC_FRAME = f"ecobreeze/{DEVICE_ID}/frame"
TOPIC_LED = f"ecobreeze/{DEVICE_ID}/led"

PERSON_CLASS_ID = 0  # COCO "person"

frame_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=1)
print("Loading YOLO12n model...")
model = YOLO("yolo12n.pt")
print("Model loaded")


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("MQTT connected")
        client.subscribe(TOPIC_FRAME)
    else:
        print(f"MQTT connect failed, rc={rc}")


def on_message(client, userdata, msg):
    # Keep only the newest frame; drop older ones if inference is behind.
    if frame_queue.full():
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            pass
    frame_queue.put(msg.payload)


def update_supabase(person_detected: bool, confidence: float) -> None:
    url = f"{SUPABASE_URL}/rest/v1/device_status?device_id=eq.{DEVICE_ID}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    body = {"person_detected": person_detected, "confidence": confidence}
    try:
        requests.patch(url, json=body, headers=headers, timeout=5)
    except requests.RequestException as e:
        print(f"Supabase update failed: {e}")


def main():
    client = mqtt.Client(client_id=f"{DEVICE_ID}-server")
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message
    print("Connecting to MQTT broker...")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()

    person_present = False
    hit_streak = 0
    miss_streak = 0

    print("Waiting for frames...")
    while True:
        payload = frame_queue.get()
        frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            continue

        results = model(frame, verbose=False)[0]
        best_conf = 0.0
        for box in results.boxes:
            if int(box.cls[0]) == PERSON_CLASS_ID:
                best_conf = max(best_conf, float(box.conf[0]))

        person_in_frame = best_conf >= PERSON_CONF_THRESHOLD
        if person_in_frame:
            hit_streak += 1
            miss_streak = 0
        else:
            miss_streak += 1
            hit_streak = 0

        if not person_present and hit_streak >= ON_STREAK:
            person_present = True
            print(f"Person detected (conf={best_conf:.2f}) -> LED ON")
            client.publish(TOPIC_LED, "ON", retain=True)
            update_supabase(True, best_conf)
        elif person_present and miss_streak >= OFF_STREAK:
            person_present = False
            print("No person -> LED OFF")
            client.publish(TOPIC_LED, "OFF", retain=True)
            update_supabase(False, best_conf)


if __name__ == "__main__":
    main()
