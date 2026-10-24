"""ข้อมูลแพทย์: หมอที่ login (ช่อง "ผู้ประเมิน" ในฟอร์ม / หน้าตั้งค่า) + แปลงรหัสหมอเป็นชื่อ"""
from fastapi import APIRouter, Depends, HTTPException

from app.auth import current_doctor
from app.db import table, to_json

router = APIRouter(prefix="/api/doctors", tags=["doctors"])


def doctor_names(ids):
    """{รหัสหมอ: ชื่อ} สำหรับรหัสที่ส่งมา (รหัสที่ไม่มีในตาราง = None หน้าเว็บจะแสดงรหัสแทน)"""
    names = {}
    for i in set(filter(None, ids)):
        d = table("doctors").get_item(Key={"doctor_id": i}).get("Item")
        names[i] = d.get("name") if d else None
    return names


@router.get("/me")
def me(doctor_id: str = Depends(current_doctor)):
    d = table("doctors").get_item(Key={"doctor_id": doctor_id}).get("Item")
    if not d:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลแพทย์")
    return to_json(d)
