import sys
import uuid

from providers.fans import FansSessionStore
from providers.fans import send_verification_code, confirm_login


def login() -> None:
    session = FansSessionStore().load()

    email = session.email
    if not email:
        email = input("Email: ").strip()
        if not email:
            print("Chưa nhập email")
            sys.exit(1)

    guid = session.guid or str(uuid.uuid4())
    client_uuid = session.client_uuid or f"web-{uuid.uuid4()}"

    print(f"Đang gửi mã xác thực đến {email}...")
    ok = send_verification_code(email, guid)
    if not ok:
        print("Gửi mã xác thực thất bại")
        sys.exit(1)
    print("Đã gửi mã! Kiểm tra email của bạn.")
    code = input("Nhập mã xác thực: ").strip()
    if not code:
        print("Chưa nhập mã")
        sys.exit(1)
    access_token, refresh_token = confirm_login(email, code, client_uuid, guid)

    session = FansSessionStore()
    session._token = access_token
    session._refresh_token = refresh_token
    session._client_uuid = client_uuid
    session._guid = guid
    session._email = email
    session.save()
    print(f"\nĐã lưu vào {session.path}")
    print("Xong! Chạy: python main.py")


if __name__ == "__main__":
    login()
