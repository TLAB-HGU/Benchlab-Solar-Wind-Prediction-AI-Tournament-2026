#!/usr/bin/env python3
"""Download NOAA ACE/SWEPAM hourly archives; standard library only."""

import argparse
import csv
import hashlib
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
HOUR = timedelta(hours=1)
SOURCE = "https://sohoftp.nascom.nasa.gov/sdb/ace/monthly"
MAX_AGE = timedelta(hours=72)
MONTH_NAMES = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp(value):
    try:
        result = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Timestamp requires a UTC offset")
        result = result.astimezone(UTC)
    if result.minute or result.second or result.microsecond:
        raise ValueError("Timestamp must be on an hourly boundary")
    return result


def months_between(start, end):
    month = start.replace(day=1, hour=0)
    while month < end:
        yield month
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def parse_archive(data, month):
    rows = {}
    issued = None
    headers = []
    for line in data.decode("ascii").splitlines():
        if line.startswith(":Issued:"):
            headers.append(line)
        fields = line.split()
        if not fields or not fields[0].isdigit():
            continue
        if len(fields) != 10:
            raise ValueError("Malformed ACE data row")
        year, mo, day = map(int, fields[:3])
        hhmm = fields[3]
        if len(hhmm) != 4 or not hhmm.isdigit():
            raise ValueError("Invalid observation time")
        observed = datetime(year, mo, day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=UTC)
        if observed.minute or observed in rows:
            raise ValueError("Duplicate or nonhourly observation")
        if (year, mo) != (month.year, month.month):
            raise ValueError("Observation outside requested archive month")
        rows[observed] = (float(fields[8]), int(fields[6]))
    if len(headers) == 1:
        try:
            _, year, mo, day, hhmm, zone = headers[0].split()
            if zone != "UT" or len(hhmm) != 4:
                raise ValueError("Invalid issue time")
            issued = datetime(int(year), MONTH_NAMES.index(mo) + 1, int(day),
                              int(hhmm[:2]), int(hhmm[2:]), tzinfo=UTC)
        except ValueError:
            pass
    if not rows:
        raise ValueError("Archive contains no observations")
    return rows, issued


def download(month, out):
    filename = month.strftime("%Y%m_ace_swepam_1h.txt")
    url = f"{SOURCE}/{filename}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError("Archive exceeds 2 MB limit")
            break
        except (OSError, urllib.error.URLError) as exc:
            if attempt == 2:
                raise
            print(f"[data] retry {attempt + 1}: {url}: {exc}", file=sys.stderr)
            time.sleep(attempt + 1)
    raw = out / "raw" / filename
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(data)
    rows, issued = parse_archive(data, month)
    return rows, issued, {
        "url": url, "file": f"raw/{filename}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "retrieved_at_utc": iso(datetime.now(UTC)),
        "issued_at_utc": iso(issued) if issued else None,
        "rows": len(rows),
    }


def valid_speed(value):
    return value is not None and math.isfinite(value) and value > 0


def collect(start, end, out, cutoff=None):
    """Write [start, end) hourly data; cutoff additionally enforces publication time."""
    if start >= end:
        raise ValueError("Start must precede end")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "input.json").unlink(missing_ok=True)
    rows, sources = {}, []
    for month in months_between(start, end):
        try:
            batch, issued, meta = download(month, out)
        except (OSError, ValueError) as exc:
            if cutoff is None:
                raise
            sources.append({"month": month.strftime("%Y-%m"), "accepted": False,
                            "reason": str(exc)})
            print(f"[data] excluded {month:%Y-%m}: {exc}", file=sys.stderr)
            continue
        accepted = cutoff is None or (issued is not None and issued <= cutoff)
        meta["accepted"] = accepted
        if not accepted:
            meta["reason"] = "Missing/invalid issue time or file issued after t0"
            print(f"[data] excluded {meta['file']}: {meta['reason']}", file=sys.stderr)
        sources.append(meta)
        if accepted:
            for observed, (speed, status) in batch.items():
                if cutoff is None or observed <= cutoff:
                    if observed in rows:
                        raise ValueError("Overlapping archive observations")
                    rows[observed] = (speed, status, issued)
    metadata = {"mode": "inference" if cutoff else "archive",
                "start_utc": iso(start), "end_exclusive_utc": iso(end),
                "t0": iso(cutoff) if cutoff else None, "sources": sources}
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    last_speed = last_time = last_issue = None
    for observed in sorted(t for t in rows if t < start):
        speed, _, issued = rows[observed]
        if valid_speed(speed):
            last_speed, last_time, last_issue = speed, observed, issued
    with (out / "hourly.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp_utc", "raw_speed_kms", "status", "filled_speed_kms",
                         "was_missing", "last_observation_utc"])
        observed = start
        while observed < end:
            speed, status, issued = rows.get(observed, (None, None, None))
            missing = not valid_speed(speed)
            if not missing:
                last_speed, last_time, last_issue = speed, observed, issued
            writer.writerow([iso(observed), speed, status, last_speed, int(missing),
                             iso(last_time) if last_time else None])
            observed += HOUR
    if cutoff is not None:
        if last_time is None or cutoff - last_time > MAX_AGE:
            raise ValueError("No eligible valid observation within 72 hours of t0")
        prepared = {"t0": iso(cutoff), "speed_kms": last_speed,
                    "observation_utc": iso(last_time), "issued_at_utc": iso(last_issue)}
        (out / "input.json").write_text(json.dumps(prepared, indent=2) + "\n")
        print(f"[data] observation={iso(last_time)} issued={iso(last_issue)} "
              f"age_hours={(cutoff-last_time)/HOUR:g} speed_kms={last_speed}", file=sys.stderr)
        return prepared
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    inference = sub.add_parser("inference", help="Enforce file publication cutoff")
    inference.add_argument("--t0", required=True, type=timestamp)
    archive = sub.add_parser("archive", help="Historical download; NOT publication-safe")
    archive.add_argument("--start", required=True, type=timestamp)
    archive.add_argument("--end", required=True, type=timestamp, help="Exclusive end")
    for command in (inference, archive):
        command.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        (args.out / "input.json").unlink(missing_ok=True)
        if args.mode == "inference":
            first = args.t0.replace(day=1, hour=0)
            previous = (first - timedelta(days=1)).replace(day=1)
            collect(previous, args.t0 + HOUR, args.out, cutoff=args.t0)
        else:
            collect(args.start, args.end, args.out)
    except (OSError, ValueError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
