# Bachelor Bingo

Cloudflare Worker. Everyone opens one link and taps their name; that phone then owns the name, and everyone else sees it locked. Each player gets a 4x4 board of 16 clues (no free square), none of them about themselves. Marking a square requires a photo with a witness; photos go to the Photos wall. First bingo wins.

- Game state: KV namespace `bachelor-bingo`
- Photos: R2 bucket `bachelor-bingo-photos`
- Sign-in: no passwords. Tapping your name sets a signed cookie tied to that claim. Sign out frees the name. The host can **Release name** if someone switches phones, clears their browser, or taps the wrong name; that signs the old phone out on its next refresh.
- Host controls: Host tab in the app. PIN is `ribeye`, stored as the `HOST_PIN` Worker secret. To change it: `npx wrangler@latest secret put HOST_PIN`.

## Deploy

Deploys are automatic: Cloudflare Workers Builds is connected to this repo and runs
`npx wrangler deploy` on every push to `main` (about a minute). Open a PR, merge it, done.
Build logs: Cloudflare dashboard → Workers & Pages → bachelor-bingo → Deployments.

Preview builds are **off** on purpose: previews would share the live KV and R2, so a
work-in-progress branch could change real boards and photos.

Manual deploy, if ever needed:

```bash
cd ~/Projects/bachelor-bingo && git pull && npx wrangler@latest deploy
```

## Test

```bash
./tests/run.sh      # starts wrangler dev locally and runs both suites (see tests/README.md)
```

Clues and players are seeded from `seed.js` the first time the Worker runs with an empty KV. After that, edit them from the Host tab; they live in KV, so no redeploy is needed.
