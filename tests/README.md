# Tests

Two suites, both run against a local `wrangler dev` with simulated phones (headless Chromium):

- `e2e.py`: every screen and feature: name picker and claiming, board, marking with photos, undo, bingo, standings, photo wall, lock, every Host control, security checks, phone layout.
- `test_names.py`: deep tests for tap-your-name sign-in:
  - Claiming, "No, go back", taken names, unknown names.
  - Cookie tampering: a renamed player, a bad signature, garbage.
  - 8 phones tapping the same name at once: exactly one owns it.
  - Host release: the old phone is signed out immediately and sent back to the name list, and an old cookie stays dead after the name is re-claimed.
  - Sign out frees the name, and cancelling sign out keeps you in.
  - A host with no name of their own can view and release boards.
  - **KV budget**: reads and writes per request, so the free tier (100k reads, 1k writes a day) lasts the weekend.
  - **KV budget**: reads and writes per request, so the free tier (100k reads, 1k writes a day) lasts the weekend.

## Run

```bash
./tests/run.sh            # starts wrangler dev, runs both suites, stops it
```

Needs Node, Python 3, and `pip install playwright pillow && python -m playwright install chromium`.
Both suites wipe all marks, photos and name claims in the **local** dev store only.
Set `BINGO_URL` to point them elsewhere, but **don't** point them at the live site during the party.
