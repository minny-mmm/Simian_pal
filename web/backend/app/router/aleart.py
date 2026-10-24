"""หน้า 7 Alerts + กล่อง 'การแจ้งเตือนล่าสุด' ในหน้า 1"""
from datetime import datetime, timezone
from typing import Literal, Optional

from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import current_doctor
from app.db import query_all, table, to_json

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
def list_alerts(limit: int = 20,
                alert_type: Optional[str] = Query(None, alias="type"),
                status: Optional[Literal["new", "seen"]] = None,          # ← เพิ่ม
                doctor_id: str = Depends(current_doctor)):
    # ดึงแจ้งเตือนทั้งหมดของหมอ เรียงใหม่ → เก่า (ScanIndexForward=False)
    items = query_all(table("alerts"), IndexName="by_doctor",
                      KeyConditionExpression=Key("doctor_id").eq(doctor_id),
                      ScanIndexForward=False)
    if alert_type:
        items = [a for a in items if a.get("type") == alert_type]
    if status:                                                          # ← เพิ่ม
        items = [a for a in items if a.get("status") == status]
    items = items[:limit]

    # เติม HN + ชื่อผู้ป่วย หน้าเว็บจะได้แสดงในตารางได้เลย
    patients = {p["patient_id"]: p for p in query_all(
        table("patients"), IndexName="by_doctor",
        KeyConditionExpression=Key("doctor_id").eq(doctor_id))}
    for a in items:
        p = patients.get(a["patient_id"], {})
        a["hn"] = p.get("hn")
        a["display_name"] = p.get("display_name")
    return to_json(items)

class AlertUpdate(BaseModel):
    status: Literal["new", "seen"] = "seen"


@router.patch("/{patient_id}/{created_at}")
def update_alert(patient_id: str, created_at: str, body: AlertUpdate,
                 doctor_id: str = Depends(current_doctor)):
    key = {"patient_id": patient_id, "created_at": created_at}   # key ของตาราง alerts มี 2 ส่วน

    # ด่านเช็กสิทธิ์: ต้องเป็นแจ้งเตือนของหมอคนนี้เท่านั้น
    alert = table("alerts").get_item(Key=key).get("Item")
    if not alert or alert.get("doctor_id") != doctor_id:
        raise HTTPException(status_code=404, detail="ไม่พบการแจ้งเตือน")

    names = {"#st": "status"}   # "status" เป็นคำสงวนของ DynamoDB ใช้ตรง ๆ ใน expression ไม่ได้
    if body.status == "seen":
        expr = "SET #st = :st, seen_by = :by, seen_at = :at"
        values = {":st": "seen", ":by": doctor_id,
                  ":at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:   # กดยกเลิก -> กลับเป็นยังไม่ได้ดู และลบว่าใครดู/ดูเมื่อไหร่ออก
        expr = "SET #st = :st REMOVE seen_by, seen_at"
        values = {":st": "new"}

    r = table("alerts").update_item(Key=key, UpdateExpression=expr,
                                    ExpressionAttributeNames=names,
                                    ExpressionAttributeValues=values,
                                    ReturnValues="ALL_NEW")   # ส่งแถวหลังแก้กลับมาเลย
    return to_json(r["Attributes"])