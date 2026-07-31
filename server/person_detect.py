"""
Polls camera snapshots from the XIAO ESP32S3 over local HTTP, runs YOLO12n
person detection on each frame, classifies standing vs. lying down from the
detected person's bounding-box aspect ratio, and:
  - calls /led/on or /led/off on the board for person presence
  - computes a PMV-based comfort band from the BME280 reading (see pmv.py,
    ported from the raspi/ teammate's comfort-band code) and fires the IR
    transmitter when the room is outside it -- unless the dashboard has
    taken manual control, in which case it just mirrors whatever the
    dashboard last set
  - shuts the AC off entirely once nobody has been detected for longer than
    the dashboard's absence delay, if that feature is switched on. This one
    overrides manual control, because an empty room should not be cooled no
    matter what the dashboard last set. Note the AC does not come back by
    itself when someone returns: the shutoff is written straight back to
    ac_on (see update_supabase), so the manual "on" the dashboard had set is
    already gone by then and the user has to switch it on again.
  - mirrors sleep mode onto the green LED, which also keys out an IR command so
    the receiver board's green LED follows. Sleep mode turns on either from the
    dashboard or, when sleep_auto_enabled is set, automatically once someone has
    been lying down for longer than the configured delay -- tolerating detection
    gaps of up to LYING_GRACE_S, since YOLO drops a lying person often enough
    that a strict "continuously" never accumulates minutes. The automatic
    trigger fires once per lying stretch, so switching sleep back off by hand
    does not have it come straight back on; the LED itself tracks the resulting state
    unconditionally, with no person-present gate.
  - writes the latest status to Supabase for the web dashboard to poll

The /led/blue/* and /led/green/* endpoints key out IR pulse frames on the board
rather than lighting a local LED -- see firmware/xiao_esp32s3_person_led for the
protocol and firmware/ecobreeze_receiver for the decoder.

Setup: pip install -r requirements.txt, then copy .env.example to .env and
fill in Supabase credentials, then `python person_detect.py`. The camera board
is found at ecobreeze-cam.local by default, so ESP32_IP is only a fallback.
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

# Camera board address. We try the mDNS name first and fall back to a fixed IP,
# because the board's DHCP address drifted between sessions and every drift meant
# editing ESP32_IP in two .env files (this machine and the Pi) before anything
# worked again. The board advertises itself as ecobreeze-cam.local -- see
# MDNS_HOSTNAME in firmware/xiao_esp32s3_person_led. ESP32_IP is now optional and
# only serves as a fallback for networks where mDNS is blocked.
ESP32_HOST = os.environ.get("ESP32_HOST", "ecobreeze-cam.local")
ESP32_IP = os.environ.get("ESP32_IP", "")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# 0.5 was too strict for the 160x120 frames this camera sends. Measured on a live
# frame with a person clearly in shot, close and well lit: YOLO returned the
# person at conf=0.401 -- rejected, so nothing was ever detected even though the
# camera was aimed correctly. Everything else in that frame scored 0.064 or below
# (a chair, plus a handful of sub-0.03 phantom "person" boxes), so 0.3 clears the
# real detection with room to spare while still sitting ~5x above the noise. The
# ON_STREAK=2 requirement filters one-frame flukes on top of that.
PERSON_CONF_THRESHOLD = float(os.environ.get("PERSON_CONF_THRESHOLD", 0.3))
# Person box width / height ratio above which we call it "lying down".
# A standing person's box is much taller than wide (~0.3-0.5); lying down
# flips that to wider than tall.
LYING_ASPECT_RATIO_THRESHOLD = float(os.environ.get("LYING_ASPECT_RATIO_THRESHOLD", 1.2))
# How long a break in the lying detection can last before the auto-sleep
# elapsed-time count gives up and restarts. ~13 frames at the default poll
# interval; the longest gap seen in a live lying test was 4.5s.
LYING_GRACE_S = float(os.environ.get("LYING_GRACE_S", 20))
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
DEFAULT_ABSENCE_DELAY_SEC = 1800

print("Loading YOLO12n model...")
model = YOLO("yolo12n.pt")
print("Model loaded")


_esp32_target = None


def esp32_target() -> str:
    """Address that the camera board actually answers on, cached once found.

    Probes the mDNS name first, then the fallback IP. Any failed request clears
    the cache (see esp32_get), so if the board reboots onto a different address
    the next loop iteration rediscovers it instead of failing forever.
    """
    global _esp32_target
    if _esp32_target:
        return _esp32_target
    for candidate in (ESP32_HOST, ESP32_IP):
        if not candidate:
            continue
        try:
            requests.get(f"http://{candidate}/led", timeout=3)
        except requests.RequestException:
            continue
        _esp32_target = candidate
        print(f"Camera board reachable at {candidate}")
        return candidate
    # Neither answered -- don't cache, so the next call probes again.
    return ESP32_HOST or ESP32_IP


def esp32_get(path: str, timeout: float = 5, check: bool = True):
    try:
        r = requests.get(f"http://{esp32_target()}{path}", timeout=timeout)
        if check:
            r.raise_for_status()
        return r
    except requests.RequestException:
        global _esp32_target
        _esp32_target = None      # re-resolve on the next call
        raise


def fetch_frame():
    r = esp32_get("/capture")
    return cv2.imdecode(np.frombuffer(r.content, dtype=np.uint8), cv2.IMREAD_COLOR)


def fetch_sensor():
    r = esp32_get("/sensor")
    data = r.json()
    return float(data["temperature"]), float(data["humidity"])


# The three setters below are fire-and-forget: the caller in the main loop has
# no except block around them, so they must never raise or one unreachable board
# would take the whole detection loop down. The IR endpoints get a longer timeout
# because the board keys out a pulse frame before replying (up to ~1.1s -- see
# sendPulses in firmware/xiao_esp32s3_person_led).
def set_led(on: bool) -> None:
    path = "on" if on else "off"
    try:
        esp32_get(f"/led/{path}", check=False)
    except requests.RequestException as e:
        print(f"LED set failed: {e}")


def set_ac(on: bool) -> None:
    # Endpoint name is a holdover from when this drove a blue LED directly -- the
    # board now keys out an IR pulse frame (1 = on, 2 = off) on the same call,
    # which the receiver board turns into its blue compressor LED.
    path = "on" if on else "off"
    try:
        esp32_get(f"/led/blue/{path}", timeout=8, check=False)
    except requests.RequestException as e:
        print(f"AC IR send failed: {e}")


def set_green_led(on: bool) -> None:
    # Also keys out an IR pulse frame (5 = sleep on, 6 = sleep off) so the
    # receiver board's green sleep LED tracks this too.
    path = "on" if on else "off"
    try:
        esp32_get(f"/led/green/{path}", timeout=8, check=False)
    except requests.RequestException as e:
        print(f"Sleep IR send failed: {e}")


_SETTINGS_BASE = "ai_sensitivity,ac_on,ac_manual,sleep_on,sleep_manual,sleep_delay_sec"
_SETTINGS_ADDED = ",absence_enabled,absence_delay_sec,sleep_auto_enabled"

# Dropped to _SETTINGS_BASE the first time Supabase says it has no absence
# columns, so we stop re-asking for them every loop.
_settings_select = _SETTINGS_BASE + _SETTINGS_ADDED


def fetch_settings() -> dict:
    """Reads the dashboard-controlled settings (AI sensitivity, manual
    overrides) back from Supabase.

    Retries without the newer columns if they are missing. PostgREST fails the
    *whole* query with a 400 when a single selected column does not exist, so
    pulling this code onto a device before running the migration in
    supabase_setup.sql would otherwise take AC manual control, sleep mode and
    the AI sensitivity down with it, rather than just leaving the newer
    features switched off.
    """
    global _settings_select
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    base = f"{SUPABASE_URL}/rest/v1/device_status?device_id=eq.{DEVICE_ID}&select="
    r = requests.get(base + _settings_select, headers=headers, timeout=5)
    if r.status_code == 400 and _settings_select != _SETTINGS_BASE:
        print(
            "Supabase is missing absence_enabled/absence_delay_sec/"
            "sleep_auto_enabled -- run server/supabase_setup.sql. Absence "
            "auto-off and auto-sleep stay off until then."
        )
        _settings_select = _SETTINGS_BASE
        r = requests.get(base + _settings_select, headers=headers, timeout=5)
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
    lying_auto_since = None  # monotonic time when the lying stretch started
    lying_last_seen = 0.0    # monotonic time of the most recent lying frame
    absent_since = None      # monotonic time when the room last went empty
    sleep_auto_fired = False  # auto-sleep already fired for this lying stretch

    last_heartbeat = 0.0

    print(f"Polling http://{esp32_target()}/capture every {POLL_INTERVAL_S}s...")
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
            # Can't be lying down if nobody's there -- but leave the streaks
            # alone. Zeroing them here threw away the first lying frame after
            # every person-detection dropout: person_present needs ON_STREAK
            # frames to come back, and this reset fired on each of those
            # frames, so the lying streak could not start counting until the
            # person had already been re-acquired. Measured on a live test
            # where someone lay down and the detection blinked out: lying
            # frames arrived at 30.5s and 32.0s but "lying" only latched at
            # 39.5s, on the *next* pair. lying_now is already gated on
            # person_in_frame, so frames with nobody in them still count as
            # misses and a real departure still clears the streak.
            if lying_present:
                state_changed = True
            lying_present = False
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
        absence_delay_sec = settings.get("absence_delay_sec") or DEFAULT_ABSENCE_DELAY_SEC

        now_mono = time.monotonic()

        # Tracks how long nobody has been detected, so the AC can be shut off
        # after the dashboard's configured delay. person_present is already
        # debounced by OFF_STREAK, and any detection at all restarts the clock,
        # so the short detection dropouts this camera produces do not add up to
        # a false "room is empty" as long as the delay is more than a few
        # seconds.
        if person_present:
            absent_since = None
        elif absent_since is None:
            absent_since = now_mono

        absence_expired = (
            bool(settings.get("absence_enabled"))
            and absent_since is not None
            and now_mono - absent_since >= absence_delay_sec
        )

        # Deliberately ahead of ac_manual: the feature exists so an empty room
        # does not get cooled, which means it has to win over whatever the
        # dashboard last set by hand.
        #
        # This is one-way. Once it fires, update_supabase writes the resulting
        # ac_on=false back to the same column the ac_manual branch below reads,
        # so a returning person does not get the AC back -- the manual "on" has
        # already been overwritten, and the dashboard has to switch it on
        # again. Measured live: after "AC OFF (absence >20s)" the next
        # "Person detected" produced no AC ON.
        if absence_expired:
            new_ac_on = False
        elif settings.get("ac_manual"):
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
            if absence_expired:
                mode = f"absence >{absence_delay_sec}s"
            elif settings.get("ac_manual"):
                mode = "manual"
            else:
                mode = "auto"
            print(f"AC {'ON' if ac_on else 'OFF'} ({mode}) -> IR")
            set_ac(ac_on)

        # Tracks how long the person has been lying down, so sleep mode can
        # auto-trigger after the dashboard's configured delay. The elapsed
        # count survives gaps of up to LYING_GRACE_S: YOLO loses a lying
        # person far more often than a standing one (conf drops from ~0.85 to
        # ~0.30 once they're horizontal), and resetting on the first missed
        # frame meant the count restarted every few seconds and could never
        # reach a delay measured in minutes. Anyone who actually gets up and
        # leaves still clears it, just LYING_GRACE_S later.
        if lying_present:
            if lying_auto_since is None:
                lying_auto_since = now_mono
            lying_last_seen = now_mono
        elif lying_auto_since is not None and now_mono - lying_last_seen > LYING_GRACE_S:
            lying_auto_since = None
            # Getting up re-arms the auto trigger for the next lying stretch.
            sleep_auto_fired = False

        # Auto-on is its own switch now (sleep_auto_enabled), checked before
        # sleep_manual rather than only when sleep_manual is false. It used to
        # be the else branch of sleep_manual, which meant the first time anyone
        # touched the dashboard toggle sleep_manual latched true forever and
        # auto never fired again -- there was no way back to automatic short of
        # editing the column by hand.
        #
        # It fires once per lying stretch: sleep_auto_fired stays set until the
        # person gets up, so switching sleep off by hand while still lying down
        # keeps it off instead of having it come straight back on.
        sleep_auto_expired = (
            bool(settings.get("sleep_auto_enabled"))
            and not sleep_auto_fired
            and not sleep_on
            and lying_auto_since is not None
            and now_mono - lying_auto_since >= sleep_delay_sec
        )

        if sleep_auto_expired:
            new_sleep_on = True
            sleep_auto_fired = True
        elif settings.get("sleep_manual"):
            new_sleep_on = bool(settings.get("sleep_on"))
        else:
            new_sleep_on = sleep_on

        if new_sleep_on != sleep_on:
            sleep_on = new_sleep_on
            state_changed = True
            if sleep_auto_expired:
                mode = f"auto, lying >{sleep_delay_sec}s"
            elif settings.get("sleep_manual"):
                mode = "manual"
            else:
                mode = "held"
            print(f"Sleep mode {'ON' if sleep_on else 'OFF'} ({mode})")

        # Track sleep mode directly, with no person_present gate. The LED used to
        # light only while someone was detected -- reasonable for a real room, but
        # it meant toggling sleep mode on the dashboard did nothing visible unless
        # the camera happened to see a person right then, which is not how you want
        # to demo a switch.
        new_green_led_on = sleep_on
        if new_green_led_on != green_led_on:
            green_led_on = new_green_led_on
            print(f"Green LED {'ON' if green_led_on else 'OFF'} (sleep_on={sleep_on})")
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
