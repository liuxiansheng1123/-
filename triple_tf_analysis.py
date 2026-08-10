"""
ETH 三周期 (15分钟 / 4小时 / 1日) 多因子回测与 DeepSeek 综合判断
- 同时拉取三个周期的 K 线数据
- 运行全部 15 个因子（原始 6 个 + 新增 9 个）
- DeepSeek 综合三个周期的信号给出最终精准价位
- Telegram 仅发送最终判断（不含回测数据）
"""

import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
import json

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd
from dotenv import load_dotenv

ENV_PATH = r"C:\Users\Administrator\Desktop\okx_ta_system\.env"
load_dotenv(ENV_PATH)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from okx_rest import OKXRestClient
from factors import run_all_factors as run_original_factors
from new_factors import run_new_factors
from deepseek_analyzer import DeepSeekAnalyzer
from decision_engine import make_final_decision
from notifier import TelegramNotifier


# =============================================================
# OKX bar 格式转换
# =============================================================

BAR_MAP = {
    "15m": "15m",
    "1H":  "1H",
    "4H":  "4H",
    "1D":  "1D",
}

MAX_BARS = 300  # OKX 公共 API 单次上限


# =============================================================
# 数据获取
# =============================================================

def fetch_data(client: OKXRestClient,
               inst_id: str = "ETH-USDT-SWAP",
               bar: str = "1D",
               total: int = 300) -> pd.DataFrame:
    """拉取指定周期的 K 线数据"""
    cache_dir = ROOT / "data"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"ETH_{bar}_{total}.parquet"

    import time as _time
    if cache_file.exists() and (_time.time() - cache_file.stat().st_mtime) < 3600:
        df = pd.read_parquet(cache_file)
        print(f"  [OK] {bar} 缓存命中 ({len(df)} 根)")
        return df

    print(f"  [FETCH] {bar} 从 OKX 拉取 {total} 根...")
    df = client.get_candlesticks_history(inst_id=inst_id, bar=bar, total_bars=total)
    if not df.empty:
        df.to_parquet(cache_file)
        print(f"  [OK] {bar} 获取 {len(df)} 根: {df['ts'].iloc[0]} ~ {df['ts'].iloc[-1]}")
    return df


# =============================================================
# 聚合所有因子信号（原始 6 个 + 新增 9 个）
# =============================================================

def aggregate_signals(df: pd.DataFrame) -> Tuple[List[Dict], Dict]:
    """
    运行全部因子, 返回 (signals_list, summary_dict)
    """
    print(f"  计算因子信号 (共 {len(df)} 根数据)...")
    all_signals = []

    # 原始 6 个因子 (保留原始 time=None)
    orig = run_original_factors(df)
    for s in orig:
        d = s.to_dict()
        all_signals.append(d)

    # 新增 9 个因子 (保留原始 time=None)
    new_ = run_new_factors(df)
    for s in new_:
        d = s.to_dict()
        all_signals.append(d)

    # 统计 (兼容新旧字段)
    long_signals = [s for s in all_signals if s["side"] == "long" and s.get("confidence", 0) >= 0.3]
    short_signals = [s for s in all_signals if s["side"] == "short" and s.get("confidence", 0) >= 0.3]

    summary = {
        "total": len(all_signals),
        "long_count": len(long_signals),
        "short_count": len(short_signals),
        "none_count": len(all_signals) - len(long_signals) - len(short_signals),
    }
    return all_signals, summary


# =============================================================
# 按周期分组信号
# =============================================================

def group_signals_by_timeframe(df_15m: pd.DataFrame,
                                df_4H: pd.DataFrame,
                                df_1D: pd.DataFrame) -> Dict[str, List[Dict]]:
    """
    对三个周期分别运行因子, 返回各周期的信号列表
    """
    result = {}
    for label, df in [("15m", df_15m), ("4H", df_4H), ("1D", df_1D)]:
        if df.empty or len(df) < 50:
            print(f"  [SKIP] {label}: 数据不足")
            result[label] = []
            continue
        signals, summary = aggregate_signals(df)
        result[label] = signals
        print(f"  [{label}] 总信号: {summary['total']}, "
              f"多头:{summary['long_count']}, 空头:{summary['short_count']}, "
              f"观望:{summary['none_count']}")
    return result


# =============================================================
# 汇总所有信号（扁平化）
# =============================================================

def flatten_all_signals(tf_signals: Dict[str, List[Dict]]) -> List[Dict]:
    """将三周期的信号扁平化到单一列表"""
    all_sig = []
    for tf, signals in tf_signals.items():
        for sig in signals:
            sig_copy = sig.copy()
            sig_copy["timeframe"] = tf
            all_sig.append(sig_copy)
    return all_sig


# =============================================================
# 主函数
# =============================================================

def main():
    print("=" * 70)
    print("ETH 三周期 (15m/4H/1D) 多因子 DeepSeek 综合判断系统")
    print("=" * 70)

    # 配置
    cfg = {
        "okx_api_key": os.getenv("OKX_API_KEY", ""),
        "okx_secret_key": os.getenv("OKX_SECRET_KEY", ""),
        "okx_passphrase": os.getenv("OKX_PASSPHRASE", ""),
        "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "deepseek_base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    }

    # OKX 客户端
    client = OKXRestClient(
        api_key=cfg["okx_api_key"],
        secret_key=cfg["okx_secret_key"],
        passphrase=cfg["okx_passphrase"],
        flag="1"
    )

    # ---- 1. 拉取三周期数据 ----
    print("\n[1] 拉取 ETH-USDT-SWAP 数据...")
    df_15m = fetch_data(client, bar="15m", total=MAX_BARS)
    df_4H  = fetch_data(client, bar="4H",  total=MAX_BARS)
    df_1D  = fetch_data(client, bar="1D",  total=MAX_BARS)

    last_price_1d = float(df_1D["close"].iloc[-1]) if not df_1D.empty else 0
    last_price_4h = float(df_4H["close"].iloc[-1]) if not df_4H.empty else last_price_1d
    last_price_15m = float(df_15m["close"].iloc[-1]) if not df_15m.empty else last_price_4h

    print(f"\n[2] 当前价格 (三周期最新收盘价):")
    print(f"  1D:  ${last_price_1d:.2f}")
    print(f"  4H:  ${last_price_4h:.2f}")
    print(f"  15m: ${last_price_15m:.2f}")

    # ---- 2. 分周期运行因子 ----
    print("\n[3] 运行全部 15 个因子 (三周期)...")
    tf_signals = group_signals_by_timeframe(df_15m, df_4H, df_1D)

    # ---- 3. 扁平化所有信号 ----
    all_signals = flatten_all_signals(tf_signals)
    print(f"\n  共收集 {len(all_signals)} 个因子信号")

    # 统计
    long_sigs = [s for s in all_signals if s["side"] == "long" and s.get("confidence", 0) >= 0.3]
    short_sigs = [s for s in all_signals if s["side"] == "short" and s.get("confidence", 0) >= 0.3]

    print(f"\n[4] 信号汇总:")
    print(f"  多头信号: {len(long_sigs)}")
    print(f"  空头信号: {len(short_sigs)}")
    print(f"  观望信号: {len(all_signals) - len(long_sigs) - len(short_sigs)}")

    # 按置信度排序多头/空头
    long_sigs.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    short_sigs.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    print(f"\n  TOP 多头信号:")
    for s in long_sigs[:5]:
        entry_type = s.get("entry_type", "market")
        ep = s.get("entry_price")
        gap = s.get("gap_to_entry_pct", 0)
        print(f"    [{s['timeframe']}] {s['factor_name']}: {entry_type}, "
              f"入场=${ep:.2f} (gap={gap:.2f}%), "
              f"SL=${s.get('stop_loss')}, TP=${s.get('take_profit')}, "
              f"置信={s.get('confidence',0):.0%}")

    print(f"\n  TOP 空头信号:")
    for s in short_sigs[:5]:
        entry_type = s.get("entry_type", "market")
        ep = s.get("entry_price")
        gap = s.get("gap_to_entry_pct", 0)
        print(f"    [{s['timeframe']}] {s['factor_name']}: {entry_type}, "
              f"入场=${ep:.2f} (gap={gap:.2f}%), "
              f"SL=${s.get('stop_loss')}, TP=${s.get('take_profit')}, "
              f"置信={s.get('confidence',0):.0%}")

    # ---- 4. 主决策引擎 (单周期主导, 避免错位拼接) ----
    print("\n[4] 主决策引擎 (锁定主导周期+主因子, 严防跨周期拼接)...")
    primary_decision = make_final_decision(tf_signals)
    p = primary_decision
    print(f"  主导周期: {p.get('source_timeframe')}")
    print(f"  主因子: {p.get('source_factor')}")
    print(f"  建议: {p.get('recommendation')}")
    print(f"  入场价 (原始硬约束): ${p.get('entry_price')}")
    print(f"  止损价 (原始硬约束): ${p.get('stop_loss')}")
    print(f"  止盈价 (原始硬约束): ${p.get('take_profit')}")
    print(f"  当前价: ${p.get('current_price')}")
    print(f"  距入场 (数学重算): {p.get('gap_to_entry_pct')}%")
    print(f"  风险回报: 1:{p.get('risk_reward_ratio')}")

    # ---- 5. DeepSeek 综合判断 (在主决策基础上做 ±2% 微调) ----
    print("\n[5] 调用 DeepSeek 做主决策约束下的微调 (禁止跨周期拼接)...")
    analyzer = DeepSeekAnalyzer(
        api_key=cfg["deepseek_api_key"],
        base_url=cfg["deepseek_base_url"],
        model="deepseek-v4-flash"
    )

    market_ctx = {
        "ETH 最新价 (1D)": f"${last_price_1d:.2f}",
        "ETH 最新价 (4H)": f"${last_price_4h:.2f}",
        "ETH 最新价 (15m)": f"${last_price_15m:.2f}",
        "1D K线数": str(len(df_1D)),
        "4H K线数": str(len(df_4H)),
        "15m K线数": str(len(df_15m)),
    }

    final_decision = analyzer.analyze_multi_factor_signals(
        factor_signals=all_signals,
        backtest_results=None,  # 不发回测数据给 DeepSeek
        market_context=market_ctx,
        primary_decision=primary_decision,  # 硬约束
    )

    print("\n" + "=" * 70)
    print("最终决策 (主决策约束 + DeepSeek 微调)")
    print("=" * 70)
    print(f"  建议: {final_decision.get('recommendation')}")
    print(f"  置信度: {final_decision.get('confidence')}")
    print(f"  入场方式: {final_decision.get('entry_type')}")
    print(f"  入场价 (挂单): {final_decision.get('entry_price')}")
    print(f"  卖出价: {final_decision.get('sell_price')}")
    print(f"  止损价: {final_decision.get('stop_loss')}")
    print(f"  止盈价: {final_decision.get('take_profit')}")
    print(f"  失效价: {final_decision.get('invalidation_price')}")
    print(f"  当前价: {final_decision.get('current_price')}")
    print(f"  距入场 (数学重算): {final_decision.get('gap_to_entry_pct')}%")
    print(f"  风险回报: 1:{final_decision.get('risk_reward_ratio')}")
    print(f"  关键因子: {final_decision.get('key_factors')}")
    print(f"  止损逻辑: {final_decision.get('stop_loss_logic')}")
    print(f"  止盈逻辑: {final_decision.get('take_profit_logic')}")
    print(f"  分析: {final_decision.get('analysis', '')[:300]}")

    # ---- 5. 保存结果 ----
    output = {
        "generated_at": datetime.now().isoformat(),
        "inst_id": "ETH-USDT-SWAP",
        "last_prices": {
            "1D": last_price_1d,
            "4H": last_price_4h,
            "15m": last_price_15m,
        },
        "bar_counts": {
            "1D": len(df_1D),
            "4H": len(df_4H),
            "15m": len(df_15m),
        },
        "signal_summary": {
            "total": len(all_signals),
            "long_count": len(long_sigs),
            "short_count": len(short_sigs),
        },
        "all_signals": all_signals,
        "deepseek_decision": {k: v for k, v in final_decision.items()
                               if k != "raw_response"},
    }

    out_path = ROOT / "data" / "eth_triple_tf_final.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n已保存: {out_path}")

    # ---- 6. Telegram 通知（仅最终价位，不含回测）----
    if cfg["telegram_bot_token"] and cfg["telegram_chat_id"]:
        notifier = TelegramNotifier(cfg["telegram_bot_token"], cfg["telegram_chat_id"])

        rec = final_decision.get("recommendation", "?")
        conf = final_decision.get("confidence", "?")
        bp = final_decision.get("entry_price")  # 主入场价 (DeepSeek 决策)
        bp_alt = final_decision.get("buy_price")  # 兼容字段
        if bp is None: bp = bp_alt
        sp = final_decision.get("sell_price")
        if sp is None: sp = final_decision.get("take_profit")
        sl = final_decision.get("stop_loss")
        tp = final_decision.get("take_profit")
        inv = final_decision.get("invalidation_price")
        cur = final_decision.get("current_price")
        if cur is None: cur = last_price_1d
        gap = final_decision.get("gap_to_entry_pct")

        # 🔒 终极数学自检: gap 必须从 entry_price 重算, 否则以数学为准
        if bp is not None and cur is not None:
            gap_math = (bp - cur) / cur * 100
            if gap is None or abs(gap - gap_math) > 0.5:
                gap = gap_math
        rr = final_decision.get("risk_reward_ratio")
        if rr is None and bp and sl and tp:
            risk = abs(bp - sl)
            reward = abs(tp - bp)
            rr = reward / risk if risk > 0 else 0
        kf = final_decision.get("key_factors", [])
        analysis = final_decision.get("analysis", "")
        sl_logic = final_decision.get("stop_loss_logic", "")
        tp_logic = final_decision.get("take_profit_logic", "")
        src_tf = final_decision.get("source_timeframe", primary_decision.get("source_timeframe", "?"))
        src_factor = final_decision.get("source_factor", primary_decision.get("source_factor", "?"))

        def f2(x):
            return f"${x:.2f}" if isinstance(x, (int, float)) else "-"

        # 多空信号汇总
        tf_summary_lines = []
        for tf in ["1D", "4H", "15m"]:
            sigs = tf_signals.get(tf, [])
            long_c = sum(1 for s in sigs if s["side"] == "long" and s.get("confidence", 0) >= 0.3)
            short_c = sum(1 for s in sigs if s["side"] == "short" and s.get("confidence", 0) >= 0.3)
            tf_summary_lines.append(f"  {tf}: 多头{long_c}个 / 空头{short_c}个 / 观望{len(sigs)-long_c-short_c}个")

        # TOP 信号
        entry_type_zh = {
            "limit_pullback": "限价回调",
            "limit_breakout": "限价突破",
            "limit_value": "价值区挂单",
            "limit_sar": "SAR反转挂单",
            "market": "市价",
        }
        top_lines = []
        for s in (long_sigs + short_sigs)[:6]:
            tf = s.get("timeframe", "?")
            side = {"long": "多", "short": "空"}.get(s.get("side", ""), "?")
            name = s.get("factor_name", "?")
            conf = s.get("confidence", 0)
            et = s.get("entry_type", "market")
            ep = s.get("entry_price")
            cp = s.get("current_price")
            gap = s.get("gap_to_entry_pct", 0)
            sl = s.get("stop_loss")
            tp = s.get("take_profit")
            et_zh = entry_type_zh.get(et, et)
            cp_str = f"${cp:.2f}" if cp else "-"
            gap_str = f"({gap:+.2f}%)" if ep and cp else ""
            top_lines.append(
                f"  [{tf}] {name} ({side}/{et_zh}): "
                f"入场{f2(ep)} {gap_str} 现{cp_str}, "
                f"损{f2(sl)}, 盈{f2(tp)}, 置信={conf:.0%}"
            )

        # DeepSeek 决定的入场方式中文
        ds_et = final_decision.get("entry_type", "market")
        ds_et_zh = entry_type_zh.get(ds_et, ds_et)

        msg = f"""<b>ETH 15m / 4H / 1D 多因子最终决策 (单周期主导, 无拼接)</b>

<b>基础信息</b>
  标的: ETH-USDT-SWAP
  1D 最新价: {f2(last_price_1d)}
  4H 最新价: {f2(last_price_4h)}
  15m 最新价: {f2(last_price_15m)}
  总因子数: {len(all_signals)} 个 (6 原始 + 9 新增) / 三周期

<b>各周期信号分布</b>
{chr(10).join(tf_summary_lines)}

<b>高置信 TOP 信号 (仅供参考, 不是最终决策)</b>
{chr(10).join(top_lines)}

<b>━━━━━━━━━━━━━━━━━━</b>
<b>主决策约束 (锁定, 严防拼接)</b>
  主导周期: <b>{src_tf}</b>
  主因子: <b>{src_factor}</b>
  主决策入场价: <code>{f2(primary_decision.get('entry_price'))}</code>
  主决策止损价: <code>{f2(primary_decision.get('stop_loss'))}</code>
  主决策止盈价: <code>{f2(primary_decision.get('take_profit'))}</code>
  主决策 RR: 1:{primary_decision.get('risk_reward_ratio', 'N/A')}

<b>━━━━━━━━━━━━━━━━━━</b>
<b>最终决策 (主因子已锁定, DeepSeek 仅做微调)</b>
  建议: <b>{rec}</b>
  置信度: {conf}
  入场方式: <b>{ds_et_zh}</b>
  <b>入场价 (挂单):</b> <code>{f2(bp)}</code>
  <b>卖出价 (止盈):</b> <code>{f2(sp)}</code>
  <b>止损价:</b> <code>{f2(sl)}</code> ({sl_logic[:60] if sl_logic else "-"})
  <b>止盈价:</b> <code>{f2(tp)}</code> ({tp_logic[:60] if tp_logic else "-"})
  失效价: {f2(inv)}
  当前价: {f2(cur)}
  距入场 (数学重算): <code>{gap:.2f}%</code>
  风险回报: <b>1:{rr if rr else '-'}</b>
  关键因子: {', '.join(kf) if kf else '-'}

<b>分析</b>
  {analysis}"""

        notifier.send(msg)
        print("\n[Telegram] 已发送最终判断通知")
    else:
        print("\n[Telegram] 未配置 bot_token / chat_id, 跳过通知")

    return output


if __name__ == "__main__":
    main()