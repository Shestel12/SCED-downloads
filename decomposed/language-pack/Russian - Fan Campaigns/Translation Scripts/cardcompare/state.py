"""All project state for one process: cards, pairs, images, checks, review data and edits."""

from __future__ import annotations

import datetime as _dt
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import hashing
from .cache import ImageCache
from .cards import (FIELDS, IMAGE_EXTENSIONS, Card, Pair, ScanResult, build_pairs, is_generic_back,
                    is_remote, local_path, natural_key, relative_to_root, scan_cards, set_card_url,
                    to_file_url)
from .project import Project
from .review import Review, export_issues

SIDES = ('face', 'back')
LANGS = ('en', 'ru')
DEFAULT_THRESHOLD = 20

CHECK_KINDS = {
    'parse': 'Unreadable JSON',
    'no-id': 'Cards without ID',
    'en-only': 'Not translated',
    'ru-only': 'No original',
    'missing': 'Images not found',
    'unused': 'Unused RU images',
    'untranslated': 'Still English URL',
    'generic': 'Generic back misuse',
    'shared': 'Shared RU image',
    'disagree': 'Duplicates disagree',
    'orientation': 'Orientation differs',
    'similarity': 'Low similarity',
}

FILTERS = {
    'all': 'All cards',
    'todo': 'Todo',
    'ok': 'OK',
    'issue': 'Has issue',
    'wrong-image': 'Wrong image',
    'missing': 'Image missing',
    'flagged': 'Flagged by checks',
}

SORTS = {
    'path': 'By Russian path',
    'name': 'By English name',
    'similarity': 'By similarity (suspicious first)',
}


def log(message: str) -> None:
    print(f'[{_dt.datetime.now():%H:%M:%S}] {message}', flush=True)


@dataclass
class Check:
    kind: str
    text: str
    pair: int | None = None          # Pair.index, if the entry opens a card
    side: str | None = None
    path: Path | None = None          # an image the entry is about (unused images)
    key: str = ''                     # stable identity, used to remember that the entry is resolved
    resolved: bool = False


@dataclass
class UndoEntry:
    description: str
    pair: int
    changes: list[tuple[Path, str, str, str]] = field(default_factory=list)  # (json, field, old, new)


class EditError(Exception):
    pass


class State:
    def __init__(self, project: Project):
        self.project = project
        self.lock = threading.RLock()
        self.cache = ImageCache(project.english_cache)
        self.hashes = hashing.HashCache(project.english_cache)
        self.review = Review(project.review_path)
        self.en_scan = ScanResult()
        self.ru_scan = ScanResult()
        self.pairs: list[Pair] = []
        self.order: list[int] = []           # Pair.index in the current sort order
        self.number: dict[int, int] = {}     # Pair.index -> 1-based number in that order
        self.checks: list[Check] = []
        self.flagged: set[int] = set()
        self.undo_stack: list[UndoEntry] = []
        self.threshold = DEFAULT_THRESHOLD
        self.sort = 'path'
        self.filter = 'all'
        self.search = ''
        self.current: int | None = None
        self.hash_progress: tuple[int, int] | None = None
        self.hashing_done = False
        self.generation = 0                  # bumped on every change that affects the UI
        self._by_json: dict[str, Card] = {}

    # ------------------------------------------------------------------ loading

    def load(self) -> None:
        p = self.project
        log(f'Scanning English cards in {p.english_cards}')
        self.en_scan = scan_cards(p.english_cards)
        log(f'Scanning Russian cards in {p.russian_cards}')
        self.ru_scan = scan_cards(p.russian_cards)
        self.pairs = build_pairs(self.en_scan.cards, self.ru_scan.cards)
        # Stable index (used in image URLs) by the default order.
        self.pairs.sort(key=self._path_key)
        for i, pair in enumerate(self.pairs):
            pair.index = i
        self._by_json = {os.path.normcase(str(c.json_path)): c for c in self.ru_scan.cards}
        self.apply_sort()
        self.compute_checks()
        complete = sum(pr.complete for pr in self.pairs)
        log(f'{len(self.ru_scan.cards)} Russian cards, {len(self.en_scan.cards)} English cards, '
            f'{complete} pairs, {len(self.pairs) - complete} unpaired IDs')
        if self.pairs:
            self.current = self.order[0]

    def pair(self, index: int | None) -> Pair | None:
        if index is None or not 0 <= index < len(self.pairs):
            return None
        return self.pairs[index]

    # ------------------------------------------------------------------ images

    def en_path(self, url: str) -> Path | None:
        if not url:
            return None
        if is_remote(url):
            return self.cache.lookup(url)
        path = local_path(url)
        return path if path and path.is_file() else None

    def ru_path(self, url: str) -> Path | None:
        return self.en_path(url)  # same rules: local from disk, remote through the cache

    def image(self, pair: Pair, side: str, lang: str) -> Path | None:
        url = pair.en_url(side) if lang == 'en' else pair.ru_url(side)
        return self.en_path(url)

    def ru_key(self, url: str) -> str | None:
        """Note key / display name of a local Russian image; None for remote or generic URLs."""
        if not url or is_remote(url):
            return None
        path = local_path(url)
        if path is None:
            return None
        rel = relative_to_root(path, self.project.russian_images)
        return rel if rel is not None else Path(os.path.abspath(path)).as_posix()

    def display_name(self, url: str) -> str:
        if not url:
            return '(none)'
        if is_generic_back(url):
            return 'generic card back'
        return self.ru_key(url) or url

    def note_key(self, pair: Pair, side: str) -> str | None:
        if not pair.ru:
            return None
        url = pair.ru_url(side)
        if is_generic_back(url):
            return None
        return self.ru_key(url)

    def image_info(self, path: Path | None) -> dict | None:
        """Size (and hash, once computed) of an image file."""
        if path is None:
            return None
        info = self.hashes.get(path)
        if info is not None:
            return info
        try:
            from PIL import Image
            with Image.open(path) as im:
                return {'w': im.size[0], 'h': im.size[1]}
        except Exception:
            return None

    def is_landscape(self, pair: Pair, side: str, lang: str) -> bool:
        info = self.image_info(self.image(pair, side, lang))
        return bool(info and info.get('w', 0) > info.get('h', 0))

    def distance(self, pair: Pair, side: str) -> int | None:
        if not pair.complete:
            return None
        return hashing.distance(self.hashes.get(self.image(pair, side, 'en')),
                                self.hashes.get(self.image(pair, side, 'ru')))

    def similarity(self, pair: Pair) -> int | None:
        values = [d for d in (self.distance(pair, s) for s in SIDES) if d is not None]
        return max(values) if values else None

    def image_version(self, path: Path | None) -> str:
        if path is None:
            return '0'
        try:
            st = path.stat()
        except OSError:
            return '0'
        return f'{abs(hash((str(path), st.st_mtime_ns, st.st_size))) % 10**10}'

    def all_image_paths(self) -> list[Path]:
        paths = []
        for pair in self.pairs:
            for side in SIDES:
                for lang in LANGS:
                    path = self.image(pair, side, lang)
                    if path is not None:
                        paths.append(path)
        return paths

    def run_hashing(self, progress=None) -> None:
        paths = self.all_image_paths()
        log(f'Hashing {len(set(paths))} images')

        def report(done, total):
            self.hash_progress = (done, total)
            if progress:
                progress(done, total)
        self.hashing_done = False
        self.hashes.ensure_all(paths, report)
        self.hash_progress = None
        self.hashing_done = True
        with self.lock:
            self.compute_checks()
            if self.sort == 'similarity':
                self.apply_sort()
            if self.review.notes:
                self.export()  # duplicate English images can now be collapsed to one link
            self.generation += 1
        log('Hashing finished')

    # ------------------------------------------------------------------ downloads

    def remote_urls(self) -> list[str]:
        urls = set()
        for card in self.en_scan.cards + self.en_scan.no_id:
            urls.update(u for u in (card.face_url, card.back_url) if is_remote(u))
        for card in self.ru_scan.cards + self.ru_scan.no_id:
            urls.update(u for u in (card.face_url, card.back_url) if is_remote(u))
        return sorted(urls)

    def missing_urls(self) -> list[str]:
        return self.cache.missing(self.remote_urls())

    # ------------------------------------------------------------------ status

    def status(self, pair: Pair) -> str:
        stored = self.review.stored_status(pair.id)
        if stored == 'wrong-image':
            return 'wrong-image'
        if any(self.review.has_note(self.note_key(pair, s)) for s in SIDES):
            return 'issue'
        return stored if stored == 'ok' else 'todo'

    def set_status(self, pair: Pair, status: str) -> None:
        self.review.set_status(pair.id, status)
        self.generation += 1
        log(f'Status of "{pair.nickname}" ({pair.id}) -> {status}')

    def counts(self) -> dict[str, int]:
        counts = {'ok': 0, 'issue': 0, 'wrong-image': 0, 'todo': 0}
        for pair in self.pairs:
            if pair.complete:
                counts[self.status(pair)] += 1
        return counts

    def set_note(self, pair: Pair, side: str, text: str) -> bool:
        key = self.note_key(pair, side)
        if key is None:
            raise EditError('This image cannot have notes')
        changed = self.review.set_note(key, text)
        if changed:
            self.export()
            self.generation += 1
            log(f'Note on {key}: {"deleted" if not text.strip() else repr(text.strip()[:60])}')
        return changed

    def export(self) -> int:
        usage: dict[str, list[tuple[str, str]]] = {}   # note key -> [(English image URL, side)]
        for pair in self.pairs:
            for card in pair.ru:
                for side in SIDES:
                    url = card.url(side)
                    if is_generic_back(url):
                        continue
                    key = self.ru_key(url)
                    if key:
                        usage.setdefault(key, []).append((pair.en_url(side) if pair.en else '', side))
        # Copies of a card often have separate English uploads of the same picture: link it once.
        for key, entries in usage.items():
            kept: list[tuple[str, str]] = []
            for en_url, side in entries:
                if not any(side == s and self._same_image(en_url, u) for u, s in kept):
                    kept.append((en_url, side))
            usage[key] = kept
        count = export_issues(self.project.export_path, self.project.title, self.review.notes, usage)
        return count

    # ------------------------------------------------------------------ ordering

    def _path_key(self, pair: Pair):
        if not pair.ru:
            return (2, natural_key(pair.en_nickname), [pair.id])
        key = self.ru_key(pair.ru_url('face'))
        if key is None:  # face is still a remote (English) URL or a generic back
            return (1, natural_key(pair.nickname), natural_key(pair.ru_card.json_rel))
        return (0, natural_key(key), natural_key(pair.ru_card.json_rel))

    def apply_sort(self) -> None:
        if self.sort == 'name':
            key = lambda p: (natural_key(p.en_nickname or p.nickname), self._path_key(p))
        elif self.sort == 'similarity':
            key = lambda p: (-(self.similarity(p) if self.similarity(p) is not None else -1), self._path_key(p))
        else:
            key = self._path_key
        self.order = [p.index for p in sorted(self.pairs, key=key)]
        self.number = {index: n + 1 for n, index in enumerate(self.order)}

    def folder_of(self, pair: Pair) -> str:
        if not pair.ru:
            return '(no Russian card)'
        key = self.ru_key(pair.ru_url('face'))
        if key is None:
            return '(face not a local image)'
        folder = key.rpartition('/')[0]
        return folder or '(root)'

    def has_missing_image(self, pair: Pair) -> bool:
        for side in SIDES:
            for lang, cards in (('en', pair.en), ('ru', pair.ru)):
                if cards and (pair.en_url(side) if lang == 'en' else pair.ru_url(side)) \
                        and self.image(pair, side, lang) is None:
                    return True
        return False

    def matches(self, pair: Pair) -> bool:
        f = self.filter
        if f in ('todo', 'ok', 'issue', 'wrong-image'):
            if not pair.complete or self.status(pair) != f:
                return False
        elif f == 'missing' and not self.has_missing_image(pair):
            return False
        elif f == 'flagged' and pair.index not in self.flagged:
            return False
        q = self.search.strip().casefold()
        if not q:
            return True
        haystack = [pair.id]
        for card in pair.en + pair.ru:
            haystack += [card.nickname, card.guid, card.json_rel]
        for side in SIDES:
            haystack.append(self.display_name(pair.ru_url(side)))
            en_url = pair.en_url(side)
            haystack.append(en_url)
            cached = self.image(pair, side, 'en')
            if cached is not None:
                haystack.append(cached.name)
        return any(q in (h or '').casefold() for h in haystack)

    def view(self) -> list[int]:
        return [i for i in self.order if self.matches(self.pairs[i])]

    # ------------------------------------------------------------------ checks

    def compute_checks(self) -> None:
        checks: list[Check] = []
        add = checks.append
        p = self.project

        for label, scan in (('English', self.en_scan), ('Russian', self.ru_scan)):
            for rel, error in scan.errors:
                add(Check('parse', f'{label}: {rel} — {error}'))
            for card in scan.no_id:
                add(Check('no-id', f'{label}: {card.json_rel} ({card.nickname or "no nickname"})'))

        used_ru: set[str] = set()
        shared: dict[str, list[tuple[str, str, str]]] = {}   # ru image key -> [(en url, label, ...)]
        shared_pairs: dict[str, set[int]] = {}
        not_downloaded: dict[str, list[int]] = {}             # remote url -> pairs using it

        for pair in self.pairs:
            name = pair.nickname or pair.id
            if pair.en and not pair.ru:
                add(Check('en-only', f'{pair.en_nickname} — {pair.en_card.json_rel}', pair.index))
                continue
            if pair.ru and not pair.en:
                add(Check('ru-only', f'{name} — {pair.ru_card.json_rel}', pair.index))
            for lang, cards in (('en', pair.en), ('ru', pair.ru)):
                for side in SIDES:
                    values = {c.url(side) for c in cards}
                    if len(values) > 1:
                        add(Check('disagree', f'{name}: {lang.upper()} {side} differs between '
                                  f'{len(cards)} files ({", ".join(c.json_rel for c in cards)})', pair.index, side))
            for card in pair.ru:
                for side in SIDES:
                    path = self.en_path(card.url(side))
                    if path is not None:
                        used_ru.add(os.path.normcase(os.path.abspath(path)))
            for side in SIDES:
                ru_url = pair.ru_url(side) if pair.ru else ''
                en_url = pair.en_url(side) if pair.en else ''
                for lang, url in (('EN', en_url), ('RU', ru_url)):
                    if url and self.en_path(url) is None:
                        if is_remote(url):
                            not_downloaded.setdefault(url, []).append(pair.index)
                        else:
                            add(Check('missing', f'{name}: RU {side} file missing — {self.display_name(url)}',
                                      pair.index, side))
                if not pair.complete:
                    continue
                if ru_url == en_url and ru_url and not is_generic_back(ru_url):
                    add(Check('untranslated', f'{name}: RU {side} still uses the English URL', pair.index, side))
                if side == 'face' and is_generic_back(ru_url):
                    add(Check('generic', f'{name}: generic back used as the Russian face', pair.index, side))
                if side == 'back' and is_generic_back(en_url) and not is_generic_back(ru_url):
                    add(Check('generic', f'{name}: English back is generic but the Russian back is '
                              f'{self.display_name(ru_url)}', pair.index, side))
                key = self.ru_key(ru_url) if not is_generic_back(ru_url) else None
                if key and en_url:
                    shared.setdefault(key, []).append((en_url, f'{name} ({side})', side))
                    shared_pairs.setdefault(key, set()).add(pair.index)
                ru_info = self.image_info(self.image(pair, side, 'ru'))
                en_info = self.image_info(self.image(pair, side, 'en'))
                if ru_info and en_info and 'w' in ru_info and 'w' in en_info:
                    ru_land = ru_info['w'] > ru_info['h']
                    en_land = en_info['w'] > en_info['h']
                    if ru_land != en_land:
                        add(Check('orientation', f'{name}: {side} is {"landscape" if en_land else "portrait"} '
                                  f'in English, {"landscape" if ru_land else "portrait"} in Russian',
                                  pair.index, side))
                d = self.distance(pair, side)
                if d is not None and d > self.threshold:
                    add(Check('similarity', f'{name}: {side} distance {d}', pair.index, side))

        for url, indexes in sorted(not_downloaded.items()):
            first = min(indexes, key=lambda i: self.number.get(i, i))
            label = 'generic card back' if is_generic_back(url) else url
            add(Check('missing', f'Not downloaded: {label} (used by {len(set(indexes))} card(s))', first))

        for key, users in shared.items():
            groups = self._distinct_images([u[0] for u in users])
            if groups > 1:
                names = '; '.join(sorted({u[1] for u in users}))
                first = min(shared_pairs[key], key=lambda i: self.number.get(i, i))
                add(Check('shared', f'{key} is used by cards with {groups} different English images: {names}',
                          first))

        root = p.russian_images
        if root.is_dir():
            for path in sorted(root.rglob('*'), key=lambda x: natural_key(x.as_posix())):
                if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file():
                    if os.path.normcase(os.path.abspath(path)) not in used_ru:
                        add(Check('unused', relative_to_root(path, root) or str(path), path=path))

        similar = [c for c in checks if c.kind == 'similarity']
        others = [c for c in checks if c.kind != 'similarity']
        similar.sort(key=lambda c: -int(c.text.rsplit(' ', 1)[1]))
        self.checks = others + similar
        for c in self.checks:
            pair = self.pair(c.pair)
            # The text is part of the key, so an entry comes back if what it reports changes
            # (e.g. a new similarity distance after the image was replaced).
            c.key = '|'.join((c.kind, pair.id if pair else '', c.side or '', c.text))
            c.resolved = self.review.is_resolved(c.key)
        self._update_flagged()
        self.cache.save()  # manifest entries adopted from old-style cache names
        self.generation += 1

    SAME_IMAGE_DISTANCE = 8

    def _same_image(self, a: str, b: str) -> bool:
        """Same URL, or (once hashed) visually the same picture."""
        if a == b:
            return True
        ha, hb = self.hashes.get(self.en_path(a)), self.hashes.get(self.en_path(b))
        d = hashing.distance(ha, hb)
        return d is not None and d <= self.SAME_IMAGE_DISTANCE

    def _distinct_images(self, urls: list[str]) -> int:
        """Number of visually different images among urls (by hash; by URL if not hashed)."""
        reps: list[tuple[str, dict | None]] = []
        for url in dict.fromkeys(urls):
            info = self.hashes.get(self.en_path(url))
            for rep_url, rep_info in reps:
                if info and rep_info and 'hash' in info and 'hash' in rep_info:
                    if hashing.distance(info, rep_info) <= self.SAME_IMAGE_DISTANCE:
                        break
                elif rep_url == url:
                    break
            else:
                reps.append((url, info))
        return len(reps)

    def _update_flagged(self) -> None:
        self.flagged = {c.pair for c in self.checks if c.pair is not None and not c.resolved}

    def open_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.resolved]

    def checks_for(self, pair: Pair, include_resolved: bool = False) -> list[Check]:
        return [c for c in self.checks if c.pair == pair.index and (include_resolved or not c.resolved)]

    def set_resolved(self, checks: list[Check], resolved: bool) -> int:
        """Mark check entries as resolved (or open again). Returns how many changed."""
        with self.lock:
            changed = [c for c in checks if c.resolved != resolved]
            if not changed:
                return 0
            self.review.set_resolved([c.key for c in changed], resolved)
            for c in changed:
                c.resolved = resolved
            self._update_flagged()
            self.generation += 1
        what = changed[0].text if len(changed) == 1 else f'{len(changed)} entries'
        log(f'Check {"resolved" if resolved else "reopened"}: {what}')
        return len(changed)

    # ------------------------------------------------------------------ edits

    def normalize_value(self, raw: str) -> str:
        value = raw.strip().strip('"').strip("'").strip()
        if not value:
            raise EditError('Empty value')
        if is_remote(value):
            return value
        path = local_path(value)
        if path is None:
            raise EditError(f'Not a path or URL: {value}')
        if not path.is_absolute():
            path = self.project.russian_images / path
        if not path.is_file():
            raise EditError(f'File not found: {path}')
        return to_file_url(path)

    def _write(self, pair: Pair, side: str, value: str, entry: UndoEntry) -> int:
        field_name = FIELDS[side]
        changed = 0
        for card in pair.ru:
            old = set_card_url(card.json_path, field_name, value)
            if old is not None:
                entry.changes.append((card.json_path, field_name, old, value))
                changed += 1
            setattr(card, 'face_url' if side == 'face' else 'back_url', value)
        return changed

    def _after_edit(self, pair: Pair) -> None:
        if self.review.stored_status(pair.id) == 'wrong-image':
            self.review.set_status(pair.id, 'todo')
        self.compute_checks()
        self.export()
        self.generation += 1

    def set_ru_url(self, pair: Pair, side: str, value: str) -> int:
        """Write a (normalised) URL to every Russian card of the pair. Returns files changed."""
        if not pair.ru:
            raise EditError('This card has no Russian version')
        with self.lock:
            entry = UndoEntry(f'{side} of "{pair.nickname}"', pair.index)
            try:
                changed = self._write(pair, side, value, entry)
            finally:
                if entry.changes:
                    self.undo_stack.append(entry)
            self._after_edit(pair)
        log(f'Set RU {side} of "{pair.nickname}" ({pair.id}) to {self.display_name(value)} '
            f'in {changed} file(s)')
        return changed

    def swap(self, pair: Pair) -> int:
        if not pair.ru:
            raise EditError('This card has no Russian version')
        with self.lock:
            face, back = pair.ru_url('face'), pair.ru_url('back')
            entry = UndoEntry(f'swap of "{pair.nickname}"', pair.index)
            changed = 0
            try:
                for card in pair.ru:
                    for field_name, value in (('FaceURL', back), ('BackURL', face)):
                        old = set_card_url(card.json_path, field_name, value)
                        if old is not None:
                            entry.changes.append((card.json_path, field_name, old, value))
                            changed += 1
                    card.face_url, card.back_url = back, face
            finally:
                if entry.changes:
                    self.undo_stack.append(entry)
            self._after_edit(pair)
        log(f'Swapped RU face and back of "{pair.nickname}" ({pair.id})')
        return changed

    def undo(self) -> UndoEntry | None:
        with self.lock:
            if not self.undo_stack:
                return None
            entry = self.undo_stack.pop()
            for path, field_name, old, _new in reversed(entry.changes):
                set_card_url(path, field_name, old)
                card = self._by_json.get(os.path.normcase(str(path)))
                if card is not None:
                    if field_name == 'FaceURL':
                        card.face_url = old
                    else:
                        card.back_url = old
            self.compute_checks()
            self.export()
            self.generation += 1
        log(f'Undid {entry.description} ({len(entry.changes)} change(s))')
        return entry
