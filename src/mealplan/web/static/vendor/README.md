# Vendored frontend libraries

Served as-is so the web app needs no CDN and no build step (ADR-0007).

| File | Package | Version | License |
| --- | --- | --- | --- |
| `preact.module.js` | preact (`dist/preact.module.js`) | 10.29.8 | MIT (`LICENSE-preact`) |
| `hooks.module.js` | preact (`hooks/dist/hooks.module.js`) | 10.29.8 | MIT (`LICENSE-preact`) |
| `htm.module.js` | htm (`dist/htm.module.js`) | 3.1.1 | Apache-2.0 (`LICENSE-htm`) |

Local changes: `hooks.module.js` imports `./preact.module.js` instead of the bare `preact`
specifier (an import map would need an inline script, which the Content-Security-Policy
forbids), and the `sourceMappingURL` comments are removed (maps are not vendored).

To update: `npm pack preact@10 htm@3`, copy the same files, reapply the two changes.
