"""测 OKX ticker 速度 + 数据"""
import time
import requests

url = "https://openapi.okx.com/api/v5/market/ticker"
params = {"instId": "ETH-USDT-SWAP"}

for i in range(5):
    t0 = time.time()
    r = requests.get(url, params=params, timeout=5)
    elapsed = time.time() - t0
    data = r.json()["data"][0]
    print(f"[{i+1}] {elapsed*1000:.0f}ms  last={data['last']} bid={data.get('bidPx','')} ask={data.get('askPx','')}")