"""ดึงข้อมูลจาก S3 ลงเครื่อง (เลือกคน / เลือกชนิดข้อมูลได้) ตรวจ md5 ทุกไฟล์ ไฟล์ที่มีในเครื่องแล้วและ md5 ตรงจะข้าม

    python tools/s3_pull.py list                                   # ดูว่าบน S3 มีใครบ้าง ชนิดไหน ขนาดเท่าไหร่
    python tools/s3_pull.py get --ids COPS-4 NHANES-73558          # ข้อมูลดิบของ 2 คน (COPS เฉพาะข้อมือที่ใช้)
    python tools/s3_pull.py get --ids COPS-4 --wrist both          # COPS ทั้ง 2 ข้อมือ (ขนาดเป็น 2 เท่า)
    python tools/s3_pull.py get --what features                    # feature ของทุกคน (ไฟล์เล็ก)
    python tools/s3_pull.py get --dataset cops --what clean        # ผล clean ของ COPS ทุกคน
    python tools/s3_pull.py get --metadata                         # รายชื่อคน + split (metadata/, reports/)
    python tools/s3_pull.py get --ids COPS-4 --dry-run             # ดูก่อนว่าจะโหลดอะไร

รหัสคน: COPS-4, NHANES-73558 หรือแค่ 73558 (ตัวเลขล้วน = NHANES)
ไฟล์ลงที่ตำแหน่งเดียวกับบน S3: <s3_root>/data/raw/cops/COPS-4/... -> data/raw/cops/COPS-4/...
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import storage  # noqa: E402
from src.ingestion.cops_reader import FILE_RE  # noqa: E402

WHAT = ("raw", "clean", "features")
METADATA = ["metadata/cops_splits.csv", "metadata/nhanes_splits.csv", "reports/cops_visit_summary.csv"]


def norm_id(s):
    s = s.strip()
    if s.isdigit():
        return f"NHANES-{s}"
    return s.upper() if s.upper().startswith(("COPS-", "NHANES-")) else s


def dataset_of(sid):
    return "cops" if sid.startswith("COPS-") else "nhanes"


def raw_dir(config, dataset, s3_root):
    d = config["datasets"][dataset]["raw_dir"].rstrip("/")
    if storage.is_remote(d):
        d = d[len(s3_root.rstrip("/")) + 1:]
    return d


class Remote:
    """รายชื่อไฟล์ทั้งหมดใต้ s3_root (list ครั้งเดียว) -> {path สัมพัทธ์: ขนาด}"""

    def __init__(self, fs, s3_root):
        self.root_key = s3_root.rstrip("/").split("://", 1)[1]
        try:
            info = fs.find(self.root_key, detail=True)
        except FileNotFoundError:
            info = {}
        self.files = {k[len(self.root_key) + 1:]: v.get("size", 0) for k, v in info.items()
                      if not k.endswith(("/", "_source.json"))}

    def under(self, prefix):
        return {k: v for k, v in self.files.items() if k.startswith(prefix)}


def subjects_on_s3(remote, config, s3_root, what):
    """{dataset: set(subject_id)} ที่มีข้อมูลชนิด what บน S3"""
    out = {"cops": set(), "nhanes": set()}
    cops_rd = raw_dir(config, "cops", s3_root)
    nh_rd = raw_dir(config, "nhanes", s3_root)
    depth = len(cops_rd.split("/"))                       # data/raw/cops/<COPS-n>/... -> parts[3]
    for rel in remote.files:
        parts = rel.split("/")
        if what == "raw":
            if rel.startswith(cops_rd + "/") and len(parts) > depth + 1 and parts[depth].upper().startswith("COPS-"):
                out["cops"].add(parts[depth].upper())
            elif rel.startswith(nh_rd + "/pax80_raw/") and rel.endswith(".tar.bz2"):
                out["nhanes"].add("NHANES-" + parts[-1].split(".")[0])
        elif rel.startswith(f"data/{what}/") and len(parts) == 4:
            ds, name = parts[2], parts[3]
            if ds in out and name.endswith((".parquet", ".csv")):
                out[ds].add(name.rsplit(".", 1)[0])
    return out


def used_wrist(sid):
    """ข้อมือที่ pipeline ใช้ของคนนี้ (คอลัมน์ use_wrist ใน metadata/cops_splits.csv) ไม่มีข้อมูล = both"""
    path = ROOT / "metadata" / "cops_splits.csv"
    if not path.exists():
        return "both"
    import pandas as pd
    w = pd.read_csv(path, dtype=str).set_index("subject_id").get("use_wrist", pd.Series(dtype=str)).get(sid)
    return w.lower() if isinstance(w, str) and w.lower() in ("left", "right") else "both"


def files_for(remote, config, s3_root, sid, what, wrist):
    ds = dataset_of(sid)
    if wrist == "auto":
        wrist = used_wrist(sid)
    if what == "raw":
        rd = raw_dir(config, ds, s3_root)
        if ds == "nhanes":
            return remote.under(f"{rd}/pax80_raw/{sid.split('-')[-1]}.tar.bz2")
        files = remote.under(f"{rd}/{sid}/")
        if wrist != "both":
            files = {k: v for k, v in files.items()
                     if not (FILE_RE.match(k.rsplit("/", 1)[-1])
                             and FILE_RE.match(k.rsplit("/", 1)[-1]).group(5).lower() != wrist)}
        files.update(remote.under(f"{rd}/Demographics.csv"))
        return files
    if what == "clean":
        return {**remote.under(f"data/clean/{ds}/{sid}.parquet"), **remote.under(f"data/clean/{ds}/{sid}.qc.json")}
    return remote.under(f"data/features/{ds}/{sid}.csv")


def gb(n):
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.1f} MB"


def cmd_list(remote, config, s3_root):
    print(f"S3: {s3_root}  ({len(remote.files)} ไฟล์, รวม {gb(sum(remote.files.values()))})")
    for what in WHAT:
        found = subjects_on_s3(remote, config, s3_root, what)
        for ds in ("cops", "nhanes"):
            ids = sorted(found[ds], key=lambda s: int(s.split("-")[-1]))
            if not ids:
                continue
            size = sum(sum(files_for(remote, config, s3_root, s, what, "both").values()) for s in ids)
            shown = ", ".join(ids[:12]) + (f" ... (+{len(ids) - 12})" if len(ids) > 12 else "")
            print(f"  {what:8s} {ds:6s} {len(ids):3d} คน  {gb(size):>9s}  {shown}")
    meta = [m for m in METADATA if m in remote.files]
    print(f"  metadata: {', '.join(meta) if meta else 'ยังไม่มี (เจ้าของรัน s3_sync.py push metadata reports/cops_visit_summary.csv)'}")


def cmd_get(remote, config, s3_root, a):
    exclude = tuple("." + e.lower().lstrip(".") for e in a.exclude)
    plan = {}
    if a.metadata:
        plan.update({m: remote.files[m] for m in METADATA if m in remote.files})
    only_metadata = a.metadata and not (a.ids or a.dataset or a.all or a.what != ["raw"])
    if not only_metadata:
        for what in a.what:
            if a.ids:
                ids = [norm_id(i) for i in a.ids]
            else:
                found = subjects_on_s3(remote, config, s3_root, what)
                ids = sorted(found["cops"] | found["nhanes"])
                if what == "raw" and not a.dataset and not a.all:
                    raise SystemExit("ข้อมูลดิบมีขนาดใหญ่ (COPS ~800 MB/คน): ระบุ --ids หรือ --dataset "
                                     "หรือใส่ --all ถ้าตั้งใจโหลดทุกคน")
            if a.dataset:
                ids = [i for i in ids if dataset_of(i) == a.dataset]
            for sid in ids:
                files = files_for(remote, config, s3_root, sid, what, a.wrist)
                if not files:
                    print(f"  ไม่มี {what} ของ {sid} บน S3")
                plan.update(files)
    plan = {k: v for k, v in sorted(plan.items()) if not k.lower().endswith(exclude)}
    print(f"จะโหลด {len(plan)} ไฟล์ รวม {gb(sum(plan.values()))} ลง {ROOT}")
    if a.dry_run:
        for k, v in plan.items():
            print(f"  {k}  ({gb(v)})")
        return
    counts = {}

    def one(rel):
        return rel, storage.download(f"{s3_root.rstrip('/')}/{rel}", ROOT / rel)

    with ThreadPoolExecutor(8) as ex:
        for i, (rel, status) in enumerate(ex.map(one, plan), 1):
            counts[status] = counts.get(status, 0) + 1
            if status != "skip" and (i % 50 == 0 or i == len(plan) or len(plan) <= 20):
                print(f"  [{i}/{len(plan)}] {status}: {rel}")
    print("สรุป:", counts)


def main():
    ap = argparse.ArgumentParser(description="ดึงข้อมูลจาก S3 ลงเครื่อง")
    ap.add_argument("action", choices=["list", "get"])
    ap.add_argument("--ids", nargs="+", help="เช่น COPS-4 NHANES-73558 73558")
    ap.add_argument("--dataset", choices=["cops", "nhanes"])
    ap.add_argument("--what", nargs="+", choices=WHAT, default=["raw"], help="ชนิดข้อมูล (ค่าเริ่มต้น raw)")
    ap.add_argument("--wrist", choices=["auto", "left", "right", "both"], default="auto",
                    help="COPS raw: ข้อมือที่โหลด (auto = ข้างที่ pipeline ใช้ตาม metadata/cops_splits.csv, "
                         "ไม่มีไฟล์นั้น = both)")
    ap.add_argument("--exclude", nargs="*", default=["mat"], help="นามสกุลที่ไม่โหลด (ค่าเริ่มต้น mat)")
    ap.add_argument("--metadata", action="store_true", help="โหลดรายชื่อคน + split ด้วย")
    ap.add_argument("--all", action="store_true", help="ยอมโหลดข้อมูลดิบทุกคน")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default=ROOT / "config" / "config.yaml")
    a = ap.parse_args()

    config = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    storage.configure(config.get("storage"))
    s3_root = storage.s3_root(config)
    remote = Remote(storage.fs_of(s3_root), s3_root)
    if a.action == "list":
        cmd_list(remote, config, s3_root)
    else:
        cmd_get(remote, config, s3_root, a)


if __name__ == "__main__":
    main()
