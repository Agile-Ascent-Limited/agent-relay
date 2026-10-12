"""Exercise the real bus on loopback with temporary storage. No agent is contacted."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import urllib.request

from init_local import initialize


def main():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('demo_bus', root / 'bus/agent_relay_bus.py')
    bus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bus)
    with tempfile.TemporaryDirectory(prefix='agent-relay-demo-') as tmp:
        tokens = initialize(Path(tmp) / 'credentials')
        bus.DB = str(Path(tmp) / 'demo.sqlite3')
        bus.TOKENS_FILE = str(Path(tmp) / 'credentials/tokens.json')
        bus.NOTIFY = ''
        server = bus.ThreadingHTTPServer(('127.0.0.1', 0), bus.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def request(agent, route, data=None):
            req = urllib.request.Request('http://127.0.0.1:%s%s' % (server.server_port, route),
                data=json.dumps(data).encode() if data is not None else None,
                headers={'Authorization': 'Bearer ' + tokens[agent], 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)

        try:
            sent = request('watcher', '/msg', {'to': 'codex', 'kind': 'question',
                'subject': 'Example upstream change', 'body': 'Review this fabricated change; do not deploy.'})
            mid = sent['id']
            inbox = request('codex', '/inbox/codex?unread=1')
            assert inbox['messages'][0]['id'] == mid
            early = request('codex', '/ack', {'ids': [mid]})
            assert early['refused']
            request('codex', '/delivered', {'msg_id': mid, 'agent': 'codex', 'skey': 'demo-chat'})
            request('codex', '/msg', {'to': 'watcher', 'kind': 'answer', 'thread_id': mid,
                'subject': 'Example review', 'body': 'Demo answer only; no real investigation occurred.'})
            assert request('codex', '/ack', {'ids': [mid]})['acked'] == [mid]
            assert len(request('human', '/thread/%s' % mid)['messages']) == 2
            print('PASS: question -> inbox -> delivery receipt -> threaded answer -> completion')
            print('Temporary bus only. No editor, live agent, or model API was contacted.')
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    main()
