"""หน้า 9 ตั้งค่า + ข้อมูลหมอที่ login (ใช้ทุกหน้า)"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import current_doctor
from app.db import table, to_json

router = APIRouter(prefix="/api/me", tags=["me"])


class Settings(BaseModel):
    """ค่าตั้งค่าทั้งหมด พร้อมค่าเริ่มต้น (หมอที่ยังไม่เคยตั้งค่าจะได้ค่าพวกนี้)"""
    theme: Literal["light", "dark", "auto"] = "light"
    lang: Literal["th", "en"] = "th"
    date_format: Literal["short", "full"] = "short"   # short = 22 ต.ค. 2569, full = 22 ตุลาคม 2569
    notify_alerts: bool = True


def get_doctor(doctor_id):
    d = table("doctors").get_item(Key={"doctor_id": doctor_id}).get("Item")
    if not d:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลแพทย์")
    return to_json(d)


@router.get("")
def me(doctor_id: str = Depends(current_doctor)):
    d = get_doctor(doctor_id)
    return {k: d.get(k) for k in ("doctor_id", "name", "position", "hospital")}   # ส่งเฉพาะที่หน้าเว็บใช้


@router.get("/settings")
def get_settings(doctor_id: str = Depends(current_doctor)):
    saved = get_doctor(doctor_id).get("settings") or {}
    return Settings(**saved)     # ช่องไหนไม่เคยบันทึก = ได้ค่าเริ่มต้นจาก class


@router.put("/settings")
def put_settings(body: Settings, doctor_id: str = Depends(current_doctor)):
    get_doctor(doctor_id)        # มีหมอคนนี้จริงก่อน ค่อยเขียน
    table("doctors").update_item(
        Key={"doctor_id": doctor_id},
        UpdateExpression="SET settings = :s",
        ExpressionAttributeValues={":s": body.model_dump()})
    return body