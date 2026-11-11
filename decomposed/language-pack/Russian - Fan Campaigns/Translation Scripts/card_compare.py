#!/usr/bin/env python3
"""Card Compare: review a translated fan campaign card by card.

    python card_compare.py [project file] [--native] [--download] [--export]

Without a project file, a start page lists recent projects and offers a New project form.
See compare_images_SPEC.md for the full description.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

from cardcompare.project import Project, ProjectError, remember_project  # noqa: E402
from cardcompare.state import State, log  # noqa: E402


def free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError('No free port')


def load_project(path: str) -> Project:
    try:
        project = Project.load(path)
    except ProjectError as e:
        sys.exit(f'Error: {e}')
    problems = project.problems()
    if problems:
        sys.exit('Error: ' + '\n       '.join(problems))
    return project


def cli_download(project: Project) -> int:
    state = State(project)
    state.load()
    urls = state.missing_urls()
    if not urls:
        log('All English images are already cached.')
        return 0
    log(f'Downloading {len(urls)} image(s) into {project.english_cache}')
    lock = threading.Lock()
    last = {'n': 0}

    def progress(done, total):
        with lock:
            if done - last['n'] >= 10 or done == total:
                last['n'] = done
                log(f'  {done}/{total}')

    report = state.cache.download(urls, progress)
    log(f'{report.downloaded} downloaded / {report.cached} already cached / {len(report.failed)} failed')
    for url, error in report.failed:
        log(f'  failed: {url} — {error}')
    return 1 if report.failed else 0


def cli_export(project: Project) -> int:
    state = State(project)
    state.load()
    count = state.export()
    log(f'Wrote {project.export_path} ({count} issues)')
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description='Review translated fan-campaign cards against the originals.')
    parser.add_argument('project', nargs='?', help='card_compare.project.json (or the folder containing it)')
    parser.add_argument('--native', action='store_true', help='open in a native window instead of a browser tab')
    parser.add_argument('--download', action='store_true', help='download missing English images and exit')
    parser.add_argument('--export', action='store_true', help='regenerate translation_issues.txt and exit')
    parser.add_argument('--port', type=int, default=8765, help='preferred port (default 8765)')
    parser.add_argument('--no-show', action='store_true', help='do not open a browser tab')
    args = parser.parse_args()

    if args.download or args.export:
        if not args.project:
            parser.error('--download and --export need a project file')
        project = load_project(args.project)
        code = 0
        if args.download:
            code |= cli_download(project)
        if args.export:
            code |= cli_export(project)
        sys.exit(code)

    from nicegui import app, ui
    from cardcompare import ui_app

    ui_app.APP['native'] = args.native
    ui_app.setup_pages()

    if args.project:
        project = load_project(args.project)

        async def startup():
            try:
                await ui_app.load_state(project)
                remember_project(project.path)
                log(f'Opened project "{project.title}" ({project.path})')
            except Exception as e:
                log(f'Cannot open project: {type(e).__name__}: {e}')
        app.on_startup(startup)

    port = free_port(args.port)
    log(f'Card Compare on http://127.0.0.1:{port}')
    ui.run(
        host='127.0.0.1',
        port=port,
        title='Card Compare',
        reload=False,
        native=args.native,
        window_size=(1600, 1000) if args.native else None,
        show=not args.no_show and not args.native,
        show_welcome_message=False,
        favicon='🃏',
    )


if __name__ in {'__main__', '__mp_main__'}:
    main()
