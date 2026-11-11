"""Reading card JSONs, pairing English and Russian cards, and writing URL changes."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

GENERIC_BACKS = frozenset({
    'https://steamusercontent-a.akamaihd.net/ugc/2342503777940351785/F64D8EFB75A9E15446D24343DA0A6EEF5B3E43DB/',
    'https://steamusercontent-a.akamaihd.net/ugc/2342503777940352139/A2D42E7E5C43D045D72CE5CFC907E4F886C8C690/',
})

IMAGE_EXTENSIONS = ('.webp', '.png', '.jpg', '.jpeg')

FIELDS = {'face': 'FaceURL', 'back': 'BackURL'}

CONTAINER_KEYS = ('ContainedObjects', 'ContainedObjects_path', 'ContainedObjects_order', 'DeckIDs')


def is_generic_back(url: str) -> bool:
    return url.strip() in GENERIC_BACKS


def is_remote(url: str) -> bool:
    return url.startswith(('http://', 'https://'))


def natural_key(text: str) -> list:
    """Sort key that orders '2-…' before '10-…'."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', text)]


def local_path(url: str) -> Path | None:
    """Turn a file:// URL or a plain path into a Path. Remote URLs return None."""
    if not url or is_remote(url):
        return None
    if url.startswith('file:///'):
        url = url[len('file:///'):]
    elif url.startswith('file://'):
        url = url[len('file://'):]
    path = Path(url)
    if not path.exists() and '%' in url:
        decoded = Path(urllib.parse.unquote(url))
        if decoded.exists():
            return decoded
    return path


def to_file_url(path: Path) -> str:
    """The form the pipeline expects: file:/// + absolute path with forward slashes."""
    return 'file:///' + str(Path(path).resolve()).replace('\\', '/')


def relative_to_root(path: Path, root: Path) -> str | None:
    """Path relative to root with forward slashes, compared case-insensitively. None if outside."""
    p = os.path.abspath(path)
    r = os.path.abspath(root).rstrip('\\/')
    if os.path.normcase(p).startswith(os.path.normcase(r) + os.sep):
        return p[len(r) + 1:].replace('\\', '/')
    return None


# ---------------------------------------------------------------------------
# Scanning

@dataclass
class Card:
    id: str
    nickname: str
    deck_key: str
    face_url: str
    back_url: str
    guid: str
    json_rel: str
    json_path: Path

    def url(self, side: str) -> str:
        return self.face_url if side == 'face' else self.back_url


@dataclass
class ScanResult:
    cards: list[Card] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)   # (json_rel, message)
    no_id: list[Card] = field(default_factory=list)


def _id_from_gmnotes(text: str) -> str:
    if not text:
        return ''
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ''
    if not isinstance(data, dict):
        return ''
    return str(data.get('id') or data.get('TtsZoopGuid') or '')


def _read_gmnotes_path(rel: str, folder: Path) -> str:
    rel = rel.replace('\\', '/')
    candidates = [folder.parent / rel]
    if '/' in rel:
        candidates.append(folder / rel.split('/', 1)[1])
    for candidate in candidates:
        if candidate.is_file():
            try:
                return candidate.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                return ''
    return ''


def card_id(data: dict, folder: Path) -> str:
    gmnotes = data.get('GMNotes', '')
    if gmnotes:
        return _id_from_gmnotes(gmnotes)
    if data.get('GMNotes_path'):
        return _id_from_gmnotes(_read_gmnotes_path(data['GMNotes_path'], folder))
    return ''


def scan_cards(folder: Path) -> ScanResult:
    """Read every TTS card under folder (recursively)."""
    result = ScanResult()
    for json_path in sorted(folder.rglob('*.json')):
        rel = json_path.relative_to(folder).as_posix()
        try:
            data = json.loads(json_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            result.errors.append((rel, str(e)))
            continue
        if not isinstance(data, dict) or 'Name' not in data:
            continue  # not a TTS object (tool output, project files, manifests…)
        if any(k in data for k in CONTAINER_KEYS):
            continue  # decks and bags hold cards, they are not cards themselves
        decks = data.get('CustomDeck')
        if not isinstance(decks, dict) or len(decks) != 1:
            continue  # containers / multi-sheet decks are not cards
        deck_key, deck = next(iter(decks.items()))
        if not isinstance(deck, dict) or 'FaceURL' not in deck:
            continue
        card = Card(
            id=card_id(data, folder),
            nickname=data.get('Nickname', '') or '',
            deck_key=deck_key,
            face_url=deck.get('FaceURL', '') or '',
            back_url=deck.get('BackURL', '') or '',
            guid=data.get('GUID', '') or '',
            json_rel=rel,
            json_path=json_path,
        )
        if card.id:
            result.cards.append(card)
        else:
            result.no_id.append(card)
    return result


# ---------------------------------------------------------------------------
# Pairing

@dataclass
class Pair:
    id: str
    en: list[Card]
    ru: list[Card]
    index: int = 0

    @property
    def en_card(self) -> Card | None:
        return self.en[0] if self.en else None

    @property
    def ru_card(self) -> Card | None:
        return self.ru[0] if self.ru else None

    @property
    def nickname(self) -> str:
        if self.ru_card and self.ru_card.nickname:
            return self.ru_card.nickname
        return self.en_card.nickname if self.en_card else ''

    @property
    def en_nickname(self) -> str:
        return self.en_card.nickname if self.en_card else ''

    def en_url(self, side: str) -> str:
        return self.en_card.url(side) if self.en_card else ''

    def ru_url(self, side: str) -> str:
        return self.ru_card.url(side) if self.ru_card else ''

    @property
    def complete(self) -> bool:
        return bool(self.en and self.ru)


def build_pairs(english: list[Card], russian: list[Card]) -> list[Pair]:
    groups_en: dict[str, list[Card]] = {}
    groups_ru: dict[str, list[Card]] = {}
    for card in english:
        groups_en.setdefault(card.id, []).append(card)
    for card in russian:
        groups_ru.setdefault(card.id, []).append(card)
    pairs = []
    for cid in set(groups_en) | set(groups_ru):
        en = sorted(groups_en.get(cid, []), key=lambda c: natural_key(c.json_rel))
        ru = sorted(groups_ru.get(cid, []), key=lambda c: natural_key(c.json_rel))
        pairs.append(Pair(cid, en, ru))
    return pairs


# ---------------------------------------------------------------------------
# Writing

def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data, *, sort_keys: bool = True) -> None:
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=sort_keys) + '\n')


def set_card_url(path: Path, field_name: str, new_value: str) -> str | None:
    """Set FaceURL/BackURL in the single CustomDeck entry of a card file.

    Returns the previous value, or None if nothing changed. The file keeps its
    formatting: only the value is replaced in the text when that can be done
    unambiguously, otherwise the file is re-serialised with the same indent,
    key order, line endings and trailing newline.
    """
    raw = path.read_bytes().decode('utf-8')
    data = json.loads(raw)
    decks = data.get('CustomDeck')
    if not isinstance(decks, dict) or len(decks) != 1:
        raise ValueError(f'{path.name} is not a single-card object')
    deck = next(iter(decks.values()))
    old = deck.get(field_name)
    if old == new_value:
        return None
    deck[field_name] = new_value

    new_text = None
    if old is not None:
        old_token = f'"{field_name}": {json.dumps(old, ensure_ascii=False)}'
        if raw.count(old_token) == 1:
            candidate = raw.replace(old_token, f'"{field_name}": {json.dumps(new_value, ensure_ascii=False)}')
            try:
                if json.loads(candidate) == data:
                    new_text = candidate
            except json.JSONDecodeError:
                pass
    if new_text is None:
        crlf = '\r\n' in raw
        new_text = json.dumps(data, indent=2, ensure_ascii=False)
        if raw.endswith(('\n', '\r\n')):
            new_text += '\n'
        if crlf:
            new_text = new_text.replace('\n', '\r\n')
    _atomic_write(path, new_text)
    return old if old is not None else ''
