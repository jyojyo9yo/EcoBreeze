"""
Polls camera snapshots from the XIAO ESP32S3 over local HTTP, runs YOLO12n
person detection on each frame, and:
  - calls /led/on or /led/off on the board
  - writes the latest status to Supabase for the web dashboard to poll

Setup: pip install -r requirements.txt, then copy .env.example to .env and
fill in the ESP32's IP + Supabase credentials, then `python person_detect.py`.
"""

import os
import time
from datetime import datetime, timezone

import cv2
import numpy as np
import requests
from dotenv import load_dotenv
from ultralytics import YOLO

load_dotenv()

DEVICE_ID = os.environ["DEVICE_ID"]
ESP32_IP = os.environ["ESP32_IP"]

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

PERSON_CONF_THRESHOLD = float(os.environ.get("PERSON_CONF_THRESHOLD", 0.5))
ON_STREAK = int(os.environ.get("ON_STREAK", 2))
OFF_STREAK = int(os.environ.get("OFF_STREAK", 3))
HEARTBEAT_INTERVAL_S = float(os.environ.get("HEARTBEAT_INTERVAL_S", 5))
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", 1.5))

PERSON_CLASS_ID = 0  # COCO "person"

print("Loading YOLO12n model...")
model = YOLO("yolo12n.pt")
print("Model loaded")


def fetch_frame():
    r = requests.get(f"http://{ESP32_IP}/capture", timeout=5)
    r.raise_for_status()
    return cv2.imdecode(np.frombuffer(r.content, dtype=np.uint8), cv2.IMREAD_COLOR)


def set_led(on: bool) -> None:
    path = "on" if on else "off"
    requests.get(f"http://{ESP32_IP}/led/{path}", timeout=5)


def update_supabase(person_detected: bool, confidence: float) -> None:
    url = f"{SUPABASE_URL}/rest/v1/device_status?device_id=eq.{DEVICE_ID}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    body = {
        "person_detected": person_detected,
        "confidence": confidence,
        # Postgres only applies "default now()" on INSERT, not UPDATE, so the
        # timestamp has to be set explicitly on every write here.
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        requests.patch(url, json=body, headers=headers, timeout=5)
    except requests.RequestException as e:
        print(f"Supabase update failed: {e}")


def main():
    person_present = False
    hit_streak = 0
    miss_streak = 0
    last_heartbeat = 0.0

    print(f"Polling http://{ESP32_IP}/capture every {POLL_INTERVAL_S}s...")
    while True:
        loop_start = time.monotonic()

        try:
            frame = fetch_frame()
        except requests.RequestException as e:
            print(f"Capture failed: {e}")
            time.sleep(POLL_INTERVAL_S)
            continue

        if frame is None:
            time.sleep(POLL_INTERVAL_S)
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

        state_changed = False
        if not person_present and hit_streak >= ON_STREAK:
            person_present = True
            state_changed = True
            print(f"Person detected (conf={best_conf:.2f}) -> LED ON")
            set_led(True)
        elif person_present and miss_streak >= OFF_STREAK:
            person_present = False
            state_changed = True
            print("No person -> LED OFF")
            set_led(False)

        # Write a heartbeat even when nothing changed, so the dashboard can
        # tell "still watching, nobody there" apart from "server is down".
        now = time.monotonic()
        if state_changed or now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            last_heartbeat = now
            update_supabase(person_present, best_conf)

        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, POLL_INTERVAL_S - elapsed))


if __name__ == "__main__":
    main()
