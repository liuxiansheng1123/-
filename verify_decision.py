import json
with open('data/eth_triple_tf_final.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

# 导出最终决策详细字段
ds = d['deepseek_decision']
print('=== 最终决策 (实跑数据) ===')
print(f"recommendation:    {ds.get('recommendation')}")
print(f"confidence:        {ds.get('confidence')}")
print(f"source_timeframe:  {ds.get('source_timeframe')}")
print(f"source_factor:     {ds.get('source_factor')}")
print(f"entry_type:        {ds.get('entry_type')}")
print(f"entry_price:       {ds.get('entry_price')}")
print(f"stop_loss:         {ds.get('stop_loss')}")
print(f"take_profit:       {ds.get('take_profit')}")
print(f"sell_price:        {ds.get('sell_price')}")
print(f"invalidation_price:{ds.get('invalidation_price')}")
print(f"current_price:     {ds.get('current_price')}")
print(f"gap_to_entry_pct:  {ds.get('gap_to_entry_pct')}")

# === 小学数学验证 ===
print('\n=== 小学数学自检 ===')
entry = ds.get('entry_price')
stop = ds.get('stop_loss')
tp = ds.get('take_profit')
cp = ds.get('current_price')
if entry and cp:
    actual_gap = (entry - cp) / cp * 100
    print(f"入场价{entry:.2f} vs 当前{cp:.2f}: 实际跌幅 = {actual_gap:.3f}%")
    print(f"声明跌幅 = {ds.get('gap_to_entry_pct'):.3f}%")
    diff = abs(actual_gap - ds.get('gap_to_entry_pct'))
    print(f"是否闭环: {'是' if diff < 0.1 else '否 (有错!)'}")
if entry and stop and tp:
    risk = abs(entry - stop)
    reward = abs(tp - entry)
    rr = reward / risk
    print(f"入场={entry:.2f} 止损={stop:.2f} (差{risk:.2f}) 止盈={tp:.2f} (差{reward:.2f})")
    print(f"风险回报 = 1:{rr:.2f}")
