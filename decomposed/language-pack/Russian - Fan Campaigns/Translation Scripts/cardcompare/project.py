"""Project files (card_compare.project.json) and the recent-projects list."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .cards import write_json_atomic

PROJECT_FILENAME = 'card_compare.project.json'
FOLDER_FIELDS = ('russian_cards', 'english_cards', 'russian_images', 'english_cache')
FIELD_LABELS = {
    'russian_cards': 'Russian cards (localised card JSONs)',
    'english_cards': 'English cards (original decomposed campaign)',
    'russian_images': 'Russian images (root folder of translated images)',
    'english_cache': 'English image cache (created if missing)',
}
RECENT_FILE = Path.home() / '.card_compare_recent.json'
RECENT_MAX = 12
SETTINGS_FILE = Path.home() / '.card_compare_settings.json'
THEMES = ('auto', 'dark', 'light')


class ProjectError(Exception):
    pass


@dataclass
class Project:
    path: Path
    title: str
    russian_cards: Path
    english_cards: Path
    russian_images: Path
    english_cache: Path

    @property
    def folder(self) -> Path:
        return self.path.parent

    @property
    def review_path(self) -> Path:
        return self.folder / 'review.json'

    @property
    def export_path(self) -> Path:
        return self.folder / 'translation_issues.txt'

    @classmethod
    def load(cls, path: str | Path) -> 'Project':
        path = Path(path).expanduser().resolve()
        if path.is_dir():
            path = path / PROJECT_FILENAME
        if not path.is_file():
            raise ProjectError(f'Project file not found: {path}')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            raise ProjectError(f'Cannot read {path}: {e}') from e
        if not isinstance(data, dict):
            raise ProjectError(f'{path} is not a JSON object')
        missing = [f for f in FOLDER_FIELDS if not data.get(f)]
        if missing:
            raise ProjectError(f'{path.name} is missing: {", ".join(missing)}')
        base = path.parent
        folders = {f: (base / data[f]).resolve() for f in FOLDER_FIELDS}
        return cls(path=path, title=str(data.get('title') or path.parent.name), **folders)

    def problems(self) -> list[str]:
        """Folder problems that prevent the project from opening."""
        return validate_folders({f: getattr(self, f) for f in FOLDER_FIELDS})


def _has_card_json(folder: Path, limit: int = 4000) -> bool:
    for n, json_path in enumerate(folder.rglob('*.json')):
        if n > limit:
            break
        try:
            data = json.loads(json_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and 'Name' in data and isinstance(data.get('CustomDeck'), dict):
            return True
    return False


def validate_folders(folders: dict[str, Path | str]) -> list[str]:
    problems = []
    for f in FOLDER_FIELDS:
        value = folders.get(f)
        if not value:
            problems.append(f'{FIELD_LABELS[f]}: not set')
            continue
        p = Path(value)
        if f == 'english_cache':
            if p.exists() and not p.is_dir():
                problems.append(f'{FIELD_LABELS[f]}: {p} is not a folder')
            continue
        if not p.is_dir():
            problems.append(f'{FIELD_LABELS[f]}: folder does not exist ({p})')
        elif f in ('russian_cards', 'english_cards') and not _has_card_json(p):
            problems.append(f'{FIELD_LABELS[f]}: no card JSONs found in {p}')
    return problems


def _rel(target: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(target, base)).as_posix()
    except ValueError:  # different drive on Windows
        return Path(target).resolve().as_posix()


def write_project(project_file: Path, title: str, folders: dict[str, Path | str]) -> Project:
    project_file = Path(project_file).resolve()
    if project_file.is_dir():
        project_file = project_file / PROJECT_FILENAME
    base = project_file.parent
    data = {'title': title}
    for f in FOLDER_FIELDS:
        data[f] = _rel(Path(folders[f]).resolve(), base)
    base.mkdir(parents=True, exist_ok=True)
    write_json_atomic(project_file, data, sort_keys=False)
    return Project.load(project_file)


# ---------------------------------------------------------------------------
# Recent projects (in the user's home folder)

def recent_projects() -> list[Path]:
    try:
        data = json.loads(RECENT_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    return [Path(p) for p in data if isinstance(p, str)] if isinstance(data, list) else []


def remember_project(path: Path) -> None:
    path = Path(path).resolve()
    items = [p for p in recent_projects() if os.path.normcase(str(p)) != os.path.normcase(str(path))]
    items.insert(0, path)
    try:
        write_json_atomic(RECENT_FILE, [str(p) for p in items[:RECENT_MAX]], sort_keys=False)
    except OSError:
        pass


def get_theme() -> str:
    """'auto' (follow the system), 'dark' or 'light'."""
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return 'auto'
    theme = data.get('theme') if isinstance(data, dict) else None
    return theme if theme in THEMES else 'auto'


def set_theme(theme: str) -> None:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        data = {}
    data['theme'] = theme if theme in THEMES else 'auto'
    try:
        write_json_atomic(SETTINGS_FILE, data)
    except OSError:
        pass


def forget_project(path: Path) -> None:
    items = [p for p in recent_projects() if os.path.normcase(str(p)) != os.path.normcase(str(path))]
    try:
        write_json_atomic(RECENT_FILE, [str(p) for p in items], sort_keys=False)
    except OSError:
        pass
