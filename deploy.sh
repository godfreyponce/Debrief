#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v vercel >/dev/null 2>&1; then
  echo "ERROR: vercel CLI not installed." >&2
  echo "Run: npm install -g vercel && vercel login && cd ~/Desktop/newsDebrief && vercel link" >&2
  exit 1
fi

vercel deploy out --prod --yes --scope godfreyps-projects 2>&1 | tee /tmp/news-debrief-deploy.log
url=$(grep -Eo '"url":\s*"https://[^"]+\.vercel\.app"' /tmp/news-debrief-deploy.log | head -1 | grep -Eo 'https://[^"]+')
[ -z "$url" ] && url=$(grep -Eo 'https://[a-zA-Z0-9.-]+\.vercel\.app' /tmp/news-debrief-deploy.log | tail -1)
echo "$url"
