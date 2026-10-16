import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools.file_dialog import choose_local_path
from tools.library_locations import LibraryLocations, LocationsConflict
from tools.planning_library import scan_library


class LibraryLocationsTests(unittest.TestCase):
    def test_file_selection_persists_only_selected_file_and_folder_can_expand_it(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); articles = base / 'drafts'; articles.mkdir()
            first = articles / 'заметка.MD'; first.write_text('# Один\n\nТекст первого файла.', encoding='utf-8')
            second = articles / 'другой.md'; second.write_text('# Два\n\nДругой текст.', encoding='utf-8')
            originals = {p: p.read_bytes() for p in articles.iterdir()}
            store = LibraryLocations(lambda: base / 'state')
            result = store.add(str(first), 'file', store.read()['revision'])
            self.assertFalse(result['selection']['has_granka'])
            again = LibraryLocations(lambda: base / 'state')
            sources = scan_library(again.roots(), again.selectors())
            self.assertEqual([s['title'] for s in sources], ['Один'])
            self.assertEqual(result['selection']['root'], sources[0]['root'])
            with self.assertRaises(LocationsConflict):
                again.add(str(second), 'file', 'stale')
            again.add(str(articles), 'folder', again.read()['revision'])
            self.assertEqual(len(scan_library(again.roots(), again.selectors())), 2)
            self.assertEqual({p: p.read_bytes() for p in articles.iterdir()}, originals)
            self.assertFalse((base / 'granka-config.json').exists())

    def test_sidecar_without_markdown_is_supported_and_invalid_paths_do_not_write(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); store = LibraryLocations(lambda: base / 'state')
            sidecar = base / 'note.granka.json'
            sidecar.write_text(json.dumps({'doc':'note.md','versions':[{'id':'v1','text':'# Текст\n\nУчебная статья.'}]}), encoding='utf-8')
            result = store.add(str(sidecar), 'file', store.read()['revision'])
            self.assertTrue(result['selection']['has_granka'])
            self.assertEqual(len(scan_library(store.roots(), store.selectors())), 1)
            before = store.file.read_bytes()
            for path, kind in [('relative.md','file'),(str(base/'missing.md'),'file'),
                               (str(sidecar),'folder'),(str(sidecar),'other')]:
                with self.subTest(path=path,kind=kind), self.assertRaises(ValueError):
                    store.add(path,kind,store.read()['revision'])
            self.assertEqual(store.file.read_bytes(), before)

    def test_picker_cancel_failure_timeout_and_argument_list(self):
        for answer in [None, 'selected.md']:
            result = subprocess.CompletedProcess([], 0, json.dumps({'path':answer}).encode(), b'')
            with patch('tools.file_dialog.subprocess.run', return_value=result) as run:
                self.assertEqual(choose_local_path('file'), answer)
                args, kwargs = run.call_args
                self.assertIsInstance(args[0], list)
                self.assertNotIn('shell', kwargs)
        for failure in [subprocess.CompletedProcess([],1,b'{"error":"unavailable"}',b''),
                        subprocess.CompletedProcess([],0,b'broken',b'')]:
            with patch('tools.file_dialog.subprocess.run', return_value=failure), self.assertRaisesRegex(ValueError,'вручную'):
                choose_local_path('folder')
        with patch('tools.file_dialog.subprocess.run', side_effect=subprocess.TimeoutExpired('fixture',1)), self.assertRaisesRegex(ValueError,'истекло'):
            choose_local_path('folder')
