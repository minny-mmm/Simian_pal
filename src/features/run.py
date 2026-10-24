"""Feature extraction from cleaned wrist-accelerometer data (v2: research-backed feature set).
Input : data/clean/<dataset>/<id>.parquet  (time_s, x, y, z [g], valid, reason, day, visit; 50 Hz)
        data/clean/<dataset>/<id>.qc.json  (skip people with "included": false)
Output: data/features/<dataset>/<id>.csv   (1 row = 1 hour)
Time axis: index 0 of the clean array = 00:00 of day 1 (see src/clean/steps.py), so
  hour_idx = whole hours since the start, hour = hour_idx % 24 = clock hour.
Pipeline:
  valid rows only -> complete 10 s windows (500 samples, nothing stitched across gaps)
  -> keep 09:00-21:00 (CFG["day_hours"]) -> per-window features -> label each window walk / rest / tremor
  -> aggregate per (day, hour) -> 1 row per hour
Feature families and why (details: see the reply / README_feature.md):
  tremor        tremor-band (3.5-7.5 Hz) amplitude, tremor time fraction, log amplitude of the peak
                (+-1 Hz) -> rest tremor monitored from wrist sensors (Powers 2021; Duke 4-7 Hz; PPP tremor time)
  bradykinesia  movement power in non-gait windows, ENMO, jerk -> slowed / reduced movement
  gait          cadence in steps/s from the stride period (+ its SD), step / stride regularity
                from the autocorrelation (Moe-Nilssen & Helbostad 2004),
                arm-swing amplitude, sample entropy of walking windows
  generic       SD, IQR, SMA, spectral entropy, dominant frequency
                (no correlation between axes: GENEActiv sensor noise is correlated between x and y,
                 the noise added to NHANES is not, so it separated the brands with nobody wearing the
                 watch, AUC 1.00; see README_feature.md 3.7)
Columns whose size depends on the sensor gain/brand (amplitude-based) start with the prefixes in
AMPLITUDE_PREFIXES. Shape/ratio/frequency columns are much less brand-sensitive. Check both
sets with the device-leakage test (README_feature.md section 4) and drop what separates the brands.
Run from the repo root:
  python -m src.features.run --dataset cops
  python -m src.features.run --dataset nhanes
  python -m src.features.run --dataset cops --ids COPS-8 COPS-12 --force
Same code and thresholds for BOTH datasets. Thresholds marked (untested) are guesses: tune on train only.
"""
import argparse
import json
import re
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter1d
from scipy.signal import welch
FS = 50                      # Hz, clean output rate
WIN_S = 10                   # window length (s)
WIN_N = FS * WIN_S           # 500 samples
CHUNK = 1500                 # windows per batch (keeps RAM low)
CFG = {
    # walking (untested)
    "walk_stride_reg": 0.5,
    "walk_dyn_rms": 0.12,        # g
    # "clean" walking used for arm swing: strongly periodic, so less likely to be mixed with other arm activity
    "clean_walk_stride_reg": 0.7,   # (untested)
    # resting (untested)
    "rest_swing_deg": 15.0,
    "rest_dyn_rms": 0.08,        # g
    # tremor window (untested)
    "tremor_ratio": 0.5,
    "tremor_rms": 0.015,         # g  (15 mg, above sensor noise)
    # an hour needs at least this many usable windows (5 min)
    "min_windows_per_hour": 30,
    # hours need at least this many walking windows before gait SD features are reported
    "min_walk_windows_for_sd": 3,
    # clock hours used [start, end): HDCZA misses part of the night in PD (fragmented sleep), so 17% of COPS
    # hours kept as awake were asleep by the diary, mostly 22:00-05:00; inside 09-21 it is 1.7%.
    # Same window for both datasets (NHANES has no diary to correct with). See README_feature.md 3.9
    "day_hours": (9, 21),
}
TREMOR_BAND = (3.5, 7.5)     # Hz
REF_BAND = (1.0, 15.0)       # Hz
STRIDE_LAG = (0.7, 2.0)      # s  stride = 2 steps (cadence 1.0-2.9 steps/s)
PEAK_FRAC = 0.8              # stride = first autocorrelation peak reaching this fraction of the highest one
GRAVITY_SMOOTH = 12          # samples (~0.25 s)
N_LAGS = 200                 # autocorrelation lags kept (4 s)
SAMPEN_DS = 5                # 50 Hz -> 10 Hz by block averaging before SampEn
SAMPEN_M = 2
SAMPEN_R = 0.2               # tolerance = 0.2 * SD of the window
AMPLITUDE_PREFIXES = ("enmo", "jerk", "dyn_rms", "dyn_power", "mag_sd", "mag_iqr", "sma",
                      "tremor_rms", "tremor_amp_log")
def amplitude_columns(df):
    """Columns whose value scales with sensor gain / noise (more likely to reveal the device brand)."""
    return [c for c in df.columns if c.startswith(AMPLITUDE_PREFIXES)]
# ---------------------------------------------------------------------------
# Sample entropy (batched, numpy only)
# ---------------------------------------------------------------------------
def sample_entropy_batch(x, m=SAMPEN_M, r_ratio=SAMPEN_R, block=128):
    """x: (n, N) -> (n,) sample entropy (Richman & Moorman 2000). NaN if undefined.
    Same number of templates (N - m) for lengths m and m+1; self-matches removed.
    """
    x = np.asarray(x, dtype=np.float32)
    out = np.full(len(x), np.nan)
    for s in range(0, len(x), block):
        xb = x[s:s + block]
        n, N = xb.shape
        r = r_ratio * xb.std(axis=1)
        def matches(mm):
            T = np.stack([xb[:, i:N - m + i] for i in range(mm)], axis=2)       # (n, N-m, mm)
            d = np.abs(T[:, :, None, :] - T[:, None, :, :]).max(axis=3)         # Chebyshev distance
            return (d <= r[:, None, None]).sum(axis=(1, 2)) - (N - m)
        B, A = matches(m), matches(m + 1)
        ok = (B > 0) & (A > 0) & (r > 0)
        se = np.full(n, np.nan)
        se[ok] = -np.log(A[ok] / B[ok])
        out[s:s + block] = se
    return out
# ---------------------------------------------------------------------------
# Per-window features (pure numpy, no I/O)
# ---------------------------------------------------------------------------
def window_features(w):
    """w: (n, WIN_N, 3) float array in g -> dict of 1-D arrays (length n)."""
    w = np.asarray(w, dtype=np.float32)
    n = w.shape[0]
    mag = np.linalg.norm(w, axis=2)
    enmo = np.maximum(mag - 1.0, 0.0).mean(axis=1)
    jerk = np.abs(np.diff(mag, axis=1)).mean(axis=1) * FS            # g/s
    mag_sd = mag.std(axis=1)
    q25, q75 = np.percentile(mag, [25, 75], axis=1)
    mag_iqr = q75 - q25
    # dynamic part = signal minus 1 s moving mean
    dyn = w - uniform_filter1d(w, size=FS, axis=1, mode="nearest")
    dyn_rms = np.sqrt((dyn ** 2).sum(axis=2).mean(axis=1))           # g
    sma = np.abs(dyn).sum(axis=2).mean(axis=1)                       # g, signal magnitude area
    # gait: UNBIASED autocorrelation summed over 3 axes (Moe-Nilssen & Helbostad 2004)
    nfft = 1024
    spec = np.fft.rfft(dyn, n=nfft, axis=1)
    ac = np.fft.irfft((spec.real ** 2 + spec.imag ** 2).sum(axis=2), n=nfft, axis=1)[:, :N_LAGS]
    lags = np.arange(N_LAGS)
    ac_u = ac * (WIN_N / (WIN_N - lags))[None, :]
    ac_n = ac_u / np.maximum(ac_u[:, :1], 1e-12)
    # The wrist swings once per stride (2 steps), so the strongest period is the stride when the arm
    # swings and the step when it does not. Searching stride lags only gives the stride either way:
    # a step-only signal also peaks at 2, 3, ... steps, and the first strong peak in range is 2 steps.
    lo, hi = int(round(STRIDE_LAG[0] * FS)), int(round(STRIDE_LAG[1] * FS))
    seg = ac_n[:, lo:hi + 1]
    is_peak = np.zeros(seg.shape, bool)
    is_peak[:, 1:-1] = (seg[:, 1:-1] >= seg[:, :-2]) & (seg[:, 1:-1] > seg[:, 2:])
    peak_val = np.where(is_peak, seg, -np.inf)
    best = peak_val.max(axis=1)
    thr = np.where(best > 0, PEAK_FRAC * best, best)
    first = (peak_val >= thr[:, None]).argmax(axis=1)
    idx = np.where(np.isfinite(best), first, seg.argmax(axis=1))    # no inner peak -> highest value
    stride_lag = lo + idx
    stride_reg = seg[np.arange(n), idx]                              # regularity of one gait cycle
    step_freq = 2 * FS / stride_lag                                  # steps/s (cadence)
    # step regularity: peak near half a stride (low / negative at the wrist when the arm swings)
    lo2 = np.round(0.4 * stride_lag).astype(int)
    hi2 = np.round(0.6 * stride_lag).astype(int)
    in2 = (lags[None, :] >= lo2[:, None]) & (lags[None, :] <= hi2[:, None])
    step_ac = np.where(in2, ac_n, -np.inf).max(axis=1)
    # spectrum (Welch density, Hann), summed over 3 axes
    f, p = welch(dyn, fs=FS, window="hann", nperseg=WIN_N, noverlap=0,
                 detrend="constant", scaling="density", axis=1)
    p = p.sum(axis=2)
    df = f[1] - f[0]
    band_t = (f >= TREMOR_BAND[0]) & (f <= TREMOR_BAND[1])
    band_r = (f >= REF_BAND[0]) & (f <= REF_BAND[1])
    t_pow = p[:, band_t].sum(axis=1) * df                            # g^2
    r_pow = p[:, band_r].sum(axis=1) * df
    tremor_rms = np.sqrt(t_pow)
    tremor_ratio = t_pow / np.maximum(r_pow, 1e-12)
    dom_freq = f[band_r][p[:, band_r].argmax(axis=1)]
    pr = p[:, band_r]
    pn = pr / np.maximum(pr.sum(axis=1, keepdims=True), 1e-20)
    spec_entropy = -(pn * np.log(np.maximum(pn, 1e-20))).sum(axis=1) / np.log(pr.shape[1])   # 0..1
    # tremor peak: log10 power within +-1 Hz of the dominant frequency inside the tremor band
    f0 = f[band_t][p[:, band_t].argmax(axis=1)]
    near = (f[None, :] >= (f0 - 1.0)[:, None]) & (f[None, :] <= (f0 + 1.0)[:, None])
    tremor_amp_log = np.log10(np.maximum((p * near).sum(axis=1) * df, 1e-12))
    # arm swing: angle of gravity direction (0.25 s mean) vs window mean direction
    g = uniform_filter1d(w, size=GRAVITY_SMOOTH, axis=1, mode="nearest")
    gn = g / np.maximum(np.linalg.norm(g, axis=2, keepdims=True), 1e-9)
    m = w.mean(axis=1)
    mn = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
    ang = np.degrees(np.arccos(np.clip((gn * mn[:, None, :]).sum(axis=2), -1.0, 1.0)))
    p5, p95 = np.percentile(ang, [5, 95], axis=1)
    swing = p95 - p5
    # tremor first: a 3.5-7.5 Hz oscillation with the arm held still, at any strength. Tremor is periodic,
    # so a strong one also passes the walking test (autocorrelation peaks at every multiple of its period)
    tremor = ((swing < CFG["rest_swing_deg"]) & (tremor_ratio > CFG["tremor_ratio"])
              & (tremor_rms > CFG["tremor_rms"]))
    walk = (~tremor) & (stride_reg > CFG["walk_stride_reg"]) & (dyn_rms > CFG["walk_dyn_rms"])
    walk_clean = walk & (stride_reg >= CFG["clean_walk_stride_reg"])
    # rest = arm still: low movement, or movement that is only tremor (tremor is a subset of rest)
    rest = (~walk) & (swing < CFG["rest_swing_deg"]) & ((dyn_rms < CFG["rest_dyn_rms"]) | tremor)
    # sample entropy of walking windows only (needs a clear signal; scale-free because r = 0.2 SD)
    sampen = np.full(n, np.nan)
    iw = np.flatnonzero(walk)
    if len(iw):
        series = np.linalg.norm(dyn[iw], axis=2).reshape(len(iw), WIN_N // SAMPEN_DS, SAMPEN_DS).mean(axis=2)
        sampen[iw] = sample_entropy_batch(series)
    return {
        "enmo": enmo, "jerk": jerk, "dyn_rms": dyn_rms, "mag_sd": mag_sd, "mag_iqr": mag_iqr, "sma": sma,
        "step_ac": step_ac, "stride_reg": stride_reg, "step_freq": step_freq,
        "swing": swing, "tremor_rms": tremor_rms, "tremor_ratio": tremor_ratio,
        "tremor_amp_log": tremor_amp_log, "tremor_freq": f0,
        "dom_freq": dom_freq, "spec_entropy": spec_entropy, "sampen": sampen,
        "walk": walk, "walk_clean": walk_clean, "rest": rest, "tremor": tremor,
    }
def make_windows(time_s, xyz):
    """Cut valid rows into complete 10 s windows.
    time_s: (N,) seconds, xyz: (N,3). Window id comes from the rounded sample index
    (round(time_s * FS) // WIN_N), so float jitter in time_s cannot split a window.
    Only windows with exactly WIN_N valid samples are kept (nothing is stitched
    across removed gaps). Yields (window_start_seconds (m,), windows (m, WIN_N, 3)).
    """
    order = np.argsort(time_s, kind="stable")
    time_s = np.asarray(time_s)[order]
    xyz = np.asarray(xyz, dtype=np.float32)[order]
    sample_idx = np.round(time_s * FS).astype(np.int64)
    wid = sample_idx // WIN_N
    ids, start, counts = np.unique(wid, return_index=True, return_counts=True)
    # A duplicate plus a missing sample can still have count==WIN_N. Require the exact grid.
    on_grid = np.abs(time_s * FS - sample_idx) <= 1e-4
    finite = np.isfinite(xyz).all(axis=1)
    ok = counts == WIN_N
    for j in np.flatnonzero(ok):
        segment = slice(start[j], start[j] + WIN_N)
        ok[j] = (on_grid[segment].all() and finite[segment].all()
                 and np.array_equal(sample_idx[segment], ids[j] * WIN_N + np.arange(WIN_N)))
    ids, start = ids[ok], start[ok]
    for s in range(0, len(ids), CHUNK):
        a, b = s, min(s + CHUNK, len(ids))
        idx = start[a:b, None] + np.arange(WIN_N)[None, :]
        yield ids[a:b] * WIN_S, xyz[idx]
# ---------------------------------------------------------------------------
# Aggregation to 1 row per hour
# ---------------------------------------------------------------------------
def _med(s):
    return s.median() if len(s) else np.nan
def _q(s, p):
    return s.quantile(p) if len(s) else np.nan
def _sd(s, k):
    return s.std() if len(s) >= k else np.nan
def aggregate_hours(win):
    """win: DataFrame, one row per window -> DataFrame, one row per (day, hour)."""
    k = CFG["min_walk_windows_for_sd"]
    rows = []
    for (day, hour, hour_idx), g in win.groupby(["day", "hour", "hour_idx"], sort=True):
        if len(g) < CFG["min_windows_per_hour"]:
            continue
        walk = g[g["walk"]]
        clean = g[g["walk_clean"]]
        rest = g[g["rest"]]
        nongait = g[~g["walk"] & ~g["tremor"]]                          # voluntary movement only
        trem = g[g["tremor"]]
        n_rest = len(rest)
        rows.append({
            "day": day, "hour": hour, "hour_idx": hour_idx,
            "n_windows": len(g), "valid_min": len(g) * WIN_S / 60.0,
            "n_walk": len(walk), "n_clean_walk": len(clean), "n_rest": n_rest, "n_tremor": len(trem),
            "walk_frac": len(walk) / len(g),
            "rest_frac": n_rest / len(g),
            "tremor_frac_all": len(trem) / len(g),                       # "tremor time" over valid time
            "tremor_frac_of_rest": len(trem) / n_rest if n_rest else np.nan,
            # ---- all windows: generic movement (amplitude-based unless noted) ---
            "enmo_median": g["enmo"].median(), "enmo_p90": g["enmo"].quantile(0.9),
            "jerk_median": g["jerk"].median(), "jerk_p90": g["jerk"].quantile(0.9),
            "dyn_rms_median": g["dyn_rms"].median(), "dyn_rms_p90": g["dyn_rms"].quantile(0.9),
            "mag_sd_median": g["mag_sd"].median(),
            "mag_iqr_median": g["mag_iqr"].median(),
            "sma_median": g["sma"].median(),
            "spec_entropy_median": g["spec_entropy"].median(),
            # ---- bradykinesia: movement power outside walking ---
            "dyn_power_nongait_median": _med(nongait["dyn_rms"] ** 2),
            "dyn_power_nongait_p90": _q(nongait["dyn_rms"] ** 2, 0.9),
            # ---- gait (walking windows) ---
            "step_freq_walk_median": _med(walk["step_freq"]),
            "step_freq_walk_sd": _sd(walk["step_freq"], k),              # cadence variability
            "step_ac_walk_median": _med(walk["step_ac"]),
            "stride_reg_walk_median": _med(walk["stride_reg"]),
            "sampen_walk_median": _med(walk["sampen"]),
            "dyn_rms_walk_median": _med(walk["dyn_rms"]),
            "swing_walk_median": _med(clean["swing"]),                   # arm swing, clean gait only
            # no swing SD: NaN in 78-80% of hours (needs >= 3 clean walking windows) and unrelated to H&Y
            # ---- resting / tremor ---
            "tremor_rms_rest_p90": _q(rest["tremor_rms"], 0.9),
            "tremor_ratio_rest_median": _med(rest["tremor_ratio"]),
            "dom_freq_rest_median": _med(rest["dom_freq"]),
            "tremor_amp_log_tremor_median": _med(trem["tremor_amp_log"]),
            "tremor_freq_tremor_median": _med(trem["tremor_freq"]),
        })
    return pd.DataFrame(rows)
# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def load_splits(meta_dir, dataset):
    """Return {key: split}. COPS uses subject_id, NHANES uses seqn."""
    path = Path(meta_dir) / f"{dataset}_splits.csv"
    if not path.exists():
        print(f"[warn] {path} not found: 'split' column will be empty")
        return {}
    df = pd.read_csv(path)
    key = "subject_id" if "subject_id" in df.columns else "seqn"
    if "split" not in df.columns:
        print(f"[warn] {path} has no 'split' column")
        return {}
    return {str(k): s for k, s in zip(df[key], df["split"])}
def lookup_split(splits, sid):
    if sid in splits:
        return splits[sid]
    digits = re.findall(r"\d+", sid)
    for d in digits[::-1]:
        if d in splits:
            return splits[d]
    return ""
def process_subject(dataset, sid, clean_dir, out_dir, splits, force=False):
    import pyarrow.parquet as pq        # imported here so the pure functions work without it
    out_path = Path(out_dir) / dataset / f"{sid}.csv"
    if out_path.exists() and not force:
        print(f"{sid}: exists, skip (use --force)")
        return None
    pq_path = Path(clean_dir) / dataset / f"{sid}.parquet"
    qc_path = Path(clean_dir) / dataset / f"{sid}.qc.json"
    if not pq_path.exists():
        print(f"{sid}: no parquet, skip")
        return None
    if qc_path.exists():
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        if not qc.get("included", True):
            print(f"{sid}: included=false, skip")
            return None
        days = [d["day"] for d in qc.get("days", []) if d.get("valid")]
    else:
        print(f"{sid}: no qc.json, using every day in the parquet")
        days = sorted(pq.read_table(pq_path, columns=["day"]).column("day").unique().to_pylist())
    parts = []
    for d in days:
        tbl = pq.read_table(
            pq_path, columns=["time_s", "x", "y", "z"],
            filters=[("day", "=", d), ("valid", "=", True)],
        )
        if tbl.num_rows < WIN_N:
            continue
        df = tbl.to_pandas()
        time_s = df["time_s"].to_numpy(dtype=np.float64)
        xyz = df[["x", "y", "z"]].to_numpy(dtype=np.float32)
        for start_s, w in make_windows(time_s, xyz):
            day_time = ((start_s // 3600) % 24 >= CFG["day_hours"][0]) & ((start_s // 3600) % 24 < CFG["day_hours"][1])
            start_s, w = start_s[day_time], w[day_time]
            if len(w) == 0:
                continue
            win = pd.DataFrame(window_features(w))
            win["day"] = d
            win["hour_idx"] = (start_s // 3600).astype(int)      # hours since start of the clean array
            win["hour"] = (win["hour_idx"] % 24).astype(int)     # clock hour (index 0 = 00:00 of day 1)
            parts.append(win)
    if not parts:
        print(f"{sid}: no complete valid windows")
        return None
    hourly = aggregate_hours(pd.concat(parts, ignore_index=True))
    if hourly.empty:
        print(f"{sid}: no hour has >= {CFG['min_windows_per_hour']} windows")
        return None
    hourly.insert(0, "split", lookup_split(splits, sid))
    hourly.insert(0, "label", 1 if dataset == "cops" else 0)
    hourly.insert(0, "dataset", dataset)
    hourly.insert(0, "subject_id", sid)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    hourly.to_csv(out_path, index=False)
    print(f"{sid}: {len(hourly)} hours -> {out_path}")
    return hourly
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, choices=["cops", "nhanes"])
    ap.add_argument("--ids", nargs="*", help="subject ids (default: every parquet in data/clean/<dataset>)")
    ap.add_argument("--clean-dir", default="data/clean")
    ap.add_argument("--out-dir", default="data/features")
    ap.add_argument("--meta-dir", default="metadata")
    ap.add_argument("--force", action="store_true", help="redo people that already have a csv")
    args = ap.parse_args()
    ids = args.ids or sorted(p.stem for p in (Path(args.clean_dir) / args.dataset).glob("*.parquet"))
    if not ids:
        raise SystemExit(f"No parquet files in {Path(args.clean_dir) / args.dataset}")
    splits = load_splits(args.meta_dir, args.dataset)
    done = 0
    for sid in ids:
        if process_subject(args.dataset, sid, args.clean_dir, args.out_dir, splits, args.force) is not None:
            done += 1
    print(f"done: {done}/{len(ids)} subjects")
if __name__ == "__main__":
    main()
