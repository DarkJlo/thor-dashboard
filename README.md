# THOR paper dashboard + engine
PAPER TRADING ONLY. No exchange keys, no real orders. Public dashboard: https://darkjlo.github.io/thor-dashboard/
- `engine/`: tracker code (one cycle per run, `tracker.py --once`), Hyperliquid public market data only.
- `.github/workflows/engine.yml`: runs every ~5 min, deploys the dashboard to Pages.
- branch `state`: tracker state (single force-pushed commit). Branch `inbox`: Telegram signal checks pushed from the operator box.
