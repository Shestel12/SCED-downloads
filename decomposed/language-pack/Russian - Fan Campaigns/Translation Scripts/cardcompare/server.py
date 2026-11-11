"""Image routes. File paths never reach the browser: routes resolve them on the server."""

from __future__ import annotations

import os
import threading
import urllib.parse
from collections import OrderedDict
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, Response
from fastapi.responses import FileResponse

from .cards import IMAGE_EXTENSIONS, relative_to_root

THUMB_SIZE = 200
_thumbs: OrderedDict[tuple, bytes] = OrderedDict()
_thumb_lock = threading.Lock()
_THUMB_MAX = 3000

MEDIA_TYPES = {'.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}


def thumbnail_bytes(path: Path, size: int = THUMB_SIZE) -> bytes:
    st = path.stat()
    key = (os.path.normcase(str(path)), st.st_mtime_ns, st.st_size, size)
    with _thumb_lock:
        if key in _thumbs:
            _thumbs.move_to_end(key)
            return _thumbs[key]
    from PIL import Image
    with Image.open(path) as im:
        im.draft('RGB', (size * 2, size * 2))
        im = im.convert('RGB')
        im.thumbnail((size, size))
        buf = BytesIO()
        im.save(buf, 'JPEG', quality=80)
    data = buf.getvalue()
    with _thumb_lock:
        _thumbs[key] = data
        while len(_thumbs) > _THUMB_MAX:
            _thumbs.popitem(last=False)
    return data


def _respond(path: Path | None, thumb: bool) -> Response:
    if path is None or not path.is_file():
        raise HTTPException(404)
    if thumb:
        try:
            data = thumbnail_bytes(path)
        except Exception as e:
            raise HTTPException(500, f'Cannot make thumbnail: {type(e).__name__}') from e
        return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'max-age=3600'})
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower()),
                        headers={'Cache-Control': 'max-age=3600'})


def picker_key_url(key: str, thumb: bool = True, version: str = '') -> str:
    q = {'key': key}
    if thumb:
        q['thumb'] = '1'
    if version:
        q['v'] = version
    return '/file?' + urllib.parse.urlencode(q)


def register_routes(app, get_state) -> None:
    @app.get('/img/{index}/{side}/{lang}')
    def image(index: int, side: str, lang: str, thumb: int = 0, v: str = ''):  # noqa: ARG001 (v busts the cache)
        state = get_state()
        if state is None or side not in ('face', 'back') or lang not in ('en', 'ru'):
            raise HTTPException(404)
        pair = state.pair(index)
        if pair is None:
            raise HTTPException(404)
        return _respond(state.image(pair, side, lang), bool(thumb))

    @app.get('/file')
    def picker_file(key: str, thumb: int = 0, v: str = ''):  # noqa: ARG001
        """An image under russian_images, addressed by its relative path."""
        state = get_state()
        if state is None:
            raise HTTPException(404)
        root = state.project.russian_images
        path = (root / key).resolve()
        if relative_to_root(path, root) is None or path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise HTTPException(404)
        return _respond(path, bool(thumb))

