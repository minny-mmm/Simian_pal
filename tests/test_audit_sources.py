import numpy as np
import pandas as pd

from tools.audit_sources import audit, coverage, separate_features


def test_coverage_keeps_absent_class_and_unknown_qc_visible():
    frame = pd.DataFrame([
        dict(dataset="cops", device="GENEActiv", split="train", label=1,
             qc_available=True, qc_included=True, current_qc=True,
             local_clean_pair=False, hours_valid=12.5),
        dict(dataset="cops", device="GENEActiv", split="train", label=1,
             qc_available=False, qc_included=False, current_qc=False,
             local_clean_pair=False, hours_valid=np.nan),
        dict(dataset="cops", device="GENEActiv", split="train", label=1,
             qc_available=True, qc_included=False, current_qc=False,
             local_clean_pair=False, hours_valid=90),
    ])
    result = coverage(frame).set_index("label")
    assert result.loc[0, "participants"] == 0
    assert result.loc[1, "participants"] == 3
    assert result.loc[1, "qc_available"] == 2
    assert result.loc[1, "qc_reported_hours"] == 12.5


def test_quarantine_preserves_duplicate_identity_and_missing_values():
    f = pd.DataFrame(dict(dataset=["cops"] * 2, subject_id=["COPS-1"] * 2,
                          day=[1, 1], hour=[5, 5], label=[1, 1], split=["train"] * 2,
                          still_frac=[0.0, np.nan], rest_tremor_ratio=[.2, .4],
                          enmo_mg=[5., 8.], accidental_predictor=[999, 999]))
    baseline, suspect = separate_features(f)
    assert "still_frac" not in baseline and "rest_tremor_ratio" not in baseline
    assert "accidental_predictor" not in baseline
    restored = baseline.merge(suspect[["source_row", "still_frac", "rest_tremor_ratio"]], on="source_row")
    assert len(restored) == len(f)
    pd.testing.assert_series_equal(restored.still_frac, f.still_frac)
    pd.testing.assert_series_equal(restored.rest_tremor_ratio, f.rest_tremor_ratio)


def test_audit_detects_stale_noise_and_counts_only_good_days(tmp_path):
    import json
    import yaml

    (tmp_path / "config").mkdir()
    cfg = dict(datasets={"cops": {"sampling_rate_hz": 100}, "nhanes": {"sampling_rate_hz": 80}},
               clean=dict(subjects=dict(cops_summary="visits.csv", cops_splits="cops.csv",
                                        nhanes_selected="nh.csv"), out_dir="clean", qc_summary="summary.csv",
                          resample={"fs_out": 50}, days={"min_valid_days": 1},
                          harmonize={"add_noise": {"datasets": ["nhanes"], "target_mg": [4.93, 5.07, 6.]}}))
    (tmp_path / "config/config.yaml").write_text(yaml.safe_dump(cfg))
    (tmp_path / "visits.csv").write_text("subject_id,use_for_main_model\nCOPS-1,yes\n")
    (tmp_path / "cops.csv").write_text("subject_id,use_wrist,split\nCOPS-1,left,train\n")
    (tmp_path / "nh.csv").write_text("seqn,split\n1,train\n")
    for ds, sid, rate in [("cops", "COPS-1", 100), ("nhanes", "NHANES-1", 80)]:
        folder = tmp_path / "clean" / ds
        folder.mkdir(parents=True)
        q = dict(subject_id=sid, dataset=ds, fs_native=rate, fs_out=50, split="train",
                 wrist="left", included=True, status="ok", hours_valid=12., valid_days=1,
                 days=[dict(day=0, valid=False, valid_h=8.), dict(day=1, valid=True, valid_h=12.)])
        (folder / f"{sid}.qc.json").write_text(json.dumps(q))
    cohort, issues = audit(tmp_path, tmp_path / "output")
    assert cohort.qc_included.sum() == 2
    assert cohort.current_qc.sum() == 1
    assert sum(i["code"] == "stale_noise_target" for i in issues) == 1
    assert not any(i["code"] == "qc_day_hours_mismatch" for i in issues)
