import importlib.util
from contextlib import closing
import json
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class BusTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('tested_bus', ROOT / 'bus/agent_relay_bus.py')
        self.bus = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bus)
        self.temp = tempfile.TemporaryDirectory(prefix='agent-relay-test-')
        self.bus.DB = str(Path(self.temp.name) / 'test.sqlite3')
        self.bus.TOKENS_FILE = str(Path(self.temp.name) / 'tokens.json')
        self.bus.NOTIFY = ''
        self.tokens = {agent: secrets.token_urlsafe(32) for agent in ('codex', 'claude', 'human', 'watcher')}
        Path(self.bus.TOKENS_FILE).write_text(json.dumps({v: k for k, v in self.tokens.items()}))
        self.server = self.bus.ThreadingHTTPServer(('127.0.0.1', 0), self.bus.Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        self.temp.cleanup()

    def request(self, path, data=None, agent='codex', authorized=True):
        headers = {'Content-Type': 'application/json'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + self.tokens[agent]
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.server.server_port, path),
            data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, json.load(response)

    def send(self, **extra):
        payload = dict(to='claude', kind='question', subject='Test question', body='Fabricated test content')
        payload.update(extra)
        status, result = self.request('/msg', payload)
        self.assertEqual(status, 200)
        return result['id']

    def test_unauthenticated_reads_and_writes_refused(self):
        self.assertEqual(self.request('/health', authorized=False)[0], 401)
        self.assertEqual(self.request('/msg', {}, authorized=False)[0], 401)

    def test_sender_is_token_identity_and_persists(self):
        mid = self.send(sender='human')
        status, result = self.request('/thread/%d' % mid, agent='human')
        self.assertEqual(status, 200)
        self.assertEqual(result['messages'][0]['sender'], 'codex')
        with closing(self.bus.db()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM msg').fetchone()[0], 1)

    def test_delivery_is_separate_from_completion(self):
        mid = self.send()
        self.request('/delivered', {'msg_id': mid, 'agent': 'claude', 'skey': 'test'})
        self.assertEqual(len(self.request('/deliveries')[1]['deliveries']), 1)
        refused = self.request('/ack', {'ids': [mid]}, agent='claude')[1]
        self.assertIn(str(mid), refused['refused'])
        self.request('/msg', dict(to='codex', kind='answer', subject='Test reply', body='Done in test', thread_id=mid), agent='claude')
        self.assertEqual(self.request('/ack', {'ids': [mid]}, agent='claude')[1]['acked'], [mid])

    def test_explicit_session_target_and_shared_feed(self):
        mid = self.send(session='one')
        self.assertEqual(self.request('/inbox/claude?session=two')[1]['messages'], [])
        self.assertEqual(self.request('/inbox/claude?session=one')[1]['messages'][0]['id'], mid)
        self.assertEqual(self.request('/messages', agent='human')[1]['messages'][0]['id'], mid)

    def test_invalid_target_and_shape_rejected(self):
        self.assertEqual(self.request('/msg', [1, 2])[0], 400)
        self.assertEqual(self.request('/msg', {'to': 'missing'})[0], 400)
        self.assertEqual(self.request('/msg', {'body': 'x' * 70000})[0], 413)

    def test_work_claim_is_a_lease(self):
        mid = self.send()
        self.assertEqual(self.request('/claim', {'ids': [mid], 'worker': 'one'})[1]['granted'], [mid])
        self.assertEqual(self.request('/claim', {'ids': [mid], 'worker': 'two'})[1]['granted'], [])
        with closing(self.bus.db()) as con:
            con.execute('UPDATE msg SET claimed_until=0 WHERE id=?', (mid,))
            con.commit()
        self.assertEqual(self.request('/claim', {'ids': [mid], 'worker': 'two'})[1]['granted'], [mid])

    def test_shared_trust_model_is_explicit(self):
        self.assertIn('not a multi-tenant', (ROOT / 'docs/architecture.md').read_text(encoding='utf-8'))
        self.assertEqual(self.bus.HOST, '127.0.0.1')


if __name__ == '__main__':
    unittest.main()
