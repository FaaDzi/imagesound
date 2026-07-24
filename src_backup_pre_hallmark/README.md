# Pre-Hallmark UI backup

A full copy of `src/` taken before the Hallmark-driven redesign (multi-page
plan: atmospheric genre, custom theme anchored on the existing neon palette,
N8 terminal nav, Bento Grid library, Marquee/Workbench-adapted Home/Player).

To restore the old UI:

```
rm -rf src
cp -r src_backup_pre_hallmark src
rm src/README.md   # this file isn't part of the original src/
```

Excluded from the TypeScript project (see `tsconfig.json`) so it doesn't
interfere with type-checking or the dev server. Safe to delete once you're
happy with the redesign.
