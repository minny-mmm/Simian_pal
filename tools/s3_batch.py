"""ทำงานกับ S3 ทีละกลุ่ม (ค่าเริ่มต้นกลุ่มละ 3 คน) ไม่ต้องเก็บข้อมูลดิบทุกคนไว้ในเครื่อง

    # 1) ส่งข้อมูลดิบจากต้นทางขึ้น S3 ทีละ 3 คน (COPS จาก OSF, NHANES จาก CDC)
    #    โหลดลงโฟลเดอร์ชั่วคราว -> อัปโหลด -> ลบ ในเครื่องใช้ที่แค่ ~3 คน (~2.5 GB)
    python tools/s3_batch.py ingest --dry-run            # ดูว่าจะส่งไฟล์อะไร ขนาดเท่าไหร่
    python tools/s3_batch.py ingest                      # ทุกคนในรายชื่อ
    python tools/s3_batch.py ingest --ids COPS-8 NHANES-74132

    # 2) ดึงข้อมูลดิบจาก S3 ทีละ 3 คน -> clean -> อัปผล clean ขึ้น S3 -> ลบไฟล์ใหญ่ในเครื่อง
    python tools/s3_batch.py clean --dry-run             # ดูว่าใครต้อง clean และเพราะอะไร
    python tools/s3_batch.py clean                       # ทุกคนที่ยังไม่มีผล clean บน S3 หรือผลเก่า
    python tools/s3_batch.py clean --dataset cops --split train
    python tools/s3_batch.py clean --ids NHANES-73558 --force

รายชื่อคน = รายชื่อเดียวกับขั้น clean (src/clean/run.py: load_subjects) ทำ COPS ก่อน NHANES
(NHANES ใช้ noise floor ของ COPS กลุ่ม train เป็นเป้า)
ผล clean บน S3 ที่ใช้เป้า noise ต่างจาก config ปัจจุบัน (NHANES) ถือว่าเก่า -> clean ใหม่ให้เอง

layout บน S3 = layout ในเครื่อง (เหมือน tools/s3_sync.py):
    <s3_root>/data/raw/cops/COPS-8/...                     ข้อมูลดิบ
    <s3_root>/data/raw/nhanes/2013-2014/pax80_raw/<SEQN>.tar.bz2
    <s3_root>/data/clean/<dataset>/<subject_id>.parquet    ผล clean (+ .qc.json)
ในเครื่องเหลือแค่ไฟล์เล็ก: .qc.json, reports/qc_summary.csv (--keep-clean = เก็บ parquet ไว้ด้วย)
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from src import storage  # noqa: E402
from src.clean import noise_target, run  # noqa: E402
from src.ingestion import READERS  # noqa: E402
from src.ingestion.cops_reader import FILE_RE  # noqa: E402

WORK_DIR = ROOT / "data" / "_batch"          # ไฟล์ดิบชั่วคราวของกลุ่มที่กำลังทำ (ลบเมื่อเสร็จ)
OSF_CACHE = ROOT / "metadata" / "osf_files.csv"


def batches(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def uri(s3_root, rel):
    return f"{s3_root.rstrip('/')}/{rel}"


def key(u):
    return str(u).split("://", 1)[1]


def raw_rel(config, dataset, s3_root):
    """raw_dir ใน config แบบ path สัมพัทธ์ (ถ้าตั้งเป็น s3:// ไว้ ตัด s3_root ออก)"""
    d = config["datasets"][dataset]["raw_dir"].rstrip("/")
    if storage.is_remote(d):
        if not d.startswith(s3_root.rstrip("/") + "/"):
            raise SystemExit(f"datasets.{dataset}.raw_dir ({d}) ไม่อยู่ใต้ storage.s3_root ({s3_root})")
        d = d[len(s3_root.rstrip("/")) + 1:]
    return d


def select_subjects(config, args):
    subjects = run.load_subjects(config)
    if args.ids:
        unknown = set(args.ids) - {s["subject_id"] for s in subjects}
        if unknown:
            raise SystemExit(f"ไม่อยู่ในรายชื่อ: {sorted(unknown)}")
        subjects = [s for s in subjects if s["subject_id"] in args.ids]
    if args.split:
        subjects = [s for s in subjects if s["split"] == args.split]
    if args.dataset:
        subjects = [s for s in subjects if s["dataset"] == args.dataset]
    return sorted(subjects, key=lambda s: s["dataset"] != "cops")


# ---------------------------------------------------------------- 1) ingest: ต้นทาง -> S3

def osf_listing(refresh=False):
    """รายชื่อไฟล์ทั้งหมดบน OSF (path, size, url) เก็บไว้ใน metadata/osf_files.csv เพราะไล่ทั้ง project ช้า"""
    if OSF_CACHE.exists() and not refresh:
        return pd.read_csv(OSF_CACHE)
    import download_pilot
    print(f"อ่านรายชื่อไฟล์บน OSF ({download_pilot.OSF_NODE}) อาจใช้เวลาหลายนาที")
    df = pd.DataFrame(list(download_pilot.osf_files(download_pilot.OSF_NODE)), columns=["path", "size", "url"])
    OSF_CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OSF_CACHE, index=False)
    print(f"  {len(df)} ไฟล์ -> {OSF_CACHE.relative_to(ROOT)}")
    return df


def cops_rel(osf_path, raw_dir):
    """path บน OSF -> path ใน layout ของเรา (จัดตามชื่อไฟล์ ไม่ขึ้นกับโฟลเดอร์บน OSF)"""
    name = osf_path.rstrip("/").rsplit("/", 1)[-1]
    m = FILE_RE.match(name)
    if m:
        return f"{raw_dir}/{m.group(1).upper()}/Accelerometry/{name}"
    m = re.match(r"^(COPS-\d+)_", name, re.I)
    if m:
        return f"{raw_dir}/{m.group(1).upper()}/{name}"
    if name.lower() == "demographics.csv":
        return f"{raw_dir}/Demographics.csv"
    return None


def ingest_plan(config, subjects, s3_root, refresh):
    """-> list ของ (subject_id, url, rel, size, kind) ที่ต้องส่งขึ้น S3
    kind = "file" (อัปทั้งไฟล์ไปที่ rel) หรือ "subject_zip" (zip ทั้งคนบน OSF: แตกแล้วอัปทีละไฟล์ใต้ rel/)"""
    plan = []
    cops = [s["subject_id"] for s in subjects if s["dataset"] == "cops"]
    if cops:
        rd = raw_rel(config, "cops", s3_root)
        osf = osf_listing(refresh)
        osf["rel"] = [cops_rel(p, rd) for p in osf["path"]]
        demo = osf[osf["rel"] == f"{rd}/Demographics.csv"]
        for r in demo.head(1).itertuples():
            plan.append(("Demographics", r.url, r.rel, int(r.size), "file"))
        for sid in cops:
            pat = re.compile(re.escape(sid) + r"(?!\d)", re.I)       # COPS-2 ไม่จับ COPS-20
            mine = osf[osf["path"].str.contains(pat)]
            if mine.empty:
                print(f"คำเตือน: ไม่พบไฟล์ของ {sid} บน OSF")
            for r in mine.itertuples():
                name = r.path.rsplit("/", 1)[-1]
                if re.fullmatch(re.escape(sid) + r"\.zip", name, re.I):   # /Data/COPS-4.zip
                    plan.append((sid, r.url, f"{rd}/{sid}", int(r.size), "subject_zip"))
                elif r.rel is None or pd.isna(r.rel):
                    print(f"  ข้าม (ไม่รู้ว่าจะวางไว้ไหน): {r.path}")
                else:
                    plan.append((sid, r.url, r.rel, int(r.size), "file"))
    nh = [s["subject_id"] for s in subjects if s["dataset"] == "nhanes"]
    if nh:
        import download_pilot
        rd = raw_rel(config, "nhanes", s3_root)
        for sid in nh:
            seqn = sid.split("-")[-1]
            plan.append((sid, download_pilot.NHANES_URL.format(seqn=seqn),
                         f"{rd}/pax80_raw/{seqn}.tar.bz2", None, "file"))
    return plan


def fetch(url, path):
    import requests
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(path)


def fetch_retry(url, path, size, label):
    for attempt in range(4):
        try:
            fetch(url, path)
            break
        except Exception as e:                              # เน็ตหลุด: ลองใหม่ 2, 4, 8 วิ
            if attempt == 3:
                raise
            print(f"  {label}: {e} ลองใหม่")
            time.sleep(2 ** (attempt + 1))
    if size is not None and path.stat().st_size != size:
        raise IOError(f"{label}: โหลดได้ {path.stat().st_size} byte แต่ต้นทางบอก {size}")


def ingest_subject_zip(fs, s3_root, sid, url, prefix, size, overwrite, exclude=()):
    """zip ทั้งคน (~800 MB) -> แตกทีละไฟล์ -> อัปขึ้น <prefix>/... ตามชื่อไฟล์ -> ลบ
    เสร็จแล้วเขียน <prefix>/_source.json ไว้ รอบหน้าข้ามทั้งคน"""
    import zipfile
    marker = key(uri(s3_root, f"{prefix}/_source.json"))
    if not overwrite and fs.exists(marker) and json.loads(fs.cat(marker)).get("size") == size:
        return {"skip": 1}
    raw_dir = prefix.rsplit("/", 1)[0]
    if not overwrite and not fs.exists(marker) and (ROOT / prefix).is_dir():
        print(f"  {sid}: มีอยู่ในเครื่องแล้ว ใช้ python tools/s3_sync.py push {prefix} แทน (ไม่โหลดซ้ำ)")
        return {"local": 1}
    work = WORK_DIR / "ingest" / sid
    zpath = work / f"{sid}.zip"
    fetch_retry(url, zpath, size, f"{sid}.zip")
    counts = {}
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if info.is_dir() or info.filename.startswith("__MACOSX") or name.startswith("._") \
                    or name == ".DS_Store" or name.lower().endswith(tuple(exclude)):
                continue
            rel = cops_rel(name, raw_dir) or f"{prefix}/{name}"
            if not rel.startswith(prefix + "/"):
                rel = f"{prefix}/{name}"                     # ไฟล์ของคนอื่นใน zip นี้ (ไม่ควรมี)
            tmp = work / "x" / name
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            status = storage.upload(tmp, uri(s3_root, rel), overwrite=overwrite)
            tmp.unlink()
            counts[status] = counts.get(status, 0) + 1
    fs.pipe(marker, json.dumps({"source": url, "size": size, "files": sum(counts.values())}).encode())
    shutil.rmtree(work, ignore_errors=True)
    return counts


def ingest_one(fs, s3_root, items, overwrite, exclude=()):
    """ไฟล์ของ 1 คน: ข้ามถ้าบน S3 มีแล้ว ไม่งั้นโหลด -> อัปโหลด (เก็บ md5) -> ลบ"""
    counts = {}
    for sid, url, rel, size, kind in items:
        if kind == "subject_zip":
            for k, v in ingest_subject_zip(fs, s3_root, sid, url, rel, size, overwrite, exclude).items():
                counts[k] = counts.get(k, 0) + v
            continue
        if rel.lower().endswith(tuple(exclude)):
            continue
        u = uri(s3_root, rel)
        if not overwrite and fs.exists(key(u)) and (size is None or fs.info(key(u))["size"] == size):
            status = "skip"
        else:
            local = WORK_DIR / "ingest" / rel
            fetch_retry(url, local, size, rel)
            status = storage.upload(local, u, overwrite=True)
            local.unlink()
        counts[status] = counts.get(status, 0) + 1
    return counts


def cmd_ingest(config, args, s3_root):
    subjects = select_subjects(config, args)
    plan = ingest_plan(config, subjects, s3_root, args.refresh_osf)
    known = [p[3] for p in plan if p[3] is not None]
    print(f"{len(plan)} รายการ จาก {len({p[0] for p in plan})} คน/ไฟล์"
          f" (COPS รวม {sum(known) / 1e9:.1f} GB, NHANES คนละ ~130-180 MB)")
    if args.dry_run:
        for sid, url, rel, size, kind in plan:
            mb = "?" if size is None else f"{size / 1e6:.0f}"
            print(f"  {rel}/  ({mb} MB zip ทั้งคน: แตกแล้วอัปทีละไฟล์)" if kind == "subject_zip"
                  else f"  {rel}  ({mb} MB)")
        return
    fs = storage.fs_of(s3_root)
    exclude = tuple("." + e.lower().lstrip(".") for e in args.exclude)
    if exclude:
        print(f"ไม่อัปไฟล์นามสกุล: {', '.join(exclude)}")
    by_sid = {}
    for p in plan:
        by_sid.setdefault(p[0], []).append(p)
    sids = list(by_sid)
    for b, group in enumerate(batches(sids, args.batch_size), 1):
        t0 = time.time()
        print(f"กลุ่ม {b}: {', '.join(group)}")
        with ThreadPoolExecutor(len(group)) as ex:
            results = list(ex.map(lambda s: ingest_one(fs, s3_root, by_sid[s], args.overwrite, exclude), group))
        for sid, c in zip(group, results):
            print(f"  {sid}: {c}")
        print(f"  เสร็จกลุ่ม {b} ({time.time() - t0:.0f} วิ)")
    shutil.rmtree(WORK_DIR, ignore_errors=True)


# ---------------------------------------------------------------- 2) clean: S3 -> clean -> S3

def raw_keys(fs, s3_root, rd, subj):
    """ไฟล์ดิบบน S3 ที่ clean ต้องใช้ของคนนี้ (COPS: diary + ข้อมือที่เลือก, NHANES: tar)"""
    sid = subj["subject_id"]
    if subj["dataset"] == "nhanes":
        return [f"{rd}/pax80_raw/{sid.split('-')[-1]}.tar.bz2"]
    root_key = key(uri(s3_root, ""))
    out = []
    for k in fs.find(key(uri(s3_root, f"{rd}/{sid}/"))):
        rel = k[len(root_key):]
        name = rel.rsplit("/", 1)[-1]
        m = FILE_RE.match(name)
        if (m and m.group(5).lower() == subj["wrist"]) or name.lower() == f"{sid.lower()}_symptomdiary.csv":
            out.append(rel)
    return out


def fetch_from_s3(s3_root, rels, dest):
    for rel in rels:
        storage.download(uri(s3_root, rel), dest / rel)


def prefetch_raw(fs, s3_root, config, subj):
    """โหลดข้อมูลดิบของ 1 คนจาก S3 ลง WORK_DIR/raw -> True ถ้ามีข้อมูลดิบบน S3"""
    rd = raw_rel(config, subj["dataset"], s3_root)
    rels = raw_keys(fs, s3_root, rd, subj)
    if not rels:
        return False
    fetch_from_s3(s3_root, rels, WORK_DIR / "raw")
    return True


def clean_rels(config, subj):
    return [p.relative_to(ROOT).as_posix() for p in run.out_paths(config, subj)]   # parquet, qc.json


def needs_clean(fs, s3_root, config, subj, target):
    """-> (ต้อง clean ไหม, เหตุผล) จากผล clean บน S3
    NHANES ที่ใช้เป้า noise ไม่ตรงกับ config ปัจจุบันถือว่าเก่า (เช่น clean ก่อนเปลี่ยนเป็นต่อแกน)"""
    pq_rel, qc_rel = clean_rels(config, subj)
    if not (fs.exists(key(uri(s3_root, pq_rel))) and fs.exists(key(uri(s3_root, qc_rel)))):
        return True, "ยังไม่มีผล clean บน S3"
    if target is not None:
        old = json.loads(fs.cat(key(uri(s3_root, qc_rel)))).get("noise_target_axes_mg")
        if old is None or not np.allclose(old, target, atol=1e-6):
            return True, f"เป้า noise เปลี่ยน ({old if old is None else np.round(old, 2).tolist()} -> {np.round(target, 2).tolist()})"
    return False, "มีผล clean บน S3 แล้ว"


def sync_small(fs, s3_root, pattern):
    """โหลดไฟล์เล็ก (qc.json) จาก S3 ที่ยังไม่มีในเครื่อง"""
    root_key = key(uri(s3_root, ""))
    for k in fs.glob(key(uri(s3_root, pattern))):
        local = ROOT / k[len(root_key):]
        if not local.exists():
            storage.download(f"s3://{k}", local)


def cmd_clean(config, args, s3_root):
    fs = storage.fs_of(s3_root)
    all_subjects = run.load_subjects(config)
    subjects = select_subjects(config, args)
    out_dir = config["clean"]["out_dir"]
    noise_ds = config["clean"]["harmonize"]["add_noise"]["datasets"]
    # qc.json ของ COPS ที่เคย clean (อาจจากเครื่องอื่น) ใช้หาเป้า noise ถ้า target_mg: auto
    sync_small(fs, s3_root, f"{out_dir}/cops/*.qc.json")
    target, _ = run.resolve_noise_target(config, all_subjects)

    todo = []
    for s in subjects:
        need, why = (True, "--force") if args.force else \
            needs_clean(fs, s3_root, config, s, target if s["dataset"] in noise_ds else None)
        if need:
            todo.append(s)
            print(f"  {s['subject_id']}: {why}")
    print(f"S3: {s3_root}\n{len(subjects)} คนในรายชื่อ, ต้อง clean {len(todo)} คน (คนที่มีผล clean ล่าสุดบน S3 แล้วข้าม)")
    if args.dry_run:
        return

    cleaned = []
    for b, group in enumerate(batches(todo, args.batch_size), 1):
        t0 = time.time()
        print(f"กลุ่ม {b}: {', '.join(s['subject_id'] for s in group)}  - โหลดข้อมูลดิบจาก S3")
        with ThreadPoolExecutor(len(group)) as ex:
            has_raw = list(ex.map(lambda s: prefetch_raw(fs, s3_root, config, s), group))
        readers = {}
        for subj, ok in zip(group, has_raw):            # clean ทีละคน (ใช้ RAM มาก)
            sid = subj["subject_id"]
            if not ok:
                print(f"  {sid}: ไม่มีข้อมูลดิบบน S3 (รัน ingest ก่อน) ข้าม")
                continue
            if subj["dataset"] not in readers:
                readers[subj["dataset"]] = READERS[subj["dataset"]](
                    raw_rel(config, subj["dataset"], s3_root), WORK_DIR / "raw")
            subj_target, target_src = (run.resolve_noise_target(config, all_subjects)
                                       if subj["dataset"] in noise_ds else (None, None))
            print(f"  {sid}: clean")
            res, qc = run.clean_subject(config, subj, readers[subj["dataset"]], subj_target,
                                        log=lambda m: print("  " + m))
            qc["noise_target_source"] = target_src
            pq_path, qc_path = run.out_paths(config, subj)
            if res is not None:
                run.write_parquet(pq_path, res)
                del res
            qc_path.parent.mkdir(parents=True, exist_ok=True)
            qc_path.write_text(json.dumps(run.to_json(qc), ensure_ascii=False, indent=1))
            for p in (pq_path, qc_path):                # qc.json ขึ้นทีหลัง = เครื่องหมายว่าเสร็จ
                if p.exists():
                    storage.upload(p, storage.mirror(p, ROOT, s3_root))
            cleaned.append(subj)
            shutil.rmtree(WORK_DIR / "raw" / raw_rel(config, subj["dataset"], s3_root) / sid,
                          ignore_errors=True)
            if not args.keep_clean and pq_path.exists():
                pq_path.unlink()                           # อยู่บน S3 แล้ว
            print(f"  {sid}: เสร็จ วันใช้ได้ {qc.get('valid_days')} ชม.ใช้ได้ {qc.get('hours_valid')}"
                  + ("" if qc.get("included") else " (ไม่ผ่านเกณฑ์จำนวนวัน)"))
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        print(f"  เสร็จกลุ่ม {b} ({time.time() - t0:.0f} วิ)")

    # clean COPS train ใหม่ -> noise ของ COPS อาจเปลี่ยน: เทียบกับ target_mg ใน config
    if any(s["dataset"] == "cops" and s["split"] == "train" for s in cleaned):
        _, _, warn = noise_target.compare(
            config, noise_target.cops_train_axes(config, all_subjects, ROOT, use_parquet=False), 0.1)
        for w in warn:
            print("คำเตือน noise:", w, "(ตรวจละเอียด: python tools/noise_target.py)")

    # รวม QC (รวมของเครื่องอื่นที่อยู่บน S3)
    sync_small(fs, s3_root, f"{out_dir}/*/*.qc.json")
    df = run.write_summary(config)
    if df is not None:
        print(f"เขียน {config['clean']['qc_summary']}: {len(df)} คน")


def main():
    ap = argparse.ArgumentParser(description="ส่งข้อมูลดิบขึ้น S3 / ดึงจาก S3 มา clean แล้วอัปผลกลับ ทีละกลุ่ม")
    ap.add_argument("action", choices=["ingest", "clean"])
    ap.add_argument("--ids", nargs="+", help="subject_id เช่น COPS-8 NHANES-74132 (ค่าเริ่มต้น = ทุกคนในรายชื่อ)")
    ap.add_argument("--split", choices=["train", "valid", "test"])
    ap.add_argument("--dataset", choices=["cops", "nhanes"])
    ap.add_argument("--batch-size", type=int, default=3, help="จำนวนคนต่อกลุ่ม (ค่าเริ่มต้น 3)")
    ap.add_argument("--dry-run", action="store_true", help="แสดงรายการอย่างเดียว (ingest: ไฟล์, clean: คนที่ต้อง clean)")
    ap.add_argument("--refresh-osf", action="store_true", help="ingest: อ่านรายชื่อไฟล์บน OSF ใหม่")
    ap.add_argument("--overwrite", action="store_true", help="ingest: ส่งใหม่แม้บน S3 มีแล้ว")
    ap.add_argument("--exclude", nargs="*", default=["mat"],
                    help="ingest: นามสกุลไฟล์ที่ไม่อัป (ค่าเริ่มต้น mat = สำเนา MATLAB ของ .csv; --exclude เปล่า ๆ = อัปทุกไฟล์)")
    ap.add_argument("--force", action="store_true", help="clean: clean ใหม่แม้บน S3 มีผลล่าสุดแล้ว")
    ap.add_argument("--keep-clean", action="store_true", help="clean: เก็บ parquet ที่ clean แล้วไว้ในเครื่อง")
    ap.add_argument("--config", default=ROOT / "config" / "config.yaml")
    args = ap.parse_args()

    config = run.load_config(args.config)
    storage.configure(config.get("storage"))
    s3_root = storage.s3_root(config)
    os.chdir(ROOT)
    if args.action == "ingest":
        cmd_ingest(config, args, s3_root)
    else:
        cmd_clean(config, args, s3_root)


if __name__ == "__main__":
    main()
