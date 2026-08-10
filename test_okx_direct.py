"""直接测 OKX 公共 API 速度"""
import time
import requests

url = "https://openapi.okx.com/api/v5/market/candles"
params = {"instId": "ETH-USDT-SWAP", "bar": "15m", "limit": "300"}

for i in range(3):
    t0 = time.time()
    r = requests.get(url, params=params, timeout=10)
    elapsed = time.time() - t0
    data = r.json()
    print(f"Attempt {i+1}: {elapsed*1000:.0f}ms, status={r.status_code}, records={len(data.get('data', []))}")
