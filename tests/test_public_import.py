import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from engine.pipeline import APP, connect, digest


class PublicImportTests(unittest.TestCase):
    def test_explicit_import_keeps_sources_progress_and_excludes_escaping_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);library=root/'books';level=library/'AA.PDF';level.mkdir(parents=True)
            book=level/'My own story.pdf'
            shutil.copy2(APP/'fixtures/generated/A Little Garden.pdf',book)
            before=(digest(book),book.stat().st_mtime_ns)
            outside=root/'outside.pdf';shutil.copy2(book,outside)
            (level/'escape.pdf').symlink_to(outside)
            data=root/'private';db=connect(data)
            edition='pdf_'+before[0]
            db.execute('INSERT INTO progress(edition_id,page) VALUES (?,2)',(edition,));db.commit();db.close()
            result=subprocess.run([sys.executable,str(APP/'scripts/import_library.py'),
                                   '--root',str(library),'--data',str(data)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            manifest=json.loads((data/'manifest.json').read_text())
            self.assertEqual(len(manifest['books']),1)
            self.assertEqual(manifest['books'][0]['edition_id'],edition)
            self.assertEqual((digest(book),book.stat().st_mtime_ns),before)
            with sqlite3.connect(data/'library.sqlite3') as db:
                self.assertEqual(db.execute('SELECT page FROM progress WHERE edition_id=?',(edition,)).fetchone()[0],2)

    def test_generated_state_cannot_be_written_into_source_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            result=subprocess.run([sys.executable,str(APP/'scripts/import_library.py'),
                                   '--root',str(root),'--data',str(root/'state')],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((root/'state').exists())
