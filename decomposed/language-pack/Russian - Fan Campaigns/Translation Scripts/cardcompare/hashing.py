"""Perceptual hashes and image sizes, cached in english_cache/hash_cache.json."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

from .cards import write_json_atomic

HASH_CACHE = 'hash_cache.json'


def _key(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return f'{os.path.normcase(os.path.abspath(path))}|{st.st_size}|{st.st_mtime_ns}'


def compute(path: Path) -> dict:
    import imagehash
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
        im.draft('RGB', (256, 256))  # faster JPEG decode, phash works on 32x32 anyway
        return {'hash': str(imagehash.phash(im.convert('RGB'))), 'w': w, 'h': h}


class HashCache:
    def __init__(self, folder: Path):
        self.path = folder / HASH_CACHE
        self.lock = threading.RLock()
        self.entries: dict[str, dict] = {}
        self._dirty = False
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    self.entries = data
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass

    def get(self, path: Path | None) -> dict | None:
        if path is None:
            return None
        key = _key(path)
        return self.entries.get(key) if key else None

    def ensure(self, path: Path) -> dict | None:
        key = _key(path)
        if key is None:
            return None
        with self.lock:
            if key in self.entries:
                return self.entries[key]
        try:
            entry = compute(path)
        except Exception as e:  # unreadable image
            entry = {'error': f'{type(e).__name__}: {e}'}
        with self.lock:
            self.entries[key] = entry
            self._dirty = True
        return entry

    def ensure_all(self, paths, progress: Callable[[int, int], None] | None = None) -> None:
        from concurrent.futures import ThreadPoolExecutor
        unique = list(dict.fromkeys(p for p in paths if p is not None))
        done = 0
        with ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) // 2)) as pool:
            for _ in pool.map(self.ensure, unique):
                done += 1
                if progress and (done % 10 == 0 or done == len(unique)):
                    progress(done, len(unique))
                if done % 100 == 0:
                    self.save()
        self.save()

    def save(self) -> None:
        with self.lock:
            if not self._dirty:
                return
            # Keep the cache small: drop entries whose file is gone.
            alive = {}
            for key, value in self.entries.items():
                if os.path.exists(key.rsplit('|', 2)[0]):
                    alive[key] = value
            self.entries = alive
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(self.path, self.entries)
            except OSError:
                return
            self._dirty = False


def distance(a: dict | None, b: dict | None) -> int | None:
    if not a or not b or 'hash' not in a or 'hash' not in b:
        return None
    import imagehash
    return imagehash.hex_to_hash(a['hash']) - imagehash.hex_to_hash(b['hash'])
