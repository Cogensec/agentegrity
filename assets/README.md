# Brand assets

Image files referenced by the README and the published spec. Keep
them small (≤ 200 KB each), in formats GitHub renders natively:

- `logo.png` — primary horizontal lockup (triangle + wordmark).
  Referenced from `README.md` at the top, height 120px. Use a
  transparent background and the canonical Cogensec navy / blue
  palette so it composites cleanly on light and dark themes.

If you need a dark-mode variant later, add `logo-dark.png` and swap
the README's `<img>` for a `<picture>` element with
`prefers-color-scheme` sources.
