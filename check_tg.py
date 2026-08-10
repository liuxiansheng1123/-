import os, requests
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
from dotenv import load_dotenv
load_dotenv(r'C:\Users\Administrator\Desktop\okx_ta_system\.env')
token = os.getenv('TELEGRAM_BOT_TOKEN')
chat = os.getenv('TELEGRAM_CHAT_ID')
url = f'https://api.telegram.org/bot{token}/getUpdates?limit=5&timeout=0'
r = requests.get(url, timeout=5)
data = r.json()
if data.get('ok'):
    msgs = data.get('result', [])
    print(f'Total: {len(msgs)} messages in queue')
    for m in msgs[-5:]:
        if 'message' in m:
            txt = m['message'].get('text', '')[:300]
            print(f"  ID={m['message']['message_id']} | {txt[:100]}")
        elif 'edited_message' in m:
            txt = m['edited_message'].get('text', '')[:300]
            print(f"  [edited] {txt[:100]}")
else:
    print('Error:', data)
