cat > /home/admin/--main/deepseek_analyzer.py << 'EOF'
import requests
import json

class DeepSeekAnalyzer:
    def __init__(self, api_key, base_url="https://api.deepseek.com/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def analyze_multi_factor_signals(self, factor_signals, backtest_results, market_context):
        # 简化版：返回一个默认建议，避免程序崩溃
        return {
            "recommendation": "none",
            "confidence": 0.5,
            "buy_price": None,
            "sell_price": None,
            "stop_loss": None,
            "take_profit": None,
            "key_factors": [],
            "risk_reward_ratio": None,
            "analysis": "DeepSeek API not fully configured, using fallback."
        }
EOF