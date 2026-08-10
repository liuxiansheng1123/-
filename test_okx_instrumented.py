"""测 get_candlesticks_history 内部耗时"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
from okx_rest import OKXRestClient

# Monkey-patch _request to log
import okx_rest
orig_request = okx_rest.OKXRestClient._request

def wrapped(self, method, endpoint, params=None, data=None, auth=False):
    t0 = time.time()
    r = orig_request(self, method, endpoint, params, data, auth)
    elapsed = time.time() - t0
    print(f"  [OKX] {endpoint} {params} -> {elapsed*1000:.0f}ms")
    return r

okx_rest.OKXRestClient._request = wrapped

client = OKXRestClient(api_key="", secret_key="", passphrase="", flag="1")
for bar in ["15m", "4H", "1D"]:
    print(f"\n[{bar}]")
    t0 = time.time()
    df = client.get_candlesticks_history(inst_id="ETH-USDT-SWAP", bar=bar, total_bars=300)
    elapsed = time.time() - t0
    print(f"  Total: {elapsed*1000:.0f}ms, {len(df)} bars")
