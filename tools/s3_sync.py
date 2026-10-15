"""sync ข้อมูลระหว่างเครื่องกับ S3 (storage.s3_root ใน config/config.yaml)

    python tools/s3_sync.py status                    # เทียบในเครื่องกับ S3 (ไม่ย้ายไฟล์)
    python tools/s3_sync.py push --dry-run            # ดูว่าจะอัปโหลดอะไร
    python tools/s3_sync.py push                      # อัปโหลด data/raw/cops, data/raw/nhanes, data/clean
    python tools/s3_sync.py pull data/clean           # ดาวน์โหลดเฉพาะบางส่วน
    python tools/s3_sync.py push data/raw/cops/COPS-8 # ทีละคนก็ได้

layout บน S3 = layout ในเครื่อง: data/raw/cops/... <-> <s3_root>/data/raw/cops/...
ทุกไฟล์ที่อัปโหลดเก็บ md5 ไว้ใน metadata; รอบต่อไปไฟล์ที่ md5 ตรงกันจะถูกข้าม
ดาวน์โหลดแล้วตรวจ md5 ก่อนแทนที่ไฟล์ในเครื่อง
"""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import storage  # noqa: E402

DEFAULT_TARGETS = ["data/raw/cops", "data/raw/nhanes", "data/clean"]
SKIP_SUFFIXES = (".part", ".tmp", ".DS_Store")


def local_files(target):
    base = ROOT / target
    if base.is_file():
        return [base]
    return sorted(p for p in base.rglob("*") if p.is_file() and not p.name.endswith(SKIP_SUFFIXES))


def remote_files(fs, s3_root, target):
    prefix = f"{s3_root.rstrip('/')}/{target}".split("://", 1)[1]
    try:
        keys = fs.find(prefix)
    except FileNotFoundError:
        return []
    root_key = s3_root.rstrip("/").split("://", 1)[1]
    return sorted(k[len(root_key) + 1:] for k in keys if not k.endswith(SKIP_SUFFIXES))


def main():
    ap = argparse.ArgumentParser(description="sync data/ <-> S3")
    ap.add_argument("action", choices=["push", "pull", "status"])
    ap.add_argument("targets", nargs="*", default=DEFAULT_TARGETS, help=f"ค่าเริ่มต้น: {DEFAULT_TARGETS}")
    ap.add_argument("--dry-run", action="store_true", help="แสดงอย่างเดียว ไม่ย้ายไฟล์")
    ap.add_argument("--overwrite", action="store_true", help="ย้ายทุกไฟล์แม้ md5 ตรงกัน")
    ap.add_argument("--config", default=ROOT / "config" / "config.yaml")
    a = ap.parse_args()

    config = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    storage.configure(config.get("storage"))
    s3_root = storage.s3_root(config)
    fs = storage.fs_of(s3_root)
    print(f"S3: {s3_root}")

    counts = {}
    for target in a.targets:
        target = target.rstrip("/")
        local = {p.relative_to(ROOT).as_posix(): p for p in local_files(target)} if (ROOT / target).exists() else {}
        remote = set(remote_files(fs, s3_root, target))
        if a.action == "status":
            only_l, only_r = sorted(set(local) - remote), sorted(remote - set(local))
            size = sum(p.stat().st_size for p in local.values()) / 1e9
            print(f"{target}: ในเครื่อง {len(local)} ไฟล์ ({size:.2f} GB) | บน S3 {len(remote)} | "
                  f"ยังไม่ขึ้น S3 {len(only_l)} | มีแต่บน S3 {len(only_r)}")
            for k in only_l[:5]:
                print(f"   ยังไม่ขึ้น: {k}")
            continue
        items = sorted(local) if a.action == "push" else sorted(remote)
        if not items:
            print(f"{target}: ไม่มีไฟล์")
            continue
        for rel in items:
            uri = f"{s3_root.rstrip('/')}/{rel}"
            if a.dry_run:
                status = "dry-run"
            elif a.action == "push":
                status = storage.upload(local[rel], uri, overwrite=a.overwrite)
            else:
                status = storage.download(uri, ROOT / rel, overwrite=a.overwrite)
            counts[status] = counts.get(status, 0) + 1
            if status != "skip":
                print(f"  {status}: {rel}")
        print(f"{target}: {len(items)} ไฟล์")
    if counts:
        print("สรุป:", counts)


if __name__ == "__main__":
    main()
