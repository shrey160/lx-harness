# web: the local UI

The React interface, served by `lx.server` from `web/dist/`.

| Path | Purpose |
|---|---|
| `src/app.jsx` | Entry point: loads state, every 1.2 seconds while a run is going and every 5 otherwise, and chooses the page |
| `src/api.js` | API calls and shared display helpers |
| `src/Sidebar.jsx` | New workspace, the workspace list, Settings |
| `src/NewWorkspace.jsx` | Create or open a workspace |
| `src/Workspace.jsx` | Workspace header and tabs; the Samples and Discuss tabs |
| `src/SpecTab.jsx` | Spec, limits and the approval review |
| `src/RunsTab.jsx` | Run list and run page |
| `src/Settings.jsx` | Settings: Tools, Usage, General |
| `src/Providers.jsx` | Settings: the provider list and form |
| `src/style.css` | The dark theme: colours are tokens at the top |
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
