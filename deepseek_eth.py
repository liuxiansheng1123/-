"""
ETH 6 因子 + DeepSeek 综合判断 + Telegram 通知
- 加载 backtest_eth.py 生成的 json 结果
- 调用 DeepSeek 给出最终精准价位
- 发送 Telegram
"""

import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import sys
import json
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

ENV_PATH = r"C:\Users\Administrator\Desktop\okx_ta_system\.env"
load_dotenv(ENV_PATH)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from deepseek_analyzer import DeepSeekAnalyzer
from notifier import TelegramNotifier


def main():
    result_path = ROOT / "data" / "eth_backtest_result.json"
    if not result_path.exists():
        print(f"未找到 {result_path}, 请先运行 backtest_eth.py")
        return

    with open(result_path, "r", encoding="utf-8") as f:
        bt_result = json.load(f)

    print("=" * 70)
    print("DeepSeek 综合判断")
    print("=" * 70)
    print(f"标的: {bt_result['inst_id']}")
    print(f"数据: {bt_result['total_bars']} 根, 最近价: ${bt_result['last_close']:.2f}")
    print(f"时间: {bt_result['data_range']}")

    # 配置
    cfg = {
        "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "deepseek_base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    }

    analyzer = DeepSeekAnalyzer(cfg["deepseek_api_key"], cfg["deepseek_base_url"])

    print("\n调用 DeepSeek 进行多因子综合判断...")
    market_ctx = {
        "最新价": f"${bt_result['last_close']:.2f}",
        "数据范围": bt_result["data_range"],
        "K 线数": bt_result["total_bars"],
    }

    final = analyzer.analyze_multi_factor_signals(
        factor_signals=bt_result["current_signals"],
        backtest_results=bt_result["backtest_results"],
        market_context=market_ctx,
    )

    print("\n" + "=" * 70)
    print("DeepSeek 最终判断")
    print("=" * 70)
    print(f"建议: {final.get('recommendation')}")
    print(f"置信度: {final.get('confidence')}")
    print(f"\n  买入价: {final.get('buy_price')}")
    print(f"  卖出价: {final.get('sell_price')}")
    print(f"  止损价: {final.get('stop_loss')}")
    print(f"  止盈价: {final.get('take_profit')}")
    print(f"\n关键因子: {final.get('key_factors')}")
    print(f"风险回报: {final.get('risk_reward_ratio')}")
    print(f"\n分析:\n  {final.get('analysis')}")

    # 保存
    deepseek_out = ROOT / "data" / "eth_deepseek_final.json"
    with open(deepseek_out, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n已保存: {deepseek_out}")

    # Telegram 通知
    if cfg["telegram_bot_token"] and cfg["telegram_chat_id"]:
        notifier = TelegramNotifier(cfg["telegram_bot_token"], cfg["telegram_chat_id"])

        # 单因子信号明细
        factor_lines = []
        for sig in bt_result["current_signals"]:
            name = sig.get("factor_name", "?")
            side = sig.get("side", "?")
            conf = sig.get("confidence", 0)
            bp = sig.get("buy_price")
            sl = sig.get("stop_loss")
            tp = sig.get("take_profit")
            symbol = {"long": "多头", "short": "空头", "none": "观望"}.get(side, side)
            if bp is not None:
                bp = f"${bp:.2f}"
            if sl is not None:
                sl = f"${sl:.2f}"
            if tp is not None:
                tp = f"${tp:.2f}"
            factor_lines.append(f"  - {name}: {symbol} (买={bp}, 损={sl}, 盈={tp}, 置信={conf:.0%})")

        # 回测
        bt_lines = []
        for r in bt_result["backtest_results"]:
            if "error" in r:
                continue
            bt_lines.append(
                f"  - {r['factor']}: {r['total_trades']}笔, "
                f"胜率{r.get('win_rate', 0):.1f}%, "
                f"收益{r.get('total_return_pct', 0):.2f}%, "
                f"Sharpe={r.get('sharpe', 0):.2f}"
            )

        # 终极建议
        rec = final.get("recommendation", "?")
        conf = final.get("confidence", "?")
        bp = final.get("buy_price")
        sp = final.get("sell_price")
        sl = final.get("stop_loss")
        tp = final.get("take_profit")
        rr = final.get("risk_reward_ratio")
        key_factors = final.get("key_factors", [])
        analysis = final.get("analysis", "")

        def f2(x):
            return f"${x:.2f}" if isinstance(x, (int, float)) else "-"

        msg = f"""<b>ETH 6 因子 + DeepSeek 最终判断</b>

<b>基础信息</b>
  标的: {bt_result['inst_id']}
  最新价: ${bt_result['last_close']:.2f}
  时间: {bt_result['data_range']}
  K 线数: {bt_result['total_bars']}

<b>各因子信号</b>
{chr(10).join(factor_lines)}

<b>历史回测 (500 根)</b>
{chr(10).join(bt_lines)}

<b>DEEPSEEK 最终判断</b>
  建议: {rec}
  置信度: {conf}
  买入价: {f2(bp)}
  卖出价: {f2(sp)}
  止损价: {f2(sl)}
  止盈价: {f2(tp)}
  风险回报: 1:{rr if rr else '-'}
  关键因子: {', '.join(key_factors) if key_factors else '-'}

<b>分析</b>
  {analysis}"""

        notifier.send(msg)
        print("\n[Telegram] 已发送通知")

    return final


if __name__ == "__main__":
    main()