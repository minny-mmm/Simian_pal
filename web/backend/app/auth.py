"""ใครคือหมอที่เรียก API อยู่ (ทุก router ใช้ผ่าน Depends(current_doctor))

โหมด (เลือกจาก env อัตโนมัติ ดูได้ที่ GET /api/auth/config)
- cognito : ตั้ง COGNITO_USER_POOL_ID + COGNITO_CLIENT_ID แล้ว -> ทุก request ต้องมี
            "Authorization: Bearer <ID token จาก Cognito>"
            doctor_id มาจาก attribute custom:doctor_id ของบัญชีผู้ใช้ใน Cognito
- dev     : ไม่ได้ตั้ง Cognito และรันในเครื่อง -> ใช้หมอทดสอบ DR-001 ไม่ต้อง login (แบบเดิม)
- disabled: ไม่ได้ตั้ง Cognito แต่รันบน Lambda -> ปฏิเสธทุก request กันเผลอเปิด API ที่ไม่มี login
"""
import os
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

REGION = os.environ.get("COGNITO_REGION") or os.environ.get("AWS_REGION", "ap-southeast-1")
POOL_ID = os.environ.get("COGNITO_USER_POOL_ID")
CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID")
DOMAIN = os.environ.get("COGNITO_DOMAIN")   # เช่น https://pd-doctor.auth.ap-southeast-1.amazoncognito.com
ON_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))   # Lambda ตั้งตัวนี้ให้เองเสมอ
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
DEV_DOCTOR = "DR-001"

router = APIRouter(prefix="/api/auth", tags=["auth"])


def mode():
    if POOL_ID and CLIENT_ID:
        return "cognito"
    return "disabled" if ON_LAMBDA else "dev"


@lru_cache(maxsize=1)
def jwks_client():
    """กุญแจสาธารณะของ user pool (ดึงครั้งเดียวแล้ว cache) ใช้ตรวจลายเซ็น token"""
    import jwt   # import ตอนใช้: โหมด dev ไม่ต้องติดตั้ง PyJWT
    return jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", cache_keys=True)


def unauthorized(detail):
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def current_doctor(authorization: Optional[str] = Header(None)):
    m = mode()
    if m == "dev":
        return DEV_DOCTOR
    if m == "disabled":
        raise HTTPException(status_code=503, detail="ยังไม่ได้ตั้งค่า Cognito บนเซิร์ฟเวอร์")
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized("กรุณาเข้าสู่ระบบ")

    import jwt
    token = authorization[len("Bearer "):]
    try:
        key = jwks_client().get_signing_key_from_jwt(token).key
        # ตรวจลายเซ็น + หมดอายุ + ออกโดย user pool ของเรา + ออกให้ app client ของเรา (aud)
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=CLIENT_ID, issuer=ISSUER,
                            options={"require": ["exp", "iat", "aud", "iss", "token_use"]})
    except jwt.PyJWTError:
        raise unauthorized("เซสชันหมดอายุหรือไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่")
    if claims.get("token_use") != "id":   # access token ไม่มี custom:doctor_id
        raise unauthorized("ต้องใช้ ID token")

    doctor_id = claims.get("custom:doctor_id")
    if not doctor_id:
        raise HTTPException(status_code=403, detail="บัญชีนี้ยังไม่ได้ผูกกับรหัสแพทย์ (custom:doctor_id)")
    return doctor_id


@router.get("/config")
def auth_config():
    """หน้าเว็บถามก่อนว่าต้อง login ไหม และไป login ที่ไหน (ค่าพวกนี้เปิดเผยได้ ไม่ใช่ความลับ)"""
    return {"mode": mode(), "region": REGION, "client_id": CLIENT_ID if mode() == "cognito" else None,
            "domain": DOMAIN if mode() == "cognito" else None}
