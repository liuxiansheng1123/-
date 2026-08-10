"""
ETH 单币种多因子回测引擎
- 拉取 ETH 500 根日 K 线
- 滚动运行 6 个因子 (每根 K 线都跑一次)
- 模拟交易, 统计胜率/收益/回撤
- 输出每个因子的交易明细 + 综合表现
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd
from dotenv import load_dotenv

# 加载 .env
ENV_PATH = r"C:\Users\Administrator\Desktop\okx_ta_system\.env"
load_dotenv(ENV_PATH)

# 添加 src 路径
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

from okx_rest import OKXRestClient, OKXAPIError
from factors import (
    run_all_factors,
    FactorSignal,
    factor_atr_dynamic,
    factor_turtle,
    factor_parabolic_sar,
    factor_macd_rsi_atr,
    factor_high_deviation_repair,
    factor_ml_atr,
)
from notifier import TelegramNotifier


def fetch_eth_history(client: OKXRestClient, total_bars: int = 500) -> pd.DataFrame:
    """拉取 ETH-USDT-SWAP 历史日线"""
    cache_path = Path(__file__).parent / "data" / "ETH_USDT_SWAP_1D.parquet"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # 检查缓存 (1 天内有效)
    import time
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < 86400:
        cached = pd.read_parquet(cache_path)
        if len(cached) >= total_bars:
            print(f"[OK] 使用缓存 ({len(cached)} 根)")
            return cached.tail(total_bars).reset_index(drop=True)

    print(f"[FETCH] 从 OKX 拉取 ETH-USDT-SWAP 1D 数据 ({total_bars} 根)...")
    df = client.get_candlesticks_history(
        inst_id="ETH-USDT-SWAP", bar="1D", total_bars=total_bars
    )

    if df.empty:
        raise RuntimeError("ETH 数据拉取失败")

    # 保存缓存
    df.to_parquet(cache_path)
    print(f"[OK] 拉取并缓存 {len(df)} 根, 时间: {df['ts'].iloc[0]} -> {df['ts'].iloc[-1]}")
    return df


def backtest_single_factor(df: pd.DataFrame,
                            factor_fn,
                            initial_capital: float = 10000.0,
                            position_pct: float = 0.95) -> dict:
    """
    单因子回测:
    - 滚动: 每根 K 线都用历史数据调用因子
    - 因子给出信号 → 次日开盘成交
    - 持仓直到: 止损/止盈/反向信号/最后一根
    """
    factor_name = factor_fn.__name__
    equity = initial_capital
    position = None  # {side, entry_price, size, sl, tp, bar_idx}
    trades = []
    equity_curve = []

    # 至少需要 50 根做样本
    start_bar = 50

    for i in range(start_bar, len(df)):
        bar = df.iloc[i]
        prev_bars = df.iloc[max(0, i - 200):i + 1]  # 用最近 200 根

        current_equity = equity
        if position is not None:
            # 计算持仓市值
            if position["side"] == "long":
                current_equity = equity * (bar["close"] / position["entry_price"]) \
                    if position["entry_price"] > 0 else equity
            else:
                current_equity = equity * (position["entry_price"] / bar["close"]) \
                    if bar["close"] > 0 else equity
        equity_curve.append({
            "ts": bar["ts"], "equity": current_equity, "position_side": position["side"] if position else None
        })

        # 1. 检查是否触发止损/止盈
        if position is not None:
            hit_sl = False
            hit_tp = False
            if position["side"] == "long":
                if bar["low"] <= position["sl"]:
                    hit_sl = True
                    exit_price = position["sl"]
                elif bar["high"] >= position["tp"]:
                    hit_tp = True
                    exit_price = position["tp"]
            else:
                if bar["high"] >= position["sl"]:
                    hit_sl = True
                    exit_price = position["sl"]
                elif bar["low"] <= position["tp"]:
                    hit_tp = True
                    exit_price = position["tp"]

            if hit_sl or hit_tp:
                pnl_pct = (exit_price - position["entry_price"]) / position["entry_price"]
                if position["side"] == "short":
                    pnl_pct = -pnl_pct
                pnl = equity * pnl_pct * position_pct
                equity += pnl
                trades.append({
                    "open_ts": position["open_ts"],
                    "close_ts": bar["ts"],
                    "side": position["side"],
                    "entry": position["entry_price"],
                    "exit": exit_price,
                    "pnl_pct": pnl_pct,
                    "pnl_usdt": pnl,
                    "exit_reason": "SL" if hit_sl else "TP",
                    "bars_held": i - position["bar_idx"]
                })
                position = None

        # 2. 当前 K 线因子信号 (基于截至 i-1 的数据)
        signal_bars = df.iloc[max(0, i - 200):i]
        if len(signal_bars) < 50:
            continue
        try:
            sig = factor_fn(signal_bars)
        except Exception as e:
            continue

        # 3. 处理信号
        if sig.side == "none":
            continue

        # 反向信号 → 平仓 + 反手
        if position is not None and sig.side != position["side"]:
            pnl_pct = (bar["close"] - position["entry_price"]) / position["entry_price"]
            if position["side"] == "short":
                pnl_pct = -pnl_pct
            pnl = equity * pnl_pct * position_pct
            equity += pnl
            trades.append({
                "open_ts": position["open_ts"],
                "close_ts": bar["ts"],
                "side": position["side"],
                "entry": position["entry_price"],
                "exit": bar["close"],
                "pnl_pct": pnl_pct,
                "pnl_usdt": pnl,
                "exit_reason": "REVERSE",
                "bars_held": i - position["bar_idx"]
            })
            position = None

        # 4. 开仓 (使用信号给出的价格作为参考, 实际以次日 open 成交 - 简化为当根 close)
        if position is None and sig.side in ("long", "short") and sig.confidence >= 0.3:
            entry_price = bar["close"]
            sl = sig.stop_loss if sig.stop_loss else entry_price * 0.95
            tp = sig.take_profit if sig.take_profit else entry_price * 1.05

            position = {
                "side": sig.side,
                "entry_price": entry_price,
                "sl": sl,
                "tp": tp,
                "open_ts": bar["ts"],
                "bar_idx": i,
            }

    # 最后一根强制平仓
    if position is not None:
        bar = df.iloc[-1]
        pnl_pct = (bar["close"] - position["entry_price"]) / position["entry_price"]
        if position["side"] == "short":
            pnl_pct = -pnl_pct
        pnl = equity * pnl_pct * position_pct
        equity += pnl
        trades.append({
            "open_ts": position["open_ts"],
            "close_ts": bar["ts"],
            "side": position["side"],
            "entry": position["entry_price"],
            "exit": bar["close"],
            "pnl_pct": pnl_pct,
            "pnl_usdt": pnl,
            "exit_reason": "END",
            "bars_held": len(df) - position["bar_idx"]
        })

    # 计算统计
    if not trades:
        return {
            "factor": factor_name,
            "total_trades": 0,
            "win_rate": 0,
            "total_return": 0,
            "max_drawdown": 0,
            "sharpe": 0,
            "trades": []
        }

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    pnls = [t["pnl_pct"] for t in trades]
    total_return = (equity / initial_capital - 1) * 100

    # 最大回撤
    eq_values = [e["equity"] for e in equity_curve]
    peak = eq_values[0]
    max_dd = 0
    for v in eq_values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe (年化)
    if len(pnls) > 1:
        sharpe = np.mean(pnls) / (np.std(pnls) + 1e-9) * np.sqrt(252)
    else:
        sharpe = 0

    return {
        "factor": factor_name,
        "total_trades": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades) * 100,
        "total_return_pct": total_return,
        "final_equity": equity,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "avg_pnl_pct": np.mean(pnls) * 100,
        "trades": trades[-10:],  # 最近 10 笔
    }


def get_current_factor_signals(df: pd.DataFrame) -> list:
    """用全部数据计算当前各因子的最新信号"""
    return run_all_factors(df)


def main():
    print("=" * 70)
    print("ETH 单币种 6 因子回测系统")
    print("=" * 70)

    # 加载配置
    cfg = {
        "okx_api_key": os.getenv("OKX_API_KEY", ""),
        "okx_secret_key": os.getenv("OKX_SECRET_KEY", ""),
        "okx_passphrase": os.getenv("OKX_PASSPHRASE", ""),
        "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "deepseek_base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    }

    notifier = None
    if cfg["telegram_bot_token"] and cfg["telegram_chat_id"]:
        notifier = TelegramNotifier(cfg["telegram_bot_token"], cfg["telegram_chat_id"])

    # OKX 客户端
    client = OKXRestClient(
        api_key=cfg["okx_api_key"],
        secret_key=cfg["okx_secret_key"],
        passphrase=cfg["okx_passphrase"],
        flag="1"
    )

    # 拉取 ETH 数据 (500 根)
    df = fetch_eth_history(client, total_bars=500)

    # 启动通知
    if notifier:
        notifier.send(
            f"<b>ETH 6 因子回测启动</b>\n\n"
            f"标的: ETH-USDT-SWAP\n"
            f"数据: {len(df)} 根日 K 线\n"
            f"时间: {df['ts'].iloc[0].strftime('%Y-%m-%d')} ~ {df['ts'].iloc[-1].strftime('%Y-%m-%d')}\n"
            f"最新价: ${df['close'].iloc[-1]:.2f}"
        )

    # 当前各因子信号 (基于全部数据)
    print("\n" + "=" * 70)
    print("当前各因子信号 (最新数据)")
    print("=" * 70)
    signals = get_current_factor_signals(df)

    last_close = float(df["close"].iloc[-1])
    print(f"ETH 最新价: ${last_close:.2f}")
    print(f"日期: {df['ts'].iloc[-1].strftime('%Y-%m-%d')}\n")

    print(f"{'因子':<25} {'方向':<6} {'买入':<10} {'止损':<10} {'止盈':<10} {'置信':<6}")
    print("-" * 80)
    for sig in signals:
        side = sig.side
        buy = sig.buy_price if sig.buy_price else "-"
        sell = sig.sell_price if sig.sell_price else "-"
        sl = sig.stop_loss if sig.stop_loss else "-"
        tp = sig.take_profit if sig.take_profit else "-"
        conf = f"{sig.confidence:.0%}"
        print(f"{sig.factor_name:<25} {side:<6} "
              f"{buy if isinstance(buy, str) else f'${buy:.2f}':<10} "
              f"{sl if isinstance(sl, str) else f'${sl:.2f}':<10} "
              f"{tp if isinstance(tp, str) else f'${tp:.2f}':<10} {conf:<6}")
        print(f"  理由: {sig.reason}")

    # 单因子回测
    print("\n" + "=" * 70)
    print("单因子历史回测 (500 根)")
    print("=" * 70)

    factor_fns = [
        factor_atr_dynamic,
        factor_turtle,
        factor_parabolic_sar,
        factor_macd_rsi_atr,
        factor_high_deviation_repair,
        factor_ml_atr,
    ]

    results = []
    for fn in factor_fns:
        print(f"\n回测 {fn.__name__}...")
        try:
            result = backtest_single_factor(df, fn, initial_capital=10000)
            results.append(result)
            print(f"  交易次数: {result['total_trades']}, "
                  f"胜率: {result.get('win_rate', 0):.1f}%, "
                  f"总收益: {result.get('total_return_pct', 0):.2f}%, "
                  f"最大回撤: {result.get('max_drawdown_pct', 0):.2f}%, "
                  f"Sharpe: {result.get('sharpe', 0):.2f}")
        except Exception as e:
            print(f"  回测失败: {e}")
            results.append({"factor": fn.__name__, "error": str(e)})

    # 输出汇总表
    print("\n" + "=" * 70)
    print("回测汇总")
    print("=" * 70)
    print(f"{'因子':<25} {'交易':<8} {'胜率':<8} {'收益%':<10} {'回撤%':<10} {'Sharpe':<8}")
    print("-" * 80)
    for r in results:
        if "error" in r:
            print(f"{r['factor']:<25} ERROR: {r['error']}")
            continue
        print(f"{r['factor']:<25} "
              f"{r['total_trades']:<8} "
              f"{r.get('win_rate', 0):<8.1f} "
              f"{r.get('total_return_pct', 0):<10.2f} "
              f"{r.get('max_drawdown_pct', 0):<10.2f} "
              f"{r.get('sharpe', 0):<8.2f}")

    # 保存到文件供 DeepSeek 分析
    output = {
        "inst_id": "ETH-USDT-SWAP",
        "data_range": f"{df['ts'].iloc[0]} ~ {df['ts'].iloc[-1]}",
        "total_bars": len(df),
        "last_close": last_close,
        "current_signals": [s.to_dict() for s in signals],
        "backtest_results": [{k: v for k, v in r.items() if k != "trades"} for r in results],
    }

    # 保存 JSON
    import json
    output_path = Path(__file__).parent / "data" / "eth_backtest_result.json"
    with open(output_path, "w", encoding="utf-8") as f:
        # 处理 datetime
        def default(o):
            if isinstance(o, (datetime, pd.Timestamp)):
                return o.isoformat()
            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, (np.floating,)):
                return float(o)
            return str(o)
        json.dump(output, f, ensure_ascii=False, indent=2, default=default)
    print(f"\n结果已保存: {output_path}")

    # Telegram 通知
    if notifier:
        lines = [
            f"<b>ETH 回测完成 ({len(df)} 根)</b>\n",
            f"最新价: ${last_close:.2f}",
            f"时间: {df['ts'].iloc[-1].strftime('%Y-%m-%d')}\n",
            f"<b>回测结果:</b>",
        ]
        for r in results:
            if "error" in r:
                lines.append(f"  {r['factor']}: ERROR")
                continue
            lines.append(
                f"  {r['factor']}: "
                f"交易{r['total_trades']}笔, "
                f"胜率{r.get('win_rate', 0):.1f}%, "
                f"收益{r.get('total_return_pct', 0):.2f}%, "
                f"Sharpe={r.get('sharpe', 0):.2f}"
            )
        notifier.send("\n".join(lines))

    return output


if __name__ == "__main__":
    main()