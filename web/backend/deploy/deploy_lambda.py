"""Deploy backend ขึ้น AWS Lambda — ครั้งแรกสร้าง role + function, ครั้งต่อไปอัปเดตโค้ด/ค่าตั้งค่าอย่างเดียว

  cd web/backend
  AWS_PROFILE=minny python deploy/deploy_lambda.py            # build + role + lambda + API Gateway
  AWS_PROFILE=minny python deploy/deploy_lambda.py --stage build   # แค่ build zip (ไม่แตะ AWS)
  AWS_PROFILE=minny python deploy/deploy_lambda.py --stage api     # แค่ API Gateway (Lambda ต้องมีแล้ว)
  AWS_PROFILE=minny python deploy/deploy_lambda.py --stage config  # แค่สิทธิ์ role + env ของ Lambda (Cognito + CORS + เดโม AI)

URL ของ API บันทึกไว้ที่ deploy/deployment.json (หน้าเว็บใช้ค่านี้ใน API_AWS ของ docter.html)

ต้องมี python3.12 ในเครื่อง (ใช้ pip ของมันดึง package แบบ Linux/x86_64 มาใส่ zip)
ค่า Cognito อ่านจาก cognito.env (สร้างโดย deploy/setup_cognito.py)
เดโม AI ของทีม (app/reference.py): หา URL จาก API Gateway ชื่อ pd-reference-demo-v1 + ARN ของ secret รหัสเข้า
  ทุกครั้งที่ deploy/config (เดโม stop แล้ว resume จะได้ URL ใหม่ -> รัน --stage config อีกครั้ง)
  แค่อ่าน ไม่แก้ resource ของเดโม · รหัสไม่ถูกใส่ใน env (Lambda อ่านจาก Secrets Manager เอง)
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
FUNCTION = "pd-web-api"
ROLE = "pd-web-api-role"
API_NAME = "pd-web-api"
THROTTLE = {"ThrottlingRateLimit": 10, "ThrottlingBurstLimit": 20}   # request/วินาที กันโดนยิงถล่ม (ไม่ใช่เพดานค่าใช้จ่าย)
RUNTIME, MEMORY, TIMEOUT = "python3.12", 512, 30
HERE = Path(__file__).resolve().parents[1]          # web/backend
BUILD = HERE / ".build"
ZIP = BUILD / f"{FUNCTION}.zip"
PYTHON312 = os.environ.get("PYTHON312", "python3.12")
REFERENCE_API = "pd-reference-demo-v1"          # API Gateway ของเดโม AI (ของเพื่อน ห้ามแก้)
REFERENCE_SECRET = "pd-reference-demo-v1-login"  # secret รหัสเข้าเดโม


# ---------------------------------------------------------------- 1. build
def build():
    """zip = package จาก requirements-lambda.txt (wheel ของ Linux x86_64 / Python 3.12) + โฟลเดอร์ app/"""
    shutil.rmtree(BUILD, ignore_errors=True)
    pkg = BUILD / "pkg"
    # venv แยกของ python3.12 เพื่อได้ pip รุ่นใหม่ที่รู้จัก tag manylinux_2_28 (ไม่แตะ .venv หลักของโปรเจกต์)
    subprocess.run([PYTHON312, "-m", "venv", BUILD / "venv"], check=True)
    pip = [BUILD / "venv" / "bin" / "python", "-m", "pip"]
    subprocess.run([*pip, "install", "-q", "--upgrade", "pip"], check=True)
    subprocess.run([*pip, "install", "-q", "--target", pkg, "--implementation", "cp", "--python-version", "3.12",
                    "--only-binary=:all:", "--platform", "manylinux2014_x86_64", "--platform", "manylinux_2_28_x86_64",
                    "-r", HERE / "requirements-lambda.txt"], check=True)

    skip = {"__pycache__"}
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for root in (pkg, HERE):
            src = [p for p in pkg.rglob("*")] if root is pkg else [p for p in (HERE / "app").rglob("*")]
            for p in src:
                if p.is_file() and not skip & set(p.parts):
                    z.write(p, p.relative_to(root))
    size = ZIP.stat().st_size / 1e6
    print(f"build แล้ว {ZIP.relative_to(HERE)} ({size:.1f} MB)")
    return ZIP


# ---------------------------------------------------------------- 2. IAM role
def ensure_role():
    """role ของ Lambda: อ่าน/เขียนตาราง pd_* (เฉพาะคำสั่งที่โค้ดใช้) + เขียน log ของ function นี้
    + อ่านรหัสเข้าเดโม AI (secret เดียว) ไม่มีสิทธิ์อื่น"""
    iam = boto3.client("iam")
    account = boto3.client("sts").get_caller_identity()["Account"]
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                      "Action": "sts:AssumeRole"}]}
    table = f"arn:aws:dynamodb:{REGION}:{account}:table/pd_*"
    log_group = f"arn:aws:logs:{REGION}:{account}:log-group:/aws/lambda/{FUNCTION}"
    secret = f"arn:aws:secretsmanager:{REGION}:{account}:secret:{REFERENCE_SECRET}-*"   # -* = ท้าย ARN ที่ AWS สุ่มให้
    policy = {"Version": "2012-10-17", "Statement": [
        {"Sid": "PdTables", "Effect": "Allow",
         "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"],
         "Resource": [table, table + "/index/*"]},
        {"Sid": "Logs", "Effect": "Allow", "Action": "logs:CreateLogGroup", "Resource": log_group},
        {"Sid": "LogEvents", "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": log_group + ":*"},
        {"Sid": "ReferenceDemoToken", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
         "Resource": secret},
    ]}
    try:
        arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
        print(f"มีแล้ว    role {ROLE}")
        created = False
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                              Description="pd-web-api: DynamoDB pd_* + logs")["Role"]["Arn"]
        print(f"สร้างแล้ว role {ROLE}")
        created = True
    iam.put_role_policy(RoleName=ROLE, PolicyName="pd-web-api-access", PolicyDocument=json.dumps(policy))
    if created:
        time.sleep(10)   # role ใหม่ต้องรอ IAM กระจายค่าก่อน Lambda จะใช้ได้
    return arn


# ---------------------------------------------------------------- 3. Lambda
def cognito_env():
    env = {}
    for line in (HERE / "cognito.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    missing = {"COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID", "COGNITO_DOMAIN"} - env.keys()
    if missing:
        sys.exit(f"cognito.env ไม่มี {missing} (รัน deploy/setup_cognito.py ก่อน)")
    return env


def reference_env():
    """URL + ARN ของ secret ของเดโม AI (อ่านอย่างเดียว) ไม่เจอ = ไม่ตั้ง หน้าเว็บจะขึ้นว่ายังไม่มีผล AI"""
    gw = boto3.client("apigatewayv2", region_name=REGION)
    api = next((a for a in gw.get_apis(MaxResults="100")["Items"] if a["Name"] == REFERENCE_API), None)
    try:
        arn = boto3.client("secretsmanager", region_name=REGION).describe_secret(SecretId=REFERENCE_SECRET)["ARN"]
    except Exception:
        arn = None
    if not (api and arn):
        print(f"ไม่พบ API หรือ secret ของเดโม AI ({REFERENCE_API}) — ไม่ตั้งค่าเดโม AI")
        return {}
    return {"REFERENCE_API_BASE_URL": api["ApiEndpoint"], "REFERENCE_SECRET_ID": arn}


def lambda_env():
    """env ของ Lambda = ค่า Cognito + โดเมนเว็บที่อนุญาต CORS (web_url ใน deployment.json จาก deploy_frontend.py)
    + ที่อยู่เดโม AI"""
    env = cognito_env()
    dep = HERE / "deploy" / "deployment.json"
    web_url = json.loads(dep.read_text()).get("web_url") if dep.exists() else None
    if web_url:
        env["CORS_ORIGINS"] = web_url
    env.update(reference_env())
    return env


def update_config():
    lam = boto3.client("lambda", region_name=REGION)
    env = lambda_env()
    lam.update_function_configuration(FunctionName=FUNCTION, Environment={"Variables": env})
    lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)
    print(f"อัปเดตแล้ว env ของ Lambda {FUNCTION}: {', '.join(env)}")


def deploy_function(role_arn, zip_path):
    lam = boto3.client("lambda", region_name=REGION)
    config = dict(FunctionName=FUNCTION, Runtime=RUNTIME, Handler="app.main.handler", Role=role_arn,
                  MemorySize=MEMORY, Timeout=TIMEOUT, Environment={"Variables": lambda_env()},
                  Description="pakinson web API (FastAPI + Mangum)")
    code = zip_path.read_bytes()
    try:
        lam.get_function(FunctionName=FUNCTION)
        exists = True
    except lam.exceptions.ResourceNotFoundException:
        exists = False

    if exists:
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)
        lam.update_function_configuration(**config)
        lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)
        print(f"อัปเดตแล้ว Lambda {FUNCTION}")
    else:
        for attempt in range(6):   # role ใหม่บางทีต้องรอเพิ่มอีกนิด
            try:
                lam.create_function(Code={"ZipFile": code}, Architectures=["x86_64"], **config)
                break
            except lam.exceptions.InvalidParameterValueException as e:
                if "role" not in str(e).lower() or attempt == 5:
                    raise
                time.sleep(5)
        lam.get_waiter("function_active_v2").wait(FunctionName=FUNCTION)
        print(f"สร้างแล้ว Lambda {FUNCTION} ({RUNTIME}, {MEMORY} MB, timeout {TIMEOUT} s)")


# ---------------------------------------------------------------- 4. API Gateway (HTTP API)
def ensure_api():
    """HTTP API ส่งทุก path/method ไปที่ Lambda (CORS ให้ FastAPI จัดการ ไม่ตั้งซ้ำที่นี่)"""
    lam = boto3.client("lambda", region_name=REGION)
    gw = boto3.client("apigatewayv2", region_name=REGION)
    account = boto3.client("sts").get_caller_identity()["Account"]
    fn_arn = lam.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]

    api = next((a for a in gw.get_apis(MaxResults="100")["Items"] if a["Name"] == API_NAME), None)
    if api:
        print(f"มีแล้ว    HTTP API {API_NAME} ({api['ApiId']})")
    else:
        # quick create: route $default + stage $default (auto deploy) + integration ไป Lambda (payload 2.0)
        api = gw.create_api(Name=API_NAME, ProtocolType="HTTP", Target=fn_arn,
                            Description="pakinson web API -> Lambda pd-web-api")
        print(f"สร้างแล้ว HTTP API {API_NAME} ({api['ApiId']})")
    api_id = api["ApiId"]
    gw.update_stage(ApiId=api_id, StageName="$default", DefaultRouteSettings=THROTTLE)

    # ให้เฉพาะ API นี้เรียก Lambda ได้ (SourceArn จำกัดที่ api id)
    try:
        lam.add_permission(FunctionName=FUNCTION, StatementId=f"apigw-{api_id}", Action="lambda:InvokeFunction",
                           Principal="apigateway.amazonaws.com",
                           SourceArn=f"arn:aws:execute-api:{REGION}:{account}:{api_id}/*")
    except lam.exceptions.ResourceConflictException:
        pass   # ให้สิทธิ์ไว้แล้ว

    url = api["ApiEndpoint"]
    dep = HERE / "deploy" / "deployment.json"
    info = json.loads(dep.read_text()) if dep.exists() else {}   # เก็บ web_url เดิมไว้
    info.update({"api_url": url, "api_id": api_id, "function": FUNCTION, "region": REGION})
    dep.write_text(json.dumps(info, indent=2) + "\n")
    print(f"API URL: {url}  (บันทึกไว้ที่ deploy/deployment.json)")
    return url


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["all", "build", "api", "config"], default="all")
    a = ap.parse_args()
    if a.stage in ("all", "build"):
        zip_path = build()
    if a.stage == "all":
        deploy_function(ensure_role(), zip_path)
    if a.stage in ("all", "api"):
        ensure_api()
    if a.stage == "config":
        ensure_role()   # สิทธิ์ใหม่ใน policy (เช่น อ่าน secret เดโม AI) มีผลโดยไม่ต้อง build ใหม่
        update_config()
