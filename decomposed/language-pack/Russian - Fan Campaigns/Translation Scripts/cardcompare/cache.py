"""English image cache: manifest lookup, adoption of older caches, and the downloader."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable

from .cards import write_json_atomic

MANIFEST = 'manifest.json'
MAX_DIM = 1050
JPEG_QUALITY = 90
WORKERS = 8
RETRIES = 4
USER_AGENT = 'card-compare/1.0 (image cache)'

_GITHUB_BLOB = re.compile(r'^https://github\.com/([^/]+)/([^/]+)/blob/(.+?)(?:\?.*)?$')


def stem_name(url: str) -> str:
    """…/no-bleed/022a.png?raw=true -> 022a.jpg ; https://i.imgur.com/WsAtjbs.jpg -> WsAtjbs.jpg"""
    last = url.split('?', 1)[0].split('#', 1)[0].rstrip('/').rsplit('/', 1)[-1]
    stem = os.path.splitext(last)[0]
    stem = re.sub(r'[<>:"/\\|?*]', '_', stem) or 'image'
    return stem + '.jpg'


def flattened_stem(url: str) -> str:
    """https://i.imgur.com/04AIYnR.jpg -> httpsiimgurcom04AIYnRjpg"""
    return re.sub(r'[^a-zA-Z0-9]', '', url)


def download_url(url: str) -> str:
    """Rewrite GitHub blob links to raw.githubusercontent.com."""
    m = _GITHUB_BLOB.match(url)
    if m:
        owner, repo, rest = m.groups()
        return f'https://raw.githubusercontent.com/{owner}/{repo}/{rest}'
    return url


@dataclass
class DownloadReport:
    downloaded: int = 0
    cached: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False


class ImageCache:
    def __init__(self, folder: Path):
        self.folder = folder
        self.lock = threading.RLock()
        self.manifest: dict[str, str] = {}
        self._dirty = False
        path = folder / MANIFEST
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(loaded, dict):
                    self.manifest = {str(k): str(v) for k, v in loaded.items()}
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass

    # -- lookup ------------------------------------------------------------

    def _claimed(self) -> set[str]:
        return {v.casefold() for v in self.manifest.values()}

    def lookup(self, url: str) -> Path | None:
        """Path of the cached image for url, adopting old-style names if needed."""
        url = url.strip()
        if not url:
            return None
        with self.lock:
            name = self.manifest.get(url)
            if name:
                path = self.folder / name
                return path if path.is_file() else None
            claimed = self._claimed()
            flat = flattened_stem(url)
            for candidate in (stem_name(url), flat + '.jpg', flat + '.png'):
                path = self.folder / candidate
                if path.is_file() and candidate.casefold() not in claimed:
                    self.manifest[url] = candidate
                    self._dirty = True
                    return path
        return None

    def save(self) -> None:
        with self.lock:
            if not self._dirty:
                return
            self.folder.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.folder / MANIFEST, self.manifest)
            self._dirty = False

    # -- downloading ---------------------------------------------------------

    def _assign_names(self, urls: list[str]) -> dict[str, str]:
        """Stem names, falling back to flattened names on any collision."""
        taken = self._claimed()
        taken |= {p.name.casefold() for p in self.folder.glob('*')} if self.folder.is_dir() else set()
        by_stem: dict[str, list[str]] = {}
        for url in urls:
            by_stem.setdefault(stem_name(url).casefold(), []).append(url)
        names = {}
        for stem, group in by_stem.items():
            if len(group) == 1 and stem not in taken:
                names[group[0]] = stem_name(group[0])
                taken.add(stem)
                continue
            for url in group:
                name = flattened_stem(url)[:180] + '.jpg'
                n = 2
                while name.casefold() in taken:
                    name = f'{flattened_stem(url)[:170]}_{n}.jpg'
                    n += 1
                names[url] = name
                taken.add(name.casefold())
        return names

    def missing(self, urls) -> list[str]:
        return sorted({u.strip() for u in urls if u and u.strip() and self.lookup(u) is None})

    def download(self, urls, progress: Callable[[int, int], None] | None = None,
                 cancel: threading.Event | None = None) -> DownloadReport:
        import requests
        from PIL import Image

        report = DownloadReport()
        wanted = sorted({u.strip() for u in urls if u and u.strip()})
        todo = []
        for url in wanted:
            if self.lookup(url) is not None:
                report.cached += 1
            else:
                todo.append(url)
        self.save()
        if not todo:
            return report
        self.folder.mkdir(parents=True, exist_ok=True)
        with self.lock:
            names = self._assign_names(todo)
        session = requests.Session()
        session.headers['User-Agent'] = USER_AGENT
        cancel = cancel or threading.Event()

        def fetch(url: str) -> tuple[str, str | None]:
            last_error = 'unknown error'
            for attempt in range(RETRIES):
                if cancel.is_set():
                    return url, 'cancelled'
                try:
                    r = session.get(download_url(url), timeout=60)
                    if r.status_code == 429 or r.status_code >= 500:
                        last_error = f'HTTP {r.status_code}'
                        retry_after = r.headers.get('Retry-After', '')
                        delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                        time.sleep(min(delay, 30))
                        continue
                    r.raise_for_status()
                    im = Image.open(BytesIO(r.content))
                    im = im.convert('RGB')
                    im.thumbnail((MAX_DIM, MAX_DIM), Image.Resampling.LANCZOS)
                    target = self.folder / names[url]
                    tmp = target.with_name(target.name + '.part')
                    im.save(tmp, 'JPEG', quality=JPEG_QUALITY)
                    os.replace(tmp, target)
                    return url, None
                except requests.RequestException as e:
                    last_error = str(e)
                    time.sleep(2 ** attempt)
                except Exception as e:  # bad image data, disk errors
                    return url, f'{type(e).__name__}: {e}'
            return url, last_error

        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(fetch, url) for url in todo]
            for future in as_completed(futures):
                url, error = future.result()
                done += 1
                if error is None:
                    with self.lock:
                        self.manifest[url] = names[url]
                        self._dirty = True
                    report.downloaded += 1
                elif error != 'cancelled':
                    report.failed.append((url, error))
                if progress:
                    progress(done, len(todo))
                if done % 25 == 0:
                    self.save()
        report.cancelled = cancel.is_set()
        self.save()
        return report
