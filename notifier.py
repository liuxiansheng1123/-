cat > /home/admin/--main/notifier.py << 'EOF'
import requests

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send(self, message, parse_mode="HTML"):
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }
        try:
            r = requests.post(self.api_url, json=payload, timeout=10)
            return r.json()
        except Exception as e:
            print(f"Telegram send error: {e}")
            return None
EOF