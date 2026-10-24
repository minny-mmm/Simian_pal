"""คำนวณค่าที่หน้าเว็บใช้จากค่ารายวัน (ไม่เก็บลง DB)"""
from datetime import date, timedelta
from statistics import mean

# ตัวชี้วัดที่โมเดลส่งมา -> หน่วยที่แสดงบนเว็บ
# model_metric = ชื่อเดียวกันในเดโม AI ของทีม (หน้ารายงาน / Analysis JSON) ค่าเป็นหน่วยดิบ ก่อนคูณ scale
# ค่าหลายวัน = ถ่วงน้ำหนักด้วยจำนวน window ตามสูตรรายวันของโมเดล (aggregate_daily):
#   weight = ตัวถ่วงของค่าเฉลี่ย, หรือ ratio = (ตัวตั้ง, ตัวหาร) สำหรับสัดส่วน
METRICS = {
    "tremor": {"field": "tremor_rest_fraction", "scale": 100, "unit": "%", "label": "Rest Tremor",
               "model_metric": "rest_tremor_window_fraction", "ratio": ("n_tremor", "n_rest")},
    "cadence": {"field": "cadence_steps_s", "scale": 60, "unit": "ก้าว/นาที", "label": "Walking Cadence",
                "model_metric": "walking_cadence_hourly_median_mean_hz", "weight": "n_walk"},
    "activity": {"field": "enmo_g", "scale": 1000, "unit": "mg (ENMO)", "label": "Activity Level",  # g ค่าเล็กมาก ปัด 2 ตำแหน่งแล้วเหลือ 0.02 ทุกคน
                 "model_metric": "enmo_hourly_median_mean_g", "weight": "n_windows"},
}


def window(rows, days, end=None):
    """แถวของ N วันล่าสุด นับจากวันล่าสุดที่มีข้อมูล (หรือ end)"""
    if not rows:
        return []
    end = end or max(r["date"] for r in rows)
    start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
    return [r for r in rows if start <= r["date"] <= end]


def values(rows, key):
    m = METRICS[key]
    return [r[m["field"]] * m["scale"] for r in rows if r.get(m["field"]) is not None]


def pooled(rows, key):
    """ค่ารวมหลายวัน (หน่วยที่แสดง) เฉพาะวันที่มีค่า: tremor = sum(n_tremor)/sum(n_rest), อื่นๆ ถ่วงด้วยจำนวน window
    แถวเก่าที่ไม่มีจำนวน window -> ค่าเฉลี่ยธรรมดา"""
    m = METRICS[key]
    have = [r for r in rows if r.get(m["field"]) is not None]
    if not have:
        return None
    if "ratio" in m and all(r.get(c) is not None for r in have for c in m["ratio"]):
        num, den = (sum(r[c] for r in have) for c in m["ratio"])
        if den:
            return num / den * m["scale"]
    w = m.get("weight")
    if w and all(r.get(w) for r in have):
        return sum(r[m["field"]] * r[w] for r in have) / sum(r[w] for r in have) * m["scale"]
    return mean(values(have, key))


def latest(rows, key):
    """วันล่าสุดที่มีค่า (หน่วยที่แสดง) ใช้เป็นบรรทัดรองของการ์ด"""
    m = METRICS[key]
    have = sorted((r for r in rows if r.get(m["field"]) is not None), key=lambda r: r["date"])
    return {"date": have[-1]["date"], "value": round(have[-1][m["field"]] * m["scale"], 2)} if have else None


def pct_change(new, old):
    if new is None or old in (None, 0):
        return None
    return round((new - old) / old * 100, 1)


def summary(rows, days=7, end=None):
    """การ์ดสรุป: ค่ารวม N วันล่าสุด (หรือ N วันที่จบที่ end) แบบถ่วงน้ำหนัก (pooled) เทียบกับ N วันก่อนหน้า
    + ค่าวันล่าสุดในช่วง (ค่ารายวันแกว่งมาก จึงเป็นแค่บรรทัดรอง)"""
    cur = window(rows, days, end)
    if not cur:
        return {}
    prev_end = (date.fromisoformat(min(r["date"] for r in cur)) - timedelta(days=1)).isoformat()
    prev = window(rows, days, prev_end)
    out = {}
    for key, m in METRICS.items():
        now, before = pooled(cur, key), pooled(prev, key)
        now = None if now is None else round(now, 2)
        out[key] = {"label": m["label"], "unit": m["unit"], "model_metric": m["model_metric"], "value": now,
                    "change_pct": pct_change(now, before),
                    "valid_days": len(values(cur, key)), "latest": latest(cur, key)}
    return out


def series(rows, key, days):
    """จุดบนกราฟรายวัน + ค่าเฉลี่ย/สูงสุด/ต่ำสุด"""
    m = METRICS[key]
    cur = window(rows, days)
    pts = [{"date": r["date"],
            "value": None if r.get(m["field"]) is None else round(r[m["field"]] * m["scale"], 3),
            "qc": r.get(m["field"].split("_")[0] + "_qc")}
           for r in sorted(cur, key=lambda r: r["date"])]
    vals = [p["value"] for p in pts if p["value"] is not None]
    # ค่าเฉลี่ยใช้สูตรเดียวกับการ์ดสรุป (ถ่วงน้ำหนักด้วยจำนวน window) ตัวเลขสองที่จะได้ตรงกัน
    stats = {"mean": round(pooled(cur, key), 3), "max": max(vals), "min": min(vals)} if vals else {}
    return {"metric": key, "label": m["label"], "unit": m["unit"], "points": pts, **stats}