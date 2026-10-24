"""Read S3 QC/diaries into a separate local snapshot; never modify S3/raw data.

python tools/audit_model_readiness.py
"""
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import storage
from tools.audit_sources import audit


def candidate_labels(diary, qc):
    """Candidate diary hours on good QC days, NOT sample-verified usable hours."""
    d = diary.copy()
    d["day"] = pd.to_numeric(d.Day.str.extract(r"(\d+)")[0], errors="coerce")
    d["hour"] = pd.to_numeric(d.Time.str.extract(r"(\d+)")[0], errors="coerce") - 1
    d["tremor_score"] = pd.to_numeric(d.TremorScore, errors="coerce")
    d["kinesia_score"] = pd.to_numeric(d.KinesiaScore, errors="coerce")
    good = {r["day"] for r in qc.get("days", []) if r.get("valid")}
    d["duplicate_hour"] = d.duplicated(["day", "hour"], keep=False)
    eligible = (qc.get("included") is True and qc.get("status") == "ok")
    d["candidate"] = (eligible & d.day.isin(good) & d.hour.between(0, 23)
                      & d.Visit.str.strip().eq("outpatient")
                      & ~d.Kinesia.str.strip().isin(["Sleep", "No Data", ""])
                      & d.WearableDataAvailability.str.strip().eq("Wearable Worn")
                      & ~d.duplicate_hour)
    d["candidate_tremor"] = d.candidate & d.tremor_score.isin([0, 1, 2])
    return d


def main():
    cfg = yaml.safe_load((ROOT / "config/config.yaml").read_text(encoding="utf-8"))
    out = ROOT / "reports/model_readiness"
    snapshot = out / "snapshots" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=True)
    storage.configure(cfg.get("storage"))
    remote = storage.s3_root(cfg).rstrip("/")
    prefix = remote.split("://", 1)[1]
    fs = storage.fs_of(remote)
    info = fs.find(prefix, detail=True)
    inventory = {k[len(prefix) + 1:]: v.get("size", 0) for k, v in info.items()
                 if not k.endswith("/") and v.get("type") != "directory"}
    (out / "s3_inventory.json").write_text(json.dumps(dict(
        fetched_utc=datetime.now(timezone.utc).isoformat(), files=inventory), indent=2), encoding="utf-8")
    # Copy local roster/config into the snapshot so refreshed QC never overwrites old evidence.
    paths = ["config/config.yaml", *cfg["clean"]["subjects"].values()]
    for rel in paths:
        target = snapshot / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / rel).read_bytes())
    small = [p for p in inventory if p.endswith(".qc.json") or p.lower().endswith("_symptomdiary.csv")]

    def fetch(rel):
        data = fs.cat(f"{prefix}/{rel}")
        target = snapshot / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return rel

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(fetch, small))
    cohort, _ = audit(snapshot, out / "qc_audit")
    cohort["remote_clean_pair"] = [
        f"{cfg['clean']['out_dir']}/{r.dataset}/{r.subject_id}.parquet" in inventory
        and r.qc_available for r in cohort.itertuples()]
    cohort.to_csv(out / "participants.csv", index=False)
    groups = []
    for (ds, split), g in cohort.groupby(["dataset", "split"]):
        ok = g.qc_included & g.remote_clean_pair
        current = g.current_qc & g.remote_clean_pair
        groups.append(dict(dataset=ds, split=split, roster=len(g), qc=int(g.qc_available.sum()),
                           remote_pairs=int(g.remote_clean_pair.sum()), included_pairs=int(ok.sum()),
                           reported_hours=round(g.loc[ok, "hours_valid"].sum(), 2),
                           current_checked_pairs=int(current.sum()),
                           current_checked_hours=round(g.loc[current, "hours_valid"].sum(), 2)))
    counts = pd.DataFrame(groups)
    counts.to_csv(out / "coverage.csv", index=False)
    report = ["# S3 model readiness", "", "Source: S3 QC JSON, object inventory and symptom diaries; local roster/config.",
              "QC checks cover metadata, inclusion/day consistency and noise target, not full historical config provenance.",
              "Remote pair means both object names exist; parquet contents/valid sample hours were not scanned.",
              "Zero reported hours with zero QC means unknown, not an observed absence of recording.",
              "", "```csv", counts.to_csv(index=False).strip(), "```", ""]
    labels, diary_status = [], []
    for r in cohort[cohort.dataset == "cops"].itertuples():
        diary = snapshot / f"data/raw/cops/{r.subject_id}/{r.subject_id}_symptomdiary.csv"
        qp = snapshot / cfg["clean"]["out_dir"] / "cops" / f"{r.subject_id}.qc.json"
        diary_status.append(dict(subject_id=r.subject_id, split=r.split,
                                 diary_available=diary.exists(), qc_available=qp.exists()))
        if not diary.exists() or not qp.exists():
            continue
        d = pd.read_csv(io.BytesIO(diary.read_bytes()), sep=";", dtype=str, keep_default_na=False)
        d = candidate_labels(d, json.loads(qp.read_text(encoding="utf-8")))
        d["subject_id"], d["split"] = r.subject_id, r.split
        d["remote_clean_pair"] = r.remote_clean_pair
        labels.append(d[["subject_id", "split", "day", "hour", "tremor_score", "kinesia_score",
                         "duplicate_hour", "candidate", "candidate_tremor", "remote_clean_pair"]])
    pd.DataFrame(diary_status).to_csv(out / "diary_availability.csv", index=False)
    if labels:
        label_frame = pd.concat(labels, ignore_index=True)
        label_frame.to_csv(out / "diary_labels_REVIEW_ONLY.csv", index=False)
        candidate = label_frame[label_frame.candidate_tremor & label_frame.remote_clean_pair]
        classes = candidate.groupby(["split", "tremor_score"]).agg(
            candidate_hours=("subject_id", "size"), participants=("subject_id", "nunique")).reset_index()
        classes.to_csv(out / "tremor_coverage.csv", index=False)
        subjects = candidate.groupby(["split", "subject_id"]).tremor_score.agg(
            candidate_hours="size", distinct_scores="nunique", min_score="min", max_score="max")
        subjects.to_csv(out / "tremor_subjects.csv")
        report.extend(["Candidate diary hours by score (not sample-verified usable hours):", "", "```csv",
                       classes.to_csv(index=False).strip(), "```", "",
                       "Candidate hours require QC inclusion, good QC day, outpatient, awake, Wearable Worn, valid score and unique day/hour.",
                       "Actual sample-valid duration per hour and diary/signal alignment still need checking.", ""])
        print("Candidate tremor labels (NOT sample-verified hours):")
        print(classes.to_string(index=False))
    print(counts.to_string(index=False))
    report.extend(["Recommended next experiment: COPS hourly tremor presence (score 0 vs 1/2), with ordinal severity as secondary.",
                   "Use grouped cross-validation within original train participants; preserve existing valid/test identities.",
                   "Existing validation/test clean coverage is incomplete. Do not report confirmatory test performance yet.",
                   "Regenerate versioned features with unique subject/day/hour keys; retain still_frac/rest_tremor_ratio only for separate ablations.",
                   "COPS-vs-NHANES classification remains exploratory because label equals dataset/device. Harmonization or augmentation cannot establish disease specificity here.",
                   "Keep NHANES for exploratory controls and separate train-only representation learning; exclude held-out participants from fitting.", ""])
    (out / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"Outputs: {out}")


if __name__ == "__main__":
    main()
