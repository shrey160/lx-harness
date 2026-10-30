# web: the local UI

The React interface, served by `lx.server` from `web/dist/`.

| Path | Purpose |
|---|---|
| `src/app.jsx` | The whole interface: workflow steps, Models, Usage, Evidence |
| `src/style.css` | Styles |
| `src/index.html` | Page shell |
| `build.mjs` | esbuild bundling into `dist/` |

Build it from `main_LX`:

```bash
npm --prefix web ci
npm --prefix web run build
```

`dist/` is gitignored, and the server answers 503 until it exists. There is no
development server: rebuild, then refresh the page. The server's content security
policy allows only its own scripts and stylesheet, so `<style>` blocks, inline scripts
and outside assets do not load.

The UI has no logic of its own beyond display: rules live in `lx.engine`, and every
change goes through `/api` with the session token.
