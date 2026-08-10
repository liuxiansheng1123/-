"""
ETH 简化监控守护进程
================================
每 15 分钟一次:
1. 拉 15m K 线 (300 根)
2. 算 3 个指标喂给 AI:
   - MA30   (定方向: 做多/做空)
   - 成交量 MA20 (确认放量)
   - ATR(14)  (算止损止盈距离)
3. 把 3 个指标打包发给 DeepSeek 分析
4. Telegram 通知 AI 回复

设计原则:
- 极简: 只算 3 个指标, 其他全部不要
- 15 分钟一次循环, 失败重试
- 全程日志审计
"""

import os
import sys
import time
import json
import signal
import logging
import traceback
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv
import pandas as pd
import requests

ENV_PATH = r"C:\Users\Administrator\Desktop\okx_ta_system\.env"
load_dotenv(ENV_PATH)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from okx_rest import OKXRestClient
from notifier import TelegramNotifier
from deepseek_analyzer import DeepSeekAnalyzer


# =============================================================
# 配置
# =============================================================
MONITOR_CONFIG = {
    "interval_sec": 900,                  # 15 分钟
    "inst_id": "ETH-USDT-SWAP",
    "bars_to_fetch": 300,                 # 拉 300 根 15m K 线
    "log_dir": ROOT / "data" / "monitor",
}


# =============================================================
# 日志
# =============================================================
class MonitorLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = log_dir / f"monitor_{self.session_id}.log"
        self.start_time = datetime.now()

    def log_line(self, tag: str, msg: str, also_print: bool = True):
        ts = datetime.now().isoformat(timespec='seconds')
        line = f"[{ts}] [{tag}] {msg}"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        if also_print:
            print(line)


# =============================================================
# 3 个指标计算
# =============================================================
def compute_three_indicators(df: pd.DataFrame) -> Dict:
    """
    只算 3 个指标, 其他全部不要

    返回:
      - ma30:           MA30 当前值
      - close:          最新收盘价
      - direction:      做多 / 做空 (价格相对 MA30 位置)
      - vol_ma20:       成交量 MA20
      - latest_vol:     最新一根 K 线的成交量
      - vol_ratio:      放量倍数 (latest_vol / vol_ma20)
      - vol_confirm:    True / False (放量 >= 1.5x)
      - atr14:          ATR(14)
    """
    closes = df["close"].astype(float)
    volumes = df["vol"].astype(float) if "vol" in df.columns else df["volume"].astype(float)

    # 1. MA30 (定方向)
    ma30 = float(closes.rolling(window=30).mean().iloc[-1])
    cur = float(closes.iloc[-1])
    direction = "做多" if cur > ma30 else "做空"

    # 2. 成交量 MA20 (确认放量)
    vol_ma20 = float(volumes.rolling(window=20).mean().iloc[-1])
    latest_vol = float(volumes.iloc[-1])
    vol_ratio = latest_vol / vol_ma20 if vol_ma20 > 0 else 0.0
    vol_confirm = vol_ratio >= 1.5

    # 3. ATR(14) (算止损止盈距离)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = closes.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(window=14).mean().iloc[-1])

    return {
        "close": cur,
        "ma30": ma30,
        "direction": direction,
        "vol_ma20": vol_ma20,
        "latest_vol": latest_vol,
        "vol_ratio": vol_ratio,
        "vol_confirm": vol_confirm,
        "atr14": atr14,
    }


# =============================================================
# DeepSeek 调用
# =============================================================
def call_deepseek(indicators: Dict, analyzer: DeepSeekAnalyzer) -> Optional[str]:
    """把 3 个指标喂给 AI, 返回 AI 的回复"""
    system_prompt = (
        "你是一个专业的 ETH 期货交易员。"
        "根据下面 3 个指标给出操作建议:"
        "1) MA30 决定方向 (做多/做空)"
        "2) 成交量 MA20 确认放量 (>=1.5x 为放量)"
        "3) ATR(14) 决定止损止盈距离"
        "请给出:"
        "【入场价】(用 ATR 给出合理的入场区间)"
        "【止损价】(1.5x ATR 距离)"
        "【止盈价】(2x ATR 距离, 盈亏比 1:1.5 以上)"
        "【建议】做多/做空/观望"
        "【置信度】高/中/低"
        "【理由】2-3 句话"
    )

    user_prompt = (
        f"ETH 当前指标:\n"
        f"- 收盘价: ${indicators['close']:.2f}\n"
        f"- MA30: ${indicators['ma30']:.2f}\n"
        f"- 方向: {indicators['direction']} (价格 {'>' if indicators['direction']=='做多' else '<'} MA30)\n"
        f"- 成交量 MA20: {indicators['vol_ma20']:.2f}\n"
        f"- 最新成交量: {indicators['latest_vol']:.2f}\n"
        f"- 放量倍数: {indicators['vol_ratio']:.2f}x ({'已放量' if indicators['vol_confirm'] else '未放量'})\n"
        f"- ATR(14): ${indicators['atr14']:.2f}\n"
    )

    try:
        url = f"{analyzer.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {analyzer.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 600,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[DS-ERR] {e}")
        return None


# =============================================================
# Telegram 通知
# =============================================================
def send_report(notifier: TelegramNotifier, indicators: Dict, ai_reply: Optional[str], logger: MonitorLogger):
    """15 分钟一次的报告: 3 个指标 + AI 回复"""
    lines = [
        f"<b>ETH 报告 [{datetime.now().strftime('%H:%M')}]</b>",
        "",
        f"<b>收盘</b>: ${indicators['close']:.2f}",
        f"<b>MA30</b>: ${indicators['ma30']:.2f} → 方向: <b>{indicators['direction']}</b>",
        f"<b>成交量 MA20</b>: {indicators['vol_ma20']:.0f}",
        f"<b>最新成交量</b>: {indicators['latest_vol']:.0f} ({indicators['vol_ratio']:.2f}x) "
        f"{'放量' if indicators['vol_confirm'] else '缩量'}",
        f"<b>ATR(14)</b>: ${indicators['atr14']:.2f}",
    ]

    if ai_reply:
        lines.append("")
        lines.append("<b>DeepSeek 分析</b>:")
        lines.append(ai_reply[:1500])
    else:
        lines.append("")
        lines.append("<i>DeepSeek 调用失败</i>")

    msg = "\n".join(lines)
    try:
        notifier.send(msg)
        logger.log_line("TG", "Report sent")
    except Exception as e:
        logger.log_line("TG-ERR", f"Send failed: {e}")


# =============================================================
# 主循环
# =============================================================
class MonitorDaemon:
    def __init__(self):
        self.config = {
            "okx_api_key": os.getenv("OKX_API_KEY", ""),
            "okx_secret_key": os.getenv("OKX_SECRET_KEY", ""),
            "okx_passphrase": os.getenv("OKX_PASSPHRASE", ""),
            "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
            "deepseek_base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        }
        self.client = OKXRestClient(
            api_key=self.config["okx_api_key"],
            secret_key=self.config["okx_secret_key"],
            passphrase=self.config["okx_passphrase"],
            flag="1",
        )
        self.ds = DeepSeekAnalyzer(
            api_key=self.config["deepseek_api_key"],
            base_url=self.config["deepseek_base_url"],
            model="deepseek-chat",
        )
        self.notifier = TelegramNotifier(
            self.config["telegram_bot_token"],
            self.config["telegram_chat_id"],
        )
        self.logger = MonitorLogger(MONITOR_CONFIG["log_dir"])
        self.running = True
        self._install_signal_handlers()

    def _install_signal_handlers(self):
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        self.logger.log_line("STOP", f"Received signal {signum}, shutting down...")
        self.running = False

    def _fetch_klines(self) -> Optional[pd.DataFrame]:
        """拉 15m K 线"""
        try:
            df = self.client.get_candlesticks_history(
                inst_id=MONITOR_CONFIG["inst_id"],
                bar="15m",
                total_bars=MONITOR_CONFIG["bars_to_fetch"],
            )
            if df is None or df.empty:
                self.logger.log_line("PULL-EMPTY", "15m returned empty")
                return None
            self.logger.log_line("PULL", f"15m OK, {len(df)} bars, last_close=${float(df['close'].iloc[-1]):.2f}")
            return df
        except Exception as e:
            self.logger.log_line("PULL-ERR", f"15m failed: {e}")
            return None

    def run(self):
        """15 分钟循环: 拉数据 -> 算 3 个指标 -> 喂 AI -> 发通知"""
        self.logger.log_line("START", f"Monitor daemon started, session={self.logger.session_id}")
        self.notifier.send(
            f"<b>ETH 简化监控已启动</b>\n"
            f"会话: {self.logger.session_id}\n"
            f"每 15 分钟: 算 MA30 + 成交量 MA20 + ATR(14) → 喂 DeepSeek"
        )

        last_run_time = 0
        try:
            while self.running:
                now = time.time()
                if now - last_run_time < MONITOR_CONFIG["interval_sec"]:
                    time.sleep(5)
                    continue

                last_run_time = now
                cycle_start = time.time()
                self.logger.log_line("CYCLE", "start")

                # 1. 拉数据
                df = self._fetch_klines()
                if df is None:
                    self.logger.log_line("CYCLE-ERR", "no data, skip this cycle")
                    continue

                # 2. 算 3 个指标
                indicators = compute_three_indicators(df)
                self.logger.log_line(
                    "INDICATOR",
                    f"close={indicators['close']:.2f} "
                    f"ma30={indicators['ma30']:.2f} "
                    f"dir={indicators['direction']} "
                    f"vol_ratio={indicators['vol_ratio']:.2f}x "
                    f"vol_confirm={indicators['vol_confirm']} "
                    f"atr14={indicators['atr14']:.2f}"
                )

                # 3. 喂给 AI
                ai_reply = call_deepseek(indicators, self.ds)
                if ai_reply:
                    self.logger.log_line("DS", f"reply len={len(ai_reply)}")
                else:
                    self.logger.log_line("DS-ERR", "no reply")

                # 4. 发 Telegram
                send_report(self.notifier, indicators, ai_reply, self.logger)

                elapsed = time.time() - cycle_start
                self.logger.log_line("CYCLE", f"done elapsed={elapsed:.1f}s")

        except KeyboardInterrupt:
            self.logger.log_line("INTERRUPT", "Ctrl+C pressed")
        finally:
            self.logger.log_line("END", "Daemon stopped.")
            self.notifier.send("<b>ETH 监控已停止</b>")


if __name__ == "__main__":
    daemon = MonitorDaemon()
    daemon.run()
