# Self-Hosted Docker App Catalogs — Programmatic Data Sources

Research date: 2026-11-25. Goal: let an automation agent enumerate self-hosted Docker apps
without rendering JS. Every endpoint below was tested with `fetch`/curl unless noted.

## Key discovery: selfh.st is a Ghost CMS + client-side app

`https://selfh.st/apps` is a Ghost 6.x page. The static HTML contains **no app list** — only a
`<noscript>` fallback and an empty `#dataContainer`. The listing is built in the browser by
`https://r2.selfh.st/build/apps/apps.<ver>.min.js`, which fetches two static JSON files from
Cloudflare R2. The HTML `<head>` even preloads them:

```html
<link rel="preload" as="fetch" crossorigin href="https://r2.selfh.st/directory/software.json">
<link rel="preload" as="fetch" crossorigin href="https://r2.selfh.st/directory/metrics.json">
```

So there **is** a clean static data file — no API, no auth, no JS needed.

## Sources

| Source | URL / endpoint | Format | Auth? | Reliability | Notes |
|---|---|---|---|---|---|
| **selfh.st catalog (PRIMARY)** | `https://r2.selfh.st/directory/software.json` | JSON `{entries:[…]}` | No | **High** — static R2 object, CORS `crossorigin`, ~1 MB | Full catalog (~1,700 apps). Per entry: `id, name, reference, website, repository, description, source, license, language, icon_source, platform[], sso, tags[], created, alternatives[]`. `tags`/`alternatives` are numeric IDs into a separate (unfound) map. |
| **selfh.st metrics** | `https://r2.selfh.st/directory/metrics.json` | JSON `{built, apps:[…]}` | No | **High** — static R2 object | Per app `id`: `stars, forks, commit, trending`. Join to software.json on `id`. `built` = ISO timestamp. Use for popularity/trending sort. |
| selfh.st JSON API | `https://selfh.st/api/apps` | — | — | **None** — 404 | No REST API exists. Ghost content API (`/ghost/api/content/`, key in page source) only serves blog posts, not the directory. |
| selfh.st tags map | `https://r2.selfh.st/directory/tags.json` | — | — | **None** — 404 | Tag names not at this path; software.json `tags[]` are numeric IDs. Resolve names from the JS bundle or the `/apps` filter UI if needed. |
| **awesome-selfhosted-data (PRIMARY alt)** | `https://github.com/awesome-selfhosted/awesome-selfhosted-data` → `software/<slug>.yml` | YAML, one file per app | No | **High** — raw.githubusercontent.com, git-versioned | ~1,500+ apps. Per file (verified `immich.yml`): `name, website_url, description, licenses[], platforms[] (Docker/K8S/…), tags[], source_code_url, demo_url, icon_url, stargazers_count, updated_at, archived, current_release{tag,published_at}, commit_history{}`. List files via GitHub contents API. |
| awesome-selfhosted README | `https://raw.githubusercontent.com/awesome-selfhosted/awesome-selfhosted/master/README.md` | Markdown | No | High | Human list grouped by category; parseable but legacy. `platforms` shown as `` `Docker` `` tokens. |
| awesome-selfhosted.net API | `https://awesome-selfhosted.net/api/apps` | JSON (claimed) | No | **Low from agents** — 403 / timeout | Site exists but blocks non-browser UAs / times out via fetch. Do not rely on it; use the GitHub data repo instead. |
| selfh.st icons | `https://cdn.jsdelivr.net/gh/selfhst/icons@main/png/<ref>.png` | PNG/SVG | No | High | Icon lookup by `reference` slug from software.json. |
| r/selfhosted, selfh community | reddit.com/r/selfhosted, selfh.st weekly | HTML/RSS | No | Medium | Discovery/news only, not structured. Use selfh.st RSS (`/rss/`) for content. |
| Docker Hub | hub.docker.com/v2/search/repositories/?query=… | JSON | No (rate-limited) | Medium | Image-level metadata (pulls, stars, description). Good for "is there an image" checks, not for app curation. |

## Docker run / docker-compose snippets for a given app

There is **no reliable universal snippet API**. The dependable path is per-app:

1. Take `repository` (software.json) or `source_code_url` (awesome-selfhosted YAML) — the canonical upstream repo.
2. Fetch `docker-compose.yml` / `compose.yaml` from that repo (default branch, or the release tag from `current_release.tag`). Most self-hosted apps ship one; some only document `docker run` in README.
3. Fallbacks: the app's own docs site (`website`/`website_url`), or community compose collections (e.g. linuxserver/* per-app READMEs — note `linuxserver/docker-compose` paths tested 404, repo layout differs; verify per app).

Compose snippet reliability is **per-app and unverified at scale** — treat as best-effort, always cross-check the upstream repo.

## Recommendation (single most reliable source for an offline agent)

**Use `https://r2.selfh.st/directory/software.json` as the primary catalog, joined with
`https://r2.selfh.st/directory/metrics.json` on `id`.**

Why: two plain GET requests, no auth, no JS rendering, CORS-open static R2 objects, ~1,700 apps
with repo URL + license + language + tags + alternatives, plus stars/forks/trending for ranking.
It is the exact dataset selfh.st's own frontend uses, so it stays in sync automatically.

For a **git-versioned, offline-cacheable** mirror (and richer per-app metadata like release tag and
commit history), use **awesome-selfhosted-data** (`software/*.yml`) as the secondary source. Cache
both locally; refresh the R2 JSON periodically and `git pull` the data repo. Resolve compose snippets
lazily from each app's `repository`/`source_code_url`.