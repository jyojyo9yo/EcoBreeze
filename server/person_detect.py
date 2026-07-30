"""
Polls camera snapshots from the XIAO ESP32S3 over local HTTP, runs YOLO12n
person detection on each frame, classifies standing vs. lying down from the
detected person's bounding-box aspect ratio, and:
  - calls /led/on or /led/off on the board for person presence
  - computes a PMV-based comfort band from the BME280 reading (see pmv.py,
    ported from the raspi/ teammate's comfort-band code) and fires the IR
    transmitter (D2) when the room is outside it -- unless the dashboard has
    taken manual control, in which case it just mirrors whatever the
    dashboard last set
  - drives the green LED (D0) once someone has been lying down continuously
    for longer than the dashboard's configured sleep-mode delay, same
    manual-override rule
  - writes the latest status to Supabase for the web dashboard to poll

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

from pmv import solve_ta_for_target_pmv

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

# PMV parameters (sleep-condition assumptions), matching raspi/config.py so
# the "optimal temperature" comes out the same on both systems.
PMV_MET = 0.75
PMV_CLO = 1.0
PMV_AIR_VEL = 0.05
PMV_CENTER_TARGET = -0.2
PMV_SOLVE_MIN_TA = 15.0
PMV_SOLVE_MAX_TA = 32.0
PMV_SOLVE_EPS = 0.01

# AI 모드 half-width around the PMV center, in degrees C -- outside
# [center - half_width, center + half_width] counts as "AC should be on".
SENSITIVITY_HALF_WIDTH_C = {"low": 0.7, "normal": 0.5, "high": 0.2}
DEFAULT_SENSITIVITY = "normal"
DEFAULT_SLEEP_DELAY_SEC = 600

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


def set_ac(on: bool) -> None:
    # Endpoint name is a holdover from when this drove a blue LED directly --
    # the board now fires an IR transmitter (D2) on the same on/off call.
    path = "on" if on else "off"
    requests.get(f"http://{ESP32_IP}/led/blue/{path}", timeout=5)


def set_green_led(on: bool) -> None:
    path = "on" if on else "off"
    requests.get(f"http://{ESP32_IP}/led/green/{path}", timeout=5)


def fetch_settings() -> dict:
    """Reads the dashboard-controlled settings (AI sensitivity, manual
    overrides) back from Supabase."""
    url = (
        f"{SUPABASE_URL}/rest/v1/device_status?device_id=eq.{DEVICE_ID}"
        "&select=ai_sensitivity,ac_on,ac_manual,sleep_on,sleep_manual,sleep_delay_sec"
    )
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    r = requests.get(url, headers=headers, timeout=5)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


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
    ac_on: bool,
    sleep_on: bool,
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
        "ac_on": ac_on,
        "sleep_on": sleep_on,
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

    ac_on = False
    sleep_on = False
    green_led_on = False
    lying_auto_since = None  # monotonic time when continuous lying started

    last_heartbeat = 0.0

    print(f"Polling http://{ESP32_IP}/capture every {POLL_INTERVAL_S}s...")
    while True:
        loop_start = time.monotonic()

        frame = None
        try:
            frame = fetch_frame()
        except requests.RequestException as e:
            print(f"Capture failed: {e}")

        # No camera attached yet is a normal, ongoing state (not a transient
        # error) -- fall through so the BME280 reading and heartbeat below
        # still reach Supabase instead of getting skipped every loop.
        best_conf = 0.0
        best_box = None
        if frame is not None:
            results = model(frame, verbose=False)[0]
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

        settings = {}
        try:
            settings = fetch_settings()
        except (requests.RequestException, ValueError, IndexError) as e:
            print(f"Settings read failed: {e}")

        sensitivity = settings.get("ai_sensitivity") or DEFAULT_SENSITIVITY
        sleep_delay_sec = settings.get("sleep_delay_sec") or DEFAULT_SLEEP_DELAY_SEC

        if settings.get("ac_manual"):
            new_ac_on = bool(settings.get("ac_on"))
        elif last_temp is not None and last_humidity is not None:
            half_width = SENSITIVITY_HALF_WIDTH_C.get(sensitivity, SENSITIVITY_HALF_WIDTH_C[DEFAULT_SENSITIVITY])
            center = solve_ta_for_target_pmv(
                PMV_CENTER_TARGET, last_humidity, PMV_AIR_VEL, PMV_MET, PMV_CLO,
                PMV_SOLVE_MIN_TA, PMV_SOLVE_MAX_TA, PMV_SOLVE_EPS,
            )
            new_ac_on = last_temp < center - half_width or last_temp > center + half_width
        else:
            new_ac_on = ac_on

        if new_ac_on != ac_on:
            ac_on = new_ac_on
            state_changed = True
            mode = "manual" if settings.get("ac_manual") else "auto"
            print(f"AC {'ON' if ac_on else 'OFF'} ({mode}) -> IR")
            set_ac(ac_on)

        # Tracks how long the person has been lying down continuously, so
        # sleep mode can auto-trigger after the dashboard's configured delay.
        if lying_present:
            if lying_auto_since is None:
                lying_auto_since = time.monotonic()
        else:
            lying_auto_since = None

        if settings.get("sleep_manual"):
            new_sleep_on = bool(settings.get("sleep_on"))
        elif (
            not sleep_on
            and lying_auto_since is not None
            and time.monotonic() - lying_auto_since >= sleep_delay_sec
        ):
            new_sleep_on = True
        else:
            new_sleep_on = sleep_on

        if new_sleep_on != sleep_on:
            sleep_on = new_sleep_on
            state_changed = True
            mode = "manual" if settings.get("sleep_manual") else "auto"
            print(f"Sleep mode {'ON' if sleep_on else 'OFF'} ({mode})")

        # Sleep mode is a setting (shown as "on" on the dashboard even while
        # the room is briefly empty), but the physical green LED should only
        # ever be lit while someone is actually there to see it.
        new_green_led_on = sleep_on and person_present
        if new_green_led_on != green_led_on:
            green_led_on = new_green_led_on
            print(f"Green LED {'ON' if green_led_on else 'OFF'} (sleep_on={sleep_on}, person_present={person_present})")
            set_green_led(green_led_on)

        # Write a heartbeat even when nothing changed, so the dashboard can
        # tell "still watching, nobody there" apart from "server is down".
        now = time.monotonic()
        if state_changed or now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            last_heartbeat = now
            update_supabase(
                person_present, lying_present, best_conf, last_temp, last_humidity, ac_on, sleep_on
            )

        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, POLL_INTERVAL_S - elapsed))


if __name__ == "__main__":
    main()
