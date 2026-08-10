import sys, os
from pathlib import Path
from dotenv import load_dotenv
ENV_PATH = r"C:\Users\Administrator\Desktop\okx_ta_system\.env"
load_dotenv(ENV_PATH)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
from okx_rest import OKXRestClient
from factors import run_all_factors as run_original_factors
from new_factors import run_new_factors

client = OKXRestClient(api_key="", secret_key="", passphrase="", flag="1")
df = client.get_candlesticks_history(inst_id="ETH-USDT-SWAP", bar="1D", total_bars=300)

print(f"DF len: {len(df)}")
all_signals = []
orig = run_original_factors(df)
for s in orig:
    d = s.to_dict()
    all_signals.append(d)

print("orig signals:")
for s in all_signals:
    print(f"  {s['factor_name']}: side={s['side']} tf={s.get('timeframe')}")

# 测试 _safe_get
from decision_engine import _safe_get, select_dominant_timeframe, make_final_decision
# 模拟 group_signals_by_timeframe
tf_signals = {"1D": all_signals, "15m": all_signals, "4H": all_signals}
print("\n_select test:")
print(_safe_get(tf_signals, "1D")[:1])
print("\nselect_dominant_timeframe:")
print(select_dominant_timeframe(tf_signals))
print("\nmake_final_decision:")
d = make_final_decision(tf_signals)
print(f"src_tf={d['source_timeframe']}, src_factor={d['source_factor']}, ep={d['entry_price']}")
