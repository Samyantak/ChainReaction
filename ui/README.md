# ChainReaction UI (M6)

React 18 + Vite + Cytoscape.js, served by FastAPI from the committed
`dist/` build. Hash routing (`#/…`) so no backend route changes are needed.

## Develop

```sh
cd ui
npm ci
npm run dev      # vite on :5173, /api proxied to localhost:8000
```

Run the backend from the repo root in another shell:

```sh
python -m chainreaction serve   # API + prebuilt UI on http://localhost:8000
```

## Build (required before every commit that touches `src/`)

`dist/` is committed so judges don't need Node. Never commit a stale build:

```sh
cd ui
npm run build       # rebuild dist/
npm run check-dist  # fails if dist/ is older than src/
```

## Layout

- `src/theme.css` — Redline Blueprint tokens + base components
- `src/components.jsx` — Sheet, Button, Chip, TitleBlock, Status, RedPencil
- `src/router.js` — hash router (`#/`, `#/features/:id`, `#/new`,
  `#/runs/:id/:view`, `#/history`)
- `src/api.js` — fetch helpers
- `EVENTS.md` — every SSE event type and the view it updates (step 4)
