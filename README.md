# CTREND 加密货币趋势因子交易系统

基于论文 **"A Trend Factor for the Cross Section of Cryptocurrency Returns"** ([Fieberg et al., 2025, JFQA](https://doi.org/10.1017/S0022109024000747)) 完整复刻的开源实现,并集成欧易 (OKX) 交易所 API 实现自动化交易。

---

## 目录

- [项目简介](#项目简介)
- [论文核心要点](#论文核心要点)
- [安装](#安装)
- [配置 OKX API](#配置-okx-api)
- [使用指南](#使用指南)
- [项目结构](#项目结构)
- [模块说明](#模块说明)
- [策略逻辑](#策略逻辑)
- [风险提示](#风险提示)

---

## 项目简介

CTREND 是一种基于**机器学习**的加密货币趋势因子,通过聚合 28 个技术指标来预测加密货币横截面收益。论文结果显示:

- **周均收益**: 3.87%
- **年化夏普比率**: 1.94
- **净收益** (扣交易成本): 2.90%/周
- **最强稳健性**: 在最大和最流动的币种中仍然显著

本项目完整复刻了 CTREND 方法论,并通过欧易 (OKX) 交易所实现自动化实盘交易。

---

## 论文核心要点

### 28 个技术指标

| 类别 | 指标 |
|------|------|
| **动量震荡 (5)** | `rsi`, `stochK`, `stochD`, `stochRSI`, `cci` |
| **移动平均 (9)** | `sma_3d/5d/10d/20d/50d/100d/200d`, `macd`, `macd_diff_signal` |
| **成交量 (10)** | `volsma_3d/5d/10d/20d/50d/100d/200d`, `volmacd`, `volmacd_diff_signal`, `chaikin` |
| **波动率 (4)** | `boll_low`, `boll_mid`, `boll_high`, `boll_width` |

### 算法: CS-C-ENet (Cross-Sectional Combined Elastic Net)

1. **单变量 Fama-MacBeth 回归**: 对每个指标 j 单独做截面回归, 得到 28 个单变量预测
2. **Elastic Net 选择**: 在 28 个预测上做 L1+L2 正则化回归, 选择有效预测
3. **等权平均**: 对 theta_j > 0 的预测做等权平均, 得到 CTREND 分数

### 关键参数

- **滚动窗口**: 52 周
- **截面标准化**: 映射到 [-0.5, 0.5]
- **权重**: 市值加权 (WLS)
- **调仓频率**: 周频 (论文) / 日频 (本项目可选)

---

## 安装

### 1. 克隆或下载项目

```bash
cd C:\Users\Administrator\ctrend_trader
```

### 2. 创建虚拟环境 (推荐)

```bash
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

---

## 配置 OKX API

### 1. 注册欧易账户

- 国际版: https://www.okx.com
- 中国大陆用户请使用: https://www.okx.com/zh-hans

### 2. 创建 API Key

1. 登录后进入 **个人中心** → **API**
2. 点击 **创建 API Key**
3. 选择权限:
   - ✅ **读取** (查询账户、行情)
   - ✅ **交易** (下单)
   - ❌ **提现** (强烈不建议勾选)
4. 设置 Passphrase (自定义密码)
5. **保存 API Key, Secret Key 和 Passphrase** (Secret 不会再次显示)

### 3. (重要) 创建模拟盘 API Key 用于测试

1. 进入 **交易** → **模拟交易**
2. 在模拟盘页面创建 API Key
3. 先用模拟盘验证策略, 再切换到实盘

### 4. 配置 API 到本项目

编辑 `src/config.py`:

```python
OKX_CONFIG = {
    "api_key": "你的_API_Key",
    "secret_key": "你的_Secret_Key",
    "passphrase": "你的_Passphrase",
    "flag": "1",  # "0" = 实盘, "1" = 模拟盘
    ...
}
```

或使用环境变量 (更安全):

```bash
# Windows PowerShell
$env:OKX_API_KEY="你的_API_Key"
$env:OKX_SECRET_KEY="你的_Secret_Key"
$env:OKX_PASSPHRASE="你的_Passphrase"
```

---

## 使用指南

### 1. 仅查看预测 (不需要 API Key 也能跑行情)

```bash
python run.py --predict-only
```

输出:
```
CTREND 最新预测:
  ETH-USDT-SWAP          CTREND = +0.1245
  SOL-USDT-SWAP          CTREND = +0.0892
  ...
  ADA-USDT-SWAP          CTREND = -0.0456
  XRP-USDT-SWAP          CTREND = -0.0823
```

### 2. 训练模型 (回填历史)

```bash
python run.py --train-only
```

模型保存在 `models/ctrend_model.pkl`。

### 3. 模拟盘运行

在 `src/config.py` 中设置:
```python
OKX_CONFIG["flag"] = "1"  # 模拟盘
RUN_CONFIG["enable_trading"] = True
```

然后运行:
```bash
python run.py
```

### 4. 实盘运行 ⚠️

> **重要**: 先在模拟盘验证至少 1-2 周!

```python
# src/config.py
OKX_CONFIG["flag"] = "0"  # 实盘
RUN_CONFIG["enable_trading"] = True
```

```bash
python run.py
```

---

## 项目结构

```
ctrend_trader/
├── README.md                   # 本文档
├── requirements.txt            # 依赖列表
├── run.py                      # 启动入口
├── src/
│   ├── config.py              # 全局配置
│   ├── indicators.py          # 28 个技术指标
│   ├── factor.py              # CTREND 因子 (CS-C-ENet)
│   ├── data_loader.py         # 数据加载与缓存
│   ├── okx_rest.py            # OKX REST API 客户端
│   ├── okx_ws.py              # OKX WebSocket 客户端
│   ├── trader.py              # 交易执行器
│   └── main.py                # 主控调度
├── data/                      # K 线缓存 (Parquet)
├── models/                    # 训练好的模型
└── logs/                      # 运行日志
```

---

## 模块说明

### 1. `indicators.py` — 28 个技术指标

严格按论文 Supplementary Appendix A 复刻:

- **动量震荡**: RSI(14), 随机 K/D(14,3,3), 随机 RSI, CCI(20)
- **移动平均**: SMA(3/5/10/20/50/100/200), MACD(12,26,9)
- **成交量**: Vol-SMA(7 个), Vol-MACD(12,26,9), Chaikin(20)
- **波动率**: Bollinger(20,2)

并提供 `cross_sectional_rank()` 函数, 按论文要求将指标映射到 [-0.5, 0.5]。

### 2. `factor.py` — CTREND 因子

实现论文公式 (7)-(11) 的 CS-C-ENet:

```python
# 单变量 FM 回归 (公式 7)
α_j, β_j = fit_univariate(z_{i,j}, r_{i,t+1})

# 单变量预测 (公式 8)
r̂_j_{i,t+1} = α_j + β_j * z_{i,j,t}

# Elastic Net 选择 (公式 10)
θ_j = argmin ||r - Σ θ_j * r̂_j||² + λ * (α||θ||₁ + (1-α)||θ||₂²)

# CTREND 聚合 (公式 11)
CTREND_{i,t+1} = (1/|J*|) Σ_{j in J*} r̂_j_{i,t+1},  where J* = {j : θ_j > 0}
```

### 3. `data_loader.py` — 数据采集

- 通过 OKX REST API 拉取 K 线
- 本地 Parquet 缓存 (1 小时有效)
- 计算技术指标
- 收益计算 + 数据清洗 (按论文 0.5%/99.5% 截断)

### 4. `okx_rest.py` — OKX REST 客户端

封装所有 OKX V5 API:
- 行情: `get_ticker`, `get_candlesticks`, `get_orderbook`
- 账户: `get_account_balance`, `get_positions`
- 交易: `place_order`, `cancel_order`, `close_position`
- 自动签名 + ISO 8601 时间戳

### 5. `okx_ws.py` — OKX WebSocket 客户端

- 公共频道: tickers, candles, books, trades
- 私有频道: orders, account, positions
- 自动重连 + 心跳 + 装饰器回调

### 6. `trader.py` — 交易执行

- 多空策略: CTREND 高的做多, 低的做空
- 自动调仓: 目标仓位 → delta → 下单
- 风险控制: 止损 / 止盈 / 日亏损限额

### 7. `main.py` — 主控调度

- 启动时训练模型
- 进入循环: 计算分数 → 调仓 → 风险检查
- WebSocket 实时监控

---

## 策略逻辑

### 完整流程图

```
┌─────────────────┐
│ 加载历史 K 线    │ ← OKX REST API
└────────┬────────┘
         ↓
┌─────────────────┐
│ 计算 28 指标    │
└────────┬────────┘
         ↓
┌─────────────────┐
│ 截面标准化      │ ← 映射到 [-0.5, 0.5]
└────────┬────────┘
         ↓
┌─────────────────┐
│ 滚动 52 周训练  │ ← CS-C-ENet
└────────┬────────┘
         ↓
┌─────────────────┐
│ 生成 CTREND 分数│
└────────┬────────┘
         ↓
┌─────────────────┐
│ 排序取多分位    │
└────────┬────────┘
         ↓
┌─────────────────┐
│ 多 N 空 N 调仓  │ ← OKX API
└────────┬────────┘
         ↓
┌─────────────────┐
│ WebSocket 监控  │ ← 实时行情
└─────────────────┘
```

### 调仓规则

1. 按 CTREND 分数排序所有标的
2. 排名 **前 N 个** → 做多 (等金额)
3. 排名 **后 N 个** → 做空 (等金额)
4. 默认 N=5 (config 中 `max_positions`)
5. 多空两侧各占可用资金 50%

### WebSocket 实时推送

- 订阅 `tickers` 获取最新价
- 订阅 `candles1D` 获取实时 K 线
- 订阅私有频道: `orders`, `positions`, `account`

---

## 风险提示

⚠️ **加密货币交易风险极高,本项目仅供学术研究和学习使用。**

1. **历史表现不代表未来收益**: 论文使用 2015-2022 数据,在新市场环境下可能失效
2. **交易成本**: 永续合约手续费 0.05%/0.02%,策略年化换手率约 70%/周
3. **杠杆风险**: 默认 3x 杠杆,可能爆仓
4. **API Key 安全**: 务必不要勾选"提现"权限,使用 IP 白名单
5. **充分测试**: 建议先在模拟盘运行至少 1 个月
6. **资金管理**: 投入资金不超过可承受亏损的范围

---

## 引用

如果本项目对你的研究有帮助,请引用原始论文:

```bibtex
@article{fieberg2025trend,
  title={A Trend Factor for the Cross Section of Cryptocurrency Returns},
  author={Fieberg, Christian and Liedtke, Gerrit and Poddig, Thorsten and Walker, Thomas and Zaremba, Adam},
  journal={Journal of Financial and Quantitative Analysis},
  year={2025},
  doi={10.1017/S0022109024000747}
}
```

---

## License

MIT License. 仅供学术研究使用,作者不对任何交易损失负责。