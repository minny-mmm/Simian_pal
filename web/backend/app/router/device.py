"""หน้า 4 ข้อมูลจากอุปกรณ์"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app import metrics
from app.auth import current_doctor
from app.db import table, to_json
from app.router.name_patient import daily_rows, get_own_patient   # ใช้ของที่เขียนไว้ในขั้น 6 ซ้ำ

router = APIRouter(prefix="/api/patients", tags=["device"])

# 3 บรรทัดข้างวงกลม: field ในตาราง -> ข้อความที่แสดง
QUALITY = {
    "wear_hours": "สวมใส่และมีสัญญาณ",
    "no_signal_hours": "ช่วงเวลาที่ไม่มีสัญญาณ",
    "low_quality_hours": "สัญญาณมีคุณภาพต่ำ",
}


@router.get("/{patient_id}/device")
def get_device(patient_id: str, doctor_id: str = Depends(current_doctor)):
    get_own_patient(patient_id, doctor_id)
    d = table("devices").get_item(Key={"patient_id": patient_id}).get("Item")
    if not d:
        raise HTTPException(status_code=404, detail="ผู้ป่วยนี้ยังไม่ได้ผูกอุปกรณ์")
    return to_json(d)


@router.get("/{patient_id}/data-quality")
def get_data_quality(patient_id: str,
                     start: str = Query(None, alias="from"),
                     end: str = Query(None, alias="to"),
                     doctor_id: str = Depends(current_doctor)):
    get_own_patient(patient_id, doctor_id)
    rows = daily_rows(patient_id)
    if start and end:
        rows = [r for r in rows if start <= r["date"] <= end]
    else:
        rows = metrics.window(rows, 7)   # ไม่ได้เลือกช่วง = 7 วันล่าสุดที่มีข้อมูล

    days = len(rows)
    # r.get(k) or 0 : วันที่ไม่มีค่า (None) ให้นับเป็น 0 จะได้บวกกันได้
    totals = {k: round(sum(r.get(k) or 0 for r in rows), 1) for k in QUALITY}
    all_hours = sum(totals.values())

    parts = [{"key": k, "label": label, "hours": totals[k],
              "per_day": round(totals[k] / days, 1) if days else 0,
              "pct": round(totals[k] / all_hours * 100) if all_hours else None}
             for k, label in QUALITY.items()]

    return {
        "from": min(r["date"] for r in rows) if rows else start,
        "to": max(r["date"] for r in rows) if rows else end,
        "days": days,
        "has_data": all_hours > 0,   # False = หน้าเว็บโชว์ "ยังไม่มีข้อมูลชั่วโมงสวมใส่"
        "parts": parts,
    }