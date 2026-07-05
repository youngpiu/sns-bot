import asyncio
import sys
import uuid

from providers.fans import FansSessionStore
from providers.fans import send_verification_code, confirm_login
try:
    from tweety import TwitterAsync
except ImportError:
    TwitterAsync = None


def login() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Interactive login cho các provider")
    sub = parser.add_subparsers(dest="provider")
    sub.add_parser("twitter", help="Đăng nhập Twitter (mở trình duyệt)")

    args = parser.parse_args()

    if args.provider == "twitter":
        if TwitterAsync is None:
            print("Tweety chưa được cài đặt. Chạy: pip install tweety-ns")
            sys.exit(1)

        session_name = "sessions/twitter_session"

        async def _login():
            app = TwitterAsync(session_name)
            print("Đang mở trình duyệt để đăng nhập Twitter...")
            print("Sau khi đăng nhập xong, nhấn Enter để tiếp tục.")
            await app.start()
            print(f"Đã lưu session vào {session_name}.tw_session")
            print("Xong! Chạy: python main.py")

        asyncio.run(_login())
        return

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
