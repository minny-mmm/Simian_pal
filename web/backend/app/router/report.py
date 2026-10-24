"""หน้า 6 รายงานและวิเคราะห์ (คำนวณทั้งกลุ่มผู้ป่วยของหมอ)"""
from datetime import date, timedelta
from statistics import mean
from typing import Literal

from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, Depends, Query

from app import metrics
from app.auth import current_doctor
from app.db import query_all, table, to_json
from app.router.name_patient import daily_rows, get_own_patient

router = APIRouter(prefix="/api/reports", tags=["reports"])

MetricName = Literal["tremor", "cadence", "activity"]


def my_patients(doctor_id):
    return query_all(table("patients"), IndexName="by_doctor",
                     KeyConditionExpression=Key("doctor_id").eq(doctor_id))


def date_range(start, end, all_rows=(), days=30):
    """ไม่ได้เลือกช่วง = 30 วันล่าสุด นับถึงวันล่าสุดที่มีข้อมูลของผู้ป่วยกลุ่มนี้ (ไม่มีข้อมูลเลย = ถึงวันนี้)"""
    latest = max((r["date"] for rows in all_rows for r in rows), default=None)
    end = end or latest or date.today().isoformat()
    start = start or (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
    return start, end


def patient_mean(rows, key, start, end):
    """ค่าเฉลี่ยของผู้ป่วย 1 คนในช่วงวันที่ (หน่วยที่แสดงบนเว็บ) ไม่มีข้อมูล = None"""
    v = metrics.values([r for r in rows if start <= r["date"] <= end], key)
    return mean(v) if v else None


def group_mean(all_rows, key, start, end):
    """เฉลี่ยของ "ค่าเฉลี่ยรายคน" -> ทุกคนมีน้ำหนักเท่ากัน ไม่ว่าจะใส่นาฬิกามากี่วัน"""
    per_person = [m for m in (patient_mean(rows, key, start, end) for rows in all_rows)
                  if m is not None]
    return (round(mean(per_person), 2) if per_person else None), len(per_person)


@router.get("/overview")
def overview(start: str = Query(None, alias="from"), end: str = Query(None, alias="to"),
             doctor_id: str = Depends(current_doctor)):
    all_rows = [daily_rows(p["patient_id"]) for p in my_patients(doctor_id)]  # ดึงครั้งเดียว ใช้ทุกตัวชี้วัด
    start, end = date_range(start, end, all_rows)
    # ช่วงก่อนหน้า ยาวเท่ากัน ต่อกันพอดี เช่น เลือก 1–31 ต.ค. -> เทียบกับ 31 วันก่อนหน้า
    n_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    prev_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    prev_start = (date.fromisoformat(start) - timedelta(days=n_days)).isoformat()

    out = {}
    for key, m in metrics.METRICS.items():
        now, n = group_mean(all_rows, key, start, end)
        before, _ = group_mean(all_rows, key, prev_start, prev_end)
        out[key] = {"label": m["label"], "unit": m["unit"], "value": now,
                    "change_pct": metrics.pct_change(now, before),
                    "n_patients": n}
    return {"from": start, "to": end, "compare_from": prev_start, "compare_to": prev_end,
            "metrics": out}


@router.get("/percentiles")
def percentiles(patient_id: str,
                cohort: Literal["COPS", "NHANES"] = "COPS",
                days: int = Query(7, ge=1, le=90),
                doctor_id: str = Depends(current_doctor)):
    get_own_patient(patient_id, doctor_id)
    rows = metrics.window(daily_rows(patient_id), days)
    out = {}
    for key, m in metrics.METRICS.items():
        ref = to_json(table("reference").get_item(
            Key={"metric": m["field"], "cohort": cohort}).get("Item")) or {}

        def scale(v):   # ค่าในตาราง reference เป็นหน่วยดิบของโมเดล ต้องแปลงหน่วยเหมือนค่าผู้ป่วย
            return None if v is None else round(v * m["scale"], 3)

        v = metrics.values(rows, key)
        value = round(mean(v), 3) if v else None
        p50 = scale(ref.get("median"))
        out[key] = {"label": m["label"], "unit": m["unit"], "value": value,
                    "p25": scale(ref.get("p25")), "p50": p50, "p75": scale(ref.get("p75")),
                    "vs_median_pct": metrics.pct_change(value, p50),   # ห่างจากค่ากลางกลุ่มกี่ %
                    "n_people": ref.get("n_people")}
    return {"patient_id": patient_id, "cohort": cohort, "days": days, "metrics": out}


@router.get("/scatter")
def scatter(x: MetricName = "tremor", y: MetricName = "cadence",
            start: str = Query(None, alias="from"), end: str = Query(None, alias="to"),
            doctor_id: str = Depends(current_doctor)):
    patients = my_patients(doctor_id)
    all_rows = [daily_rows(p["patient_id"]) for p in patients]
    start, end = date_range(start, end, all_rows)
    points = []
    for p, rows in zip(patients, all_rows):
        px, py = patient_mean(rows, x, start, end), patient_mean(rows, y, start, end)
        if px is not None and py is not None:   # ต้องมีครบทั้ง 2 แกนถึงจะวาดจุดได้
            points.append({"patient_id": p["patient_id"], "hn": p.get("hn"),
                           "x": round(px, 3), "y": round(py, 3)})
    axis = lambda k: {"metric": k, "label": metrics.METRICS[k]["label"],
                      "unit": metrics.METRICS[k]["unit"]}
    return {"from": start, "to": end, "x": axis(x), "y": axis(y), "points": points}