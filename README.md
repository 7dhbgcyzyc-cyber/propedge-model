# PropEdge — experimental prop research dashboard

This project automatically fetches NBA/NFL player-prop lines from a third-party data feed, then compares them to **independent** projections when available. It does **not** derive projections from betting lines or claim a profitable betting edge.

## Setup
1. Add `PRIZEPICKS_API_KEY` in repository Settings → Secrets and variables → Actions.
2. Upload the contents of this folder to the root of the GitHub repository (including `.github/workflows/update.yml`).
3. Go to Actions → Update PropEdge → Run workflow. The workflow writes `data/latest.json` on success.
4. Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main`, folder `/ (root)` → Save.
5. Visit the Pages URL GitHub displays after deployment.

## Independent projections
`projections.csv` has the columns `player,league,stat,projection,source`. Fill it with independently obtained model estimates; the player and stat strings must match the API. Empty values are not fabricated. This is a **feed-connected dashboard**, not yet a validated prediction engine. Add a historical game-log source and backtesting before treating estimates as predictions. This file is public: do not put private keys or personal information in it.

## Notes
- GitHub scheduled runs may be delayed and can be disabled after inactivity. The snapshot includes its timestamp.
- The third-party provider's endpoint/response format and request limits can change; adjust `update.py` accordingly.
- GitHub Actions writes public line data to a public repository. Confirm your data provider's terms allow redistribution before enabling public Pages.
- `limit=500` is a test setting, not a guarantee of full coverage. Pagination may be needed.
- All stats are for research; colors indicate projection direction, not actual probabilities.
