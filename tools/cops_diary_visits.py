"""โหลด symptom diary ของ COPS ทุกคนจาก OSF แล้วสรุปว่าแต่ละคนอยู่โรงพยาบาลกี่วัน อยู่บ้านกี่วัน

วิธีใช้ (รันจากโฟลเดอร์หลักของ pd-pipeline):
    pip install pandas requests
    python tools/cops_diary_visits.py
    python tools/cops_diary_visits.py --no-save   # อ่านจากเว็บตรงๆ ไม่เก็บ diary ลงเครื่อง

ได้:
    data/raw/cops/COPS-<n>/COPS-<n>_symptomdiary.csv   ไฟล์ละ ~45 KB (ข้ามคนที่มีแล้ว)
    reports/cops_visit_summary.csv                     1 แถว / 1 คน
ไม่โหลดโฟลเดอร์ Accelerometry จึงเร็ว (รวมไม่ถึง 5 MB)
"""
import io
import os
import re
import sys
import zipfile

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OSF_NODE = "5xvwn"
OSF_API = "https://api.osf.io/v2"
RAW = "data/raw/cops"
ID_RE = re.compile(r"COPS[-_ ]?0*(\d+)(?!\d)", re.I)

# ลองใหม่อัตโนมัติเมื่อ OSF ตอบช้าหรือ error ชั่วคราว
S = requests.Session()
S.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])))


def pages(url):
    while url:
        r = S.get(url, params={"page[size]": 100} if "page" not in url else None, timeout=60)
        r.raise_for_status()
        j = r.json()
        yield from j["data"]
        url = j["links"].get("next")


class HttpFile(io.RawIOBase):
    """เปิดไฟล์บนเว็บแบบอ่านทีละช่วง (HTTP Range) ให้ zipfile อ่านเฉพาะส่วนที่ต้องใช้ ไม่ต้องโหลดทั้งก้อน"""

    def __init__(self, url, size):
        r = S.get(url, headers={"Range": "bytes=0-0"}, timeout=60, allow_redirects=True)
        if r.status_code != 206:
            raise RuntimeError("เซิร์ฟเวอร์ไม่รองรับการอ่านทีละช่วง")
        self.url, self.size, self.pos = r.url, size, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = [off, self.pos + off, self.size + off][whence]
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b""
        end = min(self.pos + n, self.size) - 1
        r = S.get(self.url, headers={"Range": f"bytes={self.pos}-{end}"}, timeout=120)
        r.raise_for_status()
        self.pos += len(r.content)
        return r.content

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


def find_diaries(node, skipped=None):
    """ไล่โฟลเดอร์บน OSF หาไฟล์ diary โดยข้ามโฟลเดอร์ Accelerometry (ไฟล์ใหญ่และเยอะ)
    คืน (รหัสคน, ชื่อไฟล์, ฟังก์ชันที่คืนเนื้อไฟล์ diary เป็น bytes)
    - ไฟล์ diary ตรงๆ: อยู่ในโฟลเดอร์หรือชื่อมีคำว่า diary และชื่อไฟล์มีรหัส COPS
    - zip ของแต่ละคน (เช่น COPS-2.zip): เปิดอ่านเฉพาะไฟล์ *symptomdiary.csv ข้างใน ไม่โหลด zip ทั้งก้อน"""
    skipped = [] if skipped is None else skipped
    stack = [f"{OSF_API}/nodes/{node}/files/osfstorage/"]
    while stack:
        n_files = 0
        for item in pages(stack.pop()):
            a = item["attributes"]
            path = a.get("materialized_path") or a["name"]
            if a["kind"] == "folder":
                if a["name"].lower() != "accelerometry":
                    print(f"  เปิดโฟลเดอร์ {path}", flush=True)
                    stack.append(item["relationships"]["files"]["links"]["related"]["href"])
                continue
            n_files += 1
            if n_files <= 5:
                print(f"    ไฟล์ {path} ({(a.get('size') or 0) / 1e6:.1f} MB)", flush=True)
            name, url, size = a["name"], item["links"]["download"], a.get("size") or 0
            m = ID_RE.search(name)
            if m and "diary" in path.lower() and name.lower().endswith((".csv", ".zip")):
                yield f"COPS-{int(m[1])}", name, (lambda url=url: S.get(url, timeout=60).content)
            elif m and name.lower().endswith(".zip") and "diary" not in path.lower():
                sid = f"COPS-{int(m[1])}"
                yield sid, name, (lambda url=url, size=size, sid=sid: diary_in_zip(url, size, sid))
            elif "diary" in path.lower():
                skipped.append(path)
        if n_files > 5:
            print(f"    ... รวม {n_files} ไฟล์", flush=True)
    for child in pages(f"{OSF_API}/nodes/{node}/children/"):
        yield from find_diaries(child["id"], skipped)


def diary_in_zip(url, size, sid):
    with zipfile.ZipFile(io.BufferedReader(HttpFile(url, size), 1 << 16)) as z:
        hits = [n for n in z.namelist() if n.lower().endswith("symptomdiary.csv")]
        if not hits:
            return None
        return z.read(hits[0])


def read_diary(content, name):
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            content = z.read(csvs[0])
    return pd.read_csv(io.BytesIO(content), sep=None, engine="python")


def report_skipped(n, skipped):
    print(f"พบ diary บน OSF {n} ไฟล์", flush=True)
    if skipped:
        print("ไฟล์ในโฟลเดอร์ diary ที่ไม่ได้ใช้ (ถ้า diary อยู่ในนี้ ส่งรายชื่อนี้ให้ Claude):")
        for p in skipped[:30]:
            print("    " + p)
    if n == 0:
        sys.exit("ไม่พบไฟล์ diary เลย")


def download_all():
    print("กำลังไล่หาไฟล์ diary บน OSF (อาจใช้หลายนาที ถ้ามีข้อความขึ้นเรื่อยๆ แปลว่ายังทำงานอยู่)", flush=True)
    n, skipped = 0, []
    for sid, name, fetch in find_diaries(OSF_NODE, skipped):
        path = os.path.join(RAW, sid, f"{sid}_symptomdiary.csv")
        if os.path.exists(path):
            n += 1
            print(f"  [{n}] มีแล้ว {sid}", flush=True)
            continue
        content = fetch()
        if content is None:
            print(f"  ไม่มี diary ใน {name}", flush=True)
            continue
        n += 1
        os.makedirs(os.path.dirname(path), exist_ok=True)
        read_diary(content, "x.csv").to_csv(path, sep=";", index=False)
        print(f"  [{n}] โหลด {path}", flush=True)
    report_skipped(n, skipped)


def diaries_on_disk():
    for sid in os.listdir(RAW):
        path = os.path.join(RAW, sid, f"{sid}_symptomdiary.csv")
        if os.path.exists(path):
            yield sid, pd.read_csv(path, sep=";")


def diaries_from_web():
    """อ่าน diary จาก OSF เข้าหน่วยความจำตรงๆ ไม่เขียนไฟล์ลงเครื่อง"""
    print("กำลังไล่หาไฟล์ diary บน OSF (อาจใช้หลายนาที ถ้ามีข้อความขึ้นเรื่อยๆ แปลว่ายังทำงานอยู่)", flush=True)
    n, skipped = 0, []
    seen = set()
    for sid, name, fetch in find_diaries(OSF_NODE, skipped):
        if sid in seen:
            continue
        content = fetch()
        if content is None:
            print(f"  ไม่มี diary ใน {name}", flush=True)
            continue
        seen.add(sid)
        n += 1
        print(f"  [{n}] อ่าน {sid} ({name})", flush=True)
        yield sid, read_diary(content, "x.csv")
    report_skipped(n, skipped)


def summarize(diaries):
    rows = []
    for sid, d in diaries:
        worn = d["WearableDataAvailability"].eq("Wearable Worn")
        awake_worn = worn & d["Kinesia"].ne("Sleep")
        # วันที่ใส่นาฬิกาตอนตื่น >= 10 ชม. นับเป็นวันใช้ได้ (ตามเกณฑ์เดียวกับ NHANES)
        day = d.assign(aw=awake_worn).groupby("Day", sort=False).agg(
            aw_h=("aw", "sum"), visit=("Visit", lambda s: s[worn.loc[s.index]].mode().iat[0] if worn.loc[s.index].any() else "none"))
        valid = day[day.aw_h >= 10]
        rows.append(dict(
            subject_id=sid,
            valid_days=len(valid),
            inpatient_days=int((valid.visit == "inpatient").sum()),
            mixed_days=int((valid.visit == "mixed").sum()),
            outpatient_days=int((valid.visit == "outpatient").sum()),
            worn_hours=int(worn.sum()),
            inpatient_worn_hours=int((worn & d["Visit"].eq("inpatient")).sum()),
            outpatient_worn_hours=int((worn & d["Visit"].eq("outpatient")).sum()),
        ))
    s = pd.DataFrame(rows)
    # ใช้ในโมเดลหลัก = มีวันอยู่บ้านที่ใช้ได้ >= 3 วัน (ได้ 49 คน)
    s["use_for_main_model"] = (s.outpatient_days >= 3).map({True: "yes", False: "no"})
    s = s.sort_values("subject_id", key=lambda c: c.str.extract(r"(\d+)$")[0].astype(int))
    os.makedirs("reports", exist_ok=True)
    s.to_csv("reports/cops_visit_summary.csv", index=False)
    print(s.to_string(index=False))
    print()
    print(f"คน: {len(s)}")
    print(f"อยู่โรงพยาบาลทุกวัน (ไม่มีวันอยู่บ้านเลย): {(s.outpatient_days == 0).sum()} คน")
    print(f"มีวันอยู่บ้านที่ใช้ได้ >= 3 วัน: {(s.outpatient_days >= 3).sum()} คน")
    print(f"วันใช้ได้รวม: {s.valid_days.sum()} = โรงพยาบาล {s.inpatient_days.sum()}"
          f" / ผสม {s.mixed_days.sum()} / บ้าน {s.outpatient_days.sum()}")


if __name__ == "__main__":
    if "--no-save" in sys.argv:
        summarize(diaries_from_web())
    else:
        if "--summary-only" not in sys.argv:
            download_all()
        summarize(diaries_on_disk())
