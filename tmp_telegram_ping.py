"""
ACB Trader — Telegram Connection Test Utility
Verifies BOT_TOKEN, CHAT_ID, and Proxy routing via system-wide or .env settings.
"""

from __future__ import annotations
import os
import requests
from dotenv import load_dotenv

# Initialize environment
load_dotenv()

def test_telegram_connectivity():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    proxy_url = os.environ.get("TELEGRAM_PROXY")

    # Defensive validation
    if not token or len(token) < 10:
        print("[error] Missing or invalid TELEGRAM_BOT_TOKEN")
        return

    if not chat_id:
        print("[error] Missing TELEGRAM_CHAT_ID")
        return

    # Masked token for secure identification
    masked_token = f"{token[:5]}...{token[-5:]}" if len(token) >= 10 else "invalid"
    print(f"[config] Using Token:  {masked_token}")
    print(f"[config] Using ChatID: {chat_id}")

    # Proxy configuration (matching main engine logic)
    proxies = {}
    if proxy_url:
        print(f"[config] Routing via Proxy: {proxy_url}")
        proxies = {"http": proxy_url, "https": proxy_url}

    payload = {
        "chat_id": chat_id,
        "text": "🏓 <b>ACB Trader Utility: Connectivity Check</b>",
        "parse_mode": "HTML"
    }

    try:
        print("[network] Sending heartbeat to Telegram API...")
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
            proxies=proxies if proxies else None
        )
        
        if resp.ok:
            print(f"[status] 200 OK: Message Delivered (ID: {resp.json().get('result', {}).get('message_id')})")
        else:
            print(f"[status] {resp.status_code} Error: {resp.text}")
            
    except Exception as e:
        print(f"[network] connection failed: {e}")

if __name__ == "__main__":
    test_telegram_connectivity()
