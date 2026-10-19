import sys
import tempfile
import unittest
from pathlib import Path
from engine.assessment_process import AssessmentProcess


# Test the real pipe/process lifecycle without loading the 1.26 GB speech model.
WORKER = '''import json, os, sys, time
for line in sys.stdin:
    request = json.loads(line)
    if request.get('exit'): sys.exit(1)
    if request.get('wait'): time.sleep(5)
    print(json.dumps({'pid': os.getpid(), 'value': request.get('value')}), flush=True)
'''


class AssessmentProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.worker = AssessmentProcess([sys.executable, '-u', '-c', WORKER],
                                        Path(self.temporary.name), timeout=.5)

    def tearDown(self):
        self.worker.close()
        self.temporary.cleanup()

    def test_reuses_process_and_does_not_mix_responses(self):
        first = self.worker.call({'value': 'one'})
        second = self.worker.call({'value': 'two'})
        self.assertEqual(first['pid'], second['pid'])
        self.assertEqual(second['value'], 'two')

    def test_dead_worker_restarts_for_next_request(self):
        first = self.worker.call({})
        with self.assertRaises(RuntimeError): self.worker.call({'exit': True})
        self.assertNotEqual(first['pid'], self.worker.call({})['pid'])

    def test_timeout_kills_old_worker_and_cannot_deliver_late_result(self):
        first = self.worker.call({})
        with self.assertRaises(RuntimeError): self.worker.call({'wait': True})
        result = self.worker.call({'value': 'fresh'})
        self.assertNotEqual(first['pid'], result['pid'])
        self.assertEqual(result['value'], 'fresh')

    def test_close_releases_child_and_prevents_restart(self):
        self.worker.call({})
        process = self.worker.process
        self.worker.close()
        self.assertIsNotNone(process.poll())
        with self.assertRaises(RuntimeError): self.worker.call({})
