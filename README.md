# Bachelor Bingo

Cloudflare Worker. Everyone opens one link, picks their name, gets a 4x4 board with one free square and no clues about themselves. Marking a square requires a photo with a witness; photos go to the Photos wall. First bingo wins.

- Game state: KV namespace `bachelor-bingo`
- Photos: R2 bucket `bachelor-bingo-photos`
- Host controls: Host tab in the app. PIN is `ribeye`, stored as the `HOST_PIN` Worker secret. To change it: `npx wrangler@latest secret put HOST_PIN`.

## Deploy

```bash
cd ~/Projects/bachelor-bingo
npx wrangler@latest deploy      # -> https://bachelor-bingo.jjworker99.workers.dev
```

## Local test

```bash
npx wrangler@latest dev --port 8787 --local
```

Clues and players are seeded from `seed.js` the first time the Worker runs with an empty KV. After that, edit them from the Host tab; they live in KV, so no redeploy is needed.
