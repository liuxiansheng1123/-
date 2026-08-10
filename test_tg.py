import os, requests, json
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
from dotenv import load_dotenv
load_dotenv(r'C:\Users\Administrator\Desktop\okx_ta_system\.env')
token = os.getenv('TELEGRAM_BOT_TOKEN')
chat = os.getenv('TELEGRAM_CHAT_ID')

# 发送测试消息
url = f'https://api.telegram.org/bot{token}/sendMessage'
resp = requests.post(url, data={
    'chat_id': chat,
    'text': 'ETH 三周期因子系统测试 - 系统已正常运行',
    'parse_mode': 'HTML'
}, timeout=10)
print('Status:', resp.status_code)
rdata = resp.json()
print('OK:', rdata.get('ok'))
if rdata.get('ok'):
    print('Message ID:', rdata['result']['message_id'])
    print('Sent to:', rdata['result']['chat'].get('username'))
