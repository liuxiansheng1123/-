"""debug 多周期对齐"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
from okx_rest import OKXRestClient
from factors import run_all_factors as run_original_factors
from new_factors import run_new_factors
from multi_tf_align import check_alignment, make_aligned_decision

client = OKXRestClient(api_key="", secret_key="", passphrase="", flag="1")
df_15m = client.get_candlesticks_history("ETH-USDT-SWAP", "15m", 300)
df_4h = client.get_candlesticks_history("ETH-USDT-SWAP", "4H", 300)
df_1d = client.get_candlesticks_history("ETH-USDT-SWAP", "1D", 300)


def run_factors(df):
    sigs = []
    for s in run_original_factors(df):
        sigs.append(s.to_dict())
    for s in run_new_factors(df):
        sigs.append(s.to_dict())
    return sigs


tf_signals = {
    "15m": run_factors(df_15m),
    "4H": run_factors(df_4h),
    "1D": run_factors(df_1d),
}

# 各周期方向统计
for tf, sigs in tf_signals.items():
    long_s = [s for s in sigs if s.get("side") == "long"]
    short_s = [s for s in sigs if s.get("side") == "short"]
    print(f"\n[{tf}] long={len(long_s)}, short={len(short_s)}, none={len(sigs)-len(long_s)-len(short_s)}")
    print(f"  LONG factors: {[s['factor_name'] for s in long_s]}")
    print(f"  SHORT factors: {[s['factor_name'] for s in short_s]}")

print("\n=== check_alignment ===")
align = check_alignment(tf_signals)
print(align)

print("\n=== make_aligned_decision ===")
aligned = make_aligned_decision(tf_signals, df_15m)
for k, v in aligned.items():
    print(f"  {k}: {v}")