"""
Polls camera snapshots from the XIAO ESP32S3 over local HTTP, runs YOLO12n
person detection on each frame, classifies standing vs. lying down from the
detected person's bounding-box aspect ratio, and:
  - calls /led/on or /led/off on the board
  - writes the latest status (person + posture) to Supabase for the web
    dashboard to poll

Setup: pip install -r requirements.txt, then copy .env.example to .env and
fill in the ESP32's IP + Supabase credentials, then `python person_detect.py`.
"""

from __future__ import annotations

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
# Person box width / height ratio above which we call it "lying down".
# A standing person's box is much taller than wide (~0.3-0.5); lying down
# flips that to wider than tall.
LYING_ASPECT_RATIO_THRESHOLD = float(os.environ.get("LYING_ASPECT_RATIO_THRESHOLD", 1.2))
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


def fetch_sensor():
    r = requests.get(f"http://{ESP32_IP}/sensor", timeout=5)
    r.raise_for_status()
    data = r.json()
    return float(data["temperature"]), float(data["humidity"])


def set_led(on: bool) -> None:
    path = "on" if on else "off"
    requests.get(f"http://{ESP32_IP}/led/{path}", timeout=5)


def is_lying_down(box) -> bool:
    x1, y1, x2, y2 = box.xyxy[0]
    width = float(x2 - x1)
    height = float(y2 - y1)
    return height > 0 and (width / height) >= LYING_ASPECT_RATIO_THRESHOLD


def update_supabase(
    person_detected: bool,
    lying_detected: bool,
    confidence: float,
    temperature: float | None,
    humidity: float | None,
) -> None:
    url = f"{SUPABASE_URL}/rest/v1/device_status?device_id=eq.{DEVICE_ID}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    body = {
        "person_detected": person_detected,
        "lying_detected": lying_detected,
        "confidence": confidence,
        "temperature": temperature,
        "humidity": humidity,
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

    lying_present = False
    lying_hit_streak = 0
    lying_miss_streak = 0

    last_temp = None
    last_humidity = None

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
        best_box = None
        for box in results.boxes:
            if int(box.cls[0]) == PERSON_CLASS_ID and float(box.conf[0]) > best_conf:
                best_conf = float(box.conf[0])
                best_box = box

        person_in_frame = best_conf >= PERSON_CONF_THRESHOLD
        if person_in_frame:
            hit_streak += 1
            miss_streak = 0
        else:
            miss_streak += 1
            hit_streak = 0

        lying_now = person_in_frame and best_box is not None and is_lying_down(best_box)
        if lying_now:
            lying_hit_streak += 1
            lying_miss_streak = 0
        else:
            lying_miss_streak += 1
            lying_hit_streak = 0

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

        if not person_present:
            # Can't be lying down if nobody's there.
            if lying_present:
                state_changed = True
            lying_present = False
            lying_hit_streak = 0
            lying_miss_streak = 0
        elif not lying_present and lying_hit_streak >= ON_STREAK:
            lying_present = True
            state_changed = True
            print("Person lying down")
        elif lying_present and lying_miss_streak >= OFF_STREAK:
            lying_present = False
            state_changed = True
            print("Person standing back up")

        try:
            last_temp, last_humidity = fetch_sensor()
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"Sensor read failed: {e}")

        # Write a heartbeat even when nothing changed, so the dashboard can
        # tell "still watching, nobody there" apart from "server is down".
        now = time.monotonic()
        if state_changed or now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            last_heartbeat = now
            update_supabase(person_present, lying_present, best_conf, last_temp, last_humidity)

        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, POLL_INTERVAL_S - elapsed))


if __name__ == "__main__":
    main()
