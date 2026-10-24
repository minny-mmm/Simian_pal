"""หน้า 1 Dashboard: ตัวเลข 4 การ์ดบนสุด"""
from datetime import date, timedelta

from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, Query

from app.db import query_all, table, to_json
from fastapi import APIRouter, Depends, Query   # เพิ่ม Depends
from app.auth import current_doctor              # เพิ่มบรรทัดนี้

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

WEAR_HOURS = 8        # เกณฑ์ "สวมใส่อุปกรณ์" ชม./วัน (เราวัดได้สูงสุด 12)


@router.get("/summary")
def summary(start: str = Query(None, alias="from"), end: str = Query(None, alias="to"), doctor_id: str = Depends(current_doctor)):
    end = end or date.today().isoformat()
    start = start or (date.fromisoformat(end) - timedelta(days=29)).isoformat()
    three_months_ago = (date.fromisoformat(end) - timedelta(days=90)).isoformat()

    # การ์ด 1: ผู้ป่วยทั้งหมดของหมอคนนี้ (ใช้ index by_doctor)
    patients = query_all(table("patients"), IndexName="by_doctor",
                         KeyConditionExpression=Key("doctor_id").eq(doctor_id))

    wearing = assessed = 0
    for p in patients:
        pid = p["patient_id"]
        # การ์ด 2: ชม.ใส่เฉลี่ยในช่วงวันที่ ≥ เกณฑ์
        days = query_all(table("daily_metrics"),
                         KeyConditionExpression=Key("patient_id").eq(pid)
                         & Key("date").between(start, end))
        hours = [d["wear_hours"] for d in days if "wear_hours" in d]
        if hours and sum(hours) / len(hours) >= WEAR_HOURS:
            wearing += 1
        # การ์ด 3: มีผลประเมินภายใน 3 เดือน
        tests = query_all(table("assessments"), KeyConditionExpression=Key("patient_id").eq(pid))
        if any(t["date"] >= three_months_ago for t in tests):
            assessed += 1

    # การ์ด 4: แจ้งเตือนในช่วงวันที่
    alerts = query_all(table("alerts"), IndexName="by_doctor",
                       KeyConditionExpression=Key("doctor_id").eq(doctor_id)
                       & Key("created_at").between(start, end + "T23:59:59"))

    total = len(patients)
    return to_json({
        "from": start, "to": end,
        "total_patients": total,
        "wearing": wearing,
        "wearing_pct": round(wearing / total * 100) if total else 0,
        "assessed_3m": assessed,
        "alerts": len(alerts),
        "wear_hours_threshold": WEAR_HOURS,
    })