"""Deploy หน้าเว็บ (frontend/public/docter.html) ขึ้น AWS Amplify Hosting แบบอัปโหลด zip (ไม่ผูกกับ git)

  cd web/backend
  AWS_PROFILE=minny python deploy/deploy_frontend.py

ครั้งแรกสร้าง app pd-web + branch main · ทุกครั้ง: อัปโหลดหน้าเว็บล่าสุด แล้ว
- เพิ่ม URL ของเว็บเป็น callback ใน Cognito (login แล้วกลับมาหน้านี้ได้)
- ตั้ง CORS_ORIGINS ของ Lambda เป็นโดเมนเว็บ (เบราว์เซอร์ยอมให้เรียก API)
URL ของเว็บบันทึกไว้ที่ deploy/deployment.json (web_url)
"""
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy_lambda   # noqa: E402  (ใช้ตั้ง env ของ Lambda)
import setup_cognito   # noqa: E402  (ใช้เพิ่ม callback)

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
APP_NAME, BRANCH = "pd-web", "main"
REPO = Path(__file__).resolve().parents[3]
PAGE = REPO / "frontend" / "public" / "docter.html"
DEPLOYMENT = Path(__file__).resolve().parent / "deployment.json"

# หน้าแรกของเว็บ: พาไป docter.html (เก็บ ?query และ #หน้า ไว้ด้วย)
INDEX = """<!doctype html><html lang="th"><head><meta charset="utf-8"><title>pakinson</title>
<meta http-equiv="refresh" content="0; url=docter.html"></head>
<body><script>location.replace('docter.html'+location.search+location.hash)</script>
<a href="docter.html">เปิดหน้าเว็บแพทย์</a></body></html>
"""

amp = boto3.client("amplify", region_name=REGION)


def site_zip():
    # กันอัปโหลดหน้าที่ยังชี้ไป localhost: หน้าต้องมี URL ของ API Gateway (api_url ใน deployment.json)
    api_url = json.loads(DEPLOYMENT.read_text()).get("api_url") if DEPLOYMENT.exists() else None
    if not api_url or api_url not in PAGE.read_text():
        sys.exit(f"docter.html ไม่มี API_AWS = {api_url} (รัน deploy_lambda.py ก่อน แล้วใส่ URL นี้ใน API_AWS) ยังไม่ได้อัปโหลด")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(PAGE, "docter.html")
        z.writestr("index.html", INDEX)
    return buf.getvalue()


def ensure_app():
    app = next((a for a in amp.list_apps(maxResults=100)["apps"] if a["name"] == APP_NAME), None)
    if app:
        print(f"มีแล้ว    Amplify app {APP_NAME} ({app['appId']})")
    else:
        app = amp.create_app(name=APP_NAME, platform="WEB", description="pakinson หน้าเว็บแพทย์ (อัปโหลด zip)")["app"]
        print(f"สร้างแล้ว Amplify app {APP_NAME} ({app['appId']})")
    try:
        amp.get_branch(appId=app["appId"], branchName=BRANCH)
    except amp.exceptions.NotFoundException:
        amp.create_branch(appId=app["appId"], branchName=BRANCH, stage="PRODUCTION")
        print(f"สร้างแล้ว branch {BRANCH}")
    return app


def upload(app_id):
    d = amp.create_deployment(appId=app_id, branchName=BRANCH)
    req = urllib.request.Request(d["zipUploadUrl"], data=site_zip(), method="PUT",
                                 headers={"Content-Type": "application/zip"})
    urllib.request.urlopen(req).read()
    amp.start_deployment(appId=app_id, branchName=BRANCH, jobId=d["jobId"])
    for _ in range(60):
        status = amp.get_job(appId=app_id, branchName=BRANCH, jobId=d["jobId"])["job"]["summary"]["status"]
        if status in ("SUCCEED", "FAILED", "CANCELLED"):
            break
        time.sleep(5)
    if status != "SUCCEED":
        sys.exit(f"deploy ไม่สำเร็จ: {status}")
    print(f"อัปโหลดแล้ว หน้าเว็บ (job {d['jobId']})")


if __name__ == "__main__":
    app = ensure_app()
    upload(app["appId"])
    web_url = f"https://{BRANCH}.{app['defaultDomain']}"

    info = json.loads(DEPLOYMENT.read_text()) if DEPLOYMENT.exists() else {}
    info.update({"web_url": web_url, "amplify_app_id": app["appId"]})
    DEPLOYMENT.write_text(json.dumps(info, indent=2) + "\n")

    pool_id = setup_cognito.find_pool()
    if not pool_id:
        sys.exit("ยังไม่มี user pool (รัน deploy/setup_cognito.py ก่อน)")
    setup_cognito.ensure_client(pool_id, [web_url + "/docter.html"])
    deploy_lambda.update_config()
    print(f"\nเว็บ: {web_url}/docter.html")
