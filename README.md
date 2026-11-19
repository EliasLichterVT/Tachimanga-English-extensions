# Tachimanga extension mirror

This repository mirrors the community-maintained [Keiyoushi extension repository](https://github.com/keiyoushi/extensions) on a daily schedule. It includes English-language manga, manhwa, and manhua sources, including upstream adult/18+ extensions. **It currently mirrors all languages, not just English.** No extensions are independently developed, audited, or repaired here.

## Publish the mirror

1. Commit these starter files to the `main` branch of `EliasLichterVT/Tachimanga-English-extensions` (including the hidden `.github/workflows/sync.yml` file).
2. In GitHub, open **Actions → Sync extensions → Run workflow**.
3. Wait for the run to succeed and verify that `index.pb`, `repo.json`, and `apk/` appear in the repository root.
4. Add the following repository index URL to Tachimanga:

`https://raw.githubusercontent.com/EliasLichterVT/Tachimanga-English-extensions/main/index.pb`

The scheduled check runs daily at 05:17 UTC. It copies upstream metadata and extension APKs unchanged. It does not fix broken websites or guarantee compatibility. GitHub storage/bandwidth limits may make a complete mirror impractical; if so, use the maintained upstream repository directly.

Only install extensions you trust. This project is independent of Tachimanga, Mihon, and Keiyoushi.
