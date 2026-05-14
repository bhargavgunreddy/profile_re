## Polygon API Key setup (local, not committed)

1) Create a file named `.env` in the repo root (this file is gitignored).

2) Add this line:

```
POLYGON_API_KEY=YOUR_KEY_HERE
```

3) Run any Polygon script normally; the scripts will auto-load `.env` (or `.env.local`) and read `POLYGON_API_KEY`.

## TroubleshootingIf a script says it can't find `POLYGON_API_KEY`:- Confirm `.env` is at: `repo_root/.env` (same folder as this README).
- Make sure the line is exactly `POLYGON_API_KEY=...` (no `export`, no spaces).
- Run this diagnostic (it won't print your key):

```bash
python - <<'PY'
import sys
sys.path.append('src/polygon')
from polygon_secrets import debug_polygon_key
print(debug_polygon_key())
PY
```
