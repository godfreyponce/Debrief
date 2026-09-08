# CLAUDE.md

Context for future Claude Code sessions working in this repo.

## What this is

A personal daily news debrief — a single page that shows what 6 tracked people posted in the last 24 hours across X, YouTube, and one Substack. Live at https://out-mu-pink.vercel.app.

The hard-coded design rule the user cares about: **keep small details — short clips, offhand tweets, replies, retweets with commentary all count**. Don't filter for "substantive" content.

## Architecture

The page is **server-rendered on every request** by a Vercel Python serverless function. There is no static HTML deploy anymore.

```
GET /         →  rewrite to /api/news (via out/vercel.json)
GET /api/news →  out/api/news.py (handler class)
                  ├─ ThreadPoolExecutor parallel-fetches YouTube/blog feeds (6s timeout each)
                  ├─ X: reads x-highlights.json from Vercel Blob; refreshes it from X's
                  │     embed backend at most every 6h (sequential, 15 min backoff on failure)
                  ├─ Parses RSS/Atom XML, filters to last 24h
                  ├─ Renders editorial HTML inline
                  └─ Returns with Cache-Control: s-maxage=300, stale-while-revalidate=900
```

A `Refresh` button in the masthead calls `fetch('/api/news?t=<timestamp>')` to bypass CDN cache and swaps `<main>`, `<h1>`, and `<footer>` content into the DOM in place — no full page reload.

## File map (only what matters)

- `out/api/news.py` — **the entire app**. Fetch + parse + render + handler. Edit this for any behavior change.
- `out/vercel.json` — rewrites `/` to `/api/news`. Without this, Vercel would 404 the root.
- `deploy.sh` — `vercel deploy out --prod --yes --scope godfreyps-projects`. The `--scope` is mandatory in non-interactive mode.
- `sources.json` — **legacy/reference only**. The function does not read it. The current source of truth for tracked people and channel IDs is the `PEOPLE` constant inside `out/api/news.py`.
- `out/.vercel/project.json` and `.vercel/project.json` — Vercel link metadata (gitignored).
- `~/.claude/commands/daily-news-debrief.md` — slash command. Lives outside the repo. **Partially out of date** — its body still describes the old static-build flow. The function is the source of truth for design and behavior.

## Common tasks

**Deploy:**
```bash
cd ~/Desktop/newsDebrief && bash deploy.sh
```

**Probe the live function:**
```bash
curl -sL "https://out-mu-pink.vercel.app/api/news?t=$(date +%s)" | head -200
```

**Add or remove a tracked person:** edit the `PEOPLE` list at the top of `out/api/news.py`. Each entry is `(name, [(kind, key), ...])` where `kind` is `"x"`, `"youtube"`, or `"blog"`. For YouTube, `key` is the `UC…` channel ID, not the `@handle`. To resolve a `@handle` → channel ID:
```bash
curl -sL "https://www.youtube.com/@<handle>" | grep -oE '"channelId":"UC[A-Za-z0-9_-]+"' | head -1
```

**Change the design:** edit the `CSS` constant in `out/api/news.py`. The HTML structure (per-item `<li>`, masthead, footer) is built in `render()` immediately below.

## Conventions and gotchas

- **No external assets.** Inline CSS, inline SVG, inline JS only. The design pass via `taste-skill` deliberately produced a self-contained page; don't introduce CDN fonts or icon libraries without a reason.
- **The function MUST stay under 10s** (Vercel hobby tier). Per-feed timeout is 6s. With 11 feeds in parallel, total runtime is normally 1–2s. Don't add synchronous fetches in series.
- **X is "highlights only", by design, since Sept 2026.** Every free X→RSS bridge is dead (nitter.net returns 410, xcancel serves a whitelist gate, RSSHub is gone). The X source is X's own embed backend (`X_SYNDICATION` in `news.py`), which Vercel can reach without login but which returns ~100 of an account's *highlighted* posts across years, not a chronological timeline: no retweets, few replies, and X refreshes it on its own schedule. It's labeled "X highlights" on the page and shown with a 7-day window (24h would be empty most days). **X is rate-limited to 30 requests per ~15 min per IP and refuses parallel bursts**, so the function never fetches X per page view: `x_highlights()` reads `x-highlights.json` from the Vercel Blob store `newsdebrief-x` (public, `BLOB_READ_WRITE_TOKEN` is linked to the project; `.env.local` holds it locally and is gitignored), refreshes it sequentially at most every `X_CACHE_TTL` (6h), keeps each handle's last good data when a fetch fails, and backs off `X_RETRY_AFTER` (15 min) after a failed attempt. To force a refresh: `vercel blob del x-highlights.json --scope godfreyps-projects` then load the page. `?debug=1` includes an `x_cache` block (served_from, attempted_at, errors, save_error). A full 24h timeline needs the paid X API ($0.005/post read) or a logged-in scraper; both were declined as of 2026-09-07. Residential IPs get 429 from the embed backend, so test it via `/api/news?probe=<url>&raw=1`, not local curl. **Don't try to "fix" empty X sections** — check `/api/news?debug=1` first; `latest` tells you the newest post X exposes.
- **YouTube RSS endpoint also flaky.** Has 404'd globally for short windows. Same handling: per-person error row, rest of the page works.
- **Times are computed in `America/Chicago`.** Header is `Weekday, Month D`. Footer is `Last updated <part-of-day> <h:mm:ssam/pm> CT` where part-of-day is morning/afternoon/evening/tonight/overnight based on the local hour.
- **Don't rebuild the page as a static deploy** to fix a content issue. The whole point of the function is that content is live. If the URL shows stale data, the answer is "wait for cache TTL" or "click refresh", not "redeploy".

## Active TODOs (as of this writing)

- **8am CT daily schedule.** A `/schedule` skill routine should ping `/api/news?t=…` daily at `0 8 * * *` `America/Chicago`. Not wired up yet.
- **Slash command body is out of date.** The frozen-template instructions still describe the old static-HTML build. Either rewrite the body to be a one-liner that pings the function URL, or delete it (the function makes it obsolete).

## Origin

Built across several Claude Code sessions in early May 2026. Iteration 1: static HTML built by a slash command, deployed by cron. Iteration 2: editorial design pass via [`taste-skill`](https://github.com/Leonxlnx/taste-skill) (`redesign-existing-projects` + `minimalist-ui`). Iteration 3: migrated to a server-rendered function so the page stays live throughout the day with a working refresh button.
