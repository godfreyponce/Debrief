# news-debrief

A personal "morning paper": a single page that shows what six tracked people posted in the last 24 hours on YouTube and one Substack, plus their X highlights from the last seven days: short clips, offhand tweets, replies, and posts you might otherwise miss. Built around the explicit rule: **keep the small details, don't filter for "substantive" content.**

**Live URL:** https://out-mu-pink.vercel.app

## How it works

Every page load runs a Vercel Python serverless function that fetches the YouTube and Substack feeds in parallel, parses RSS/Atom XML, filters them to the last 24 hours, adds the X highlights cached in Vercel Blob with their seven-day window, and renders the editorial HTML on the fly. The CDN caches the response for 5 minutes (with stale-while-revalidate up to 15 minutes), so most visits are instant.

A `Refresh` button in the masthead bypasses the CDN cache (via `?t=<timestamp>`) and swaps fresh content into the DOM in place — no full page reload.

```
Visitor → /  →  rewrite to /api/news  →  Python function
                                          ├─ parallel-fetch 5 YouTube and Substack feeds, keep last 24h
                                          ├─ read X highlights from Vercel Blob, keep last 7 days
                                          ├─ render HTML
                                          └─ return with Cache-Control
```

## Tracked feeds

Configured in `api/news.py` (the `PEOPLE` constant). Channel IDs are pre-resolved.

| Person          | Surfaces                              |
|-----------------|---------------------------------------|
| Andrej Karpathy | X (`@karpathy`), YouTube              |
| Theo (t3.gg)    | YouTube, X (`@theo`)                  |
| NetworkChuck    | YouTube, X (`@NetworkChuck`)          |
| Nate Herk       | YouTube, X (`@NateHerk`)              |
| Elon Musk       | X (`@elonmusk`)                       |
| Dean Ball       | Substack RSS, X (`@deanwball`)        |

X comes from X's own embed backend, highlights only, cached 6 hours in a Vercel Blob, because every free bridge died. YouTube uses the Atom endpoint `https://www.youtube.com/feeds/videos.xml?channel_id={id}`. Dean Ball's blog uses his Substack feed directly.

## File layout

```
./
├── README.md            # this file
├── deploy.sh            # `vercel deploy out --prod --yes --scope godfreyps-projects`
├── sources.json         # legacy: kept for reference; function has its own PEOPLE list
├── out/                 # what gets deployed to Vercel
│   ├── api/
│   │   └── news.py      # the serverless function (fetch + parse + render)
│   └── vercel.json      # rewrites `/` → `/api/news`
└── .vercel/             # vercel project link metadata (gitignored if repo'd)
```

The Vercel project is `godfreyps-projects/news-debrief`.

## Design

Editorial / Notion-Linear minimalism, applied via the [`taste-skill`](https://github.com/Leonxlnx/taste-skill) skills (`redesign-existing-projects` + `minimalist-ui`).

- Warm bone background (`#F7F6F3`), charcoal ink (`#2F3437`)
- Serif display heading (system serif: New York / Iowan Old Style)
- Sans body, mono for metadata (timestamps, eyebrow, footer)
- Type pills with muted-pastel backgrounds (yellow for blog, red for YouTube, neutral for X)
- Real red YouTube logo (inline SVG) for YouTube items; unicode `𝕏` and `✎` for X and blog
- Relative time chip per item (`3h`, `12m`, `now`) with absolute CT timestamp on hover
- Section stagger fade-in on page load (`prefers-reduced-motion` respected)

The frozen template (CSS + per-item HTML) lives in two places that must stay in sync:

1. **`out/api/news.py`** — the function. Source of truth for what's served.
2. **`~/.claude/commands/daily-news-debrief.md`** — the Claude Code slash command, kept around for manual rebuilds and as documentation.

If you change the design, update both.

## Deploying

```bash
# from the repo root
bash deploy.sh
```

Requires `vercel` CLI logged in. The script bakes in `--scope godfreyps-projects` (Vercel CLI requires explicit scope in non-interactive mode).

## Adding or changing a person

Edit the `PEOPLE` list in `out/api/news.py`. Each entry is `(name, [(kind, key), ...])`:

- `("x", "<handle>")` — Twitter/X handle without the `@`
- `("youtube", "<channel_id>")` — must be the `UC…` channel ID, not the @handle
- `("blog", "<full RSS URL>")` — for Substack and similar

To resolve a YouTube `@handle` to a channel ID:
```bash
curl -sL "https://www.youtube.com/@<handle>" | grep -oE '"channelId":"UC[A-Za-z0-9_-]+"' | head -1
```

Mirror the change in `~/.claude/commands/daily-news-debrief.md` so manual runs match.

## Known issues / caveats

- **X supplies highlights, not a complete timeline.** The page shows a seven-day window.
- **YouTube RSS endpoint is intermittent.** Has 404'd for all channel IDs simultaneously across the entire RSS service at least once. When this happens, every YouTube section renders a fetch-error row.
- **24h window is recompute-on-render.** The function uses `now - 24h`, so if you visit at 8am CT and again at 6pm, the windows are different (and items can roll out of frame).
- **No archive.** X highlights are cached in Vercel Blob. There is no archive of past mornings.
- **Hobby-tier serverless.** 10s timeout per invocation. Parallel fetch with 6s per-feed timeout keeps total under 7s in practice.

## Origin

Started as a Claude Code session in early May 2026 to mimic the OpenClaw-style "agent that builds you a news debrief" pattern, but using only Claude Max + Claude Code (no API key, no third-party agent infra). The first iteration was a static HTML deploy by a slash command; iteration two added editorial design via taste-skill; iteration three migrated to a server-rendered function with a refresh button so the page stays live throughout the day.
