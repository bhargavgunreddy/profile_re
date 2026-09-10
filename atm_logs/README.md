# ATM Checkpoint Logs

Self-contained HTML archive of dated ATM option checkpoint tables.

## Open the log

Open this file in any browser:

```text
atm_checkpoint_log.html
```

No server needed — each date’s table is embedded.

## CSV archive

| Date | File |
|------|------|
| 2026-08-19 calls | `atm_logs/aug19_2026_atm_call_checkpoints.csv` |
| 2026-08-19 puts | `atm_logs/aug19_2026_atm_put_checkpoints.csv` |
| 2026-08-27 calls | `atm_logs/aug27_2026_atm_call_checkpoints.csv` |
| 2026-09-01 calls | `atm_logs/sep1_2026_atm_call_checkpoints.csv` |
| 2026-09-02 calls | `atm_logs/sep2_2026_atm_call_checkpoints.csv` |

## Rebuild after adding a new date

1. Drop the new CSV into `atm_logs/`
2. Register it in `scripts/build_atm_checkpoint_log.py` (`SESSIONS`)
3. Run:

```bash
python3 scripts/build_atm_checkpoint_log.py
```
