## UTBot Signal Success Stats (Options proxy)

This tool estimates how often UTBot BUY/SELL alerts would have reached option profit targets before a 30-minute time stop.

- BUY alert -> buy CALL
- SELL alert -> buy PUT
- Exit if option target hits (`+10%`, `+20%`, etc), otherwise force exit after N minutes

### Script
- `src/polygon/utbot_option_signal_stats.py`

### Input format
Alerts CSV needs at least:
- `timestamp` (or `time`/`datetime`)
- `signal` (or `side`/`action`) with values like `BUY`/`SELL`

Example:

```csv
timestamp,signal
2026-01-02 09:35:00-05:00,BUY
2026-01-02 10:20:00-05:00,SELL
```

Use template:
- `src/polygon/utbot_alerts_template.csv`

### Run

From repo root:

```bash
python3 src/polygon/utbot_option_signal_stats.py \
  --alerts_csv src/polygon/utbot_alerts_template.csv \
  --spy_cache_root src/polygon/data/polygon \
  --targets_pct 10,20 \
  --max_hold_minutes 30 \
  --entry_mode same_bar_close
```

### Outputs
- `utbot_option_stats_target_10.csv`
- `utbot_option_stats_target_20.csv`
- `utbot_option_stats_summary.csv`

Summary fields:
- `trades`
- `win_rate_pct`
- `avg_option_pnl_pct`
- `median_option_pnl_pct`
- `target_hits`
- `time_stops`
- `stop_losses`

### Model assumptions (important)
This is a **delta-based options proxy**:
- default `call_delta=0.45`, `put_delta=0.45`
- default `entry_option_price=1.00`
- no IV changes, no spread/slippage, no theta decay intraday

So the stats are best for signal-quality estimation, not exact option fill replication.

For more realism:
- set a stop loss, e.g. `--stop_loss_pct -20`
- test `--entry_mode next_bar_open`
- tune delta values by moneyness and day type

