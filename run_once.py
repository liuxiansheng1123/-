"""
CTREND 一次性运行脚本
完整流程:
1. 从 .env 加载所有凭证
2. 加载历史 K 线数据 (OKX)
3. 计算 28 个技术指标
4. 计算 CTREND 因子分数
5. DeepSeek AI 分析
6. Telegram 通知
7. (可选) 执行模拟/实盘交易

运行: py -3.13 run_once.py
"""

import sys
import os
import time
from pathlib import Path

# 强制 UTF-8 输出 (Windows GBK 控制台)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from datetime import datetime
from dotenv import load_dotenv

# 加载 .env
ENV_PATH = r"C:\Users\Administrator\Desktop\okx_ta_system\.env"
load_dotenv(ENV_PATH)

# 添加 src 到路径
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

import pandas as pd
import numpy as np

from config import TRADING_CONFIG, FACTOR_CONFIG, DATA_CONFIG
from okx_rest import OKXRestClient, OKXAPIError
from data_loader import DataLoader
from indicators import compute_all_indicators, INDICATOR_COLUMNS, cross_sectional_rank
from factor import quick_ctrend_score
from notifier import TelegramNotifier
from deepseek_analyzer import DeepSeekAnalyzer


def load_env_config():
    """从环境变量加载配置"""
    return {
        "okx_api_key": os.getenv("OKX_API_KEY"),
        "okx_passphrase": os.getenv("OKX_PASSPHRASE"),
        "okx_secret_key": os.getenv("OKX_SECRET_KEY"),  # 可选
        "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY"),
        "deepseek_base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
    }


def compute_ctrend_scores(instruments, data_loader, indicator_config):
    """计算 CTREND 分数"""
    print("\n" + "=" * 60)
    print("计算 CTREND 因子分数")
    print("=" * 60)

    # 加载面板数据
    panel = data_loader.load_panel(
        instruments=instruments,
        bar=DATA_CONFIG["bar"],
        total_bars=DATA_CONFIG["history_bars"]
    )

    if panel.empty:
        print("❌ 数据加载失败")
        return None

    print(f"✓ 加载了 {panel['inst_id'].nunique()} 个标的, {len(panel)} 条记录")

    # 计算指标
    panel_with_ind = []
    for inst_id, group in panel.groupby('inst_id'):
        group = group.sort_values('ts').reset_index(drop=True)
        try:
            group = compute_all_indicators(group, indicator_config)
            panel_with_ind.append(group)
        except Exception as e:
            print(f"  ⚠ {inst_id} 指标计算失败: {e}")

    panel = pd.concat(panel_with_ind, ignore_index=True)
    print(f"✓ 计算 28 个技术指标完成")

    # 计算收益
    panel['date'] = pd.to_datetime(panel['ts']).dt.tz_localize(None)
    panel = panel.sort_values(['inst_id', 'date']).reset_index(drop=True)
    panel['ret'] = panel.groupby('inst_id')['close'].pct_change()

    # 截面标准化
    panel = cross_sectional_rank(panel, INDICATOR_COLUMNS, date_col='date')
    print(f"✓ 截面标准化完成 (映射到 [-0.5, 0.5])")

    # 计算 CTREND 分数
    scores_df = quick_ctrend_score(panel, date_col='date')
    if scores_df.empty:
        print("❌ CTREND 分数计算失败")
        return None

    scores = dict(zip(scores_df['inst_id'], scores_df['ctrend_score']))

    print(f"\n✓ CTREND 分数计算完成:")
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    for i, (inst_id, score) in enumerate(ranked, 1):
        icon = "🟢" if i <= len(ranked) // 2 else "🔴"
        print(f"  {icon} {i:2d}. {inst_id:20s} CTREND = {score:+.4f}")

    return scores


def get_market_context(client, instruments):
    """获取市场上下文 (24h 涨跌, 总市值等)"""
    context = {}
    try:
        # 获取 BTC 数据作为基准
        if "BTC-USDT-SWAP" in instruments:
            btc_ticker = client.get_ticker("BTC-USDT-SWAP")
            if btc_ticker:
                last = float(btc_ticker.get("last", 0))
                open24h = float(btc_ticker.get("open24h", 0))
                if open24h > 0:
                    change_24h = (last - open24h) / open24h * 100
                else:
                    change_24h = 0.0
                high24h = btc_ticker.get("high24h", "N/A")
                low24h = btc_ticker.get("low24h", "N/A")
                vol24h = btc_ticker.get("vol24h", "N/A")
                context["BTC 24h涨跌"] = f"{change_24h:+.2f}%"
                context["BTC 最新价"] = f"${last:,.2f}"
                context["BTC 24h区间"] = f"${low24h} - ${high24h}"
                context["BTC 24h成交"] = f"{vol24h} 张"
    except Exception as e:
        print(f"获取市场上下文失败: {e}")
    return context


def main():
    print("=" * 70)
    print("CTREND 加密货币趋势因子交易系统 - 一次性运行")
    print("=" * 70)
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 1. 加载配置
    cfg = load_env_config()

    print("\n[Config Check]")
    print(f"  OKX API Key: {'[OK]' if cfg['okx_api_key'] else '[X]'} {(cfg['okx_api_key'][:8] + '...') if cfg['okx_api_key'] else 'N/A'}")
    print(f"  OKX Secret Key: {'[OK]' if cfg['okx_secret_key'] else '[X] (Read-Only Mode)'}")
    print(f"  OKX Passphrase: {'[OK]' if cfg['okx_passphrase'] else '[X]'}")
    print(f"  DeepSeek API: {'[OK]' if cfg['deepseek_api_key'] else '[X]'}")
    print(f"  Telegram Bot: {'[OK]' if cfg['telegram_bot_token'] else '[X]'}")
    print(f"  Telegram Chat: {'[OK]' if cfg['telegram_chat_id'] else '[X]'}")

    # 初始化模块
    notifier = None
    if cfg['telegram_bot_token'] and cfg['telegram_chat_id']:
        notifier = TelegramNotifier(cfg['telegram_bot_token'], cfg['telegram_chat_id'])
        notifier.send(f"🔔 CTREND 系统启动\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 2. 初始化 OKX 客户端
    flag = "1"  # 默认模拟盘 (用户没指定 secret key 时只能读行情)
    client = OKXRestClient(
        api_key=cfg['okx_api_key'] or "",
        secret_key=cfg['okx_secret_key'] or "",
        passphrase=cfg['okx_passphrase'] or "",
        flag=flag
    )

    # 3. 测试连接
    print("\n【测试 OKX 连接】")
    try:
        server_time = client.get_server_time()
        print(f"✓ OKX 服务器时间: {server_time}")
        if notifier:
            notifier.send(f"✅ OKX 连接成功\n服务器时间: {server_time}")
    except OKXAPIError as e:
        print(f"❌ OKX 连接失败: {e}")
        if notifier:
            notifier.send_error(f"OKX 连接失败: {e}")
        return 1

    # 4. 计算 CTREND 分数
    instruments = TRADING_CONFIG["instruments"]
    from config import INDICATOR_CONFIG
    data_loader = DataLoader(client)
    scores = compute_ctrend_scores(instruments, data_loader, INDICATOR_CONFIG)

    if not scores:
        if notifier:
            notifier.send_error("CTREND 分数计算失败")
        return 1

    # 5. 通知 CTREND 排名
    if notifier:
        notifier.send_ctrend_rankings(scores, top_n=5)

    # 6. DeepSeek AI 分析
    if cfg['deepseek_api_key']:
        print("\n" + "=" * 60)
        print("DeepSeek AI 分析中...")
        print("=" * 60)

        analyzer = DeepSeekAnalyzer(
            api_key=cfg['deepseek_api_key'],
            base_url=cfg['deepseek_base_url']
        )

        market_context = get_market_context(client, instruments)

        try:
            analysis = analyzer.analyze_ctrend_signals(scores, market_context)
            print(f"\n✓ DeepSeek 建议: {analysis['recommendation']}")
            print(f"  置信度: {analysis['confidence']}")
            print(f"\n【详细分析】\n{analysis['analysis']}")

            if notifier:
                notifier.send_deepseek_analysis(
                    scores=scores,
                    analysis=analysis['analysis'],
                    recommendation=analysis['recommendation']
                )
        except Exception as e:
            print(f"❌ DeepSeek 分析失败: {e}")
            if notifier:
                notifier.send_error(f"DeepSeek 分析失败: {e}")
    else:
        print("\n⚠ 未配置 DeepSeek API Key, 跳过 AI 分析")

    # 7. 总结
    print("\n" + "=" * 70)
    print("运行完成")
    print("=" * 70)
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if notifier:
        notifier.send(
            f"✅ <b>CTREND 预测完成</b>\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📊 监控 {len(scores)} 个标的\n"
            f"🎯 建议做多: {', '.join([s for s, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]])}\n"
            f"🎯 建议做空: {', '.join([s for s, _ in sorted(scores.items(), key=lambda x: x[1])[:3]])}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())