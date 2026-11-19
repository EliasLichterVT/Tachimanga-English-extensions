#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import datetime, timezone
lines = ['# Sync status', '', 'Last run (UTC): ' + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'), '']
p = Path('index.json')
if p.is_file():
    items = json.loads(p.read_text(encoding='utf-8'))
    if isinstance(items, list):
        en = [item for item in items if item.get('lang') == 'en' or any(s.get('lang') == 'en' for s in item.get('sources', []))]
        lines += [f'- Upstream extension packages: {len(items)}', f'- Entries with English sources: {len(en)}', '']
lines += ['This is a full upstream mirror, **not** an English-only filtered catalog.', 'Mature/18+ entries are included when present upstream.', 'Extension functionality depends on upstream maintenance and source websites.']
Path('STATUS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
