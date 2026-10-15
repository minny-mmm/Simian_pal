"""รัน ingestion + schema standardization

    python -m src.pipeline                    # ทุก dataset ที่ enabled
    python -m src.pipeline --datasets cops    # เฉพาะบาง dataset
    python -m src.pipeline --force            # เขียน Parquet ทับของเดิม

ผลลัพธ์:
    data/processed/subject_data/subject_metadata_<dataset>.csv   (แยกไฟล์ต่อ dataset)
    data/processed/sensor_data/<dataset>/<condition>/<subject_id>/<recording_id>.parquet
    metadata/manifest.csv
"""
import argparse
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import yaml

from src import storage
from src.ingestion import READERS
from src.schema.sensor_schema import summarize_recording, to_sensor_table
from src.schema.subject_schema import to_subject_frame
from src.validation.manifest import file_md5, to_manifest_frame

ROOT = Path(__file__).resolve().parent.parent


def load_config(path=ROOT / "config" / "config.yaml"):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def ingest_dataset(key, cfg, paths, force):
    reader = READERS[key](cfg["raw_dir"], ROOT)
    subjects = reader.read_subjects()
    condition = {s["subject_id"]: s["condition"] or "unknown" for s in subjects}
    rows, seen_md5 = [], set()
    recordings = reader.list_recordings()
    labels = reader.recording_labels(recordings)

    for rec in recordings:
        if rec.subject_id not in condition:
            raise ValueError(f"{rec.recording_id}: ไม่พบ {rec.subject_id} ใน subject metadata")
        md5 = file_md5(rec.path)
        cond = condition[rec.subject_id]
        out = (ROOT / paths["sensor_data"] / key / cond / rec.subject_id
               / f"{rec.recording_id}.parquet")
        row = dict(
            recording_id=rec.recording_id, dataset=rec.dataset, subject_id=rec.subject_id,
            condition=cond, task=rec.task, wrist=rec.wrist, day=rec.day, hour_start=rec.hour_start,
            raw_path=reader.rel(rec.path), size_mb=round(rec.path.stat().st_size / 1e6, 3),
            md5=md5, is_duplicate=md5 in seen_md5, processed_path=None,
            fs_expected_hz=cfg.get("sampling_rate_hz"), acc_unit=cfg.get("acc_unit"),
            gyro_unit=cfg.get("gyro_unit"), acc_includes_gravity=cfg.get("acc_includes_gravity"),
            **labels.get(rec.recording_id, {}),
        )
        seen_md5.add(md5)

        if row["is_duplicate"]:
            row["status"] = "skipped_duplicate"
        else:
            if out.exists() and not force:
                df = pq.read_table(out, columns=["time_s"]).to_pandas()
                row["status"] = "exists"
            else:
                df = reader.read_recording(rec)
                out.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(to_sensor_table(df, rec.subject_id, rec.recording_id, rec.task),
                               out, compression="zstd")
                row["status"] = "written"
            row.update(summarize_recording(df), processed_path=str(out.relative_to(ROOT)))
        rows.append(row)

    print(f"[{reader.name}] subjects={len(subjects)} recordings={len(rows)} labeled={len(labels)}")
    return subjects, rows


def main():
    ap = argparse.ArgumentParser(description="อ่าน raw data แล้วแปลงเป็น common schema")
    ap.add_argument("--datasets", nargs="+", choices=sorted(READERS),
                    help="ค่าเริ่มต้น = ทุก dataset ที่ enabled ใน config")
    ap.add_argument("--force", action="store_true", help="เขียน Parquet ทับของเดิม")
    ap.add_argument("--config", default=ROOT / "config" / "config.yaml")
    args = ap.parse_args()

    config = load_config(args.config)
    storage.configure(config.get("storage"))
    paths = config["paths"]
    keys = args.datasets or [k for k, c in config["datasets"].items() if c.get("enabled")]

    manifest = []
    for key in keys:
        cfg = config["datasets"][key]
        s, m = ingest_dataset(key, cfg, paths, args.force)
        manifest += m
        # ข้อมูลผู้ป่วยแยกไฟล์ต่อ dataset (คอลัมน์เหมือนกันทุกไฟล์)
        path = ROOT / cfg["subject_file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        to_subject_frame(s).to_csv(path, index=False)
        print(f"เขียน {path.relative_to(ROOT)}: {len(s)} แถว")

    # manifest รวมทุก dataset: ถ้ารันแค่บาง dataset ให้คงแถวของ dataset อื่นไว้
    manifest_df = to_manifest_frame(manifest)
    path = ROOT / paths["manifest"]
    if args.datasets and path.exists() and path.stat().st_size > 0:
        old = pd.read_csv(path, dtype=str)
        old = old[~old["dataset"].isin(manifest_df["dataset"].unique())]
        manifest_df = pd.concat(
            [old, manifest_df.astype(str).replace({"<NA>": None, "None": None, "nan": None})],
            ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest_df.sort_values(["dataset", "recording_id"]).to_csv(path, index=False)
    print(f"เขียน {path.relative_to(ROOT)}: {len(manifest_df)} แถว")

    print(manifest_df.groupby(["dataset", "status"]).size().to_string())


if __name__ == "__main__":
    main()
