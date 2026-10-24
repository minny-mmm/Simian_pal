"""ต่อเดโม AI ของทีม (pd-reference-demo-v1): ผลวิเคราะห์ + คำอธิบายจาก LLM ของผู้ป่วยที่มี ai_subject

รหัสเข้าเดโมไม่อยู่ในโค้ด/ไฟล์/env ของ Lambda — อ่านจาก Secrets Manager ตอนใช้ครั้งแรกแล้วเก็บในหน่วยความจำ
(role ของ Lambda มีสิทธิ์ GetSecretValue เฉพาะ secret นี้ ดู deploy/deploy_lambda.py)
- REFERENCE_API_BASE_URL : URL ของ API เดโม (deploy_lambda.py หาให้จาก API Gateway ชื่อ pd-reference-demo-v1)
- REFERENCE_SECRET_ID    : ARN ของ secret pd-reference-demo-v1-login
- รันในเครื่อง: ส่งรหัสผ่าน env REFERENCE_DEMO_TOKEN ตอนรันเท่านั้น ห้ามเขียนลงไฟล์
ห้ามส่งรหัสกลับไปให้เบราว์เซอร์ และห้าม log

ข้อจำกัดของเดโม: 100 request/วัน รวมทุกคน, LLM 20 ครั้ง/วัน (รีเซ็ต 07:00 น.), เรียกครั้งแรกรอ ~15 วินาที
จึงเก็บผลที่ได้ไว้ในหน่วยความจำ (ข้อมูลเดโมตรึงไว้ ไม่เปลี่ยน) และไม่ retry เอง
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

from fastapi import HTTPException

BASE_URL = (os.environ.get("REFERENCE_API_BASE_URL") or "").rstrip("/")
SECRET_ID = os.environ.get("REFERENCE_SECRET_ID")
REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
TIMEOUT = 25            # วินาที: API Gateway ของเราตัดที่ 30
CACHE_SECONDS = 3600    # เก็บผลไว้ 1 ชม. (เพื่อนออก release ใหม่ก็ได้ค่าใหม่ภายในชั่วโมง)

# error จากเดโม -> (status ที่ตอบหน้าเว็บ, ข้อความ)
ERRORS = {
    400: (502, "คำขอไม่ตรงกับเดโม AI (แจ้งผู้ดูแล)"),
    401: (502, "รหัสเข้าเดโม AI ใช้ไม่ได้ (แจ้งผู้ดูแล)"),
    404: (404, "ไม่มีผลวิเคราะห์ AI ของวันนี้"),
    410: (410, "เดโม AI หมดอายุแล้ว"),
    429: (429, "ใช้โควตา AI ของวันนี้ครบแล้ว (เริ่มนับใหม่ 07:00 น.)"),
}
UNAVAILABLE = (503, "บริการ AI ไม่พร้อมใช้งาน ลองใหม่ภายหลัง")

_cache = {}   # (path, query) -> (เวลาที่ได้, JSON)


def enabled():
    return bool(BASE_URL and (SECRET_ID or os.environ.get("REFERENCE_DEMO_TOKEN")))


@lru_cache(maxsize=1)
def demo_token():
    token = os.environ.get("REFERENCE_DEMO_TOKEN")
    if not token:
        import boto3
        token = boto3.client("secretsmanager", region_name=REGION).get_secret_value(SecretId=SECRET_ID)["SecretString"]
    return token.strip()


def fetch(url, token):
    """GET ครั้งเดียว คืน (status, JSON) — แยกออกมาให้ test แทนได้"""
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except ValueError:
            body = {}
        return e.code, body


def get(path, **query):
    """เรียก GET <เดโม>/api/<path> ได้ JSON หรือ HTTPException ข้อความไทย (ไม่มีรายละเอียดรหัส/URL)"""
    if not enabled():
        raise HTTPException(status_code=503, detail="ยังไม่ได้ตั้งค่าเดโม AI")
    key = (path, tuple(sorted(query.items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    url = f"{BASE_URL}/api/{path}?{urllib.parse.urlencode(query)}"
    try:
        status, body = fetch(url, demo_token())
    except TimeoutError:
        raise HTTPException(status_code=504, detail="AI ตอบช้าเกินไป ลองใหม่อีกครั้ง") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise HTTPException(status_code=504, detail="AI ตอบช้าเกินไป ลองใหม่อีกครั้ง") from None
        raise HTTPException(status_code=UNAVAILABLE[0], detail=UNAVAILABLE[1]) from None
    except Exception:   # อ่าน secret ไม่ได้ / JSON เสีย
        raise HTTPException(status_code=UNAVAILABLE[0], detail=UNAVAILABLE[1]) from None
    if status == 401:
        demo_token.cache_clear()   # เผื่อเพื่อนเปลี่ยนรหัส: ครั้งหน้าอ่าน secret ใหม่
    if status != 200:
        code, detail = ERRORS.get(status, UNAVAILABLE)
        raise HTTPException(status_code=code, detail=detail)
    _cache[key] = (time.time(), body)
    return body
