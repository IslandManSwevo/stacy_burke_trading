import os
from dotenv import load_dotenv
load_dotenv()
import requests

token = os.environ.get('TELEGRAM_BOT_TOKEN')
chat_id = os.environ.get('TELEGRAM_CHAT_ID')

print(f"Token: {token[:5]}...{token[-5:]}")
print(f"Chat ID: {chat_id}")

try:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "🏓 <b>PING from ACB Trader</b>", "parse_mode": "HTML"},
        timeout=10
    )
    print("Status code:", r.status_code)
    print("Response:", r.text)
except Exception as e:
    print(f"Exception triggered: {e}")
