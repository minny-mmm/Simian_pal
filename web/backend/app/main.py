"""จุดเริ่มของ API: สร้างแอป + ต่อ router ทุกหน้า"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from app import auth
from app.router import ai, aleart, assessment, dashboard, device, doctor, name_patient, report, appointment, me



app = FastAPI(title="pakinson API")
app.include_router(auth.router)          # /api/auth/config (ไม่ต้อง login)
app.include_router(dashboard.router)
app.include_router(aleart.router)
app.include_router(name_patient.router)
app.include_router(device.router)
app.include_router(assessment.router)
app.include_router(doctor.router)
app.include_router(report.router)
app.include_router(appointment.router)
app.include_router(me.router)
app.include_router(ai.router)            # ผล AI จากเดโมของทีม (app/reference.py)

# อนุญาตหลายโดเมนได้ คั่นด้วย , เช่น "http://localhost:5173,https://main.xxxx.amplifyapp.com"
ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,          # ← เปลี่ยนจาก ["http://localhost:5173"]
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",   # เว็บที่เปิดจากเครื่องตัวเอง (localhost ทุก port เช่น 5500)
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"ok": True}

handler = Mangum(app)  # ใช้ตอนขึ้น Lambda (ตอนรันในเครื่องไม่ได้ใช้)