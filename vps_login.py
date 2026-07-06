from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired

cl = Client()
cl.challenge_code_handler = lambda username, choice: input(f"Nhập mã xác thực Instagram gửi đến email/SMS: ")

try:
    cl.login("nguyenvanning", "Hoaiduc@08768")
except ChallengeRequired as e:
    print("Instagram yêu cầu xác thực. Đang xử lý...")
    cl.challenge_resolve(e.last_json)
    print("Xác thực thành công!")

cl.dump_settings("sessions/instagram_session.json")
print("Đã lưu session vào sessions/instagram_session.json")
