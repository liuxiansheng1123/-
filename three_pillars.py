"""
三件套决策 - 一次性运行脚本
==========================

完整流程:
1. 拉取 ETH 三周期 K 线 (15m / 4H / 1D)
2. 计算三件套: MA30 (方向) + OBV (真伪) + ATR (生存)
3. 把三件套喂给 DeepSeek, 让它输出精准的 入场/止损/止盈 三档价位
4. Telegram 通知

设计原则:
- 单标的 (默认 ETH-USDT-SWAP), 简化版的"方向/真伪/生存"框架
- 多周期 MA30 共振判定
- OBV 斜率交叉验证 (防骗炮)
- ATR 只算生存空间, SL/TP 倍数全交给 DeepSeek 决定
- DeepSeek 失败时自动 fallback 到规则生成保守价位

运行: py -3.13 three_pillars.py
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

# 强制 UTF-8 输出
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv
import pandas as pd

ENV_PATH = r"C:\Users\Administrator\Desktop\okx_ta_system\.env"
load_dotenv(ENV_PATH)

ROOT = Path(__file__).parent
SRC_DIR = ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from okx_rest import OKXRestClient
from decision_engine import compute_three_pillars
from deepseek_analyzer import DeepSeekAnalyzer
from notifier import TelegramNotifier


# 配置
CONFIG = {
    "inst_id": "ETH-USDT-SWAP",
    "bars_per_tf": {
        "15m": 300,
        "4H": 200,
        "1D": 100,
    },
    "okx_flag": "1",  # 默认模拟盘
}


def load_env_config() -> dict:
    return {
        "okx_api_key": os.getenv("OKX_API_KEY", ""),
        "okx_passphrase": os.getenv("OKX_PASSPHRASE", ""),
        "okx_secret_key": os.getenv("OKX_SECRET_KEY", ""),
        "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "deepseek_base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    }


def fetch_three_tf_klines(client: OKXRestClient, inst_id: str,
                          bars_per_tf: dict) -> dict:
    """拉 15m / 4H / 1D 三套 K 线"""
    print("\n" + "=" * 60)
    print(f"拉取 {inst_id} 三周期 K 线")
    print("=" * 60)

    result = {}
    for tf, n in bars_per_tf.items():
        try:
            df = client.get_candlesticks_history(
                inst_id=inst_id, bar=tf, total_bars=n
            )
            if df is None or df.empty:
                print(f"  ❌ {tf} 拉取失败 (空数据)")
                return {}
            print(f"  ✓ {tf:4s} {len(df):3d} 根  最新 ${float(df['close'].iloc[-1]):.2f}")
            result[tf] = df
        except Exception as e:
            print(f"  ❌ {tf} 拉取异常: {e}")
            return {}

    return result


def print_three_pillars(pillars: dict):
    """打印三件套结果"""
    print("\n" + "=" * 60)
    print(f"【三件套】{pillars['inst_id']}  @  ${pillars['current_price']:.2f}")
    print("=" * 60)

    # MA30
    m = pillars['ma30']
    print(f"\n[1] MA30 方向  →  {m['direction']}  (确信度: {m['conviction']})")
    print(f"    理由: {m['reason']}")
    for tf, data in m['per_tf'].items():
        print(f"    {tf:4s}: close=${data['close']:>9.2f}  "
              f"MA30=${data['ma30']:>9.2f}  "
              f"diff={data['diff_pct']:+5.2f}%  "
              f"slope={data['slope_pct']:+6.3f}%")

    # OBV
    o = pillars['obv']
    print(f"\n[2] OBV 真伪  →  {o['verdict']}  (is_real={o['is_real']})")
    print(f"    理由: {o['reason']}")
    for tf, data in o['per_tf'].items():
        print(f"    {tf:4s}: OBV={data['obv_current']:>12.0f}  "
              f"slope_norm={data['obv_slope_norm']:+5.3f}  "
              f"strength={data['confirm_strength']}")

    # ATR
    a = pillars['atr']
    print(f"\n[3] ATR 生存  →  4H ATR = ${a['atr_4h']:.2f}  ({a['atr_4h_pct']:.2f}%)")
    print(f"    15m ATR = ${a['atr_15m']:.2f}   1D ATR = ${a['atr_1d']:.2f}")

    print(f"\n[综合置信度] {pillars['confidence']}    [建议方向] {pillars['recommendation']}")


def print_deepseek_result(ds: dict):
    """打印 DeepSeek 给的精准价位"""
    print("\n" + "=" * 60)
    print("【DeepSeek 精准价位】")
    print("=" * 60)
    print(f"  建议:   {ds.get('recommendation', '?')}")
    print(f"  置信度: {ds.get('confidence', '?')}")
    print(f"  类型:   {ds.get('entry_type', '?')}")
    print(f"  入场:   ${ds.get('entry_price', '?')}")
    print(f"  止损:   ${ds.get('stop_loss', '?')}")
    print(f"  止盈:   ${ds.get('take_profit', '?')}")
    print(f"  失效:   ${ds.get('invalidation_price', '?')}")
    print(f"  偏离:   {ds.get('gap_to_entry_pct', '?')}%")
    print(f"  盈亏比: 1:{ds.get('risk_reward_ratio', '?')}")
    print(f"  ATR倍数: SL={ds.get('atr_multiplier_sl', '?')}x, TP={ds.get('atr_multiplier_tp', '?')}x")
    print(f"\n  分析: {ds.get('analysis', '-')}")
    print(f"  SL逻辑: {ds.get('stop_loss_logic', '-')}")
    print(f"  TP逻辑: {ds.get('take_profit_logic', '-')}")

    if ds.get('_fallback'):
        print(f"\n  ⚠ 这是规则兜底结果 (DeepSeek 失败: {ds.get('_error', '?')[:100]})")


def format_telegram_report(pillars: dict, ds: dict) -> str:
    """格式化 Telegram 通知"""
    m = pillars['ma30']
    o = pillars['obv']
    a = pillars['atr']

    lines = [
        f"<b>三件套决策 [{datetime.now().strftime('%H:%M')}]</b>",
        f"<b>{pillars['inst_id']}</b> @ ${pillars['current_price']:.2f}",
        "",
        f"<b>[1] MA30 方向</b>: {m['direction']} (确信度 {m['conviction']})",
        f"  {m['reason'][:120]}",
        "",
        f"<b>[2] OBV 真伪</b>: {o['verdict']} (is_real={o['is_real']})",
        f"  {o['reason'][:120]}",
        "",
        f"<b>[3] ATR 生存</b>: 4H ATR=${a['atr_4h']:.2f} ({a['atr_4h_pct']:.2f}%)",
        "",
        "────────── DeepSeek 精准价位 ──────────",
        f"<b>建议</b>: {ds.get('recommendation', '?')}",
        f"<b>置信度</b>: {ds.get('confidence', '?')}",
        f"<b>入场</b>: ${ds.get('entry_price', '?')}  ({ds.get('entry_type', '?')})",
        f"<b>止损</b>: ${ds.get('stop_loss', '?')}",
        f"<b>止盈</b>: ${ds.get('take_profit', '?')}",
        f"<b>盈亏比</b>: 1:{ds.get('risk_reward_ratio', '?')}",
        f"<b>ATR倍数</b>: SL={ds.get('atr_multiplier_sl', '?')}x / TP={ds.get('atr_multiplier_tp', '?')}x",
        "",
        f"<b>分析</b>: {ds.get('analysis', '-')[:300]}",
    ]
    if ds.get('_fallback'):
        lines.append("\n⚠ 规则兜底 (DeepSeek 失败)")

    return "\n".join(lines)


def main():
    print("=" * 70)
    print("三件套决策系统 - MA30 (方向) + OBV (真伪) + ATR (生存)")
    print("=" * 70)
    print(f"启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    cfg = load_env_config()
    print(f"\n[配置]")
    print(f"  OKX: {'[OK]' if cfg['okx_api_key'] else '[X]'}")
    print(f"  DeepSeek: {'[OK]' if cfg['deepseek_api_key'] else '[X]'}")
    print(f"  Telegram: {'[OK]' if cfg['telegram_bot_token'] else '[X]'}")

    notifier = None
    if cfg['telegram_bot_token'] and cfg['telegram_chat_id']:
        notifier = TelegramNotifier(cfg['telegram_bot_token'], cfg['telegram_chat_id'])

    # 1. OKX 客户端
    client = OKXRestClient(
        api_key=cfg['okx_api_key'] or "",
        secret_key=cfg['okx_secret_key'] or "",
        passphrase=cfg['okx_passphrase'] or "",
        flag=CONFIG['okx_flag'],
    )

    print("\n[测试 OKX 连接]")
    try:
        server_time = client.get_server_time()
        print(f"  ✓ {server_time}")
    except Exception as e:
        print(f"  ❌ {e}")
        return 1

    # 2. 拉 K 线
    inst_id = CONFIG['inst_id']
    per_tf = fetch_three_tf_klines(client, inst_id, CONFIG['bars_per_tf'])
    if not per_tf:
        print("❌ K 线拉取失败")
        return 1

    # 3. 算三件套
    print("\n[计算三件套]")
    pillars = compute_three_pillars(inst_id, per_tf)
    print_three_pillars(pillars)

    # 4. DeepSeek 分析
    if not cfg['deepseek_api_key']:
        print("\n⚠ 未配置 DeepSeek API Key, 跳过 AI 分析")
        return 0

    print("\n[调用 DeepSeek 输出精准价位...]")
    analyzer = DeepSeekAnalyzer(
        api_key=cfg['deepseek_api_key'],
        base_url=cfg['deepseek_base_url'],
    )

    ds_result = analyzer.analyze_three_pillars(inst_id, pillars)
    print_deepseek_result(ds_result)

    # 5. Telegram 通知
    if notifier:
        try:
            notifier.send(format_telegram_report(pillars, ds_result))
            print("\n✓ Telegram 通知已发送")
        except Exception as e:
            print(f"\n❌ Telegram 通知失败: {e}")

    # 6. 保存结果到 JSON (供后续回溯)
    output_dir = ROOT / "data" / "three_pillars"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "inst_id": inst_id,
            "three_pillars": pillars,
            "deepseek": ds_result,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✓ 结果已保存: {output_file}")

    print("\n" + "=" * 70)
    print("完成")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
