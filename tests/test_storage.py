"""อ่าน raw จาก S3 ต้องได้ผลเหมือนอ่านจากเครื่อง (ทดสอบกับ S3 จำลองของ moto ไม่แตะ AWS จริง)"""
import io
import tarfile
import zipfile

import numpy as np
import pytest

pytest.importorskip("moto")
pytest.importorskip("s3fs")

from moto.server import ThreadedMotoServer  # noqa: E402

from src import storage  # noqa: E402
from src.ingestion.cops_reader import CopsReader  # noqa: E402
from src.ingestion.nhanes_reader import NhanesReader  # noqa: E402

BUCKET = "test-bucket"


@pytest.fixture(scope="module")
def s3_server():
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def s3(s3_server, monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    storage.configure({"endpoint_url": s3_server, "region": "us-east-1"})
    fs = storage.fs_of("s3://")
    fs.invalidate_cache()
    if not fs.exists(BUCKET):
        import boto3
        boto3.client("s3", endpoint_url=s3_server, region_name="us-east-1").create_bucket(Bucket=BUCKET)
        fs.invalidate_cache()
    for k in fs.find(BUCKET):
        fs.rm(k)
    fs.invalidate_cache()
    yield fs
    storage.configure(None)


@pytest.fixture
def raw(tmp_path):
    cops = tmp_path / "data/raw/cops"
    acc = cops / "COPS-01/Accelerometry"
    acc.mkdir(parents=True)
    (cops / "Demographics.csv").write_text(
        "ID;Age;Sex;Handedness;PD_Subtype;PD_DominantSide;PD_HoehnAndYahr;"
        "PD_YearsSinceDiagnosis;DBS\nCOPS-01;60;female;right;akinetic-rigid;left;2.5;10;yes\n")
    for h in (13, 14):
        csv = "Time;X;Y;Z;Photo;Temp\n" + "".join(
            f"{h:02d}:00:{s // 100:02d}.{s % 100 * 10:03d};{0.01 * s:.4f};0.0;1.0;5;30\n" for s in range(300))
        with zipfile.ZipFile(acc / f"COPS-01_Day0_{h}h-{h + 1}h_leftWrist.zip", "w") as z:
            z.writestr(f"COPS-01_Day0_{h}h-{h + 1}h_leftWrist.csv", csv)
    (cops / "COPS-01/COPS-01_symptomdiary.csv").write_text(
        "ID;Day;Time;Visit;Kinesia\nCOPS-01;0 days;14 hr;outpatient;Sleep\n")

    nh = tmp_path / "data/raw/nhanes/pax80_raw"
    nh.mkdir(parents=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        def add(name, text):
            b = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(b)
            tf.addfile(info, io.BytesIO(b))
        add("74132_Logs.csv", "DAY_OF_DATA,START_TIME,END_TIME,DATA_QUALITY_FLAG_CODE,DATA_QUALITY_FLAG_VALUE\n"
                              "1,10:00:00.000,10:00:01.000,X,1\n")
        add("GT3XPLUS-AccelerationCalibrated-x.2000-01-01-10-00-00-000-P0000.sensor.csv",
            "HEADER_TIMESTAMP,X,Y,Z\n" + "".join(
                f"2000-01-01 10:00:00.{i * 125:03d},0.{i},0.0,1.0\n" for i in range(8)))
    (nh / "74132.tar.bz2").write_bytes(buf.getvalue())
    return tmp_path


def push_all(root):
    for p in sorted(root.rglob("*")):
        if p.is_file():
            assert storage.upload(p, storage.mirror(p, root, f"s3://{BUCKET}")) == "uploaded"


def test_cops_from_s3_matches_local(raw, s3):
    push_all(raw)
    local = CopsReader("data/raw/cops", raw)
    remote = CopsReader(f"s3://{BUCKET}/data/raw/cops", raw)
    assert [r.recording_id for r in remote.list_recordings()] == [r.recording_id for r in local.list_recordings()]
    assert remote.has_raw("COPS-01") and not remote.has_raw("COPS-99")
    got = list(remote.iter_hours("COPS-01", "left"))
    want = list(local.iter_hours("COPS-01", "left"))
    assert len(got) == 2
    for (t1, x1), (t2, x2) in zip(got, want):
        np.testing.assert_array_equal(t1, t2)
        np.testing.assert_array_equal(x1, x2)
    assert remote.hour_labels("COPS-01").equals(local.hour_labels("COPS-01"))
    assert [s["subject_id"] for s in remote.read_subjects()] == ["COPS-01"]
    assert remote.rel(remote.list_recordings()[0].path).startswith("s3://")


def test_nhanes_from_s3_matches_local(raw, s3):
    push_all(raw)
    local = NhanesReader("data/raw/nhanes", raw)
    remote = NhanesReader(f"s3://{BUCKET}/data/raw/nhanes", raw)
    assert remote.has_raw("NHANES-74132") and not remote.has_raw("NHANES-1")
    (t1, x1), = remote.iter_hours("NHANES-74132")
    (t2, x2), = local.iter_hours("NHANES-74132")
    np.testing.assert_array_equal(t1, t2)
    np.testing.assert_array_equal(x1, x2)
    np.testing.assert_array_equal(remote.flag_intervals("NHANES-74132"), local.flag_intervals("NHANES-74132"))


def test_upload_skips_same_file_and_download_checks_md5(raw, s3, tmp_path):
    f = raw / "data/raw/cops/Demographics.csv"
    uri = storage.mirror(f, raw, f"s3://{BUCKET}")
    assert uri == f"s3://{BUCKET}/data/raw/cops/Demographics.csv"
    assert storage.upload(f, uri) == "uploaded"
    assert storage.upload(f, uri) == "skip"
    f.write_text(f.read_text() + "\n")
    assert storage.upload(f, uri) == "uploaded"           # เนื้อหาเปลี่ยน -> อัปโหลดใหม่
    out = tmp_path / "dl/Demographics.csv"
    assert storage.download(uri, out) == "downloaded"
    assert out.read_bytes() == f.read_bytes()
    assert storage.download(uri, out) == "skip"


def test_s3_root_must_be_set():
    with pytest.raises(SystemExit):
        storage.s3_root({"storage": {"s3_root": "s3://CHANGE-ME-bucket/pd-pipeline"}})
    assert storage.s3_root({"storage": {"s3_root": "s3://b/p"}}) == "s3://b/p"
