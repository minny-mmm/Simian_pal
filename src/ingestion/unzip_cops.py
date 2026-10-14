import argparse
import re
import zipfile
from pathlib import Path

ZIP_RE = re.compile(
    r"^(COPS-(\d+))_Day(\d+)_(\d+)h-(\d+)h_(left|right)Wrist\.zip$", re.I
)


def main():
    ap = argparse.ArgumentParser(description="แตก zip ของ COPS เฉพาะส่วนที่เลือก")
    ap.add_argument("src", help="โฟลเดอร์ต้นทางที่มีไฟล์ .zip (เช่น data2_COS)")
    ap.add_argument("--dest", default="data/raw/cops", help="โฟลเดอร์ปลายทาง")
    ap.add_argument("--subjects", type=int, nargs="+", default=[10, 12, 14])
    ap.add_argument("--days", type=int, nargs="+", default=[0, 1])
    ap.add_argument("--hour-from", type=int, default=13, help="ชั่วโมงเริ่มต้นต่ำสุด (13 = 13h-14h)")
    ap.add_argument("--hour-to", type=int, default=19, help="ชั่วโมงเริ่มต้นสูงสุด (19 = 19h-20h)")
    ap.add_argument("--wrists", nargs="+", default=["left"])
    ap.add_argument("--dry-run", action="store_true", help="แค่แสดงรายการ ไม่แตกไฟล์จริง")
    a = ap.parse_args()

    wrists = {w.lower() for w in a.wrists}
    found = picked = extracted = skipped = 0
    total_mb = 0.0

    for z in sorted(Path(a.src).expanduser().rglob("*.zip")):
        m = ZIP_RE.match(z.name)
        if not m:
            continue
        found += 1
        subj, day, h0, wrist = int(m[2]), int(m[3]), int(m[4]), m[6].lower()
        if (subj not in a.subjects or day not in a.days or wrist not in wrists
                or not (a.hour_from <= h0 <= a.hour_to)):
            continue
        picked += 1
        out_dir = Path(a.dest) / m[1]
        with zipfile.ZipFile(z) as zf:
            members = [n for n in zf.namelist()
                       if n.lower().endswith(".csv")
                       and not n.startswith("__MACOSX")
                       and not Path(n).name.startswith("._")]
            if not members:
                print(f"คำเตือน: ไม่พบ .csv ใน {z.name}")
                continue
            for n in members:
                out = out_dir / Path(n).name
                if out.exists():
                    skipped += 1
                    continue
                if a.dry_run:
                    print(f"[dry-run] จะแตก {z.name} -> {out}")
                    continue
                out_dir.mkdir(parents=True, exist_ok=True)
                with zf.open(n) as src, open(out, "wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                extracted += 1
                total_mb += out.stat().st_size / 1e6

    print(f"พบ zip ที่ชื่อตรงรูปแบบ {found} ไฟล์ | เข้าเงื่อนไข {picked} ไฟล์ | "
          f"แตกใหม่ {extracted} ไฟล์ ({total_mb:.1f} MB) | ข้ามเพราะมีอยู่แล้ว {skipped} ไฟล์")


if __name__ == "__main__":
    main()
