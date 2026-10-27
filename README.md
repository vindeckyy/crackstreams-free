# crackstreams-free

Ad-free static mirror of [crackstreams.mx](https://crackstreams.mx/index) for
GitHub Pages.

- Same league cards, schedules, and multi-link stream players as the original.
- All ad/tracking scripts removed (Admaven pop-unders, Histats, Google
  Analytics, Cloudflare beacon). Stream embeds and the Chatango chat remain.
- Team/league logos stay hotlinked to their original hosts.

## Structure

- `mirror.py` — generator. Fetches the homepage, `/league/<slug>` pages, and
  every `/stream/<slug>` page linked from them; rewrites root-relative links;
  strips ad scripts; writes `site/`.
- `site/` — generated output (deployed to Pages).
- `.github/workflows/deploy.yml` — deploys on push to `main`, and re-fetches
  upstream twice daily (09:05 / 17:05 UTC) so schedules and stream links stay
  fresh.

## Regenerate locally

```sh
python3 mirror.py
```

Env: `BASE=/` (default). Set e.g. `BASE=/crackstreams-free/` for a project
page under a repo subpath.

Note: pages that are missing upstream at build time (404, or stream links not
published yet) are skipped or replaced with a placeholder league page.
