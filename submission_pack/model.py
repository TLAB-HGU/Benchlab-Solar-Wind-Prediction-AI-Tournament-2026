#!/usr/bin/env python3
"""Naive forecast from the latest eligible ACE/SWEPAM hourly speed."""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from ace_data import MAX_AGE, iso, timestamp, valid_speed

FORECAST_HOURS = 72


def predict(t0, prepared):
    observed = timestamp(prepared["observation_utc"])
    issued = datetime.fromisoformat(prepared["issued_at_utc"].replace("Z", "+00:00"))
    if timestamp(prepared["t0"]) != t0 or issued.tzinfo is None or issued > t0:
        raise ValueError("Prepared input does not satisfy t0 publication cutoff")
    if not timedelta(0) <= t0 - observed <= MAX_AGE:
        raise ValueError("Observation is in the future or older than 72 hours")
    speed = float(prepared["speed_kms"])
    if not valid_speed(speed):
        raise ValueError("Invalid input speed")
    return [speed] * FORECAST_HOURS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("t0", type=timestamp)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        speeds = predict(args.t0, json.loads(args.input.read_text()))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    json.dump({"t0": iso(args.t0), "valid_from": iso(args.t0 + timedelta(hours=1)),
               "forecast_speed_kms": speeds}, sys.stdout, allow_nan=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
