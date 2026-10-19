"""Inspect actual COPS TRAIN parquet samples against diary hours; never train a model.

python tools/audit_signal_hours.py
Downloads ~4.3 GB on the audited snapshot; retains a local cache, never writes S3.
"""
import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from scipy.signal import welch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import storage
from src.ingestion.cops_reader import FILE_RE
from tools.audit_model_readiness import candidate_labels

POLICY = dict(split="train", grid_minutes=[5, 10, 15, 20, 30],
              retain_hours_at_least=.90, retain_participants_at_least=.90,
              rule="largest grid threshold meeting both retention targets among structurally aligned candidates",
              fallback="5 minutes if no grid point meets targets; report failure explicitly",
              label_blind=True, purpose="engineering coverage gate, not a validated clinical cutoff")
COLUMNS = ["time_s", "x", "y", "z", "valid", "reason", "day", "visit"]
FLAGS = ["missing", "clip", "abnormal", "ism", "nonwear", "sleep", "visit", "short_day", "few_days"]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""):
            h.update(b)
    return h.hexdigest()


def hours_from_batches(batches):
    """Accumulate an entire clock hour across batch AND row-group boundaries."""
    pending, current, previous = [], None, -np.inf
    for batch in batches:
        t = batch.column("time_s").to_numpy(zero_copy_only=False)
        if not np.isfinite(t).all() or (len(t) and (t[0] < previous or np.any(np.diff(t) < 0))):
            raise ValueError("Nonfinite or backwards time_s: cannot safely join diary")
        if not len(t):
            continue
        previous = t[-1]
        hour = np.floor(t / 3600).astype(np.int64)
        boundaries = np.r_[0, np.flatnonzero(hour[1:] != hour[:-1]) + 1, len(t)]
        for lo, hi in zip(boundaries[:-1], boundaries[1:]):
            key = int(hour[lo])
            if current is not None and key != current:
                yield current, pa.Table.from_batches(pending).to_pandas()
                pending = []
            current = key
            pending.append(batch.slice(int(lo), int(hi - lo)))
    if pending:
        yield current, pa.Table.from_batches(pending).to_pandas()


def measure_hour(frame, hour_index, fs):
    t = frame.time_s.to_numpy(float)
    xyz = frame[["x", "y", "z"]].to_numpy(float)
    valid = frame.valid.fillna(False).to_numpy(bool)
    finite = np.isfinite(xyz).all(axis=1)
    dt = np.diff(t)
    sample = np.rint(t * fs).astype(np.int64)
    aligned = np.abs(t * fs - sample) <= 1e-4
    reason = frame.reason.astype(object).fillna("")
    result = dict(hour_index=hour_index, day=hour_index // 24, hour=hour_index % 24,
                  rows=len(t), valid_samples=int(valid.sum()),
                  valid_min=float(valid.sum() / fs / 60),
                  finite_recorded_min=float(finite.sum() / fs / 60),
                  expected_samples=int(3600 * fs), first_time_s=float(t[0]), last_time_s=float(t[-1]),
                  median_dt_s=float(np.median(dt)) if len(dt) else np.nan,
                  duplicate_time_samples=int(np.sum(dt == 0)),
                  bad_cadence_steps=int(np.sum(~np.isclose(dt, 1 / fs, atol=1e-7, rtol=0))),
                  off_grid_samples=int((~aligned).sum()),
                  null_valid_samples=int(frame.valid.isna().sum()),
                  day_mismatch_samples=int((frame.day.to_numpy() != hour_index // 24).sum()),
                  valid_nonfinite_samples=int((valid & ~finite).sum()),
                  valid_reason_conflicts=int((valid & reason.ne("").to_numpy()).sum()),
                  invalid_reason_missing=int((~valid & reason.eq("").to_numpy()).sum()),
                  valid_visit_conflicts=int((valid & ~frame.visit.eq("outpatient").fillna(False).to_numpy(bool)).sum()))
    # Exact non-overlapping 10s windows: must have the complete expected grid and all valid.
    w = (sample - hour_index * 3600 * fs) // int(10 * fs)
    counts = np.bincount(w, minlength=360)[:360]
    goodcounts = np.bincount(w, weights=(valid & finite & aligned).astype(int), minlength=360)[:360]
    unique_grid = bool(np.all(np.diff(sample) > 0))
    result["complete_valid_10s_windows"] = int(np.sum((counts == int(10 * fs)) & (goodcounts == int(10 * fs)))) if unique_grid else 0
    # Longest valid run; split on invalid samples and time discontinuities.
    start = valid & np.r_[True, (~valid[:-1]) | ~np.isclose(dt, 1 / fs, atol=1e-7, rtol=0)]
    end = valid & np.r_[(~valid[1:]) | ~np.isclose(dt, 1 / fs, atol=1e-7, rtol=0), True]
    lengths = np.flatnonzero(end) - np.flatnonzero(start) + 1
    result["longest_valid_run_s"] = float(lengths.max() / fs) if len(lengths) else 0.
    values = reason.value_counts()
    for flag in FLAGS:
        n = sum(int(count) for name, count in values.items() if flag in name.split("|"))
        result[f"{flag}_min"] = n / fs / 60
    norm = np.linalg.norm(xyz[valid & finite], axis=1)
    result["valid_norm_median_g"] = float(np.median(norm)) if len(norm) else np.nan
    result["valid_norm_p99_g"] = float(np.quantile(norm, .99)) if len(norm) else np.nan
    result["valid_max_abs_g"] = float(np.max(np.abs(xyz[valid & finite]))) if len(norm) else np.nan
    return result


def scan_subject(sid, path, source, q, cfg, snapshot):
    """Independent scan in a worker process; aggregation/threshold stay in parent."""
    fs_hz = q["fs_out"]
    if fs_hz != cfg["clean"]["resample"]["fs_out"] or q["fs_native"] != cfg["datasets"]["cops"]["sampling_rate_hz"]:
        raise ValueError(f"QC sampling config mismatch: {sid}")
    p = pq.ParquetFile(path)
    metrics = []
    for hour, frame in hours_from_batches(p.iter_batches(batch_size=262144, columns=COLUMNS)):
        row = measure_hour(frame, hour, fs_hz)
        row["subject_id"] = sid
        metrics.append(row)
    actual_h = sum(r["valid_samples"] for r in metrics) / fs_hz / 3600
    reconciliation = dict(subject_id=sid, qc_hours=q["hours_valid"], parquet_valid_hours=actual_h,
                          difference_hours=actual_h - q["hours_valid"],
                          agrees_with_rounded_qc=abs(actual_h - q["hours_valid"]) <= .0051,
                          parquet_rows=p.metadata.num_rows, row_groups=p.num_row_groups)
    provenance = dict(subject_id=sid, source=source, parquet_sha256=digest(path),
                      qc_sha256=digest(snapshot / cfg["clean"]["out_dir"] / "cops" / f"{sid}.qc.json"),
                      diary_sha256=digest(snapshot / f"data/raw/cops/{sid}/{sid}_symptomdiary.csv"))
    return metrics, reconciliation, provenance


def filename_alignment(row, sid, wrist):
    """Check the selected-wrist filename independently of diary Day/Time parsing."""
    side = wrist.capitalize()
    names = [str(row.get(f"WearableData{side}{ext}", "")).strip() for ext in ["CSV", "ZIP"]]
    names = [n for n in names if n]
    if not names:
        return False, "diary_selected_wrist_filename_missing"
    for name in names:
        m = FILE_RE.match(Path(name).name)
        if not m or (m[1].upper(), int(m[2]), int(m[3]), m[5].lower()) != (
                sid, int(row.day), int(row.hour), wrist.lower()):
            return False, "diary_filename_time_or_wrist_mismatch"
        if int(m[4]) != (int(row.hour) + 1) % 24 and not (int(row.hour) == 23 and int(m[4]) == 24):
            return False, "diary_filename_end_hour_mismatch"
    return True, ""


def select_threshold(hours):
    aligned = hours[hours.structural_ok]
    rows = []
    for threshold in POLICY["grid_minutes"]:
        retained = aligned[aligned.valid_min >= threshold]
        rows.append(dict(min_valid_minutes=threshold, retained_hours=len(retained),
                         hour_fraction=len(retained) / max(len(aligned), 1),
                         retained_participants=int(retained.subject_id.nunique()),
                         participant_fraction=retained.subject_id.nunique() / max(aligned.subject_id.nunique(), 1),
                         valid_sample_hours=retained.valid_min.sum() / 60))
    sensitivity = pd.DataFrame(rows)
    passed = sensitivity[(sensitivity.hour_fraction >= POLICY["retain_hours_at_least"]) &
                         (sensitivity.participant_fraction >= POLICY["retain_participants_at_least"])]
    chosen = int(passed.min_valid_minutes.max()) if len(passed) else min(POLICY["grid_minutes"])
    return chosen, sensitivity, bool(len(passed))


def plot_hour(frame, metrics, label, fs, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = frame.time_s.to_numpy(float)
    xyz = frame[["x", "y", "z"]].to_numpy(float)
    valid = frame.valid.fillna(False).to_numpy(bool)
    minutes = (t - metrics["hour_index"] * 3600) / 60
    bins = np.floor(minutes * 60).astype(int)
    envelope = pd.DataFrame(dict(second=bins, norm=np.linalg.norm(xyz, axis=1), valid=valid)).groupby("second").agg(
        lo=("norm", "min"), hi=("norm", "max"), valid=("valid", "mean"))
    contiguous = np.isclose(np.diff(t), 1 / fs, atol=1e-7, rtol=0)
    starts = np.flatnonzero(valid & np.r_[True, (~valid[:-1]) | ~contiguous])
    ends = np.flatnonzero(valid & np.r_[(~valid[1:]) | ~contiguous, True]) + 1
    longest = int(np.argmax(ends - starts)) if len(starts) else None
    lo = int(starts[longest]) if longest is not None else 0
    hi = min(lo + int(20 * fs), int(ends[longest]) if longest is not None else len(t))
    segment = xyz[lo:hi]
    fig, ax = plt.subplots(4, 1, figsize=(13, 10), constrained_layout=True)
    ax[0].fill_between(envelope.index / 60, envelope.lo, envelope.hi, alpha=.5, color="#3379a6")
    ax[0].set(xlabel="Minute within diary hour", ylabel="|a| (g)", title="1-second min/max envelope (including excluded samples)")
    ax[1].plot(envelope.index / 60, envelope.valid, lw=1, color="#16856b")
    ax[1].set(xlabel="Minute within diary hour", ylabel="Valid fraction", ylim=(-.05, 1.05))
    tt = t[lo:hi] - t[lo] if hi > lo else []
    for i, axis in enumerate("xyz"):
        ax[2].plot(tt, segment[:, i], label=axis, lw=.8)
    ax[2].legend(loc="upper right")
    segment_title = ("First 20s of longest valid run (shorter if unavailable)" if longest is not None
                     else "NO VALID RUN: first 20s of excluded samples for inspection only")
    ax[2].set(xlabel="Seconds from segment start", ylabel="Acceleration (g)", title=segment_title)
    if len(segment) >= int(2 * fs) and valid[lo:hi].all() and np.isfinite(segment).all():
        frequency, psd = welch(segment, fs=fs, nperseg=min(len(segment), int(4 * fs)), axis=0)
        ax[3].semilogy(frequency, np.sum(psd, axis=1))
        ax[3].axvspan(3.5, 7.5, alpha=.15, color="#e58c35")
        ax[3].set(xlim=(.5, 15), xlabel="Frequency (Hz)", ylabel="Sum XYZ PSD (g²/Hz)",
                  title="Diagnostic PSD of a contiguous valid segment; not a diagnosis")
    else:
        ax[3].text(.05, .5, "No contiguous valid segment >= 2 seconds", transform=ax[3].transAxes)
    fig.suptitle(f"{label['subject_id']} day {metrics['day']} hour {metrics['hour']:02d}:00 | diary TremorScore={label['tremor_score']} | valid={metrics['valid_min']:.2f} min")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/signal_audit")
    parser.add_argument("--refresh-plots", action="store_true", help="Regenerate graphs from completed local evidence without rescanning or changing the gate")
    args = parser.parse_args()
    if args.refresh_plots:
        refresh_plots(args.out)
        return
    snapshot = args.snapshot or sorted((ROOT / "reports/model_readiness/snapshots").iterdir())[-1]
    cfg = yaml.safe_load((snapshot / "config/config.yaml").read_text(encoding="utf-8"))
    split = pd.read_csv(snapshot / cfg["clean"]["subjects"]["cops_splits"])
    subjects, labels, qcs = [], [], {}
    for r in split[split.split == "train"].itertuples():
        qp = snapshot / cfg["clean"]["out_dir"] / "cops" / f"{r.subject_id}.qc.json"
        dp = snapshot / f"data/raw/cops/{r.subject_id}/{r.subject_id}_symptomdiary.csv"
        if not qp.exists() or not dp.exists():
            raise ValueError(f"Missing train QC/diary: {r.subject_id}")
        q = json.loads(qp.read_text(encoding="utf-8"))
        if q.get("split") != "train" or q.get("subject_id") != r.subject_id or q.get("dataset") != "cops":
            raise ValueError(f"QC identity/split mismatch: {r.subject_id}")
        d = candidate_labels(pd.read_csv(dp, sep=";", dtype=str, keep_default_na=False), q)
        d["subject_id"], d["split"] = r.subject_id, "train"
        for _, row in d.iterrows():
            # Only candidates require a selected-wrist signal filename.
            ok, reason = filename_alignment(row, r.subject_id, r.use_wrist) if row.candidate_tremor else (False, "not_diary_candidate")
            labels.append(dict(subject_id=r.subject_id, split="train", day=row.day, hour=row.hour,
                               tremor_score=row.tremor_score, candidate=bool(row.candidate_tremor),
                               filename_aligned=ok, diary_alignment_reason=reason))
        if d.candidate_tremor.any():
            subjects.append(r.subject_id)
            qcs[r.subject_id] = q
    out, cache = args.out, args.out / "cache"
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    (out / "threshold_policy_pre_scan.json").write_text(json.dumps(POLICY, indent=2), encoding="utf-8")
    diary = pd.DataFrame(labels)
    diary.to_csv(out / "all_train_diary_hours.csv", index=False)
    candidates = diary[diary.candidate].copy()
    if candidates.duplicated(["subject_id", "day", "hour"]).any():
        raise ValueError("Duplicate candidate diary keys")
    storage.configure(cfg.get("storage"))
    root = storage.s3_root(cfg).split("://", 1)[1].rstrip("/")
    fs = storage.fs_of(storage.s3_root(cfg))

    def download(sid):
        key = f"{root}/{cfg['clean']['out_dir']}/cops/{sid}.parquet"
        info = fs.info(key)
        identity = dict(key=key, size=info["size"], etag=info.get("ETag"), version=info.get("VersionId"))
        path, marker = cache / f"{sid}.parquet", cache / f"{sid}.source.json"
        if not (path.exists() and marker.exists() and path.stat().st_size == info["size"]
                and json.loads(marker.read_text()) == identity):
            tmp = path.with_suffix(".parquet.part")
            fs.get_file(key, str(tmp))
            if tmp.stat().st_size != info["size"]:
                raise ValueError(f"Incomplete download: {sid}")
            tmp.replace(path)
            marker.write_text(json.dumps(identity, indent=2), encoding="utf-8")
        # Refuse a moving/mixed QC snapshot. No overwrite of prior evidence.
        remote_qc = json.loads(fs.cat(key.removesuffix(".parquet") + ".qc.json"))
        if remote_qc != qcs[sid]:
            raise ValueError(f"Remote QC changed since snapshot: {sid}; refresh readiness first")
        print(f"Cached {sid}: {info['size'] / 1e6:.1f} MB", flush=True)
        return sid, path, identity

    with ThreadPoolExecutor(4) as pool:
        downloaded = list(pool.map(download, subjects))
    measured, reconciled, provenance = [], [], []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(scan_subject, sid, path, source, qcs[sid], cfg, snapshot)
                   for sid, path, source in downloaded]
        for future in as_completed(futures):
            metrics, reconciliation, source = future.result()
            measured.extend(metrics)
            reconciled.append(reconciliation)
            provenance.append(source)
            print(f"Scanned {source['subject_id']}: {reconciliation['parquet_valid_hours']:.4f} valid hours / QC {reconciliation['qc_hours']}", flush=True)
            pd.DataFrame(measured).sort_values(["subject_id", "day", "hour"]).to_csv(out / "all_signal_hours.csv", index=False)
    m = pd.DataFrame(measured)
    reconciled = pd.DataFrame(reconciled)
    reconciled.to_csv(out / "qc_reconciliation.csv", index=False)
    hours = candidates.merge(m, on=["subject_id", "day", "hour"], how="left", validate="one_to_one", indicator=True)
    structural_columns = ["duplicate_time_samples", "bad_cadence_steps", "off_grid_samples", "day_mismatch_samples",
                          "valid_nonfinite_samples", "valid_reason_conflicts", "invalid_reason_missing",
                          "valid_visit_conflicts", "null_valid_samples"]
    hours["structural_ok"] = (hours.filename_aligned & hours._merge.eq("both") & hours[structural_columns].eq(0).all(axis=1))
    agrees = reconciled.set_index("subject_id").agrees_with_rounded_qc
    hours["structural_ok"] &= hours.subject_id.map(agrees).fillna(False)
    threshold, sensitivity, target_met = select_threshold(hours)
    sensitivity.to_csv(out / "duration_sensitivity_train.csv", index=False)
    hours["included"] = hours.structural_ok & hours.valid_min.ge(threshold)

    def reject(r):
        reasons = []
        if not r.filename_aligned:
            reasons.append(r.diary_alignment_reason)
        if r._asdict().get("_merge") == "left_only" or pd.isna(r.valid_min):
            reasons.append("signal_hour_missing")
        for c in structural_columns:
            if pd.notna(getattr(r, c)) and getattr(r, c) > 0:
                reasons.append(c)
        if not agrees.get(r.subject_id, False):
            reasons.append("qc_total_disagrees")
        if pd.notna(r.valid_min) and r.valid_min < threshold:
            reasons.append("valid_duration_below_train_threshold")
        return "|".join(reasons)

    hours["exclusion_reason"] = [reject(r) for r in hours.itertuples()]
    hours.drop(columns="_merge").to_csv(out / "candidate_hours.csv", index=False)
    hours[hours.included].drop(columns="_merge").to_csv(out / "included_hours.csv", index=False)
    hours[~hours.included].drop(columns="_merge").to_csv(out / "excluded_hours.csv", index=False)
    counts = hours.groupby("tremor_score").agg(candidates=("included", "size"), kept=("included", "sum"),
                                              participants=("subject_id", "nunique"))
    counts.to_csv(out / "class_retention.csv")
    plots = out / "plots"
    plots.mkdir(exist_ok=True)
    # Deterministic examples: median coverage in each score, minimum coverage, near gate.
    selected = []
    for _, g in hours[hours.structural_ok].groupby("tremor_score"):
        selected.append(g.sort_values(["valid_min", "subject_id", "day", "hour"]).iloc[len(g) // 2])
    selected.append(hours.sort_values(["valid_min", "subject_id"]).iloc[0])
    selected.append(hours.iloc[(hours.valid_min - threshold).abs().argmin()])
    plot_index = []
    for r in pd.DataFrame(selected).drop_duplicates(["subject_id", "day", "hour"]).to_dict("records"):
        sid = r["subject_id"]
        path = cache / f"{sid}.parquet"
        start = (r["day"] * 24 + r["hour"]) * 3600
        frame = pq.read_table(path, columns=COLUMNS, filters=[("time_s", ">=", start), ("time_s", "<", start + 3600)]).to_pandas()
        name = f"{sid}_day{int(r['day'])}_hour{int(r['hour']):02d}.png"
        plot_hour(frame, r, r, qcs[sid]["fs_out"], plots / name)
        plot_index.append(dict(subject_id=sid, day=r["day"], hour=r["hour"], score=r["tremor_score"], valid_min=r["valid_min"], file=f"plots/{name}"))
    pd.DataFrame(plot_index).to_csv(out / "plot_index.csv", index=False)
    plot_summary(hours, threshold, plots)
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), snapshot=str(snapshot.resolve()),
                    code_sha256=digest(Path(__file__)), policy=POLICY, threshold_minutes=threshold,
                    retention_targets_met=target_met, sources=provenance,
                    limitation="Checks internal study-time and filename alignment; no independent absolute clock synchronization or clinical label verification")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    kept = hours[hours.included]
    report = ["# Actual train signal audit", "", f"Scanned {len(subjects)} COPS train participants; {len(m)} clock hours.",
              f"Candidate diary hours: {len(hours)}; structurally aligned: {int(hours.structural_ok.sum())}.",
              f"Train-only coverage gate: {threshold} valid minutes/hour; retention targets met: {target_met}.",
              f"Included: {len(kept)} hours / {kept.subject_id.nunique()} participants / {kept.valid_min.sum()/60:.2f} valid sample hours.",
              f"Excluded: {int((~hours.included).sum())} candidate hours. All exclusions recorded in excluded_hours.csv.",
              f"QC total reconciliation passed: {int(reconciled.agrees_with_rounded_qc.sum())}/{len(reconciled)}.",
              "", "```csv", counts.to_csv().strip(), "```", "",
              "Time convention: Day N / Time H hr represents [N*86400+(H-1)*3600, N*86400+H*3600).",
              "This convention was cross-checked against selected-wrist CSV/ZIP filenames, parquet time_s/day/visit and existing reader code.",
              "Hours are accumulated across batches and row groups. A sample contributes 1/fs seconds only after cadence/grid/uniqueness checks.",
              "A complete 10s window requires every expected sample, finite XYZ and valid=True, without stitching gaps.",
              "Duration policy was written before scanning; chosen using coverage only, with no scores or model performance as input.",
              "The gate is an engineering choice, not a clinically validated minimum. Class retention is reported after selection.",
              "Flag durations overlap; do not sum flag minutes. The saved clean samples include clipping/noise harmonization and are not unprocessed raw signals.",
              "Internal timing agreement cannot establish absolute device/diary clock synchronization. PSD examples cannot confirm tremor clinically.",
              "Only train was inspected. Valid/test are untouched; no model was trained. No source or S3 files were modified.", "",
              "![Train coverage](plots/train_coverage.png)", ""]
    for r in plot_index:
        report.append(f"![{r['subject_id']} day {r['day']} hour {r['hour']}]({r['file']})")
    (out / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report[2:9]), flush=True)


def plot_summary(hours, threshold, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    ax[0].hist(hours.valid_min.dropna(), bins=np.arange(0, 62, 2), color="#3379a6")
    ax[0].axvline(threshold, color="#bb4433", label=f"Train-only gate: {threshold} min")
    ax[0].legend()
    ax[0].set(xlabel="Actual valid minutes per candidate hour", ylabel="Candidate hours")
    groups = hours.groupby("tremor_score").included.agg(["size", "sum"])
    ax[1].bar(groups.index.astype(str), groups["size"], label="Candidates", color="#ccd9e1")
    ax[1].bar(groups.index.astype(str), groups["sum"], label="Retained", color="#16856b")
    ax[1].set(xlabel="Diary TremorScore", ylabel="Hours")
    ax[1].legend()
    fig.savefig(out / "train_coverage.png", dpi=150)
    plt.close(fig)


def refresh_plots(out):
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    snapshot = Path(manifest["snapshot"])
    cfg = yaml.safe_load((snapshot / "config/config.yaml").read_text(encoding="utf-8"))
    hours = pd.read_csv(out / "candidate_hours.csv")
    for r in pd.read_csv(out / "plot_index.csv").to_dict("records"):
        sid = r["subject_id"]
        row = hours[(hours.subject_id == sid) & (hours.day == r["day"]) & (hours.hour == r["hour"])].iloc[0].to_dict()
        q = json.loads((snapshot / cfg["clean"]["out_dir"] / "cops" / f"{sid}.qc.json").read_text(encoding="utf-8"))
        start = (r["day"] * 24 + r["hour"]) * 3600
        frame = pq.read_table(out / "cache" / f"{sid}.parquet", columns=COLUMNS,
                              filters=[("time_s", ">=", start), ("time_s", "<", start + 3600)]).to_pandas()
        plot_hour(frame, row, row, q["fs_out"], out / r["file"])
    plot_summary(hours, manifest["threshold_minutes"], out / "plots")
    manifest["plot_code_sha256"] = digest(Path(__file__))
    manifest["plots_refreshed_utc"] = datetime.now(timezone.utc).isoformat()
    archive = out / "executed_scan_code.py"
    if archive.exists():
        if digest(archive) != manifest["code_sha256"]:
            raise ValueError("Archived scan code does not match original manifest")
        manifest["executed_scan_code_archive"] = archive.name
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Refreshed plots; original measurements, exclusions and duration gate unchanged")


if __name__ == "__main__":
    main()
