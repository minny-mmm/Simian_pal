import numpy as np
import pandas as pd
import pyarrow as pa

from tools.audit_signal_hours import filename_alignment, hours_from_batches, measure_hour, select_threshold


def frame(times, valid=None):
    n = len(times)
    valid = np.ones(n, bool) if valid is None else np.asarray(valid)
    return pd.DataFrame(dict(time_s=times, x=np.zeros(n), y=np.zeros(n), z=np.ones(n),
                             valid=valid, reason=np.where(valid, "", "ism"),
                             day=np.floor(np.asarray(times) / 86400).astype(int), visit=["outpatient"] * n))


def test_clock_hours_survive_batch_and_row_group_splits():
    f = frame(np.arange(3590, 3620, .25))
    table = pa.Table.from_pandas(f, preserve_index=False)
    batches = table.to_batches(max_chunksize=17)
    hours = list(hours_from_batches(batches))
    assert [key for key, _ in hours] == [0, 1]
    assert [len(g) for _, g in hours] == [40, 80]
    a = measure_hour(hours[0][1], 0, 4)
    b = measure_hour(hours[1][1], 1, 4)
    assert a["complete_valid_10s_windows"] == 1
    assert b["complete_valid_10s_windows"] == 2
    assert a["valid_min"] + b["valid_min"] == .5


def test_gaps_and_invalid_samples_never_become_complete_windows():
    t = np.delete(np.arange(0, 20, .25), 5)
    f = frame(t)
    f.loc[50, ["valid", "reason"]] = [False, "ism|sleep"]
    r = measure_hour(f, 0, 4)
    assert r["bad_cadence_steps"] == 1
    assert r["complete_valid_10s_windows"] == 0
    assert r["valid_samples"] == 78
    assert r["ism_min"] == r["sleep_min"] == 1 / 4 / 60
    assert r["valid_reason_conflicts"] == 0


def test_duplicate_samples_are_detected_not_double_counted_as_windows():
    f = frame(np.r_[0., np.arange(0, 10, .25)])
    f.loc[0, "reason"] = "abnormal"
    r = measure_hour(f, 0, 4)
    assert r["duplicate_time_samples"] == 1
    assert r["complete_valid_10s_windows"] == 0
    assert r["valid_reason_conflicts"] == 1


def test_filename_independently_checks_one_based_diary_time_and_wrist():
    row = pd.Series(dict(day=4, hour=11, WearableDataLeftCSV="COPS-8_Day4_11h-12h_leftWrist.csv"))
    assert filename_alignment(row, "COPS-8", "left") == (True, "")
    row["hour"] = 12
    assert not filename_alignment(row, "COPS-8", "left")[0]
    assert not filename_alignment(row, "COPS-8", "right")[0]


def test_threshold_uses_train_coverage_without_labels_or_predictions():
    h = pd.DataFrame(dict(subject_id=["a"] * 10, valid_min=[35.] * 9 + [20.], structural_ok=[True] * 10))
    threshold, sensitivity, met = select_threshold(h)
    assert threshold == 30 and met
    assert sensitivity.iloc[-1].retained_hours == 9


def test_backwards_time_refuses_ambiguous_alignment():
    import pytest
    table = pa.Table.from_pandas(frame([1., 0.]), preserve_index=False)
    with pytest.raises(ValueError, match="backwards"):
        list(hours_from_batches(table.to_batches()))


def test_actual_parquet_rowgroups_do_not_duplicate_hour_and_midnight_matches_day(tmp_path):
    import pyarrow.parquet as pq
    f = frame(np.arange(86390., 86420., .25))
    path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pandas(f, preserve_index=False), path, row_group_size=13)
    p = pq.ParquetFile(path)
    hours = list(hours_from_batches(p.iter_batches(batch_size=19)))
    assert [key for key, _ in hours] == [23, 24]
    assert sum(len(g) for _, g in hours) == len(f)
    measured = [measure_hour(g, key, 4) for key, g in hours]
    assert [r["day"] for r in measured] == [0, 1]
    assert all(r["day_mismatch_samples"] == 0 for r in measured)
    assert sum(r["valid_samples"] for r in measured) == len(f)
