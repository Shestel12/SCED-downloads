"""The NiceGUI user interface: start page, review screen, compare view, pickers and checks."""

from __future__ import annotations

import asyncio
import os
import string
import threading
from pathlib import Path

from nicegui import app, background_tasks, run, ui

from .cards import IMAGE_EXTENSIONS, is_generic_back, is_remote, natural_key, relative_to_root
from .project import (FIELD_LABELS, FOLDER_FIELDS, PROJECT_FILENAME, THEMES, Project, ProjectError,
                      forget_project, get_theme, recent_projects, remember_project, set_theme, validate_folders,
                      write_project)
from .server import picker_key_url, register_routes
from .state import CHECK_KINDS, FILTERS, SIDES, SORTS, EditError, State, log

STATUS_COLORS = {'todo': '#9e9e9e', 'ok': '#21ba45', 'issue': '#f2a900', 'wrong-image': '#c10015'}
STATUS_LABELS = {'todo': 'todo', 'ok': 'OK', 'issue': 'issue', 'wrong-image': 'wrong image'}
SIDE_LABELS = {'face': 'Face', 'back': 'Back'}

APP: dict = {'state': None, 'native': False, 'hash_task': None}

SHORTCUTS = [
    ('← / →', 'Previous / next card'),
    ('Home / End', 'First / last card'),
    ('Enter', 'Mark OK and go to the next card'),
    ('I', 'Focus the face note'),
    ('F / B', 'Compare view of the face / back'),
    ('S', 'Swap the Russian face and back'),
    ('Ctrl+Z', 'Undo the last JSON edit (outside text fields)'),
    ('?', 'This help'),
    ('Compare view: Space', 'Flicker between English and Russian'),
    ('Compare view: 0', 'Reset zoom'),
    ('Compare view: Esc', 'Close'),
]

CSS = r'''
<style>
:root {
  --cc-img-h: calc(100vh - 380px);
  --cc-page: #f4f4f6;
  --cc-panel: #ffffff;
  --cc-hover: #e8eaf6;
  --cc-current: #c5cae9;
  --cc-accent: #3949ab;
  --cc-muted: #666666;
  --cc-thumb: #eeeeee;
  --cc-stripe-a: #fafafa;
  --cc-stripe-b: #f0f0f0;
  --cc-border: #cccccc;
  --cc-error: #b00020;
  --cc-chip: #ffe0b2;
  --cc-chip-text: #3e2723;
  --cc-banner: #fff8e1;
  --cc-stage: #222222;
}
body.body--dark {
  --cc-page: #121212;
  --cc-panel: #1e1e1e;
  --cc-hover: #2c2f45;
  --cc-current: #3a3f6b;
  --cc-accent: #9fa8da;
  --cc-muted: #a0a0a0;
  --cc-thumb: #2a2a2a;
  --cc-stripe-a: #1c1c1c;
  --cc-stripe-b: #242424;
  --cc-border: #444444;
  --cc-error: #ff8a80;
  --cc-chip: #4e342e;
  --cc-chip-text: #ffe0b2;
  --cc-banner: #3e3420;
  --cc-stage: #0b0b0b;
}
body { background: var(--cc-page); }
/* NiceGUI loads Quasar's !important utilities in a cascade layer; an !important rule only beats them from
   an earlier layer such as `overrides`. */
@layer overrides {
  /* Quasar's grey/indigo text utilities are too dark on a dark background. */
  body.body--dark .text-grey-6, body.body--dark .text-grey-7 { color: #9e9e9e !important; }
  body.body--dark .text-grey-8 { color: #bdbdbd !important; }
  body.body--dark .text-indigo-9 { color: #9fa8da !important; }
  .cc-chip { background: var(--cc-chip) !important; color: var(--cc-chip-text) !important; }
  .cc-banner { background: var(--cc-banner) !important; }
}
body.body--dark .q-drawer { background: var(--cc-panel); }
.cc-list-row { cursor: pointer; padding: 1px 6px; min-height: 30px; border-radius: 4px; gap: 6px; flex-wrap: nowrap; }
.cc-list-row:hover { background: var(--cc-hover); }
.cc-list-row.cc-current { background: var(--cc-current); }
.cc-list-row .cc-nick { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1 1 auto; min-width: 0; }
.cc-folder { font-weight: 600; font-size: 12px; color: var(--cc-accent); padding: 8px 6px 2px; position: sticky;
             top: 0; background: var(--cc-panel); z-index: 1; }
.cc-dot { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 auto; }
.cc-thumb { width: 22px; height: 30px; object-fit: contain; flex: 0 0 auto; background: var(--cc-thumb); }
.cc-side { flex: 1 1 420px; min-width: 300px; background: var(--cc-panel); border-radius: 8px; padding: 8px; }
.cc-side.cc-wide { flex: 2 1 760px; }
.cc-imgs { display: flex; gap: 8px; width: 100%; }
.cc-imgbox { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; align-items: center; }
.cc-imgbox img { width: 100%; height: auto; max-height: max(var(--cc-img-h), 260px); object-fit: contain;
                 cursor: zoom-in; }
.cc-missing { width: 100%; aspect-ratio: 5 / 7; max-height: max(var(--cc-img-h), 260px); display: flex;
              align-items: center;
              justify-content: center; text-align: center;
              background: repeating-linear-gradient(45deg, var(--cc-stripe-a), var(--cc-stripe-a) 10px,
              var(--cc-stripe-b) 10px, var(--cc-stripe-b) 20px);
              color: var(--cc-error); border: 1px dashed var(--cc-border); padding: 8px; }
.cc-cap { font-size: 11px; color: var(--cc-muted); }
.cc-path { font-family: ui-monospace, Consolas, monospace; font-size: 12px; word-break: break-all; }
.cc-cmp-card { width: 96vw; max-width: 96vw !important; height: 94vh; max-height: 94vh !important;
               display: flex; flex-direction: column; padding: 8px; }
.cc-cmp-body { flex: 1 1 auto; display: flex; gap: 8px; min-height: 0; width: 100%; align-self: stretch; }
.cc-zp { position: relative; flex: 1 1 0; overflow: hidden; background: var(--cc-stage); border-radius: 4px;
         cursor: grab; user-select: none; }
.cc-zp img { position: absolute; left: 0; top: 0; width: 100%; height: 100%; object-fit: contain;
             transform-origin: 0 0; pointer-events: none; }
.cc-zp .cc-zlabel { position: absolute; left: 8px; top: 6px; color: white; background: rgba(0,0,0,.55);
                    padding: 1px 8px; border-radius: 4px; font-weight: 600; z-index: 2; }
.cc-flicker img.cc-ru, .cc-flicker .cc-zlabel.cc-ru { display: none; }
.cc-flicker.cc-show-ru img.cc-ru, .cc-flicker.cc-show-ru .cc-zlabel.cc-ru { display: block; }
.cc-flicker.cc-show-ru img.cc-en, .cc-flicker.cc-show-ru .cc-zlabel.cc-en { display: none; }
.cc-pick-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 8px; }
.cc-pick { cursor: pointer; border: 2px solid transparent; border-radius: 6px; padding: 4px;
           background: var(--cc-stripe-a); }
.cc-pick:hover { border-color: #5c6bc0; }
.cc-pick.cc-selected { border-color: #21ba45; }
.cc-pick img { width: 100%; height: 160px; object-fit: contain; }
.cc-pick .cc-cap { word-break: break-all; }
.cc-check { cursor: pointer; padding: 3px 6px; border-radius: 4px; }
.cc-check:hover { background: var(--cc-hover); }
.cc-check.cc-resolved .cc-check-text { opacity: .5; text-decoration: line-through; }
.cc-kbd { font-family: ui-monospace, Consolas, monospace; background: var(--cc-thumb); border-radius: 3px;
          padding: 0 5px; }
</style>
'''

JS = r'''
<script>
window.cc = window.cc || {s: 1, x: 0, y: 0, drag: null, timer: null};
function ccApply() {
  document.querySelectorAll('.cc-zp img').forEach(img => {
    img.style.transform = `translate(${cc.x}px, ${cc.y}px) scale(${cc.s})`;
  });
}
function ccReset() { cc.s = 1; cc.x = 0; cc.y = 0; ccApply(); }
function ccFlick() { document.querySelectorAll('.cc-flicker').forEach(f => f.classList.toggle('cc-show-ru')); }
function ccAuto(on) { clearInterval(cc.timer); cc.timer = on ? setInterval(ccFlick, 700) : null; }
function ccPreload(urls) { (window.ccPre = urls.map(u => { const i = new Image(); i.src = u; return i; })); }
document.addEventListener('wheel', e => {
  const v = e.target.closest && e.target.closest('.cc-zp');
  if (!v) return;
  e.preventDefault();
  const r = v.getBoundingClientRect();
  const cx = e.clientX - r.left, cy = e.clientY - r.top;
  const ns = Math.min(25, Math.max(1, cc.s * Math.exp(-e.deltaY * 0.0015)));
  const k = ns / cc.s;
  cc.x = cx - (cx - cc.x) * k; cc.y = cy - (cy - cc.y) * k; cc.s = ns;
  if (ns === 1) { cc.x = 0; cc.y = 0; }
  ccApply();
}, {passive: false});
document.addEventListener('mousedown', e => {
  const v = e.target.closest && e.target.closest('.cc-zp');
  if (!v || e.button !== 0) return;
  e.preventDefault();
  cc.drag = {x: e.clientX - cc.x, y: e.clientY - cc.y};
  v.style.cursor = 'grabbing';
});
document.addEventListener('mousemove', e => {
  if (!cc.drag) return;
  cc.x = e.clientX - cc.drag.x; cc.y = e.clientY - cc.drag.y; ccApply();
});
document.addEventListener('mouseup', e => {
  if (cc.drag) document.querySelectorAll('.cc-zp').forEach(v => v.style.cursor = '');
  cc.drag = null;
  // Clicked buttons must not keep the focus, or page shortcuts would be ignored afterwards.
  const b = e.target.closest && e.target.closest('button, .q-btn, .q-tab, .q-toggle, .q-checkbox');
  if (b) setTimeout(() => { if (document.activeElement && document.activeElement.closest &&
                                document.activeElement.closest('button, .q-btn, .q-tab, .q-toggle, .q-checkbox'))
                              document.activeElement.blur(); }, 0);
});
</script>
'''


def get_state() -> State | None:
    return APP['state']


THEME_INFO = {
    'auto': ('brightness_auto', 'Theme: follow system (click for dark)'),
    'dark': ('dark_mode', 'Theme: dark (click for light)'),
    'light': ('light_mode', 'Theme: light (click to follow system)'),
}
THEME_VALUE = {'auto': None, 'dark': True, 'light': False}


def apply_theme() -> ui.dark_mode:
    """Dark mode for the current page, from the saved preference."""
    return ui.dark_mode(THEME_VALUE[get_theme()])


def theme_button(dark: ui.dark_mode, color: str = '') -> ui.button:
    """A button that cycles follow-system → dark → light and remembers the choice."""
    theme = {'current': get_theme()}
    button = ui.button(icon=THEME_INFO[theme['current']][0]).props(f'flat round dense {color}')
    with button:
        tooltip = ui.tooltip(THEME_INFO[theme['current']][1])

    def cycle():
        theme['current'] = THEMES[(THEMES.index(theme['current']) + 1) % len(THEMES)]
        set_theme(theme['current'])
        dark.set_value(THEME_VALUE[theme['current']])
        button.props(f'icon={THEME_INFO[theme["current"]][0]}')
        tooltip.text = THEME_INFO[theme['current']][1]
        log(f'Theme: {theme["current"]}')
    button.on_click(cycle)
    return button


def notify(message: str, kind: str = 'positive', console: bool = True) -> None:
    if console:
        log(message)
    ui.notify(message, type=kind, position='bottom-right', timeout=2500 if kind == 'positive' else 5000)


async def confirm(message: str, ok: str = 'OK') -> bool:
    with ui.dialog() as dialog, ui.card().classes('max-w-xl'):
        ui.label(message).style('white-space: pre-wrap')
        with ui.row().classes('w-full justify-end'):
            ui.button('Cancel', on_click=lambda: dialog.submit(False)).props('flat')
            ui.button(ok, on_click=lambda: dialog.submit(True)).props('color=negative')
    result = await dialog
    dialog.delete()
    return bool(result)


def _drives() -> list[Path]:
    if os.name != 'nt':
        return [Path('/')]
    return [Path(f'{d}:/') for d in string.ascii_uppercase if os.path.exists(f'{d}:/')]


async def pick_path(title: str, start: Path | None = None, *, files: tuple[str, ...] = ()) -> Path | None:
    """Folder (or file, if `files` lists suffixes) browser built from server-side listings."""
    start = Path(start) if start else Path.cwd()
    while not start.exists() and start.parent != start:
        start = start.parent
    if start.is_file():
        start = start.parent
    current = {'dir': start.resolve()}

    with ui.dialog() as dialog, ui.card().style('width: 760px; max-width: 95vw; height: 80vh; display:flex; '
                                                'flex-direction: column'):
        ui.label(title).classes('text-lg font-medium')
        with ui.row().classes('w-full items-center no-wrap'):
            ui.button(icon='arrow_upward', on_click=lambda: go(current['dir'].parent)).props('flat dense') \
                .tooltip('Parent folder')
            path_input = ui.input(value=str(current['dir'])).classes('flex-grow').props('dense outlined')
            path_input.on('keydown.enter', lambda: go(Path(path_input.value.strip().strip('"'))))
            for drive in _drives():
                ui.button(str(drive).rstrip('\\/'), on_click=lambda d=drive: go(d)).props('flat dense')
        listing = ui.scroll_area().classes('flex-grow w-full border rounded')
        with ui.row().classes('w-full justify-end'):
            if APP['native']:
                ui.button('System dialog…', on_click=lambda: system_dialog()).props('flat')
            ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')
            if not files:
                ui.button('Select this folder', on_click=lambda: dialog.submit(current['dir'])).props('color=primary')

    async def system_dialog():
        try:
            import webview
            kind = webview.FileDialog.OPEN if files else webview.FileDialog.FOLDER
            result = await app.native.main_window.create_file_dialog(kind, directory=str(current['dir']))
        except Exception as e:  # pragma: no cover - native only
            notify(f'System dialog failed: {e}', 'negative')
            return
        if result:
            dialog.submit(Path(result[0]))

    def go(folder: Path):
        try:
            folder = folder.resolve()
            entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
        except OSError as e:
            notify(f'Cannot open {folder}: {e}', 'warning', console=False)
            return
        current['dir'] = folder
        path_input.value = str(folder)
        listing.clear()
        with listing:
            for entry in entries:
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                if is_dir:
                    with ui.row().classes('cc-check w-full items-center no-wrap') \
                            .on('click', lambda e=entry: go(e)):
                        ui.icon('folder', color='amber-8')
                        ui.label(entry.name)
                elif files and entry.name.lower().endswith(files):
                    with ui.row().classes('cc-check w-full items-center no-wrap') \
                            .on('click', lambda e=entry: dialog.submit(e)):
                        ui.icon('description', color='indigo')
                        ui.label(entry.name)

    go(current['dir'])
    result = await dialog
    dialog.delete()
    return result


# =============================================================================
# Start page

def start_page() -> None:
    ui.page_title('Card Compare')
    ui.add_head_html(CSS)
    dark = apply_theme()
    with ui.column().classes('w-full max-w-4xl mx-auto p-4 gap-6'):
        with ui.row().classes('w-full items-center'):
            ui.label('Card Compare').classes('text-3xl font-medium')
            ui.space()
            theme_button(dark)

        with ui.card().classes('w-full'):
            ui.label('Recent projects').classes('text-lg font-medium')
            recent = [p for p in recent_projects()]
            if not recent:
                ui.label('No recent projects.').classes('text-grey-7')
            for path in recent:
                try:
                    title = Project.load(path).title
                    ok = True
                except ProjectError:
                    title, ok = '(missing)', False
                with ui.row().classes('w-full items-center no-wrap'):
                    ui.button(title, on_click=lambda p=path: open_project(p)).props('flat no-caps') \
                        .classes('whitespace-nowrap').set_enabled(ok)
                    ui.label(str(path)).classes('cc-path text-grey-7 flex-grow')
                    ui.button(icon='close', on_click=lambda p=path: (forget_project(p), ui.navigate.reload())) \
                        .props('flat dense round').tooltip('Remove from list')
            with ui.row().classes('w-full items-center no-wrap'):
                open_input = ui.input('Open project file').classes('flex-grow').props('dense outlined')

                async def browse_project():
                    path = await pick_path('Open project file', Path(open_input.value or Path.cwd()),
                                           files=('.project.json',))
                    if path:
                        open_input.value = str(path)
                        await open_project(path)
                ui.button('Browse…', on_click=browse_project).props('flat')
                ui.button('Open', on_click=lambda: open_project(Path(open_input.value.strip().strip('"')))) \
                    .props('color=primary')

        with ui.card().classes('w-full'):
            ui.label('New project').classes('text-lg font-medium')
            title_input = ui.input('Title').classes('w-full').props('outlined dense')
            inputs: dict[str, ui.input] = {}
            for f in FOLDER_FIELDS:
                with ui.row().classes('w-full items-center no-wrap'):
                    inputs[f] = ui.input(FIELD_LABELS[f]).classes('flex-grow').props('outlined dense')

                    async def browse(field=f):
                        base = inputs[field].value or next((i.value for i in inputs.values() if i.value), '') \
                            or str(Path.cwd())
                        path = await pick_path(FIELD_LABELS[field], Path(base))
                        if path:
                            inputs[field].value = str(path)
                            if field == 'russian_cards' and not project_input.value:
                                project_input.value = str(path.parent / PROJECT_FILENAME)
                            if field == 'russian_cards' and not title_input.value:
                                title_input.value = path.name.split('.')[0]
                    ui.button('Browse…', on_click=browse).props('flat')
            with ui.row().classes('w-full items-center no-wrap'):
                project_input = ui.input('Project file (normally next to the Russian card folder)',
                                         placeholder=PROJECT_FILENAME).classes('flex-grow').props('outlined dense')

                async def browse_target():
                    path = await pick_path('Folder for the project file',
                                           Path(project_input.value or inputs['russian_cards'].value or Path.cwd()))
                    if path:
                        project_input.value = str(path / PROJECT_FILENAME)
                ui.button('Browse…', on_click=browse_target).props('flat')
            errors = ui.column().classes('gap-0')

            async def create():
                errors.clear()
                folders = {f: Path(inputs[f].value.strip().strip('"')) if inputs[f].value.strip() else ''
                           for f in FOLDER_FIELDS}
                problems = await run.io_bound(validate_folders, folders)
                target = project_input.value.strip().strip('"')
                if not target:
                    problems.append('Project file: not set')
                elif Path(target).exists() and Path(target).is_file():
                    if not await confirm(f'{target} exists. Overwrite it?', 'Overwrite'):
                        return
                if problems:
                    with errors:
                        for p in problems:
                            ui.label(p).classes('text-negative')
                    notify('Please fix the project settings', 'warning', console=False)
                    return
                try:
                    project = write_project(Path(target), title_input.value.strip() or Path(target).parent.name,
                                            folders)
                except (OSError, ProjectError) as e:
                    notify(f'Cannot write the project file: {e}', 'negative')
                    return
                notify(f'Project file written: {project.path}')
                await open_project(project.path)
            ui.button('Create project', on_click=create).props('color=primary')


async def open_project(path: Path) -> None:
    try:
        project = Project.load(path)
    except ProjectError as e:
        notify(str(e), 'negative')
        return
    problems = await run.io_bound(project.problems)
    if problems:
        notify('Cannot open project: ' + '; '.join(problems), 'negative')
        return
    spinner = ui.notification('Loading project…', spinner=True, timeout=None, position='center')
    try:
        state = await load_state(project)
    except Exception as e:
        spinner.dismiss()
        notify(f'Cannot open project: {e}', 'negative')
        return
    spinner.dismiss()
    remember_project(project.path)
    log(f'Opened project "{state.project.title}" ({project.path})')
    ui.navigate.to('/')


async def load_state(project: Project) -> State:
    state = State(project)
    await run.io_bound(state.load)
    APP['state'] = state
    APP['hash_task'] = background_tasks.create(_hash_in_background(state), name='hashing')
    return state


async def _hash_in_background(state: State) -> None:
    try:
        await run.io_bound(state.run_hashing)
    except Exception as e:  # never fatal: hashes are only hints
        log(f'Hashing failed: {type(e).__name__}: {e}')


def restart_hashing(state: State) -> None:
    task = APP.get('hash_task')
    if task is not None and not task.done():
        return
    APP['hash_task'] = background_tasks.create(_hash_in_background(state), name='hashing')


# =============================================================================
# Review page

class NoteField:
    """A note textarea that saves itself about a second after typing stops."""

    def __init__(self, page: 'ReviewPage', side: str):
        self.page = page
        self.side = side
        self.pair_index: int | None = None
        self.key: str | None = None
        self.pending: str | None = None
        self.task: asyncio.Task | None = None
        self.textarea: ui.textarea | None = None

    def build(self, pair) -> None:
        state = self.page.state
        self.flush()
        self.pair_index = pair.index
        self.key = state.note_key(pair, self.side)
        label = f'Note ({self.side})'
        if self.key is None:
            reason = 'generic back — no notes' if is_generic_back(pair.ru_url(self.side)) else 'no local Russian image'
            label += f' — {reason}'
        elif self.key:
            label += f' — {self.key}'
        self.textarea = ui.textarea(label, value=state.review.note(self.key),
                                    on_change=lambda e: self.changed(e.value)) \
            .classes('w-full').props('outlined autogrow dense')
        if self.key is None:
            self.textarea.disable()

    def changed(self, value: str) -> None:
        if self.key is None:
            return
        self.pending = value or ''
        if self.task is not None:
            self.task.cancel()
        self.task = asyncio.create_task(self._later(self.textarea))

    async def _later(self, element) -> None:
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return
        with element:
            self.flush()

    def flush(self) -> None:
        if self.task is not None and asyncio.current_task() is not self.task:
            self.task.cancel()
        self.task = None
        if self.pending is None or self.key is None:
            self.pending = None
            return
        text, self.pending = self.pending, None
        state = self.page.state
        pair = state.pair(self.pair_index)
        if pair is None:
            return
        try:
            had = state.review.has_note(self.key)
            if state.set_note(pair, self.side, text):
                if not text.strip():
                    msg = f'Note deleted ({self.key})'
                elif had:
                    msg = f'Note saved ({self.key})'
                else:
                    msg = f'Note added ({self.key})'
                ui.notify(msg, type='positive', position='bottom-right', timeout=1500)
                self.page.status_changed(pair)
        except Exception as e:
            notify(f'Cannot save note: {e}', 'negative')


class ReviewPage:
    def __init__(self, state: State):
        self.state = state
        self.rows: dict[int, tuple[ui.element, ui.element]] = {}
        self.view: list[int] = []
        self.notes = {side: NoteField(self, side) for side in SIDES}
        self.distance_labels: dict[str, ui.label] = {}
        self.seen_generation = -1
        self.seen_hashing_done = state.hashing_done
        self.cmp_side = 'face'
        self.cmp_mode = 'side'
        self.download_cancel: threading.Event | None = None
        self.build()

    # ------------------------------------------------------------------ layout

    def build(self) -> None:
        s = self.state
        ui.page_title(f'{s.project.title} — Card Compare')
        ui.add_head_html(CSS)
        ui.add_body_html(JS)
        self.dark = apply_theme()

        with ui.header().classes('items-center gap-3 py-1 px-3 bg-indigo-9'):
            ui.button(icon='menu', on_click=lambda: self.drawer.toggle()).props('flat round dense color=white')
            ui.label(s.project.title).classes('text-lg font-medium')
            self.counter = ui.label().classes('text-sm')
            self.counts_row = ui.row().classes('gap-3 items-center')
            ui.space()
            self.hash_label = ui.label().classes('text-xs text-indigo-2')
            self.download_button = ui.button(on_click=self.download_dialog).props('flat dense color=white no-caps')
            self.checks_button = ui.button('Checks', icon='rule', on_click=self.checks_dialog) \
                .props('flat dense color=white no-caps')
            with self.checks_button:
                self.checks_badge = ui.badge('0', color='orange').props('floating')
            ui.button(icon='undo', on_click=self.undo).props('flat round dense color=white').tooltip('Undo (Ctrl+Z)')
            theme_button(self.dark, 'color=white')
            ui.button(icon='help_outline', on_click=self.help_dialog).props('flat round dense color=white') \
                .tooltip('Shortcuts (?)')
            ui.button(icon='logout', on_click=self.close_project).props('flat round dense color=white') \
                .tooltip('Close project')

        with ui.left_drawer(value=True).props('width=340 bordered').classes('p-2') as self.drawer:
            with ui.column().classes('w-full gap-1 h-full no-wrap'):
                self.search = ui.input(placeholder='Search name, ID, GUID, file…', value=s.search,
                                       on_change=lambda e: self.set_search(e.value)) \
                    .props('dense outlined clearable debounce=250').classes('w-full')
                with ui.row().classes('w-full no-wrap gap-1'):
                    self.filter_select = ui.select(FILTERS, value=s.filter,
                                                   on_change=lambda e: self.set_filter(e.value)) \
                        .props('dense outlined options-dense').classes('flex-grow').tooltip('Filter')
                    ui.select(SORTS, value=s.sort, on_change=lambda e: self.set_sort(e.value)) \
                        .props('dense outlined options-dense').classes('flex-grow').tooltip('Sort')
                with ui.row().classes('w-full no-wrap items-center gap-1'):
                    self.goto_input = ui.number(placeholder='Go to #', min=1, format='%d') \
                        .props('dense outlined').classes('flex-grow')
                    self.goto_input.on('keydown.enter', self.goto_number)
                    ui.button('Go', on_click=self.goto_number).props('flat dense')
                self.list_info = ui.label().classes('text-xs text-grey-7')
                self.list_area = ui.scroll_area().classes('w-full flex-grow')

        with ui.column().classes('w-full gap-2 p-2'):
            self.banner = ui.row().classes('w-full')
            self.detail = ui.column().classes('w-full gap-2')

        self.keyboard = ui.keyboard(on_key=self.on_key, ignore=['input', 'select', 'button', 'textarea'])
        self.build_compare_dialog()
        self.timer = ui.timer(0.6, self.poll)

        self.render_list()
        self.render_detail()
        self.update_header()

        others = [c for c in app.clients('/') if c.has_socket_connection]
        if len(others) > 1:
            notify('Card Compare is open in another tab. Both tabs show the same project state; '
                   'use only one of them for editing.', 'warning', console=False)

    # ------------------------------------------------------------------ header / polling

    def update_header(self) -> None:
        s = self.state
        current = s.current
        number = s.number.get(current, 0) if current is not None else 0
        position = (self.view.index(current) + 1) if current in self.view else '–'
        if len(self.view) != len(s.pairs):
            self.counter.text = f'#{number} of {len(s.pairs)} · {position} / {len(self.view)} filtered'
        else:
            self.counter.text = f'{number} / {len(s.pairs)}'
        counts = s.counts()
        self.counts_row.clear()
        with self.counts_row:
            for status in ('ok', 'issue', 'wrong-image', 'todo'):
                with ui.row().classes('items-center gap-1 no-wrap cursor-pointer') \
                        .on('click', lambda st=status: self.set_filter(st, update_select=True)):
                    ui.element('div').classes('cc-dot').style(f'background:{STATUS_COLORS[status]}')
                    ui.label(f'{counts[status]} {STATUS_LABELS[status]}').classes('text-sm')
        n = len(s.open_checks())
        self.checks_badge.text = str(n)
        self.checks_badge.set_visibility(n > 0)
        missing = len(s.missing_urls())
        self.download_button.text = f'Download missing images ({missing})'
        self.download_button.set_visibility(missing > 0)
        if s.hash_progress:
            done, total = s.hash_progress
            self.hash_label.text = f'Hashing {done}/{total}…'
        else:
            self.hash_label.text = '' if s.hashing_done else 'Hashing…'
        self.render_banner(missing)
        self.seen_generation = s.generation

    def render_banner(self, missing: int) -> None:
        self.banner.clear()
        if not missing:
            return
        with self.banner, ui.card().classes('w-full cc-banner p-2'):
            with ui.row().classes('items-center w-full'):
                ui.icon('cloud_download', color='amber-9')
                ui.label(f'{missing} English image(s) are not in the cache yet.')
                ui.button('Download', on_click=self.download_dialog).props('color=amber-9 dense')

    def poll(self) -> None:
        """Pick up changes made by background tasks (hashing) without disturbing the notes."""
        s = self.state
        if s.generation != self.seen_generation or s.hash_progress:
            self.update_header()
            if s.hashing_done and not self.seen_hashing_done:
                self.update_distances()
                if s.sort == 'similarity':
                    self.render_list()
        self.seen_hashing_done = s.hashing_done

    def update_distances(self) -> None:
        pair = self.state.pair(self.state.current)
        if pair is None:
            return
        for side, label in self.distance_labels.items():
            self._set_distance(label, pair, side)

    def _set_distance(self, label: ui.label, pair, side: str) -> None:
        d = self.state.distance(pair, side)
        if d is None:
            label.text = '' if self.state.hashing_done else 'Δ …'
            label.classes(replace='text-xs text-grey-6')
            return
        label.text = f'Δ {d}'
        color = 'text-positive' if d <= self.state.threshold * 0.6 else (
            'text-warning' if d <= self.state.threshold else 'text-negative')
        label.classes(replace=f'text-sm font-bold {color}')

    def status_changed(self, pair) -> None:
        row = self.rows.get(pair.index)
        if row:
            row[1].style(f'background:{STATUS_COLORS[self.state.status(pair)] if pair.complete else "#90a4ae"}')
        if pair.index == self.state.current and hasattr(self, 'status_badge'):
            status = self.state.status(pair)
            self.status_badge.text = STATUS_LABELS[status]
            self.status_badge.props(f'color={_quasar_color(status)}')
        self.update_header()

    # ------------------------------------------------------------------ list

    def set_search(self, value) -> None:
        self.state.search = value or ''
        self.render_list()

    def set_filter(self, value: str, update_select: bool = False) -> None:
        self.state.filter = value
        if update_select and self.filter_select.value != value:
            self.filter_select.set_value(value)  # triggers on_change -> set_filter again
            return
        self.render_list()
        ui.run_javascript('document.activeElement && document.activeElement.blur && document.activeElement.blur()')

    def set_sort(self, value: str) -> None:
        self.state.sort = value
        self.state.apply_sort()
        self.render_list()
        ui.run_javascript('document.activeElement && document.activeElement.blur && document.activeElement.blur()')

    def render_list(self) -> None:
        s = self.state
        self.view = s.view()
        self.rows = {}
        self.list_area.clear()
        group_by_folder = s.sort == 'path'
        last_folder = None
        with self.list_area, ui.column().classes('w-full gap-0'):
            if not self.view:
                ui.label('No cards match.').classes('text-grey-7 p-2')
            for index in self.view:
                pair = s.pairs[index]
                if group_by_folder:
                    folder = s.folder_of(pair)
                    if folder != last_folder:
                        ui.label(folder).classes('cc-folder w-full')
                        last_folder = folder
                status = s.status(pair) if pair.complete else None
                classes = 'cc-list-row w-full items-center' + (' cc-current' if index == s.current else '')
                with ui.row().classes(classes).on('click', lambda i=index: self.goto(i)) as row:
                    lang = 'ru' if pair.ru else 'en'
                    if s.image(pair, 'face', lang) is not None:
                        ui.element('img').classes('cc-thumb').props(
                            f'src="/img/{index}/face/{lang}?thumb=1&v={s.image_version(s.image(pair, "face", lang))}"'
                            ' loading="lazy"')
                    else:
                        ui.element('div').classes('cc-thumb')
                    ui.label(f'{s.number[index]:03d}').classes('text-xs text-grey-7')
                    nick = pair.nickname
                    if pair.en_nickname and pair.en_nickname != nick:
                        nick = f'{nick} · {pair.en_nickname}'
                    ui.label(nick).classes('cc-nick text-sm').tooltip(nick)
                    if not pair.complete:
                        ui.badge('EN only' if pair.en else 'RU only', color='blue-grey').props('outline')
                    dot = ui.element('div').classes('cc-dot').style(
                        f'background:{STATUS_COLORS[status] if status else "#90a4ae"}')
                self.rows[index] = (row, dot)
        self.list_info.text = f'{len(self.view)} of {len(s.pairs)} cards'
        self.scroll_to_current()
        self.update_header()

    def scroll_to_current(self) -> None:
        row = self.rows.get(self.state.current)
        if row:
            ui.run_javascript(f'setTimeout(() => document.getElementById("{row[0].html_id}")'
                              f'?.scrollIntoView({{block: "nearest"}}), 50)')

    # ------------------------------------------------------------------ navigation

    def flush_notes(self) -> None:
        for note in self.notes.values():
            note.flush()

    def goto(self, index: int | None) -> None:
        if index is None:
            return
        self.flush_notes()
        old = self.rows.get(self.state.current)
        if old:
            old[0].classes(remove='cc-current')
        self.state.current = index
        new = self.rows.get(index)
        if new:
            new[0].classes(add='cc-current')
        self.render_detail()
        self.scroll_to_current()
        self.update_header()
        if self.cmp_dialog.value:
            self.render_compare()

    def step(self, delta: int) -> None:
        if not self.view:
            return
        current = self.state.current
        if current in self.view:
            pos = self.view.index(current) + delta
            if not 0 <= pos < len(self.view):
                ui.notify('End of the list' if delta > 0 else 'Start of the list', position='bottom-right',
                          timeout=1000)
                return
        else:
            # current card is filtered out: continue from its place in the full order
            order_pos = self.state.order.index(current) if current in self.state.order else -1
            later = [i for i in self.view if self.state.order.index(i) > order_pos]
            earlier = [i for i in self.view if self.state.order.index(i) < order_pos]
            target = (later[0] if later else None) if delta > 0 else (earlier[-1] if earlier else None)
            if target is None:
                return
            self.goto(target)
            return
        self.goto(self.view[pos])

    def edge(self, last: bool) -> None:
        if self.view:
            self.goto(self.view[-1] if last else self.view[0])

    def goto_number(self) -> None:
        try:
            n = int(self.goto_input.value)
        except (TypeError, ValueError):
            return
        if not 1 <= n <= len(self.state.order):
            notify(f'No card #{n}', 'warning', console=False)
            return
        self.goto(self.state.order[n - 1])

    # ------------------------------------------------------------------ detail

    def img_url(self, pair, side: str, lang: str) -> str | None:
        path = self.state.image(pair, side, lang)
        if path is None:
            return None
        return f'/img/{pair.index}/{side}/{lang}?v={self.state.image_version(path)}'

    def render_detail(self) -> None:
        s = self.state
        self.detail.clear()
        self.distance_labels = {}
        pair = s.pair(s.current)
        with self.detail:
            if pair is None:
                ui.label('No card selected.').classes('text-grey-7')
                return
            status = s.status(pair) if pair.complete else None
            with ui.row().classes('w-full items-center gap-3'):
                ui.label(f'#{s.number.get(pair.index, 0)}').classes('text-grey-7')
                ui.label(pair.nickname).classes('text-xl font-medium')
                if pair.en_nickname and pair.en_nickname != pair.nickname:
                    ui.label(pair.en_nickname).classes('text-lg text-grey-8')
                ui.label(f'id {pair.id}').classes('cc-path text-grey-7').style('user-select: all')
                if status:
                    self.status_badge = ui.badge(STATUS_LABELS[status]).props(f'color={_quasar_color(status)}')
                else:
                    ui.badge('English only' if pair.en else 'Russian only', color='blue-grey')
            checks = s.checks_for(pair)
            if checks:
                with ui.row().classes('w-full gap-1'):
                    for c in checks:
                        ui.badge(f'{CHECK_KINDS[c.kind]}: {c.text}') \
                            .classes('text-xs cc-chip').style('white-space: normal; text-align: left')

            with ui.row().classes('w-full gap-2 items-stretch'):
                for side in SIDES:
                    wide = s.is_landscape(pair, side, 'en') or s.is_landscape(pair, side, 'ru')
                    with ui.column().classes('cc-side gap-1' + (' cc-wide' if wide else '')):
                        with ui.row().classes('w-full items-center gap-2'):
                            ui.label(SIDE_LABELS[side].upper()).classes('font-bold text-indigo-9')
                            self.distance_labels[side] = ui.label()
                            self._set_distance(self.distance_labels[side], pair, side)
                        with ui.element('div').classes('cc-imgs'):
                            for lang in ('en', 'ru'):
                                self._image_box(pair, side, lang)

            with ui.card().classes('w-full p-3 gap-1'):
                for side in SIDES:
                    self._url_row(pair, side)
                with ui.row().classes('w-full items-center gap-2'):
                    ui.button('Swap RU face ⇄ back', icon='swap_horiz', on_click=lambda: self.swap()) \
                        .props('flat dense no-caps').set_enabled(bool(pair.ru))
                    ui.label('(S)').classes('text-xs text-grey-6')
                for side in SIDES:
                    self.notes[side].pair_index = None
                    if pair.ru:
                        self.notes[side].build(pair)
                with ui.row().classes('w-full gap-4 text-xs text-grey-8'):
                    for lang, cards in (('RU', pair.ru), ('EN', pair.en)):
                        if not cards:
                            continue
                        dup = len(cards) - 1
                        plural = 's' if dup != 1 else ''
                        ui.label(f'{lang} JSON: {cards[0].json_rel} (+{dup} duplicate{plural})') \
                            .classes('cc-path') \
                            .tooltip(' · '.join(f'{c.json_rel} [GUID {c.guid}]' for c in cards))

            with ui.row().classes('w-full items-center gap-2'):
                ui.button('Prev', icon='chevron_left', on_click=lambda: self.step(-1)).props('outline')
                ok = ui.button('OK & next', icon='check', on_click=self.ok_next).props('color=positive')
                ui.button('Issue', icon='flag', on_click=self.mark_issue).props('color=warning text-color=black')
                ui.button('Wrong image', icon='image_not_supported', on_click=self.toggle_wrong) \
                    .props('color=negative outline')
                ui.button('Next', on_click=lambda: self.step(1)).props('outline icon-right=chevron_right')
                ok.set_enabled(pair.complete)
                ui.space()
                ui.label('Enter = OK & next · ←/→ · F/B compare · ? help').classes('text-xs text-grey-6')

        # Preload the neighbours' images.
        urls = []
        if s.current in self.view:
            pos = self.view.index(s.current)
            for other in (pos - 1, pos + 1):
                if 0 <= other < len(self.view):
                    neighbour = s.pairs[self.view[other]]
                    urls += [u for u in (self.img_url(neighbour, sd, lg) for sd in SIDES for lg in ('en', 'ru')) if u]
        if urls:
            ui.run_javascript(f'ccPreload({urls!r})')

    def _image_box(self, pair, side: str, lang: str) -> None:
        s = self.state
        with ui.element('div').classes('cc-imgbox'):
            url = self.img_url(pair, side, lang)
            raw = pair.en_url(side) if lang == 'en' else pair.ru_url(side)
            cards = pair.en if lang == 'en' else pair.ru
            if url:
                img = ui.element('img').props(f'src="{url}" draggable="false"')
                img.on('click', lambda sd=side: self.open_compare(sd))
            else:
                if not cards:
                    text = 'no English card' if lang == 'en' else 'no Russian card'
                elif not raw:
                    text = '(no URL)'
                elif is_remote(raw):
                    text = 'not downloaded — use “Download missing images”'
                else:
                    text = 'file missing: ' + s.display_name(raw)
                ui.label(text).classes('cc-missing')
            caption = 'EN' if lang == 'en' else 'RU'
            if raw and is_generic_back(raw):
                caption += ' · generic back'
            ui.label(caption).classes('cc-cap')

    def _url_row(self, pair, side: str) -> None:
        s = self.state
        with ui.row().classes('w-full items-center gap-2 no-wrap'):
            ui.label(f'RU {side}:').classes('text-sm font-medium').style('min-width: 64px')
            if pair.ru:
                url = pair.ru_url(side)
                name = s.display_name(url)
                ui.label(name).classes('cc-path flex-grow').style('user-select: all')
                ui.button('Browse', icon='photo_library', on_click=lambda sd=side: self.browse(sd)) \
                    .props('flat dense no-caps')
                ui.button('Edit', icon='edit', on_click=lambda sd=side: self.edit_url(sd)).props('flat dense no-caps')
            else:
                ui.label('—').classes('flex-grow')
        if pair.en:
            with ui.row().classes('w-full items-center gap-2 no-wrap'):
                ui.label(f'EN {side}:').classes('text-xs text-grey-7').style('min-width: 64px')
                en = pair.en_url(side)
                ui.label('generic card back' if is_generic_back(en) else en) \
                    .classes('cc-path text-grey-7 flex-grow').style('user-select: all; font-size: 11px')

    # ------------------------------------------------------------------ status

    def ok_next(self) -> None:
        pair = self.state.pair(self.state.current)
        if pair is None or not pair.complete:
            self.step(1)
            return
        self.flush_notes()
        self.state.set_status(pair, 'ok')
        if self.state.status(pair) == 'issue':
            notify(f'"{pair.nickname}" marked OK, but it still shows as an issue because its images have notes',
                   'warning', console=False)
        else:
            ui.notify(f'OK: {pair.nickname}', type='positive', position='bottom-right', timeout=1000)
        self.status_changed(pair)
        if self.state.filter in ('todo', 'issue', 'wrong-image'):
            # the card leaves the filtered list: keep position
            pos = self.view.index(pair.index) if pair.index in self.view else None
            self.render_list()
            if pos is not None and self.view:
                self.goto(self.view[min(pos, len(self.view) - 1)])
            return
        self.step(1)

    def mark_issue(self) -> None:
        pair = self.state.pair(self.state.current)
        if pair is None:
            return
        note = self.notes['face']
        if note.textarea is None or note.key is None:
            note = self.notes['back']
        if note.textarea is None or note.key is None:
            notify('This card has no Russian image that can take a note', 'warning', console=False)
            return
        note.textarea.run_method('focus')
        ui.notify('Type the correction — it is saved automatically', position='bottom-right', timeout=2000)

    def toggle_wrong(self) -> None:
        pair = self.state.pair(self.state.current)
        if pair is None or not pair.complete:
            return
        stored = self.state.review.stored_status(pair.id)
        new = 'todo' if stored == 'wrong-image' else 'wrong-image'
        self.state.set_status(pair, new)
        notify(f'"{pair.nickname}": {"wrong image" if new == "wrong-image" else "wrong-image mark removed"}',
               'warning' if new == 'wrong-image' else 'positive')
        self.status_changed(pair)

    # ------------------------------------------------------------------ edits

    def after_edit(self, pair) -> None:
        self.render_detail()
        self.status_changed(pair)
        row = self.rows.get(pair.index)
        if row:
            self.render_list()  # thumbnail / grouping may have changed
        restart_hashing(self.state)

    async def apply_url(self, side: str, value: str) -> None:
        s = self.state
        pair = s.pair(s.current)
        if pair is None or not pair.ru:
            return
        if is_generic_back(value):
            where = 'the face' if side == 'face' else 'the back'
            if not await confirm(f'This is a generic card back. Write it as {where} of "{pair.nickname}" '
                                 f'in {len(pair.ru)} file(s)?', 'Write generic back'):
                return
        if value == pair.ru_url(side):
            notify('No change', 'info', console=False)
            return
        self.flush_notes()
        try:
            changed = s.set_ru_url(pair, side, value)
        except (EditError, OSError, ValueError) as e:
            notify(f'Cannot write: {e}', 'negative')
            return
        ui.notify(f'RU {side} → {s.display_name(value)} ({changed} file(s))', type='positive',
                  position='bottom-right')
        self.after_edit(pair)

    async def edit_url(self, side: str) -> None:
        s = self.state
        pair = s.pair(s.current)
        if pair is None or not pair.ru:
            return
        with ui.dialog() as dialog, ui.card().style('width: 900px; max-width: 95vw'):
            ui.label(f'RU {side} of "{pair.nickname}"').classes('text-lg font-medium')
            ui.label('A path (absolute, or relative to the Russian images folder), a file:/// URL or a web URL.') \
                .classes('text-xs text-grey-7')
            field = ui.input(value=pair.ru_url(side)).classes('w-full').props('outlined dense autofocus')
            error = ui.label().classes('text-negative text-sm')

            def submit():
                try:
                    dialog.submit(s.normalize_value(field.value))
                except EditError as e:
                    error.text = str(e)
            field.on('keydown.enter', submit)
            with ui.row().classes('w-full justify-end'):
                ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')
                ui.button('Write', on_click=submit).props('color=primary')
        value = await dialog
        dialog.delete()
        if value:
            await self.apply_url(side, value)

    async def browse(self, side: str) -> None:
        s = self.state
        pair = s.pair(s.current)
        if pair is None or not pair.ru:
            return
        root = s.project.russian_images
        key = s.ru_key(pair.ru_url(side))
        start = root
        if key and not os.path.isabs(key):
            start = (root / key).parent
        used = {s.ru_key(c.url(sd)) for p in s.pairs for c in p.ru for sd in SIDES}
        state = {'dir': start, 'filter': ''}

        with ui.dialog() as dialog, ui.card().style('width: 1100px; max-width: 96vw; height: 88vh; '
                                                    'display: flex; flex-direction: column'):
            ui.label(f'Choose the RU {side} for "{pair.nickname}"').classes('text-lg font-medium')
            with ui.row().classes('w-full items-center no-wrap gap-2'):
                ui.button(icon='arrow_upward', on_click=lambda: go(state['dir'].parent)).props('flat dense') \
                    .tooltip('Parent folder')
                where = ui.label().classes('cc-path flex-grow')
                ui.input(placeholder='Filter file names (searches subfolders)',
                         on_change=lambda e: (state.update(filter=(e.value or '').strip()), go(state['dir']))) \
                    .props('dense outlined clearable debounce=250').style('width: 320px')
            folders_row = ui.row().classes('w-full gap-1')
            grid_area = ui.scroll_area().classes('w-full flex-grow')
            with ui.row().classes('w-full justify-end'):
                ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')

        def go(folder: Path):
            folder = Path(folder)
            if relative_to_root(folder, root) is None and os.path.normcase(str(folder.resolve())) != \
                    os.path.normcase(str(root.resolve())):
                folder = root
            state['dir'] = folder
            rel = relative_to_root(folder, root) or ''
            where.text = '/' + rel
            folders_row.clear()
            grid_area.clear()
            try:
                children = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
            except OSError as e:
                notify(f'Cannot list {folder}: {e}', 'warning', console=False)
                return
            with folders_row:
                for child in children:
                    if child.is_dir():
                        ui.button(child.name, icon='folder', on_click=lambda c=child: go(c)) \
                            .props('outline dense no-caps size=sm')
            q = state['filter'].casefold()
            if q:
                images = [p for p in folder.rglob('*') if p.suffix.lower() in IMAGE_EXTENSIONS
                          and q in p.name.casefold()]
            else:
                images = [p for p in children if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file()]
            images.sort(key=lambda p: natural_key(p.as_posix()))
            with grid_area:
                if not images:
                    ui.label('No images here.').classes('text-grey-7')
                with ui.element('div').classes('cc-pick-grid w-full'):
                    for image in images[:600]:
                        k = relative_to_root(image, root)
                        if k is None:
                            continue
                        classes = 'cc-pick' + (' cc-selected' if k == key else '')
                        with ui.element('div').classes(classes).on('click', lambda k=k: dialog.submit(k)):
                            ui.element('img').props(f'src="{picker_key_url(k, version=s.image_version(image))}" '
                                                    'loading="lazy" draggable="false"')
                            ui.label(k[len(rel) + 1:] if rel and k.startswith(rel + '/') else k).classes('cc-cap')
                            if k not in used:
                                ui.badge('unused', color='green').props('outline')
                    if len(images) > 600:
                        ui.label(f'… {len(images) - 600} more, refine the filter').classes('text-grey-7')

        go(start)
        chosen = await dialog
        dialog.delete()
        if chosen:
            try:
                value = s.normalize_value(str(root / chosen))
            except EditError as e:
                notify(str(e), 'negative')
                return
            await self.apply_url(side, value)

    async def swap(self) -> None:
        s = self.state
        pair = s.pair(s.current)
        if pair is None or not pair.ru:
            return
        face, back = pair.ru_url('face'), pair.ru_url('back')
        if face == back:
            notify('Face and back are the same image', 'info', console=False)
            return
        if is_generic_back(back):
            if not await confirm(f'The back of "{pair.nickname}" is a generic card back. Swapping writes it as the '
                                 f'face. Continue?', 'Swap'):
                return
        self.flush_notes()
        try:
            changed = s.swap(pair)
        except (EditError, OSError, ValueError) as e:
            notify(f'Cannot swap: {e}', 'negative')
            return
        ui.notify(f'Swapped face and back of "{pair.nickname}" ({changed} change(s))', type='positive',
                  position='bottom-right')
        self.after_edit(pair)

    def undo(self) -> None:
        self.flush_notes()
        try:
            entry = self.state.undo()
        except (OSError, ValueError) as e:
            notify(f'Undo failed: {e}', 'negative')
            return
        if entry is None:
            notify('Nothing to undo', 'info', console=False)
            return
        ui.notify(f'Undone: {entry.description} ({len(entry.changes)} change(s))', type='positive',
                  position='bottom-right')
        pair = self.state.pair(entry.pair)
        if pair is not None:
            self.goto(entry.pair)
            self.after_edit(pair)

    # ------------------------------------------------------------------ compare view

    def build_compare_dialog(self) -> None:
        with ui.dialog() as self.cmp_dialog, ui.card().classes('cc-cmp-card'):
            with ui.row().classes('w-full items-center gap-3 no-wrap'):
                self.cmp_title = ui.label().classes('text-lg font-medium')
                self.cmp_side_toggle = ui.toggle(SIDE_LABELS, value='face',
                                                 on_change=lambda e: self.set_cmp(side=e.value))
                self.cmp_mode_toggle = ui.toggle({'side': 'Side by side', 'flicker': 'Flicker'}, value='side',
                                                 on_change=lambda e: self.set_cmp(mode=e.value))
                self.cmp_auto = ui.checkbox('Auto (0.7 s)', on_change=lambda e: ui.run_javascript(
                    f'ccAuto({"true" if e.value else "false"})'))
                ui.button('Reset zoom', on_click=lambda: ui.run_javascript('ccReset()')).props('flat dense no-caps')
                self.cmp_distance = ui.label().classes('text-sm')
                ui.space()
                ui.label('wheel: zoom · drag: pan · Space: flicker · ←/→: card').classes('text-xs text-grey-6')
                ui.button(icon='close', on_click=self.cmp_dialog.close).props('flat round dense')
            self.cmp_body = ui.element('div').classes('cc-cmp-body')
        self.cmp_dialog.on('hide', lambda: ui.run_javascript('ccAuto(false); ccReset()'))

    def open_compare(self, side: str) -> None:
        self.cmp_side = side
        self.cmp_side_toggle.set_value(side)
        self.render_compare()
        self.cmp_dialog.open()

    def set_cmp(self, side: str | None = None, mode: str | None = None) -> None:
        if side:
            self.cmp_side = side
        if mode:
            self.cmp_mode = mode
            if mode == 'side':
                self.cmp_auto.set_value(False)
        self.render_compare()

    def render_compare(self) -> None:
        s = self.state
        pair = s.pair(s.current)
        self.cmp_body.clear()
        if pair is None:
            return
        side = self.cmp_side
        self.cmp_title.text = f'#{s.number.get(pair.index, 0)} {pair.nickname}'
        d = s.distance(pair, side)
        self.cmp_distance.text = f'Δ {d}' if d is not None else ''
        urls = {lang: self.img_url(pair, side, lang) for lang in ('en', 'ru')}
        with self.cmp_body:
            if self.cmp_mode == 'side':
                for lang in ('en', 'ru'):
                    with ui.element('div').classes('cc-zp'):
                        ui.label(lang.upper()).classes('cc-zlabel')
                        if urls[lang]:
                            ui.element('img').props(f'src="{urls[lang]}" draggable="false"')
                        else:
                            ui.label('image not available').classes('text-white absolute-center')
            else:
                with ui.element('div').classes('cc-zp cc-flicker'):
                    for lang in ('en', 'ru'):
                        ui.label(lang.upper()).classes(f'cc-zlabel cc-{lang}')
                        if urls[lang]:
                            ui.element('img').classes(f'cc-{lang}').props(f'src="{urls[lang]}" draggable="false"')
        ui.run_javascript('ccApply()')

    # ------------------------------------------------------------------ dialogs

    def help_dialog(self) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label('Keyboard shortcuts').classes('text-lg font-medium')
            ui.label('Ignored while typing in a text field.').classes('text-xs text-grey-7')
            with ui.grid(columns=2).classes('gap-x-6 gap-y-1'):
                for key, action in SHORTCUTS:
                    ui.label(key).classes('cc-kbd')
                    ui.label(action)
            ui.button('Close', on_click=dialog.close).props('flat')
        dialog.on('hide', dialog.delete)
        dialog.open()

    def checks_dialog(self) -> None:
        s = self.state
        selected = {'tab': None, 'show_resolved': False}
        changed = {'any': False}
        with ui.dialog() as dialog, ui.card().style('width: 1000px; max-width: 96vw; height: 85vh; '
                                                    'display: flex; flex-direction: column'):
            with ui.row().classes('w-full items-center'):
                ui.label('Checks').classes('text-lg font-medium')
                ui.space()
                ui.switch('Show resolved', value=False,
                          on_change=lambda e: (selected.update(show_resolved=e.value), render())) \
                    .props('dense')
                ui.label('Similarity threshold')
                ui.number(value=s.threshold, min=0, max=64, step=1, format='%d',
                          on_change=lambda e: set_threshold(e.value)).props('dense outlined').style('width: 90px')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense')
            body = ui.column().classes('w-full flex-grow gap-0').style('min-height: 0')

        def set_threshold(value):
            try:
                s.threshold = int(value)
            except (TypeError, ValueError):
                return
            s.compute_checks()
            render()
            self.update_header()

        def resolve(checks, value):
            if s.set_resolved(checks, value):
                changed['any'] = True
                render()
                self.update_header()

        def on_hide():
            if changed['any']:
                self.render_list()
                self.render_detail()
            dialog.delete()

        def open_entry(check):
            if check.pair is not None:
                dialog.close()
                if check.pair not in self.view:
                    self.state.filter = 'all'
                    self.state.search = ''
                    self.search.value = ''
                    self.set_filter('all', update_select=True)
                self.goto(check.pair)
            elif check.path is not None:
                key = relative_to_root(check.path, s.project.russian_images)
                if key:
                    with ui.dialog() as preview, ui.card():
                        ui.label(key).classes('cc-path')
                        ui.element('img').props(f'src="{picker_key_url(key, thumb=False)}"') \
                            .style('max-height: 75vh; max-width: 80vw; object-fit: contain')
                    preview.on('hide', preview.delete)
                    preview.open()

        def render():
            body.clear()
            kinds = [k for k in CHECK_KINDS if any(c.kind == k for c in s.checks)]
            if 'similarity' not in kinds:
                kinds.append('similarity')
            if selected['tab'] not in kinds:
                selected['tab'] = kinds[0]
            with body:
                with ui.tabs(value=selected['tab'], on_change=lambda e: selected.update(tab=e.value)) \
                        .props('dense align=left outside-arrows mobile-arrows').classes('w-full') as tabs:
                    for k in kinds:
                        n = sum(c.kind == k and not c.resolved for c in s.checks)
                        done = sum(c.kind == k and c.resolved for c in s.checks)
                        ui.tab(k, f'{CHECK_KINDS[k]} ({n}' + (f' + {done} resolved)' if done else ')')) \
                            .props('no-caps')
                with ui.tab_panels(tabs, value=selected['tab']).classes('w-full flex-grow') \
                        .style('min-height: 0; overflow: auto'):
                    for k in kinds:
                        with ui.tab_panel(k).classes('p-1 gap-0'):
                            all_entries = [c for c in s.checks if c.kind == k]
                            open_entries = [c for c in all_entries if not c.resolved]
                            hidden = len(all_entries) - len(open_entries)
                            entries = all_entries if selected['show_resolved'] else open_entries
                            if k == 'similarity' and not s.hashing_done:
                                ui.label('Hashes are still being computed…').classes('text-grey-7')
                            if open_entries:
                                with ui.row().classes('w-full justify-end'):
                                    ui.button(f'Resolve all ({len(open_entries)})', icon='done_all',
                                              on_click=lambda es=open_entries: resolve(es, True)) \
                                        .props('flat dense no-caps size=sm')
                            if not entries:
                                ui.label(f'All {hidden} entries are resolved.' if hidden else 'Nothing found.') \
                                    .classes('text-grey-7')
                            for c in entries:
                                with ui.row().classes('cc-check w-full no-wrap items-start gap-2'
                                                      + (' cc-resolved' if c.resolved else '')) \
                                        .on('click', lambda c=c: open_entry(c)):
                                    if c.pair is not None:
                                        ui.label(f'#{s.number.get(c.pair, 0)}') \
                                            .classes('text-grey-7 text-xs cc-check-text').style('min-width: 36px')
                                    ui.label(c.text).classes('text-sm cc-check-text flex-grow') \
                                        .style('word-break: break-word')
                                    if c.resolved:
                                        ui.button(icon='undo').props('flat round dense size=sm color=grey') \
                                            .on('click.stop', lambda c=c: resolve([c], False)) \
                                            .tooltip('Mark as not resolved')
                                    else:
                                        ui.button(icon='check').props('flat round dense size=sm color=positive') \
                                            .on('click.stop', lambda c=c: resolve([c], True)) \
                                            .tooltip('Mark as resolved')
            if not s.hashing_done:
                ui.timer(1.0, lambda: render() if s.hashing_done and dialog.value else None, once=True)

        render()
        dialog.on('hide', on_hide)
        dialog.open()

    async def download_dialog(self) -> None:
        s = self.state
        urls = s.missing_urls()
        if not urls:
            notify('All English images are cached', 'info', console=False)
            return
        progress = {'done': 0, 'total': len(urls)}
        cancel = threading.Event()
        with ui.dialog().props('persistent') as dialog, ui.card().style('width: 760px; max-width: 95vw'):
            ui.label(f'Downloading {len(urls)} image(s)').classes('text-lg font-medium')
            bar = ui.linear_progress(value=0, show_value=False).classes('w-full')
            info = ui.label('Starting…')
            result_area = ui.column().classes('w-full gap-0')
            with ui.row().classes('w-full justify-end'):
                cancel_button = ui.button('Cancel', on_click=lambda: (cancel.set(), info.set_text('Cancelling…'))) \
                    .props('flat')
                close_button = ui.button('Close', on_click=dialog.close).props('color=primary')
                close_button.set_visibility(False)

        def report(done, total):
            progress['done'], progress['total'] = done, total

        timer = ui.timer(0.3, lambda: (bar.set_value(progress['done'] / max(progress['total'], 1)),
                                       info.set_text(f'{progress["done"]} / {progress["total"]}')))
        dialog.open()
        log(f'Downloading {len(urls)} English image(s)')
        try:
            rep = await run.io_bound(s.cache.download, urls, report, cancel)
        except Exception as e:
            timer.cancel()
            info.text = f'Download failed: {e}'
            cancel_button.set_visibility(False)
            close_button.set_visibility(True)
            notify(f'Download failed: {e}', 'negative')
            return
        timer.cancel()
        bar.set_value(1)
        summary = f'{rep.downloaded} downloaded / {rep.cached} already cached / {len(rep.failed)} failed'
        if rep.cancelled:
            summary += ' (cancelled)'
        info.text = summary
        log(summary)
        with result_area:
            for url, error in rep.failed:
                ui.label(f'{url} — {error}').classes('cc-path text-negative')
                log(f'  failed: {url} — {error}')
        cancel_button.set_visibility(False)
        close_button.set_visibility(True)
        notify(summary, 'positive' if not rep.failed else 'warning', console=False)
        s.compute_checks()
        self.render_detail()
        self.render_list()
        restart_hashing(s)
        dialog.on('hide', dialog.delete)

    def close_project(self) -> None:
        self.flush_notes()
        APP['state'] = None
        log('Project closed')
        ui.navigate.to('/')

    # ------------------------------------------------------------------ keyboard

    async def on_key(self, e) -> None:
        if not e.action.keydown:
            return
        code = e.key.code
        name = e.key.name
        mods = e.modifiers
        ctrl = mods.ctrl or mods.meta
        if ctrl and code == 'KeyZ' and not mods.shift:
            self.undo()
            return
        if ctrl or mods.alt:
            return
        repeat = e.action.repeat
        if self.cmp_dialog.value:
            if code == 'Space' and not repeat:
                ui.run_javascript('ccFlick()')
                if self.cmp_mode != 'flicker':
                    self.cmp_mode_toggle.set_value('flicker')
            elif code == 'Digit0':
                ui.run_javascript('ccReset()')
            elif code in ('KeyF', 'KeyB') and not repeat:
                self.cmp_side_toggle.set_value('face' if code == 'KeyF' else 'back')
            elif code == 'ArrowLeft':
                self.step(-1)
            elif code == 'ArrowRight':
                self.step(1)
            return
        if code == 'ArrowLeft':
            self.step(-1)
        elif code == 'ArrowRight':
            self.step(1)
        elif repeat:
            return
        elif code == 'Home':
            self.edge(False)
        elif code == 'End':
            self.edge(True)
        elif code in ('Enter', 'NumpadEnter'):
            self.ok_next()
        elif code == 'KeyI':
            self.mark_issue()
        elif code == 'KeyF':
            self.open_compare('face')
        elif code == 'KeyB':
            self.open_compare('back')
        elif code == 'KeyS':
            await self.swap()
        elif name == '?' or (code == 'Slash' and mods.shift):
            self.help_dialog()


def _quasar_color(status: str) -> str:
    return {'todo': 'grey-6', 'ok': 'positive', 'issue': 'warning', 'wrong-image': 'negative'}[status]


# =============================================================================
# Pages

def setup_pages() -> None:
    register_routes(app, get_state)

    @ui.page('/')
    def index():
        state = get_state()
        if state is None:
            start_page()
        else:
            ReviewPage(state)
