cat > /home/admin/--main/okx_rest.py << 'EOF'
import requests
import pandas as pd

class OKXRestClient:
    def __init__(self, base_url="https://openapi.okx.com/api/v5"):
        self.base_url = base_url

    def get_candles(self, inst_id, bar="15m", limit=300):
        url = f"{self.base_url}/market/candles"
        params = {"instId": inst_id, "bar": bar, "limit": limit}
        try:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if data.get("code") == "0" and "data" in data:
                raw = data["data"]
                df = pd.DataFrame(raw, columns=["ts","o","h","l","c","vol","volCcy","volCcyQuote","confirm"])
                df["ts"] = pd.to_datetime(df["ts"], unit='ms')
                for col in ["o","h","l","c","vol"]:
                    df[col] = df[col].astype(float)
                return df
            else:
                print(f"OKX API error: {data}")
                return None
        except Exception as e:
            print(f"get_candles error: {e}")
            return None
EOF