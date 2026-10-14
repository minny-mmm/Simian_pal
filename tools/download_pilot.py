"""โหลดข้อมูลตัวอย่าง (pilot) 10 คนสำหรับเขียนโค้ด clean: NHANES จาก CDC และ COPS จาก OSF ตามรายชื่อใน pilot_sample.csv

วิธีใช้:
    pip install pandas requests
    python download_pilot.py pilot_sample.csv --list-only      # ดูก่อนว่าจะโหลดไฟล์อะไร ขนาดเท่าไหร่ (ยังไม่โหลด)
    python download_pilot.py pilot_sample.csv --out data/pilot  # โหลดจริง
    python download_pilot.py pilot_sample.csv --out data/pilot --only nhanes   # โหลดชุดเดียว (nhanes หรือ cops)

ได้:
    data/pilot/nhanes/<SEQN>.tar.bz2      คนละ ~130 MB (CSV รายชั่วโมง 80 Hz ข้างใน)
    data/pilot/cops/<path เดิมบน OSF>      ทุกไฟล์ที่ชื่อมีรหัสคนนั้น (ทั้ง 2 ข้อมือ) คนละ ~750 MB
รันซ้ำได้: ไฟล์ที่โหลดครบแล้วจะข้าม ไฟล์ที่โหลดค้างจะเริ่มใหม่
"""
import argparse
import os
import re
import sys

import pandas as pd
import requests

NHANES_URL = "https://ftp.cdc.gov/pub/pax_h/{seqn}.tar.bz2"
OSF_NODE = "5xvwn"  # https://osf.io/5xvwn
OSF_API = "https://api.osf.io/v2"


def download(url, path, size=None):
    if os.path.exists(path) and (size is None or os.path.getsize(path) == size):
        print(f"  มีแล้ว {path}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(tmp, path)
    print(f"  โหลดแล้ว {path} ({os.path.getsize(path) / 1e6:.0f} MB)")


def pages(url):
    """อ่าน API ของ OSF ทีละหน้าจนหมด"""
    while url:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        j = r.json()
        yield from j["data"]
        url = j["links"].get("next")


def osf_files(node):
    """ไล่ทุกไฟล์ใน osfstorage ของ project และ component ย่อย: (path, ขนาด byte, ลิงก์โหลด)"""
    stack = [f"{OSF_API}/nodes/{node}/files/osfstorage/"]
    while stack:
        for item in pages(stack.pop()):
            a = item["attributes"]
            if a["kind"] == "folder":
                stack.append(item["relationships"]["files"]["links"]["related"]["href"])
            else:
                yield a["materialized_path"], a.get("size") or 0, item["links"]["download"]
    for child in pages(f"{OSF_API}/nodes/{node}/children/"):
        yield from osf_files(child["id"])


def id_pattern(pid):
    """COPS-2 ต้องไม่ไปจับ COPS-20 ถึง COPS-29"""
    return re.compile(re.escape(pid) + r"(?!\d)", re.IGNORECASE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pilot", help="pilot_sample.csv")
    ap.add_argument("--out", default="data/pilot")
    ap.add_argument("--list-only", action="store_true", help="แสดงรายการไฟล์และขนาด ยังไม่โหลด")
    ap.add_argument("--only", choices=["nhanes", "cops"], help="โหลดชุดเดียว")
    a = ap.parse_args()
    p = pd.read_csv(a.pilot, dtype={"id": str})

    if a.only != "cops":
        seqns = p.loc[p["dataset"] == "NHANES", "id"].tolist()
        print(f"NHANES {len(seqns)} คน (~{len(seqns) * 130} MB)")
        for s in seqns:
            url = NHANES_URL.format(seqn=s)
            if a.list_only:
                print("  " + url)
            else:
                download(url, os.path.join(a.out, "nhanes", f"{s}.tar.bz2"))

    if a.only != "nhanes":
        ids = p.loc[p["dataset"] == "COPS", "id"].tolist()
        pats = {i: id_pattern(i) for i in ids}
        print(f"COPS {len(ids)} คน: กำลังอ่านรายชื่อไฟล์บน OSF (อาจใช้เวลาสักพัก)")
        seen, hits = [], []
        for path, size, url in osf_files(OSF_NODE):
            seen.append(path)
            if any(pt.search(path) for pt in pats.values()):
                hits.append((path, size, url))
        missing = [i for i, pt in pats.items() if not any(pt.search(h[0]) for h in hits)]
        if missing:
            print(f"  ไม่พบไฟล์ของ {', '.join(missing)} ตัวอย่าง path ที่เจอบน OSF ({len(seen)} ไฟล์):")
            for path in seen[:30]:
                print("    " + path)
        if not hits:
            sys.exit("  ไม่พบไฟล์ของคนในรายชื่อเลย ส่งข้อความด้านบนให้ทีมดูว่า OSF ตั้งชื่อไฟล์แบบไหน")
        print(f"  เจอ {len(hits)} ไฟล์ รวม {sum(h[1] for h in hits) / 1e9:.1f} GB")
        for path, size, url in hits:
            if a.list_only:
                print(f"  {path} ({size / 1e6:.0f} MB)")
            else:
                download(url, os.path.join(a.out, "cops", path.lstrip("/")), size)


if __name__ == "__main__":
    main()
