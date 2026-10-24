"""สร้างระบบ login ของหน้าเว็บแพทย์ (Amazon Cognito) — รันซ้ำได้ ของที่มีแล้วจะไม่สร้างซ้ำ

สร้าง user pool + หน้า login (Hosted UI) + app client:
  AWS_PROFILE=minny python deploy/setup_cognito.py --domain-prefix pd-doctor-xxxx \\
      --callback http://localhost:5500/docter.html

เพิ่ม URL ของเว็บจริงทีหลัง: รันคำสั่งเดิมแล้วใส่ --callback เพิ่ม (ของเดิมยังอยู่)

เพิ่มบัญชีหมอ (Cognito ส่งรหัสผ่านชั่วคราวไปทางอีเมล ต้องเปลี่ยนรหัสตอน login ครั้งแรก):
  AWS_PROFILE=minny python deploy/setup_cognito.py --domain-prefix pd-doctor-xxxx \\
      --callback http://localhost:5500/docter.html --add-doctor someone@example.com DR-001

ไม่อยากให้ส่งอีเมล (เช่น อีเมลทดสอบ): ตั้งรหัสชั่วคราวผ่าน env DOCTOR_TEMP_PASSWORD แทน
(ไม่รับทาง argument จะได้ไม่ค้างใน history) login ครั้งแรกยังถูกบังคับให้ตั้งรหัสใหม่เหมือนเดิม
  DOCTOR_TEMP_PASSWORD='...' AWS_PROFILE=minny python deploy/setup_cognito.py ... --add-doctor ...

จบแล้วพิมพ์ env ที่ต้องตั้งให้ backend: COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID / COGNITO_DOMAIN
"""
import argparse
import os

import boto3

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
POOL_NAME = "pd-doctors"
CLIENT_NAME = "pd-web"

idp = boto3.client("cognito-idp", region_name=REGION)


def find_pool():
    for page in idp.get_paginator("list_user_pools").paginate(MaxResults=60):
        for p in page["UserPools"]:
            if p["Name"] == POOL_NAME:
                return p["Id"]
    return None


def ensure_pool():
    pool_id = find_pool()
    if pool_id:
        print(f"มีแล้ว    user pool {POOL_NAME} ({pool_id})")
        return pool_id
    pool_id = idp.create_user_pool(
        PoolName=POOL_NAME,
        UsernameAttributes=["email"],                      # login ด้วยอีเมล
        AutoVerifiedAttributes=["email"],
        Schema=[{"Name": "doctor_id", "AttributeDataType": "String", "Mutable": True,
                 "StringAttributeConstraints": {"MinLength": "1", "MaxLength": "32"}}],
        AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},   # สมัครเองไม่ได้ แอดมินเพิ่มให้เท่านั้น
        # เน้นความยาว (≥10) + ตัวอักษร + ตัวเลข ไม่บังคับตัวพิมพ์ใหญ่/สัญลักษณ์
        Policies={"PasswordPolicy": {"MinimumLength": 10, "RequireUppercase": False, "RequireLowercase": True,
                                     "RequireNumbers": True, "RequireSymbols": False,
                                     "TemporaryPasswordValidityDays": 7}},
        AccountRecoverySetting={"RecoveryMechanisms": [{"Priority": 1, "Name": "verified_email"}]},
    )["UserPool"]["Id"]
    print(f"สร้างแล้ว user pool {POOL_NAME} ({pool_id})")
    return pool_id


def ensure_domain(pool_id, prefix):
    current = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"].get("Domain")
    if current:
        if current != prefix:
            print(f"ใช้ domain เดิม {current} (ไม่ได้เปลี่ยนเป็น {prefix})")
        prefix = current
    else:
        # ManagedLoginVersion=1 = หน้า login แบบคลาสสิก ใช้ได้ทันทีโดยไม่ต้องตั้งค่า branding
        idp.create_user_pool_domain(Domain=prefix, UserPoolId=pool_id, ManagedLoginVersion=1)
        print(f"สร้างแล้ว หน้า login {prefix}")
    return f"https://{prefix}.auth.{REGION}.amazoncognito.com"


def ensure_client(pool_id, callbacks):
    existing = next((c for page in idp.get_paginator("list_user_pool_clients").paginate(UserPoolId=pool_id, MaxResults=60)
                     for c in page["UserPoolClients"] if c["ClientName"] == CLIENT_NAME), None)
    if existing:   # รวม callback เดิมกับที่ส่งมาใหม่ ไม่ลบของเดิม
        old = idp.describe_user_pool_client(UserPoolId=pool_id, ClientId=existing["ClientId"])["UserPoolClient"]
        callbacks = sorted(set(old.get("CallbackURLs", [])) | set(callbacks))
    settings = dict(
        UserPoolId=pool_id, ClientName=CLIENT_NAME,
        AllowedOAuthFlows=["code"], AllowedOAuthFlowsUserPoolClient=True,   # authorization code + PKCE (หน้าเว็บเก็บ secret ไม่ได้)
        AllowedOAuthScopes=["openid", "email", "profile"],
        CallbackURLs=callbacks, LogoutURLs=callbacks, SupportedIdentityProviders=["COGNITO"],
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
        ReadAttributes=["email", "email_verified", "custom:doctor_id"],
        WriteAttributes=["email"],   # ห้ามผู้ใช้แก้ custom:doctor_id เอง (ไม่งั้นเปลี่ยนตัวเองเป็นหมอคนอื่นได้)
        PreventUserExistenceErrors="ENABLED",
        IdTokenValidity=1, AccessTokenValidity=1, RefreshTokenValidity=1,
        TokenValidityUnits={"IdToken": "hours", "AccessToken": "hours", "RefreshToken": "days"},
        EnableTokenRevocation=True,
    )
    if existing:
        idp.update_user_pool_client(ClientId=existing["ClientId"], **settings)
        print(f"อัปเดตแล้ว app client {CLIENT_NAME} (callback: {', '.join(callbacks)})")
        return existing["ClientId"]
    client_id = idp.create_user_pool_client(GenerateSecret=False, **settings)["UserPoolClient"]["ClientId"]
    print(f"สร้างแล้ว app client {CLIENT_NAME} (callback: {', '.join(callbacks)})")
    return client_id


def add_doctor(pool_id, email, doctor_id):
    attrs = [{"Name": "custom:doctor_id", "Value": doctor_id}]
    temp = os.environ.get("DOCTOR_TEMP_PASSWORD")
    # มีรหัสชั่วคราวใน env = ไม่ส่งอีเมล (SUPPRESS) · ไม่มี = Cognito สุ่มรหัสแล้วส่งทางอีเมล
    send = {"TemporaryPassword": temp, "MessageAction": "SUPPRESS"} if temp else {"DesiredDeliveryMediums": ["EMAIL"]}
    try:
        idp.admin_create_user(UserPoolId=pool_id, Username=email, **send,
                              UserAttributes=[{"Name": "email", "Value": email},
                                              {"Name": "email_verified", "Value": "true"}, *attrs])
        how = "ตั้งรหัสชั่วคราวจาก DOCTOR_TEMP_PASSWORD (ไม่ส่งอีเมล)" if temp else "ส่งรหัสผ่านชั่วคราวไปที่อีเมลแล้ว"
        print(f"เพิ่มแล้ว  {email} -> {doctor_id} ({how})")
    except idp.exceptions.UsernameExistsException:
        idp.admin_update_user_attributes(UserPoolId=pool_id, Username=email, UserAttributes=attrs)
        print(f"มีแล้ว    {email} -> อัปเดต doctor_id เป็น {doctor_id}")
    d = boto3.resource("dynamodb", region_name=REGION).Table("pd_doctors").get_item(Key={"doctor_id": doctor_id}).get("Item")
    if not d:
        print(f"  ⚠ ยังไม่มี {doctor_id} ในตาราง pd_doctors หน้าเว็บจะดึงชื่อหมอไม่ได้")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain-prefix", required=True, help="ชื่อหน้า login ต้องไม่ซ้ำกับใครทั้ง AWS เช่น pd-doctor-tu2026")
    ap.add_argument("--callback", action="append", required=True, help="URL หน้าเว็บ (ใส่ได้หลายครั้ง)")
    ap.add_argument("--add-doctor", nargs=2, metavar=("EMAIL", "DOCTOR_ID"))
    a = ap.parse_args()

    pool_id = ensure_pool()
    domain = ensure_domain(pool_id, a.domain_prefix)
    client_id = ensure_client(pool_id, a.callback)
    if a.add_doctor:
        add_doctor(pool_id, *a.add_doctor)

    print("\nตั้ง env ให้ backend แล้วรันใหม่:")
    print(f"  export COGNITO_USER_POOL_ID={pool_id}")
    print(f"  export COGNITO_CLIENT_ID={client_id}")
    print(f"  export COGNITO_DOMAIN={domain}")
