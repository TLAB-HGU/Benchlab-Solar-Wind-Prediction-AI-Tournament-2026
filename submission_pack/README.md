# ACE/SWEPAM hourly data and Naive forecast

This workflow downloads NOAA SWPC ACE/SWEPAM hourly bulk speed from the
[NASA SOHO monthly archive](https://sohoftp.nascom.nasa.gov/sdb/ace/monthly/).
It uses the same public product as our local baseline analysis, without L2 or
PostgreSQL access. Every forecast repeats the latest eligible speed for 72 hours.
The archive is not asserted to be identical to Benchlab's final scoring data.

## Execution

```text
run.sh <t0>
  → ace_data.py inference: download previous/current month, validate, prepare input
  → model.py: repeat last valid speed for t0+1h … t0+72h
  → upload.sh: POST forecast.json to the platform's OUTPUT_POST destination
```

`t0` uses the platform format, for example `20260926T080000Z`.
The output retains `t0`, `valid_from`, and `forecast_speed_kms` (72 numbers in km/s).
Each run uses a fresh temporary directory, which is removed on exit. No persistent
cache, credentials, trained weights, or third-party Python packages are required.
The image includes Python, CA certificates, curl, and jq. The runtime source domain
`sohoftp.nascom.nasa.gov` is declared in `submission.json`.

## Publication cutoff and missing data

- Only files with one valid `:Issued:` header at or before `t0` are accepted for
  inference. Missing, malformed, duplicate, or later issue headers exclude a file.
- Only observations at or before `t0` can affect predictions. This is a conservative
  file-level publication check: even old rows in a file issued after `t0` are excluded.
- A finite positive speed is valid, regardless of its status flag, matching our
  existing analysis policy. The original status is retained. This is not a claim
  about the organizer's final quality-control policy.
- Missing rows, nonfinite speeds, and nonpositive speeds (including `-9999.9`) are
  missing data. Fill uses only preceding valid observations; leading gaps stay empty.
- Freshness is measured from the actual observation, never from a filled row.
  Exactly 72 hours of age is allowed; older observations fail inference.
- Each month gets at most three HTTP attempts, with a 20-second socket timeout per
  attempt and 1/2-second retry delays. Responses are limited to 2 MB per file.
  An unavailable or malformed month can be excluded if the other month supplies an
  eligible observation. Without an eligible observation, the process exits nonzero
  and does not invoke the uploader. There is no constant-speed fallback.

Monthly files change as new observations arrive. A file refreshed after the cycle
cutoff can make that run fail, even when recent historical rows are present. A past
`t0` supplied today often fails for the same reason. This strict behavior is intentional;
archive-only backtests do not prove availability at historical inference times.

## Download data for analysis

Run from `submission_pack/`:

```bash
python3 ace_data.py archive \
  --start 2025-09-26T00:00:00Z --end 2026-09-26T00:00:00Z \
  --out data/analysis
```

The interval is `[start, end)`; timestamps require an explicit timezone and hourly
boundaries. Platform timestamp syntax is also accepted. The command downloads all
intersecting months afresh. Earlier valid rows within the first downloaded month
may seed forward fill; it does not search additional months for a seed.

Outputs:

- `raw/YYYYMM_ace_swepam_1h.txt`: downloaded originals, including missing sentinels.
- `hourly.csv`: UTC timestamp, raw speed, status, filled speed, missing mask, and
  timestamp of the last real observation.
- `metadata.json`: mode, requested interval, source URLs, issue/retrieval times,
  SHA-256 hashes, and file acceptance decisions.

Archive mode does **not** enforce historical publication availability and never
creates model input. Preserve a download directory to retain its exact snapshot;
rerunning in that directory refreshes the files.

## Prepare and inspect an inference locally

```bash
T0=$(date -u +%Y%m%dT%H0000Z)
python3 ace_data.py inference --t0 "$T0" --out data/inference
python3 model.py "$T0" --input data/inference/input.json
```

Inference additionally creates `input.json` containing the selected speed, actual
observation time, source issue time, and `t0`. Failed refreshes remove any previous
`input.json`. Model diagnostics go to stderr; stdout contains only forecast JSON.

## Validation

```bash
# Deterministic offline tests; jq and curl enable the real uploader/mock test.
python3 -m unittest discover -s local_tests -p 'test_*.py' -v

# Actual linux/amd64 container and mock upload; requires Docker and live data.
cd local_tests
bash test_local.sh
```

The offline suite injects fixed HTTP responses only inside test processes and
covers missing data, publication/observation cutoffs, freshness, retries, year
boundaries, and the actual run.sh → model → upload.sh → mock endpoint path.
The container test uses the current hourly timestamp by default and may fail when
strict publication/freshness checks exclude the available upstream data.

Before a real competition submission, set your team/contact details in
`submission.json` (the template placeholders remain), run the container test, and
follow [PARTICIPANT_CONTRACT.md](../PARTICIPANT_CONTRACT.md). No competition upload
or qualification is performed by these local tests. The conformance kit is unchanged.
