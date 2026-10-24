"""แต่งหน้า login ของ Cognito (Hosted UI แบบ classic) ให้เข้าชุดกับเว็บ: โลโก้ + สีเขียวของ pakinson

  cd web/backend
  AWS_PROFILE=minny python deploy/brand_cognito.py          # ใส่โลโก้ + CSS ให้ app client pd-web
  AWS_PROFILE=minny python deploy/brand_cognito.py --reset  # กลับเป็นหน้าตาเริ่มต้นของ AWS

แก้ได้แค่สี / ขอบ / ขนาด / โลโก้ ของ class ที่ AWS อนุญาต (*-customizable) ข้อความบนหน้ายังเป็นภาษาอังกฤษ
โลโก้ deploy/cognito_logo.png (PNG พื้นใส ไม่เกิน 100 KB) ตัวอักษรสีขาว วางบนแถบสีเขียวด้านบน
"""
import argparse
from pathlib import Path

import boto3

import setup_cognito

LOGO = Path(__file__).resolve().parent / "cognito_logo.png"
GREEN, GREEN_DARK, INK, MUTE, LINE = "#3f6e5b", "#2f5546", "#26302c", "#7b8680", "#dfe4e1"

CSS = f"""
.banner-customizable {{ padding: 22px 0px 22px 0px; background-color: {GREEN}; }}
.logo-customizable {{ max-width: 80%; max-height: 80px; }}
.background-customizable {{ background-color: #f7f8f7; }}
.textDescription-customizable {{ padding-top: 8px; padding-bottom: 4px; display: block; font-size: 16px; font-weight: 600; color: {INK}; }}
.label-customizable {{ font-weight: 500; font-size: 14px; color: {INK}; }}
.inputField-customizable {{ width: 100%; height: 44px; color: {INK}; background-color: #ffffff; border: 1px solid {LINE}; border-radius: 10px; font-size: 15px; }}
.inputField-customizable:focus {{ border-color: {GREEN}; outline: 0; }}
.submitButton-customizable {{ font-size: 15px; font-weight: 600; margin: 18px 0px 8px 0px; height: 46px; width: 100%; color: #ffffff; background-color: {GREEN}; border: 1px solid {GREEN}; border-radius: 10px; }}
.submitButton-customizable:hover {{ color: #ffffff; background-color: {GREEN_DARK}; border-color: {GREEN_DARK}; }}
.errorMessage-customizable {{ padding: 8px 10px; font-size: 14px; width: 100%; background: #fdecea; border: 1px solid #f3c3bd; color: #c0392b; border-radius: 8px; }}
.redirect-customizable {{ text-align: center; }}
.legalText-customizable {{ color: {MUTE}; font-size: 12px; }}
.passwordCheck-notValid-customizable {{ color: #e0483c; }}
.passwordCheck-valid-customizable {{ color: #1f9d63; }}
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    idp = setup_cognito.idp
    pool_id = setup_cognito.find_pool()
    client_id = next(c["ClientId"] for page in idp.get_paginator("list_user_pool_clients").paginate(UserPoolId=pool_id)
                     for c in page["UserPoolClients"] if c["ClientName"] == setup_cognito.CLIENT_NAME)
    if a.reset:
        idp.set_ui_customization(UserPoolId=pool_id, ClientId=client_id)
        print(f"ล้างแล้ว หน้าตา login ของ {setup_cognito.CLIENT_NAME} (กลับเป็นค่าเริ่มต้นของ AWS)")
    else:
        idp.set_ui_customization(UserPoolId=pool_id, ClientId=client_id, CSS=CSS, ImageFile=LOGO.read_bytes())
        print(f"แต่งแล้ว หน้า login ของ {setup_cognito.CLIENT_NAME} (โลโก้ {LOGO.stat().st_size // 1024} KB + CSS)")
