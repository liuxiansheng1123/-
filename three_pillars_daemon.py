"""
三件套决策守护进程 - 持续监控, 每 15 分钟一轮
================================================

设计目标:
  - 持续运行, 永不停 (Ctrl+C 才停)
  - 每 15 分钟 (可配) 自动跑一次:
    1. 拉取 三周期 K 线 (15m / 4H / 1D)
    2. 算三件套 (MA30 方向 + OBV 真伪 + ATR 生存)
    3. 喂给 DeepSeek 出精准价位
    4. Telegram 通知
    5. 保存到 JSON 供回溯
  - 任何一步失败都继续运行, 不崩
  - 全程日志审计 (logs/three_pillars_daemon_*.log)
  - Windows / Linux / Mac 通用 (用 signal 而非 Win32 API)

启动:
  py -3.13 three_pillars_daemon.py

停止:
  Ctrl+C (SIGINT) 或任务管理器结束进程
"""

import os
import sys
import time
import json
import signal
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

# 强制 UTF-8 输出 (Windows GBK 控制台)
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
from decision_engine import (
    compute_three_pillars,
    resolve_final_prices,
    resolve_position_by_context,
    adjust_position_by_confidence,
)
from deepseek_analyzer import DeepSeekAnalyzer
from deepseek_feeder import DeepSeekFeeder
from notifier import TelegramNotifier


# =============================================================
# 配置
# =============================================================

DAEMON_CONFIG = {
    "interval_sec": 900,             # 15 分钟一轮
    "inst_id": "ETH-USDT-SWAP",      # 监控标的 (可改为 BTC-USDT-SWAP 等)
    "bars_per_tf": {                 # 各周期拉多少根
        "15m": 300,
        "4H": 200,
        "1D": 100,
    },
    "okx_flag": "1",                 # 1=模拟盘, 0=实盘
    "log_dir": ROOT / "data" / "monitor",
    "output_dir": ROOT / "data" / "three_pillars",
    "sleep_step_sec": 5,             # 循环 sleep 步长, 减少 CPU
    "save_to_json": True,            # 每轮结果落盘
}


# =============================================================
# 日志 (每轮一行, 易回溯)
# =============================================================

class DaemonLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = log_dir / f"three_pillars_daemon_{self.session_id}.log"
        self.start_time = datetime.now()
        self.cycle_count = 0

    def log(self, tag: str, msg: str, also_print: bool = True):
        ts = datetime.now().isoformat(timespec='seconds')
        line = f"[{ts}] [{tag}] {msg}"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        if also_print:
            print(line)

    def header(self, title: str):
        sep = "=" * 70
        self.log("BOOT", sep)
        self.log("BOOT", title)
        self.log("BOOT", sep)


# =============================================================
# 守护进程主类
# =============================================================

class ThreePillarsDaemon:
    def __init__(self):
        self.config = {
            "okx_api_key": os.getenv("OKX_API_KEY", ""),
            "okx_passphrase": os.getenv("OKX_PASSPHRASE", ""),
            "okx_secret_key": os.getenv("OKX_SECRET_KEY", ""),
            "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
            "deepseek_base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        }

        self.client = OKXRestClient(
            api_key=self.config["okx_api_key"],
            secret_key=self.config["okx_secret_key"],
            passphrase=self.config["okx_passphrase"],
            flag=DAEMON_CONFIG["okx_flag"],
        )
        self.ds = DeepSeekAnalyzer(
            api_key=self.config["deepseek_api_key"],
            base_url=self.config["deepseek_base_url"],
            model="deepseek-chat",
        )
        self.feeder = DeepSeekFeeder(okx_client=self.client)
        self.notifier = TelegramNotifier(
            self.config["telegram_bot_token"],
            self.config["telegram_chat_id"],
        )

        self.logger = DaemonLogger(DAEMON_CONFIG["log_dir"])
        self.running = True
        self.last_signal_ts = 0  # 上一次发送 TG 的时间戳, 用于去重
        self.last_ds_result = None  # 上一次 DeepSeek 结果 (供"复用"使用)

        # 注册信号 (Windows + Unix 通用)
        self._install_signal_handlers()

    def _install_signal_handlers(self):
        """Ctrl+C / 任务管理器结束信号"""
        try:
            signal.signal(signal.SIGINT, self._shutdown)
            signal.signal(signal.SIGTERM, self._shutdown)
        except (AttributeError, ValueError):
            # Windows 某些环境不允许
            pass

    def _shutdown(self, signum, frame):
        self.logger.log("STOP", f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    # =========================================================
    # 数据获取
    # =========================================================

    def _fetch_three_tf(self) -> Optional[Dict[str, pd.DataFrame]]:
        """拉三周期 K 线, 任一失败返回 None"""
        result = {}
        for tf, n in DAEMON_CONFIG["bars_per_tf"].items():
            try:
                df = self.client.get_candlesticks_history(
                    inst_id=DAEMON_CONFIG["inst_id"],
                    bar=tf,
                    total_bars=n,
                )
                if df is None or df.empty:
                    self.logger.log("PULL-ERR", f"{tf}: empty data")
                    return None
                result[tf] = df
            except Exception as e:
                self.logger.log("PULL-ERR", f"{tf}: {e}")
                return None

        for tf, df in result.items():
            last = float(df['close'].iloc[-1])
            self.logger.log("PULL", f"{tf} OK, {len(df)} bars, last=${last:.2f}")
        return result

    # =========================================================
    # 单轮核心流程
    # =========================================================

    def run_one_cycle(self) -> bool:
        """跑一轮 (v3 流程): 拉数据 → 三件套 → feeder 富数据 → 智能触发 DeepSeek → 代码取最严价 → 语境调仓 → TG通知 → 落盘
        返回 True 表示成功, False 表示本轮失败
        """
        self.cycle_start = time.time()
        self.logger.cycle_count += 1
        cycle_no = self.logger.cycle_count

        self.logger.log("CYCLE", f"#{cycle_no} start")

        # 1. 拉 K 线
        per_tf = self._fetch_three_tf()
        if not per_tf:
            self.logger.log("CYCLE-ERR", "pull failed, skip this cycle")
            return False

        # 2. 算三件套 (含硬过滤)
        try:
            pillars = compute_three_pillars(DAEMON_CONFIG["inst_id"], per_tf)
        except Exception as e:
            self.logger.log("PILLAR-ERR", f"compute_three_pillars failed: {e}")
            self.logger.log("PILLAR-ERR", traceback.format_exc())
            return False

        self.logger.log(
            "PILLAR",
            f"dir={pillars['ma30']['direction']} "
            f"conf={pillars['confidence']} "
            f"vol={pillars['volume']['verdict']} "
            f"corr={pillars['volume']['corr']:+.2f} "
            f"hard_filter={pillars['hard_filter_pass']}"
        )

        # 3. 硬过滤: 只有 BLOCK 档才硬禁 (价量背离 / 放量出货接货 / 方向不明)
        if pillars['volume'].get('level') == 'BLOCK':
            self.logger.log("FILTER", f"硬过滤 BLOCK: {pillars['volume']['verdict']}")
            ds_result = {
                "context_verdict": "unclear",
                "event_risk": "none",
                "entry_strategy": "none",
                "sl_zone": {"low": None, "high": None},
                "tp_zone": {"low": None, "high": None},
                "position_suggestion": "none",
                "risk_warning": pillars['volume']['reason'],
                "analysis": f"量价硬禁: {pillars['volume']['verdict']}",
                "_blocked_by_hard_filter": True,
                "_hard_filter_reason": pillars['volume']['verdict'],
                "_feeder_skipped": True,  # 硬禁时不调 feeder/AI
            }
            feeder_result = None
        else:
            # v3 新流程: feeder 聚合丰富指标, 智能触发判定
            try:
                feeder_result = self.feeder.compute(
                    DAEMON_CONFIG["inst_id"], per_tf, pillars
                )
                self.logger.log(
                    "FEEDER",
                    f"fp={feeder_result['fingerprint'][:8]} "
                    f"call={feeder_result['should_call_deepseek']} "
                    f"reason={feeder_result['trigger_reason']} "
                    f"skip={feeder_result['skip_count']}"
                )
            except Exception as e:
                self.logger.log("FEEDER-ERR", f"feeder.compute failed: {e}")
                self.logger.log("FEEDER-ERR", traceback.format_exc())
                feeder_result = None

            if feeder_result and feeder_result['should_call_deepseek']:
                # 触发 → 调 v3 analyzer
                try:
                    ds_result = self.ds.analyze_with_feeder(
                        DAEMON_CONFIG["inst_id"], feeder_result
                    )
                    # 注入量价警告
                    if pillars['volume'].get('level') == 'WARN':
                        existing = ds_result.get('risk_warning', '')
                        ds_result['risk_warning'] = (
                            f"[量价警告: {pillars['volume']['verdict']}] {existing}"
                        )
                        ds_result['_volume_warn'] = pillars['volume']['verdict']
                except Exception as e:
                    self.logger.log("DS-ERR", f"analyze_with_feeder failed: {e}")
                    self.logger.log("DS-ERR", traceback.format_exc())
                    ds_result = {
                        "context_verdict": "unclear",
                        "event_risk": "unknown",
                        "entry_strategy": "market",
                        "sl_zone": {"low": None, "high": None},
                        "tp_zone": {"low": None, "high": None},
                        "position_suggestion": "none",
                        "risk_warning": "DeepSeek 调用失败, 完全兜底",
                        "analysis": "DeepSeek 调用失败",
                        "_fallback": True,
                        "_parse_failed": True,  # 让 TG 显示失败标记
                        "_error": str(e),
                    }
            else:
                # 不触发 → 复用上一次 AI 结果 (但要更新价格/ATR)
                ds_result = self._reuse_last_ds_result(pillars, feeder_result)

        self.logger.log(
            "DS",
            f"verdict={ds_result.get('context_verdict')} "
            f"event_risk={ds_result.get('event_risk')} "
            f"strategy={ds_result.get('entry_strategy')} "
            f"pos={ds_result.get('position_suggestion')} "
            f"reused={ds_result.get('_reused', False)}"
        )

        # 5. 翻译语境 → 仓位乘数
        context_action = resolve_position_by_context(
            ds_result.get('context_verdict', 'unclear'),
            ds_result.get('event_risk', 'none'),
        )

        # 5b. v3 新增: 用 AI 的 confidence_score 微调仓位
        confidence_score = ds_result.get('confidence_score', 50)
        confidence_breakdown = ds_result.get('confidence_breakdown', [])
        context_action_before = dict(context_action)  # 备份
        context_action = adjust_position_by_confidence(
            context_action, confidence_score
        )
        if context_action.get('_confidence_adj_note') and \
           context_action['position_multiplier'] != context_action_before['position_multiplier']:
            self.logger.log(
                "CONF-ADJ",
                f"{context_action['_confidence_adj_note']} | "
                f"base_mult={context_action_before['position_multiplier']} → "
                f"new_mult={context_action['position_multiplier']}"
            )

        self.logger.log(
            "CTX",
            f"allow={context_action['allow_trade']} "
            f"mult={context_action['position_multiplier']} "
            f"max_risk={context_action['max_risk_pct']}% "
            f"confidence={confidence_score}"
        )

        # 6. 代码从 AI 区间取最严价
        if context_action['allow_trade'] and pillars['ma30']['direction'] in ('long', 'short'):
            final_prices = resolve_final_prices(ds_result, pillars)
        else:
            # 不开仓, 也算一下价位供 TG 显示
            final_prices = resolve_final_prices(ds_result, pillars)
            final_prices['_no_trade_reason'] = (
                "事件风险" if ds_result.get('event_risk') == 'high'
                else "语境不明" if ds_result.get('context_verdict') == 'unclear'
                else "MA30 方向未定"
            )

        self.logger.log(
            "PRICE",
            f"entry=${final_prices.get('entry_price')} "
            f"sl=${final_prices.get('stop_loss')} "
            f"tp=${final_prices.get('take_profit')} "
            f"rr={final_prices.get('risk_reward_ratio')}"
        )

        # 保存本次 AI 结果, 供下次复用 (智能触发跳过时)
        if not ds_result.get('_reused', False):
            self.last_ds_result = dict(ds_result)  # copy
            self.last_ds_result['_ts'] = time.time()

        # 7. Telegram 通知
        try:
            self._send_telegram(pillars, ds_result, context_action, final_prices, feeder_result)
            self.logger.log("TG", "sent")
        except Exception as e:
            self.logger.log("TG-ERR", f"send failed: {e}")

        # 8. 落盘
        if DAEMON_CONFIG["save_to_json"]:
            self._save_result(pillars, ds_result, context_action, final_prices,
                              cycle_no, feeder_result)

        elapsed = time.time() - self.cycle_start
        self.logger.log("CYCLE", f"#{cycle_no} done, elapsed={elapsed:.1f}s")
        return True

    def _reuse_last_ds_result(self, pillars: Dict, feeder_result: Optional[Dict]) -> Dict:
        """
        智能触发跳过时, 复用上次 AI 结果, 但更新:
          - 当前价
          - ATR
          - risk_warning (标记为"复用")
          - 复用时间
        """
        if self.last_ds_result is None:
            # 没历史结果, 用规则兜底
            return {
                "context_verdict": "unclear",
                "event_risk": "unknown",
                "entry_strategy": "market",
                "sl_zone": {"low": None, "high": None},
                "tp_zone": {"low": None, "high": None},
                "position_suggestion": "none",
                "risk_warning": "首次运行, 尚未建立 AI 上下文",
                "analysis": "等待下次指标变化触发 AI",
                "_fallback": True,
                "_reused": True,
            }

        # 复用上次结果
        reused = dict(self.last_ds_result)
        reused['_reused'] = True
        reused['_reuse_count'] = reused.get('_reuse_count', 0) + 1
        reused['_last_ai_ts'] = reused.get('_ts', 0)
        reused['_current_ts'] = time.time()

        # 在 risk_warning 标注"复用"
        skip_count = feeder_result.get('skip_count', 0) if feeder_result else 0
        existing_warn = reused.get('risk_warning', '')
        reused['risk_warning'] = f"[复用上次 AI 结果, 已跳过 {skip_count} 轮] {existing_warn}"

        # 保留原始 timestamp, 删除动态字段
        reused.pop('_ts', None)
        return reused

    # =========================================================
    # 矛盾检测: 比较代码判定 vs AI 判定 vs 指标, 找出冲突点
    # =========================================================
    @staticmethod
    def _detect_conflicts(pillars: dict, ds: dict,
                          feeder_result: Optional[Dict] = None) -> List[str]:
        """检测三件套之间的矛盾点 (返回人类可读的冲突描述列表)

        规则 (按重要性排序):
          1. MA30 方向 vs 4H MACD 方向: 一多一空 → 强烈矛盾
          2. MA30 方向 vs Volume WARN: 量价背离 → 持续性存疑
          3. MA30 方向 vs AI context_verdict: 不一致 → AI 不跟方向
          4. event_risk=high: 不管什么方向, 事件优先
          5. MA30=none 方向不明 → 任何 AI verdict 都是猜测
        """
        conflicts = []

        ma30_dir = pillars['ma30']['direction']
        ma30_conviction = pillars['ma30'].get('conviction', '?')

        # 4H MACD 方向 (从 feeder 拿)
        macd_cross = None
        if feeder_result and feeder_result.get('indicators'):
            h4_ind = feeder_result['indicators'].get('4H', {})
            if h4_ind.get('valid'):
                macd_cross = h4_ind.get('macd', {}).get('cross', '?')

        # 1. MA30 vs 4H MACD 矛盾
        if ma30_dir == 'long' and macd_cross == 'bear_below':
            conflicts.append(
                f"MA30 做多 ↔ 4H MACD 死叉下方 ({ma30_conviction}确信度 vs MACD空头信号)"
            )
        elif ma30_dir == 'short' and macd_cross == 'bull_above':
            conflicts.append(
                f"MA30 做空 ↔ 4H MACD 金叉上方 ({ma30_conviction}确信度 vs MACD多头信号)"
            )

        # 2. MA30 vs 量价背离
        vol_level = pillars['volume'].get('level', 'PASS')
        if ma30_dir in ('long', 'short') and vol_level == 'WARN':
            direction_word = '上涨' if ma30_dir == 'long' else '下跌'
            conflicts.append(
                f"MA30 {direction_word} ↔ 量价背离 (持续性存疑)"
            )

        # 3. MA30 vs AI 语境判定
        ai_verdict = ds.get('context_verdict', '?')
        if ma30_dir == 'long' and ai_verdict in ('reversal', 'unclear'):
            conflicts.append(
                f"MA30 做多 ↔ AI 判定 {ai_verdict} (不跟方向)"
            )
        elif ma30_dir == 'short' and ai_verdict in ('trending', 'breakout'):
            conflicts.append(
                f"MA30 做空 ↔ AI 判定 {ai_verdict} (不跟方向)"
            )

        # 4. 高风险事件
        if ds.get('event_risk') == 'high':
            conflicts.append("AI 判定高风险事件 (CPI/NFP/利率决议), 不管方向都暂停")

        # 5. 方向不明 + 强 AI 信号 (矛盾)
        if ma30_dir == 'none' and ai_verdict in ('trending', 'breakout'):
            conflicts.append(
                f"MA30 方向不明 ↔ AI 判定 {ai_verdict} (方向缺失, AI 在猜)"
            )

        return conflicts

    # =========================================================
    # Telegram 通知
    # =========================================================

    def _send_telegram(self, pillars: dict, ds: dict,
                        context_action: dict, final_prices: dict,
                        feeder_result: Optional[Dict] = None):
        m = pillars['ma30']
        v = pillars['volume']
        a = pillars['atr']
        kl = pillars['key_levels']

        dir_icon = {"long": "🟢", "short": "🔴", "none": "⚪"}.get(
            pillars['ma30']['direction'], "⚪"
        )
        verdict_icon = {
            "trending": "📈", "breakout": "🚀",
            "ranging": "↔️", "reversal": "⚠️", "unclear": "❓"
        }.get(ds.get('context_verdict', 'unclear'), "❓")

        # 量价等级标签
        vol_level = v.get('level', '?')
        vol_level_icon = {
            'BLOCK': '🚫',  # 硬禁
            'WARN': '⚠️',   # 警告
            'PASS': '✅',   # 通过
        }.get(vol_level, '❓')

        # 智能触发状态
        trigger_status = ""
        if feeder_result is not None:
            if ds.get('_reused'):
                trigger_status = f"  ⏸ [复用AI skip={feeder_result.get('skip_count', 0)}]"
            else:
                trigger_status = f"  🔥 [本次调AI: {feeder_result.get('trigger_reason', '?')[:30]}]"

        # 是否禁开: 严格判定每种禁开原因, 优先级清晰
        if ds.get('_blocked_by_hard_filter'):
            blocked = True
            block_reason = f"硬过滤禁开 ({ds.get('_hard_filter_reason', '量价问题')})"
        elif ds.get('event_risk') == 'high':
            blocked = True
            block_reason = f"事件风险高 (high), 系统拦截"
        elif not context_action['allow_trade']:
            blocked = True
            block_reason = f"语境禁开 ({ds.get('context_verdict', 'unclear')}): {context_action.get('reason', '-')[:80]}"
        elif final_prices.get('_no_trade_reason'):
            blocked = True
            block_reason = f"代码侧禁开: {final_prices['_no_trade_reason']}"
        else:
            blocked = False
            block_reason = None

        lines = [
            f"<b>三件套 v3 #{self.logger.cycle_count}</b>  "
            f"[{datetime.now().strftime('%m-%d %H:%M')}]{trigger_status}",
            f"<b>{pillars['inst_id']}</b> @ ${pillars['current_price']:.2f}  "
            f"{dir_icon} {pillars['ma30']['direction']}",
            "",
            f"<b>[1] 方向 MA30</b>: {m['direction']} ({m['conviction']}确信度)",
            f"  {m['reason'][:120]}",
            "",
            f"<b>[2] 实时量</b> {vol_level_icon} {vol_level}: {v['verdict']} "
            f"(corr={v['corr']:+.2f}, ratio={v['ratio']:.2f}x)",
            f"  {v['reason'][:120]}",
        ]

        # v3 新增: 显示核心指标摘要 (RSI/MACD/BB)
        if feeder_result is not None and feeder_result.get('indicators'):
            h4 = feeder_result['indicators'].get('4H', {})
            if h4.get('valid'):
                macd_cross = h4.get('macd', {}).get('cross', '?')
                bb_pos = h4.get('bollinger', {}).get('position', 0.5)
                bb_regime = h4.get('bollinger', {}).get('regime', '?')
                lines.append("")
                lines.append(
                    f"<b>[3] 4H 核心指标</b>: "
                    f"RSI={h4.get('rsi_14', 0):.1f}({h4.get('rsi_state', '?')}), "
                    f"MACD={macd_cross}, "
                    f"BB位置={bb_pos:.2f}({bb_regime})"
                )

            okx = feeder_result.get('okx')
            if okx and okx.funding_rate is not None:
                fr_anno = okx.funding_rate_annualized or 0
                fr_pctile = okx.funding_rate_percentile or 0
                lines.append(
                    f"<b>[4] 资金费率</b>: {okx.funding_rate_state} "
                    f"(年化 {fr_anno*100:+.1f}%, 历史百分位 {fr_pctile:.0f}%)"
                )
            if okx and okx.open_interest is not None:
                oi_chg = okx.oi_change_24h_pct or 0
                lines.append(
                    f"<b>[5] 持仓量</b>: {okx.oi_state} "
                    f"(24h {oi_chg:+.2f}%)"
                )

        lines.append("")
        lines.append(f"<b>关键位 (代码算的)</b>:")
        lines.append(f"  4H 阻力 ${kl['4h']['resistance_4h']:.2f} / 支撑 ${kl['4h']['support_4h']:.2f}")
        lines.append(f"  1D 阻力 ${kl['1d']['resistance_4h']:.2f} / 支撑 ${kl['1d']['support_4h']:.2f}")

        # v3 新增: 矛盾检测 (在 AI 部分之前, 先帮交易员看清冲突)
        conflicts = self._detect_conflicts(pillars, ds, feeder_result)
        if conflicts:
            lines.append("")
            lines.append(f"<b>⚠️ 矛盾点 ({len(conflicts)} 个)</b>:")
            for c in conflicts[:3]:  # 最多显示 3 个, 避免太长
                lines.append(f"  • {c}")

        # v3 新增: AI 截断救援标记 (透明度)
        if ds.get('_truncated'):
            rescued_fields = []
            if ds.get('sl_zone', {}).get('low') is not None:
                rescued_fields.append('SL/TP')
            if ds.get('confidence_score', 0) > 0:
                rescued_fields.append('confidence')
            if ds.get('context_verdict') and ds['context_verdict'] != 'unclear':
                rescued_fields.append('verdict')
            if ds.get('market_context'):
                rescued_fields.append('context')
            rescued_str = ', '.join(rescued_fields) if rescued_fields else '部分字段'
            lines.append("")
            lines.append(
                f"⚠️ <i>AI 输出被截断 (max_tokens 触发), 已抢救: {rescued_str}</i>"
            )
        elif ds.get('_lenient_parsed'):
            lines.append("")
            lines.append("ℹ️ <i>AI JSON 有小不规范 (如 +15 号), 代码自动修复, 数据可信</i>")
        elif ds.get('_parse_failed'):
            lines.append("")
            lines.append("⚠️ <i>AI 输出完全无法解析, 走规则兜底 (confidence=0)</i>")

        # 只有 WARN/PASS 档才显示 AI 部分 (BLOCK 档根本没调 AI)
        if vol_level == 'BLOCK':
            lines.append("")
            lines.append(f"────────── 硬过滤拦截, 不调 AI ──────────")
        else:
            lines.append("")
            reused_tag = " [♻️ 复用上次结果]" if ds.get('_reused') else ""
            lines.append(f"────────── DeepSeek 找关键位 + 语境判定{reused_tag} ──────────")
            lines.append(f"{verdict_icon} <b>语境</b>: {ds.get('context_verdict', '?')}  "
                         f"<b>事件风险</b>: {ds.get('event_risk', '?')}")
            lines.append(f"<b>入场策略</b>: {ds.get('entry_strategy', '?')}")
            lines.append(f"<b>SL区间</b>: ${ds.get('sl_zone', {}).get('low', '?')} ~ ${ds.get('sl_zone', {}).get('high', '?')}")
            lines.append(f"<b>TP区间</b>: ${ds.get('tp_zone', {}).get('low', '?')} ~ ${ds.get('tp_zone', {}).get('high', '?')}")
            lines.append(f"<b>风险警告</b>: {ds.get('risk_warning', '-')[:200]}")

            # v3 新增: AI 置信度评分 (0-100 + 加分减分明细)
            conf_score = ds.get('confidence_score', None)
            if isinstance(conf_score, (int, float)):
                conf_int = int(round(conf_score))
                # 等级图标
                if conf_int >= 85:
                    conf_icon = '🔥🔥🔥'
                    conf_tier = '极强信号'
                elif conf_int >= 70:
                    conf_icon = '🔥🔥'
                    conf_tier = '强信号'
                elif conf_int >= 50:
                    conf_icon = '✅'
                    conf_tier = '正常'
                elif conf_int >= 30:
                    conf_icon = '⚠️'
                    conf_tier = '弱信号'
                else:
                    conf_icon = '🚫'
                    conf_tier = '禁开'
                lines.append("")
                lines.append(f"<b>[6] AI 置信度</b>: {conf_icon} <b>{conf_int}/100</b> ({conf_tier})")

                # 加分减分明细 (最多 6 条)
                breakdown = ds.get('confidence_breakdown', [])
                if isinstance(breakdown, list) and breakdown:
                    lines.append(f"  <b>评分明细</b>:")
                    for item in breakdown[:6]:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get('item', '-'))[:50]
                        try:
                            delta = int(round(float(item.get('delta', 0))))
                        except (ValueError, TypeError):
                            delta = 0
                        if delta > 0:
                            delta_str = f"<b>+{delta}</b>"
                            icon = '➕'
                        elif delta < 0:
                            delta_str = f"<b>{delta}</b>"
                            icon = '➖'
                        else:
                            delta_str = "±0"
                            icon = '⚪'
                        lines.append(f"    {icon} {delta_str}  {name}")

                # 置信度调整注记 (如果触发了)
                if context_action.get('_confidence_adj_note'):
                    lines.append(f"  <i>→ {context_action['_confidence_adj_note']}</i>")

        lines.append("")
        lines.append(f"────────── 代码守住数学底线 ──────────")

        if blocked:
            lines.append(f"🚫 <b>禁开仓</b>: {block_reason}")
        else:
            pos_mult = context_action['position_multiplier']
            mult_icon = "💯" if pos_mult == 1.0 else f"{pos_mult*100:.0f}%"
            lines.extend([
                f"<b>入场</b>: <code>${final_prices.get('entry_price')}</code>  "
                f"({ds.get('entry_strategy', '?')})",
                f"<b>止损</b>: <code>${final_prices.get('stop_loss')}</code>  "
                f"<b>止盈</b>: <code>${final_prices.get('take_profit')}</code>",
                f"<b>盈亏比</b>: 1:{final_prices.get('risk_reward_ratio', '?')}",
                f"<b>仓位乘数</b>: {mult_icon} (单笔风险 {context_action['max_risk_pct']}%)",
                f"<b>理由</b>: {context_action['reason']}",
            ])
            # ⚠ 警告: AI 给的区间方向与方向矛盾, 代码已自动 flip
            if final_prices.get('_direction_corrected'):
                lines.append("")
                lines.append("⚠ <i>AI 区间方向与方向矛盾, 代码已自动 flip, 实际 TP/SL 偏离 AI 原意</i>")

        lines.append("")
        lines.append(f"<b>分析</b>: {ds.get('analysis', '-')[:250]}")
        if ds.get('_fallback'):
            lines.append("\n⚠ <i>DeepSeek 失败, 用规则兜底</i>")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3990] + "\n…(已截断)"
        self.notifier.send(msg)

    # =========================================================
    # 落盘
    # =========================================================

    def _save_result(self, pillars: dict, ds: dict,
                     context_action: dict, final_prices: dict,
                     cycle_no: int, feeder_result: Optional[Dict] = None):
        try:
            output_dir = DAEMON_CONFIG["output_dir"]
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_c{cycle_no}.json"
            data = {
                "timestamp": datetime.now().isoformat(),
                "cycle": cycle_no,
                "inst_id": DAEMON_CONFIG["inst_id"],
                "three_pillars": pillars,
                "deepseek": ds,
                "context_action": context_action,
                "final_prices": final_prices,
            }
            # v3: 保存 feeder 结果 (含指标 + 触发原因)
            if feeder_result is not None:
                # 不保存 raw_response (太大了), 其他都保存
                fr_save = {
                    "fingerprint": feeder_result.get('fingerprint'),
                    "trigger_reason": feeder_result.get('trigger_reason'),
                    "should_call_deepseek": feeder_result.get('should_call_deepseek'),
                    "skip_count": feeder_result.get('skip_count'),
                    "indicators_4h": feeder_result.get('indicators', {}).get('4H', {}),
                    "indicators_15m": feeder_result.get('indicators', {}).get('15m', {}),
                    "indicators_1d": feeder_result.get('indicators', {}).get('1D', {}),
                    "okx_external": {
                        "funding_rate": feeder_result.get('okx', None) and feeder_result['okx'].funding_rate,
                        "funding_rate_annualized": feeder_result.get('okx', None) and feeder_result['okx'].funding_rate_annualized,
                        "funding_rate_percentile": feeder_result.get('okx', None) and feeder_result['okx'].funding_rate_percentile,
                        "funding_rate_state": feeder_result.get('okx', None) and feeder_result['okx'].funding_rate_state,
                        "open_interest": feeder_result.get('okx', None) and feeder_result['okx'].open_interest,
                        "open_interest_usd": feeder_result.get('okx', None) and feeder_result['okx'].open_interest_usd,
                        "oi_change_24h_pct": feeder_result.get('okx', None) and feeder_result['okx'].oi_change_24h_pct,
                        "oi_state": feeder_result.get('okx', None) and feeder_result['okx'].oi_state,
                        "ticker_24h": feeder_result.get('okx', None) and feeder_result['okx'].ticker_24h,
                    } if feeder_result.get('okx') else None,
                }
                data['feeder'] = fr_save
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            self.logger.log("SAVE-ERR", f"save failed: {e}")

    # =========================================================
    # 主循环
    # =========================================================

    def run(self):
        """主循环: 每 15 分钟跑一次, 永远不停 (除非收到停止信号)"""
        self.logger.header(
            f"三件套决策守护进程启动  标的={DAEMON_CONFIG['inst_id']}  "
            f"周期={DAEMON_CONFIG['interval_sec']}s"
        )
        self.logger.log("BOOT", f"session={self.logger.session_id}")
        self.logger.log("BOOT", f"OKX flag={DAEMON_CONFIG['okx_flag']}")

        # 启动 TG 通知
        try:
            self.notifier.send(
                f"<b>三件套守护进程已启动</b>\n"
                f"会话: {self.logger.session_id}\n"
                f"标的: {DAEMON_CONFIG['inst_id']}\n"
                f"周期: 每 {DAEMON_CONFIG['interval_sec']//60} 分钟\n"
                f"流程: 拉K线 → MA30方向 → OBV真伪 → ATR生存 → DeepSeek精准价位"
            )
        except Exception as e:
            self.logger.log("TG-ERR", f"startup notify failed: {e}")

        last_run_time = 0
        try:
            # 启动时立即跑一次 (不等 15 分钟)
            self.logger.log("BOOT", "first cycle will run immediately")

            while self.running:
                now = time.time()

                # 到点就跑
                if now - last_run_time >= DAEMON_CONFIG["interval_sec"]:
                    last_run_time = now
                    try:
                        self.run_one_cycle()
                    except Exception as e:
                        self.logger.log("CYCLE-ERR", f"unhandled: {e}")
                        self.logger.log("CYCLE-ERR", traceback.format_exc())

                # 步进 sleep, 既省 CPU 也方便响应 Ctrl+C
                # 每 5 秒检查一次, 是否到下一轮时间
                for _ in range(DAEMON_CONFIG["sleep_step_sec"]):
                    if not self.running:
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            self.logger.log("INTERRUPT", "Ctrl+C pressed")
        finally:
            self.logger.log("END", "Daemon stopped.")
            try:
                self.notifier.send(
                    f"<b>三件套守护进程已停止</b>\n"
                    f"会话: {self.logger.session_id}\n"
                    f"本会话共跑 {self.logger.cycle_count} 轮\n"
                    f"运行时长: {datetime.now() - self.logger.start_time}"
                )
            except Exception:
                pass


# =============================================================
# 入口
# =============================================================

def main():
    print("=" * 70)
    print("三件套决策守护进程")
    print("=" * 70)
    print(f"启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"标的: {DAEMON_CONFIG['inst_id']}")
    print(f"周期: {DAEMON_CONFIG['interval_sec']} 秒 ({DAEMON_CONFIG['interval_sec']//60} 分钟)")
    print(f"OKX flag: {DAEMON_CONFIG['okx_flag']} (1=模拟盘, 0=实盘)")
    print()
    print("按 Ctrl+C 停止")
    print("=" * 70)
    print()

    daemon = ThreePillarsDaemon()
    daemon.run()


if __name__ == "__main__":
    main()
