"""Penulisan JSON atomik dan cache pasar yang rusak (insiden feaf.web.id 18 Nov 2026).

Dua thread menulis cache harga TWSE lewat satu berkas `.tmp` bernama tetap, sehingga
`/data/pasar/harga_twse.json` berisi JSON valid diikuti sisa tulisan ("Extra data"). Pembacanya
tidak menangkap galat itu, jadi menu samping dan pipeline gagal dan semua halaman 500, dan
cache tidak pernah diambil ulang karena galat terjadi sebelum langkah ambil-ulang.

Tanpa jaringan: `_ambil_json` di-patch, semua berkas di folder sementara.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import berkas, pasar, tarik, umpan_balik  # noqa: E402

BARIS_HARGA = [{"Code": "2330", "Name": "台積電", "Date": "1151118", "ClosingPrice": "2,170.00",
                "Change": "-15.00"}]


def _json_rusak(path: str) -> None:
    """Bentuk yang sama dengan di server: JSON valid lalu ekor tulisan penulis lain."""
    panjang = json.dumps({"waktu": 1.0, "data": BARIS_HARGA * 50}, ensure_ascii=False)
    pendek = json.dumps({"waktu": 2.0, "data": BARIS_HARGA * 10}, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(pendek + panjang[len(pendek):])


class TestCachePasarRusak(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._asli = (pasar.CACHE_DIR, pasar._ambil_json)
        pasar.CACHE_DIR = os.path.join(self._tmp.name, "pasar")
        os.makedirs(pasar.CACHE_DIR)
        _json_rusak(pasar._path("harga_twse"))
        with self.assertRaises(ValueError):          # prasyarat: berkas benar-benar rusak
            json.load(open(pasar._path("harga_twse"), encoding="utf-8"))

    def tearDown(self):
        pasar.CACHE_DIR, pasar._ambil_json = self._asli
        self._tmp.cleanup()

    def _tanpa_jaringan(self, *a, **k):
        raise tarik.Diblokir("uji: tanpa jaringan")

    def test_baca_cache_rusak_dianggap_kosong(self):
        with mock.patch("builtins.print"):
            self.assertEqual(pasar._baca_cache("harga_twse"), (None, None))

    def test_menu_samping_dan_pipeline_tidak_ikut_gagal(self):
        # harga_dari_cache = menu samping di setiap halaman; kapitalisasi_dari_cache = pipeline.
        with mock.patch("builtins.print"):
            self.assertIsNone(pasar.harga_dari_cache("2330"))
            self.assertIsNone(pasar.kapitalisasi_dari_cache("2330"))

    def test_cache_rusak_diambil_ulang_dan_ditimpa(self):
        pasar._ambil_json = lambda url: BARIS_HARGA
        with mock.patch("builtins.print"):
            data = pasar._daftar("harga_twse", "harga")
        self.assertEqual(data, BARIS_HARGA)
        with open(pasar._path("harga_twse"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["data"], BARIS_HARGA)    # pulih sendiri
        self.assertEqual(pasar.harga_dari_cache("2330")["tutup"], 2170.0)

    def test_cache_rusak_dan_sumber_mati_tetap_tanpa_galat(self):
        pasar._ambil_json = self._tanpa_jaringan
        with mock.patch("builtins.print"):
            self.assertEqual(pasar._daftar("harga_twse", "harga"), [])

    def test_isi_bukan_objek_dianggap_kosong(self):
        with open(pasar._path("harga_twse"), "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(pasar._baca_cache("harga_twse"), (None, None))


class TestTulisJsonAtomik(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _sisa_tmp(self):
        return [f for f in os.listdir(self.folder) if f.endswith(".tmp")]

    def _serbu(self, tulis, path, penulis=8, kali=25):
        """Banyak thread menulis isi berbeda panjang ke berkas yang sama sekaligus."""
        mulai = threading.Barrier(penulis)
        galat = []

        def kerja(n):
            mulai.wait()
            try:
                for k in range(kali):
                    tulis(path, {"riwayat": [], "suara": {}, "isi": ["x" * 50] * (3000 - (n * 37 + k * 11) % 900)})
            except Exception as e:                      # noqa: BLE001
                galat.append(e)
        ts = [threading.Thread(target=kerja, args=(i,)) for i in range(penulis)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(galat, [])
        with open(path, encoding="utf-8") as f:
            json.load(f)                                # tidak ada "Extra data"
        self.assertEqual(self._sisa_tmp(), [])

    def test_helper_tahan_penulis_bersamaan(self):
        self._serbu(berkas.tulis_json_atomik, os.path.join(self.folder, "cache.json"))

    def test_tarik_tahan_penulis_bersamaan(self):
        # details.json dan status_tarik ditulis oleh penarik otomatis dan tombol Check now.
        self._serbu(tarik._tulis_atomik, os.path.join(self.folder, "details.json"))

    def test_umpan_balik_tahan_penulis_bersamaan(self):
        path = os.path.join(self.folder, "ambang.json")
        with mock.patch.object(umpan_balik, "FILE", path):
            self._serbu(lambda p, d: umpan_balik._tulis(d), path)

    def test_gagal_menulis_tidak_merusak_berkas_lama(self):
        path = os.path.join(self.folder, "a.json")
        berkas.tulis_json_atomik(path, {"lama": True})
        with self.assertRaises(TypeError):
            berkas.tulis_json_atomik(path, {"tidak_bisa": object()})
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"lama": True})
        self.assertEqual(self._sisa_tmp(), [])

    def test_meneruskan_opsi_json_dan_membuat_folder(self):
        path = os.path.join(self.folder, "baru", "b.json")
        berkas.tulis_json_atomik(path, {"teks": "台積電"}, indent=2)
        with open(path, encoding="utf-8") as f:
            isi = f.read()
        self.assertIn("台積電", isi)                    # ensure_ascii=False
        self.assertIn('\n  "teks"', isi)                # indent diteruskan

    @unittest.skipIf(os.name != "posix", "izin berkas POSIX")
    def test_izin_berkas_bisa_dibaca_proses_lain(self):
        path = os.path.join(self.folder, "c.json")
        berkas.tulis_json_atomik(path, {})
        self.assertEqual(os.stat(path).st_mode & 0o644, 0o644)


if __name__ == "__main__":
    unittest.main()
