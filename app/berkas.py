"""Menulis berkas JSON secara atomik, aman untuk beberapa thread sekaligus.

Dulu beberapa modul menulis lewat berkas sementara bernama tetap (`<path>.tmp`). Bila dua
thread menulis berkas yang sama bersamaan (penarik otomatis dan permintaan pengguna, misalnya),
keduanya menulis ke `.tmp` yang sama: hasilnya JSON yang valid diikuti sisa tulisan thread lain
("Extra data"), lalu `os.replace` memindahkan berkas rusak itu ke tempatnya. Di server 18 Nov
2026 itu merusak cache harga TWSE dan membuat semua halaman 500.

Di sini setiap penulis mendapat berkas sementara sendiri di folder yang sama, jadi
`os.replace` tetap atomik dan pembaca hanya pernah melihat isi lama atau isi baru yang utuh.
"""

from __future__ import annotations

import json
import os
import tempfile


def tulis_json_atomik(path: str, data, **dump_kw) -> None:
    """Tulis `data` sebagai JSON ke `path`. `dump_kw` diteruskan ke `json.dump` (mis. indent)."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        try:
            os.fchmod(fd, 0o644)          # mkstemp membuat 0600; samakan dengan open() biasa
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, **dump_kw)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
