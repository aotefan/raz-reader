"""Regression for mobile WebM clocks that advance far faster than the samples."""
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave

from engine.recordings import Recordings


class RecordingTimestampsTest(unittest.TestCase):
    def test_bad_webm_clock_keeps_complete_playback_and_original_take(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, broken = root / 'clean.webm', root / 'broken.webm'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'sine=frequency=440:duration=2', '-c:a', 'libopus', str(source)], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-itsscale', '1000', '-i', str(source),
                            '-c:a', 'copy', str(broken)], check=True)
            payload = broken.read_bytes()
            # Demonstrate the old duration guard truncating a complete two-second take.
            old = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(broken),
                '-t', '61', '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:1'])
            self.assertLess(len(old) / 32000, .4)
            store = Recordings(root)
            version = store.reserve('book', 1)
            store.save('book', 1, version, 'audio/webm', payload)
            mime, rendered = store.playback('book', 1)
            self.assertEqual(mime, 'audio/wav')
            with wave.open(io.BytesIO(rendered)) as audio:
                self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 2, places=2)
            self.assertEqual(store.read('book', 1), ('audio/webm', payload))
            self.assertEqual(store.assessment_snapshot('book', 1)[1], payload)


if __name__ == '__main__':
    unittest.main()
