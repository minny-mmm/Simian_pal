import json
import zipfile

import numpy as np
import pytest

from src.ingestion.cops_reader import CopsReader
from src.schema.sensor_schema import SENSOR_COLUMNS, to_sensor_table
from src.schema.subject_schema import normalize_condition, to_subject_frame


@pytest.fixture
def raw(tmp_path):
    cops = tmp_path / "data/raw/cops/COPS-01"
    cops.mkdir(parents=True)
    (cops.parent / "Demographics.csv").write_text(
        "ID;Age;Sex;Handedness;PD_Subtype;PD_DominantSide;PD_HoehnAndYahr;"
        "PD_YearsSinceDiagnosis;DBS\nCOPS-01;60;female;right;akinetic-rigid;left;2.5;10;yes\n")
    (cops / "COPS-01_Day1_23h-24h_leftWrist.csv").write_text(
        "Time;X;Y;Z;Photo;Temp\n23:59:59.990;1;0;0;5;30\n00:00:00.000;1;0;0;5;30\n")
    return tmp_path


def test_cops_midnight_rollover(raw):
    r = CopsReader("data/raw/cops", raw)
    [s] = to_subject_frame(r.read_subjects()).to_dict("records")
    assert (s["subject_id"], s["condition"], s["age_at_diagnosis_years"]) == ("COPS-01", "parkinsons", 50)
    assert json.loads(s["extra_json"])["hoehn_yahr"] == 2.5
    [rec] = r.list_recordings()
    assert rec.recording_id == "COPS-01_Day1_23h_LeftWrist"
    df = r.read_recording(rec)
    np.testing.assert_allclose(df["time_s"], [0, 0.01])
    assert df["study_time_s"].iloc[1] == pytest.approx(2 * 86400)
    table = to_sensor_table(df, rec.subject_id, rec.recording_id)
    assert table.column("gyro_x").null_count == 2
    assert table.column("task").null_count == 2


def test_schema_rejects_bad_data():
    with pytest.raises(ValueError):
        normalize_condition("flu")
    with pytest.raises(ValueError):
        to_subject_frame([{"subject_id": "A", "dataset": "X", "source_subject_id": "A"}] * 2)



def test_cops_reads_zip_and_drops_subjects_without_data(raw):
    cops = raw / "data/raw/cops"
    (cops / "Demographics.csv").write_text(
        (cops / "Demographics.csv").read_text() + "COPS-99;70;male;left;tremordominant;left;3;7;no\n")
    acc = cops / "COPS-02/Accelerometry"
    acc.mkdir(parents=True)
    with zipfile.ZipFile(acc / "COPS-02_Day0_18h-19h_rightWrist.zip", "w") as zf:
        zf.writestr("COPS-02_Day0_18h-19h_rightWrist.csv",
                    "Time;X;Y;Z;Photo;Temp\n18:00:00.000;1;0;0;5;30\n18:00:00.010;0;1;0;5;30\n")
    r = CopsReader("data/raw/cops", raw)
    ids = to_subject_frame(r.read_subjects())["subject_id"].tolist()
    assert ids == ["COPS-01", "COPS-02"]   # COPS-99 ไม่มีไฟล์สัญญาณ
    rec = next(x for x in r.list_recordings() if x.subject_id == "COPS-02")
    assert (rec.recording_id, rec.wrist) == ("COPS-02_Day0_18h_RightWrist", "right")
    df = r.read_recording(rec)
    np.testing.assert_allclose(df["acc_y"], [0, 1])
    assert df["study_time_s"].iloc[0] == pytest.approx(18 * 3600)
