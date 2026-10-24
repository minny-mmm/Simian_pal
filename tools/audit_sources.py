"""Audit local cohort/QC evidence without downloading or changing source data.

Run: python tools/audit_sources.py [--root PATH] [--out reports/source_audit]
Hours are QC-reported valid sample duration, not counts of hourly feature rows.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

SUSPECT = ["still_frac", "rest_tremor_ratio"]
BASELINE = ["enmo_mg", "walk_windows", "rest_windows", "tremor_windows",
            "swing_deg", "walk_jerk", "move_jerk", "rest_peak_hz"]
KEY = ["dataset", "subject_id", "day", "hour"]


def coverage(cohort):
    """Include explicit zero cells; never turn missing QC into observed hours."""
    rows = []
    for (ds, device, split), group in cohort.groupby(["dataset", "device", "split"], dropna=False):
        for label in [0, 1]:
            g = group[group.label == label]
            rows.append(dict(dataset=ds, device=device, split=split, label=label,
                             disease="PD" if label else "selected_control",
                             participants=len(g), qc_available=int(g.qc_available.sum()),
                             qc_included=int(g.qc_included.sum()),
                             current_qc=int(g.current_qc.sum()),
                             local_clean_pairs=int(g.local_clean_pair.sum()),
                             qc_reported_hours=round(g.loc[g.qc_included, "hours_valid"].sum(), 2),
                             current_qc_hours=round(g.loc[g.current_qc, "hours_valid"].sum(), 2)))
    return pd.DataFrame(rows)


def separate_features(features):
    """Preserve row identity, duplicates and suspicious values for inspection."""
    f = features.copy()
    f.insert(0, "source_row", np.arange(len(f)))
    ids = ["source_row"] + [c for c in KEY + ["label", "split", "valid_min", "windows"] if c in f]
    return f[ids + [c for c in BASELINE if c in f]], f[ids + [c for c in SUSPECT if c in f]]


def audit(root, out):
    config_path = root / "config/config.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    inputs = {config_path}
    issues = []

    def issue(code, subject="", detail="", severity="error"):
        issues.append(dict(severity=severity, code=code, subject_id=subject, detail=str(detail)))

    def read(rel):
        path = root / rel
        inputs.add(path)
        return pd.read_csv(path, dtype=str).fillna("")

    sources = cfg["clean"]["subjects"]
    visits = read(sources["cops_summary"])
    cops = read(sources["cops_splits"])
    nh = read(sources["nhanes_selected"])
    nh["subject_id"] = "NHANES-" + nh.seqn
    for name, frame in [("visit_summary", visits), ("cops_splits", cops), ("nhanes_splits", nh)]:
        for sid in frame.loc[frame.subject_id.duplicated(keep=False), "subject_id"].unique():
            issue("duplicate_subject", sid, name)
    selected = visits.loc[visits.use_for_main_model.str.lower() == "yes", "subject_id"]
    for sid in set(selected) - set(cops.subject_id):
        issue("missing_split", sid, "selected in COPS visit summary")
    for sid in set(cops.subject_id) - set(selected):
        issue("split_subject_not_selected", sid)
    # Duplicate IDs are ambiguous and must not inflate cohort totals.
    bad = set(cops.loc[cops.subject_id.duplicated(False), "subject_id"]) | set(
        visits.loc[visits.subject_id.duplicated(False), "subject_id"]) | set(
        nh.loc[nh.subject_id.duplicated(False), "subject_id"])
    cops = cops[cops.subject_id.isin(selected)]
    cops = cops.assign(dataset="cops", label=1, device="GENEActiv",
                       label_basis="COPS PD cohort; not independently re-diagnosed")
    nh = nh.assign(dataset="nhanes", label=0, device="ActiGraph GT3X+",
                   label_basis="selected by tools/select_nhanes.py; absence of PD drugs is not confirmed absence of PD")
    cohort = pd.concat([cops, nh], ignore_index=True).fillna("")
    cohort = cohort[~cohort.subject_id.isin(bad)].copy()
    summary_path = root / cfg["clean"]["qc_summary"]
    summary = read(cfg["clean"]["qc_summary"]) if summary_path.exists() else pd.DataFrame()
    if not summary.empty:
        for sid in summary.loc[summary.subject_id.duplicated(False), "subject_id"].unique():
            issue("duplicate_summary_subject", sid)
    records = []
    for r in cohort.to_dict("records"):
        sid, ds = r["subject_id"], r["dataset"]
        r.update(qc_available=False, qc_included=False, current_qc=False,
                 local_clean_pair=False, hours_valid=np.nan, fs_native=np.nan, fs_out=np.nan)
        if r["split"] not in {"train", "valid", "test"}:
            issue("invalid_split", sid, r["split"])
        base = root / cfg["clean"]["out_dir"] / ds / sid
        qp = base.with_suffix(".qc.json")
        parquet = base.with_suffix(".parquet")
        r["parquet_local"] = parquet.exists()
        if not qp.exists():
            issue("missing_local_qc", sid, "unknown; may exist on S3", "warning")
            records.append(r)
            continue
        inputs.add(qp)
        try:
            q = json.loads(qp.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            issue("unreadable_qc", sid, exc)
            records.append(r)
            continue
        r.update(qc_available=True, fs_native=q.get("fs_native"), fs_out=q.get("fs_out"))
        valid = True
        expected = {"subject_id": sid, "dataset": ds, "split": r["split"],
                    "fs_native": cfg["datasets"][ds]["sampling_rate_hz"],
                    "fs_out": cfg["clean"]["resample"]["fs_out"]}
        if ds == "cops":
            expected["wrist"] = r["use_wrist"].lower()
        for k, value in expected.items():
            if q.get(k) != value:
                issue("qc_metadata_mismatch", sid, f"{k}: {q.get(k)!r} != {value!r}")
                valid = False
        hours = q.get("hours_valid")
        days = q.get("valid_days")
        status_ok = q.get("status") == "ok"
        if status_ok:
            if not isinstance(hours, (float, int)) or not np.isfinite(hours) or hours < 0:
                issue("invalid_qc_hours", sid, hours)
                valid = False
            else:
                r["hours_valid"] = hours
            if not isinstance(days, int) or q.get("included") != (days >= cfg["clean"]["days"]["min_valid_days"]):
                issue("qc_inclusion_mismatch", sid)
                valid = False
            day_rows = q.get("days", [])
            if sum(d.get("valid") is True for d in day_rows) != days:
                issue("qc_day_count_mismatch", sid)
                valid = False
            # Per-day valid_h is BEFORE participant exclusion and includes short days.
            expected_h = sum(d["valid_h"] for d in day_rows if d.get("valid")) if q.get("included") else 0
            if isinstance(hours, (int, float)) and abs(hours - expected_h) > .01 * (len(day_rows) + 1):
                issue("qc_day_hours_mismatch", sid, f"{hours} vs {expected_h}")
                valid = False
        else:
            issue("qc_not_ok", sid, q.get("status"), "warning")
        r["qc_included"] = status_ok and q.get("included") is True
        h = cfg["clean"]["harmonize"]["add_noise"]
        if ds in h["datasets"]:
            target = q.get("noise_target_axes_mg")
            if target is None or (h["target_mg"] != "auto" and not np.allclose(target, h["target_mg"], atol=1e-6)):
                issue("stale_noise_target", sid, f"QC axes={target}; config={h['target_mg']}")
                valid = False
        r["current_qc"] = valid and r["qc_included"]
        r["local_clean_pair"] = r["current_qc"] and parquet.exists()
        if not parquet.exists():
            issue("missing_local_parquet", sid, "cannot verify sample flags, actual sampling rate or recompute features", "warning")
        if not summary.empty:
            ss = summary[summary.subject_id == sid]
            if len(ss) != 1:
                issue("summary_match_count", sid, len(ss))
            else:
                for k in ["dataset", "split", "fs_native", "fs_out", "included", "hours_valid", "valid_days", "status"]:
                    a, b = ss.iloc[0].get(k, ""), q.get(k)
                    if isinstance(b, (float, int)) and not isinstance(b, bool):
                        equal = a != "" and np.isclose(float(a), b)
                    else:
                        equal = str(a).lower() == str(b).lower()
                    if not equal:
                        issue("summary_qc_mismatch", sid, f"{k}: summary={a}; JSON={b}")
        records.append(r)
    cohort = pd.DataFrame(records)
    known = set(zip(cohort.dataset, cohort.subject_id))
    for path in (root / cfg["clean"]["out_dir"]).glob("*/*.qc.json"):
        if (path.parent.name, path.name.removesuffix(".qc.json")) not in known:
            issue("orphan_qc", path.name, path.as_posix())
    if not summary.empty:
        for sid in set(summary.subject_id) - set(cohort.subject_id):
            issue("orphan_summary", sid)
    out.mkdir(parents=True, exist_ok=True)
    cohort.to_csv(out / "participants.csv", index=False)
    counts = coverage(cohort)
    counts.to_csv(out / "coverage.csv", index=False)
    for ds, g in cohort.groupby("dataset"):
        if g.label.nunique() < 2:
            issue("dataset_label_confounding", detail=f"{ds}: only label {g.label.unique().tolist()}")
    fpath = root / "reports/features_hours.csv"
    feature_info = {}
    if fpath.exists():
        inputs.add(fpath)
        f = pd.read_csv(fpath)
        baseline, suspect = separate_features(f)
        baseline.to_csv(out / "baseline_features_REVIEW_ONLY.csv", index=False)
        suspect.to_csv(out / "suspect_features_REVIEW_ONLY.csv", index=False)
        duplicates = f.duplicated(KEY, keep=False)
        f.loc[duplicates].to_csv(out / "duplicate_feature_hours.csv", index=False)
        feature_info = dict(rows=len(f), subjects=int(f.subject_id.nunique()),
                            duplicate_rows=int(duplicates.sum()),
                            duplicate_hour_keys=int(f.loc[duplicates, KEY].drop_duplicates().shape[0]))
        if duplicates.any():
            issue("duplicate_feature_hours", detail=feature_info)
        lookup = cohort.set_index(["dataset", "subject_id"])
        for (ds, sid), g in f.groupby(["dataset", "subject_id"]):
            if (ds, sid) not in lookup.index:
                issue("orphan_features", sid)
                continue
            r = lookup.loc[(ds, sid)]
            for k in ["label", "split"]:
                if not g[k].eq(r[k]).all():
                    issue("feature_metadata_mismatch", sid, k)
            if not r.qc_included:
                issue("features_for_excluded_subject", sid)
            qp = root / cfg["clean"]["out_dir"] / ds / f"{sid}.qc.json"
            if qp.exists():
                q = json.loads(qp.read_text(encoding="utf-8"))
                good = {d["day"] for d in q.get("days", []) if d.get("valid")}
                if not g.day.isin(good).all():
                    issue("features_on_invalid_days", sid)
        issue("unversioned_legacy_features", detail="No code/config/clean hash manifest; review exports are not training-ready")
    plan = dict(target="Exploratory COPS PD cohort vs selected NHANES controls, participant-level prediction",
                label_caveat="NHANES control is a selection proxy, not independently confirmed PD-negative",
                secondary_target="COPS hourly TremorScore 0-2; requires diary extraction and time/coverage audit",
                baseline=BASELINE, quarantine=SUSPECT,
                experiments={"baseline": [], "add_still": [SUSPECT[0]],
                             "add_rest_tremor": [SUSPECT[1]], "add_both": SUSPECT},
                predictors_exclude=["subject_id", "dataset", "device", "label", "split", "day", "hour", "valid_min", "windows", "source_row"],
                protocol=["Reuse participant splits in every experiment; fit imputation/scaling/selection on train only",
                          "Compare validation balanced accuracy, AUROC and participant-bootstrap confidence intervals; keep test untouched until protocol frozen",
                          "Aggregate hourly predictions per participant; never treat hours as independent people",
                          "Predict dataset/device as a diagnostic with identical splits; with perfect label-device confounding this cannot identify disease signal",
                          "Require both labels on the same device/dataset for a disease-vs-device conclusion"],
                execution_status="BLOCKED: legacy features lack provenance; missing local clean evidence and label/device confounding must be resolved")
    (out / "experiment_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(issues, columns=["severity", "code", "subject_id", "detail"]).to_csv(out / "issues.csv", index=False)
    provenance = []
    for path in sorted(inputs):
        if path.exists():
            provenance.append(dict(path=path.relative_to(root).as_posix(), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (out / "input_manifest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    lines = ["# Source audit (local snapshot)", "", "No S3 inventory was queried. Missing locally does not mean missing remotely.",
             "Device names and labels are cohort-level assignments from repository documentation/selection code, not measured device serials or new diagnoses.",
             "", f"Roster: {len(cohort)} unambiguous participants; QC JSON: {int(cohort.qc_available.sum())}; QC included: {int(cohort.qc_included.sum())}.",
             f"QC-reported included hours: {cohort.loc[cohort.qc_included, 'hours_valid'].sum():.2f}; current QC hours: {cohort.loc[cohort.current_qc, 'hours_valid'].sum():.2f}.",
             f"Current QC + local parquet pairs: {int(cohort.local_clean_pair.sum())}. Parquet samples were not scanned.",
             "QC hours sum valid sample duration after day/participant exclusion; they are not full clock hours or feature-row counts.", "",
             "```csv", counts.to_csv(index=False).strip(), "```", "",
             "Primary question: exploratory participant-level COPS PD vs selected NHANES controls. This cohort cannot establish disease specificity because disease, dataset and device coincide.",
             "Secondary question: hourly COPS symptoms, requiring matched diary scores. Current CopsReader.hour_labels returns visit/sleep only, not TremorScore.",
             "", f"Legacy features: {json.dumps(feature_info)}.",
             "Both REVIEW_ONLY exports retain source_row for exact joins; suspicious columns are absent from baseline. Duplicate rows are retained for investigation, not repaired by averaging.",
             "No model was trained: legacy feature provenance and duplicate hours must be fixed before ablation scores are interpretable.",
             "See experiment_plan.json for the four fixed experiments and evaluation protocol.", "",
             "Next: inventory S3 clean pairs read-only; regenerate stale NHANES QC/clean with current per-axis target; obtain matching parquets; regenerate versioned hourly features; verify control label sources; add both labels per device/dataset.",
             "Current-QC checks cover recorded metadata, inclusion/day consistency and noise target; no historical full clean-config hash exists, so this is not full proof of clean freshness.", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[4:9]))
    print(f"Audit written to {out}; {len(issues)} findings")
    return cohort, issues


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, default=Path("reports/source_audit"))
    args = parser.parse_args()
    audit(args.root, args.out if args.out.is_absolute() else args.root / args.out)
