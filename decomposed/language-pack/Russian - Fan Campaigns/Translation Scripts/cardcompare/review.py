"""review.json (statuses and notes) and the translation_issues.txt export."""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from pathlib import Path

from .cards import _atomic_write, natural_key, write_json_atomic

STATUSES = ('todo', 'ok', 'issue', 'wrong-image')
STORED_STATUSES = ('ok', 'wrong-image')


def _now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


class Review:
    """The review data. Re-read from disk before every write so outside edits survive."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._read()

    def _read(self) -> dict:
        data = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
                raise RuntimeError(f'Cannot read {self.path}: {e}. Fix or remove it before opening the project.') from e
        if not isinstance(data, dict):
            data = {}
        data.setdefault('version', 1)
        if not isinstance(data.get('status'), dict):
            data['status'] = {}
        if not isinstance(data.get('notes'), dict):
            data['notes'] = {}
        if not isinstance(data.get('resolved'), dict):
            data['resolved'] = {}
        return data

    def reload(self) -> None:
        with self.lock:
            self.data = self._read()

    def _write(self, change) -> None:
        with self.lock:
            self.data = self._read()
            change(self.data)
            write_json_atomic(self.path, self.data, sort_keys=True)

    # -- status --------------------------------------------------------------

    def stored_status(self, card_id: str) -> str:
        return self.data['status'].get(card_id, 'todo')

    def set_status(self, card_id: str, status: str) -> None:
        def change(data):
            if status in STORED_STATUSES:
                data['status'][card_id] = status
            else:
                data['status'].pop(card_id, None)
        self._write(change)

    # -- notes ---------------------------------------------------------------

    def note(self, key: str | None) -> str:
        if not key:
            return ''
        entry = self.data['notes'].get(key)
        return entry.get('text', '') if isinstance(entry, dict) else ''

    def has_note(self, key: str | None) -> bool:
        return bool(key and self.note(key).strip())

    def set_note(self, key: str, text: str) -> bool:
        """Save (or delete, if empty) a note. Returns True if anything changed."""
        text = text.rstrip()
        if text == self.note(key).rstrip():
            # Still re-read so an outside edit is not hidden by a no-op.
            self.reload()
            if text == self.note(key).rstrip():
                return False

        def change(data):
            if text.strip():
                data['notes'][key] = {'text': text, 'updated': _now()}
            else:
                data['notes'].pop(key, None)
        self._write(change)
        return True

    # -- resolved checks -----------------------------------------------------

    def is_resolved(self, key: str) -> bool:
        return key in self.data['resolved']

    def set_resolved(self, keys: list[str], resolved: bool) -> None:
        def change(data):
            for key in keys:
                if resolved:
                    data['resolved'].setdefault(key, _now())
                else:
                    data['resolved'].pop(key, None)
        self._write(change)

    @property
    def notes(self) -> dict[str, dict]:
        return {k: v for k, v in self.data['notes'].items() if isinstance(v, dict) and v.get('text', '').strip()}


def export_issues(path: Path, title: str, notes: dict[str, dict],
                  usage: dict[str, list[tuple[str, str]]]) -> int:
    """Write translation_issues.txt. usage maps note key -> [(English image URL, side)]."""
    by_folder: dict[str, list[str]] = {}
    for key in notes:
        folder, _, _ = key.replace('\\', '/').rpartition('/')
        by_folder.setdefault(folder, []).append(key)

    count = len(notes)
    lines = [f'# {title} — {count} issues ({_dt.date.today().isoformat()})', '']
    if not count:
        lines += ['No issues recorded.', '']
    for folder in sorted(by_folder, key=natural_key):
        lines += [f'## {folder or "(root)"}', '']
        for key in sorted(by_folder[folder], key=natural_key):
            name = os.path.splitext(key.replace('\\', '/').rpartition('/')[2])[0]
            links = []
            for en_url, _side in usage.get(key, []):
                # A bare URL stays readable as plain text and GitHub turns it into a link.
                link = en_url or '(no English card)'
                if link not in links:
                    links.append(link)
            if not links:
                links = ['(no card uses this image)']
            lines += [f'### {name} — ' + '; '.join(links), notes[key]['text'].strip(), '']
    _atomic_write(path, '\n'.join(lines).rstrip('\n') + '\n')
    return count
