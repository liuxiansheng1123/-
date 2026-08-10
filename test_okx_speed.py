"""测试 OKX 拉 K 线速度"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
from okx_rest import OKXRestClient

client = OKXRestClient(api_key="", secret_key="", passphrase="", flag="1")

for bar in ["15m", "4H", "1D"]:
    t0 = time.time()
    df = client.get_candlesticks_history(inst_id="ETH-USDT-SWAP", bar=bar, total_bars=300)
    elapsed = time.time() - t0
    print(f"{bar}: {len(df)} bars, {elapsed*1000:.0f}ms")
