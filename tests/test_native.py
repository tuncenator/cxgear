"""Real Codex binary, fake accounts, localhost model. Never contacts a paid model.

CXGEAR_TEST_CODEX=/absolute/path/to/codex python -m unittest discover -s tests -v
"""
import base64
import fcntl
import http.server
import json
import os
from pathlib import Path
import queue
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import patch

from cxgear import broker, store
from cxgear.session import Session


def token(account):
    encode = lambda x: base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip('=')
    payload = {'email': account+'@example.invalid', 'exp': int(time.time()+3600), 'https://api.openai.com/auth': {'chatgpt_account_id': account, 'chatgpt_user_id': 'user-'+account, 'chatgpt_plan_type': 'pro'}}
    return encode({'alg': 'none', 'typ': 'JWT'})+'.'+encode(payload)+'.ZmFrZQ'


@unittest.skipUnless(os.environ.get('CXGEAR_TEST_CODEX'), 'Set CXGEAR_TEST_CODEX for the isolated native integration test')
class NativeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cxgear-test-')
        self.path = Path(self.temp.name)
        self.requests = []
        self.mode = 'message'
        self.used = {'a': 10, 'b': 10}
        self.started = threading.Event()
        self.release = threading.Event()
        owner = self

        class Backend(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, value, content_type='application/json'):
                body = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Expected when the terminal is closed during a request.

            def do_GET(self):
                if 'usage' in self.path:
                    bearer = self.headers.get('Authorization', '').split(' ')[-1]
                    account = json.loads(base64.urlsafe_b64decode(bearer.split('.')[1]+'==='))['https://api.openai.com/auth']['chatgpt_account_id']
                    self.reply(200, {'plan_type': 'pro', 'rate_limit': {'allowed': True, 'limit_reached': False, 'primary_window': {'used_percent': owner.used[account], 'limit_window_seconds': 604800, 'reset_at': int(time.time()+86400), 'reset_after_seconds': 86400}}})
                else:
                    self.reply(200, {})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                if not self.path.endswith('/responses'):
                    self.reply(200, {})
                    return
                data = json.loads(raw)
                bearer = self.headers.get('Authorization', '').split(' ')[-1]
                claims = json.loads(base64.urlsafe_b64decode(bearer.split('.')[1]+'==='))
                account = claims['https://api.openai.com/auth']['chatgpt_account_id']
                owner.requests.append({'account': account, 'input': data.get('input'), 'schema': data.get('text')})
                owner.started.set()
                if owner.mode == 'wait':
                    owner.release.wait(15)
                    return
                if owner.mode == 'failure':
                    self.reply(400, {'error': {'message': 'Fixture failure', 'type': 'invalid_request_error'}})
                    return
                i = len(owner.requests)
                if owner.mode == 'tool':
                    item = {'type': 'function_call', 'call_id': 'pause-1', 'name': 'cxgear_pause', 'arguments': '{}'}
                    owner.mode = 'message'
                else:
                    item = {'type': 'message', 'id': f'msg-{i}', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': f'Answer {i} from {account}'}]}
                events = [{'type': 'response.created', 'response': {'id': f'resp-{i}'}}, {'type': 'response.output_item.done', 'item': item}, {'type': 'response.completed', 'response': {'id': f'resp-{i}', 'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}}]
                body = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
                self.reply(200, body, 'text/event-stream')

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{self.server.server_port}'
        config = f'''model = "mock-model"
model_provider = "mock_provider"
chatgpt_base_url = "{url}/backend-api"
cli_auth_credentials_store = "file"
approval_policy = "never"
sandbox_mode = "read-only"
[features]
shell_snapshot = false
[model_providers.mock_provider]
name = "Local cxgear fixture"
base_url = "{url}/v1"
wire_api = "responses"
requires_openai_auth = true
request_max_retries = 0
stream_max_retries = 0
'''
        self.env = patch.dict(os.environ, {'CXGEAR_HOME': str(self.path/'state'), 'CODEX_HOME': str(self.path/'codex'), 'CXGEAR_CODEX_BINARY': os.environ['CXGEAR_TEST_CODEX'], 'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src'), 'HTTP_PROXY': 'http://127.0.0.1:1', 'HTTPS_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': '127.0.0.1,localhost'})
        self.env.start()
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'CXGEAR_BYPASS'):
            os.environ.pop(key, None)
        # Regression: a globally exported API key must not disable subscription
        # rotation, including the real interactive launcher and exec adapter.
        os.environ['OPENAI_API_KEY'] = 'fixture-unrelated-api-key'
        for name in ('a', 'b'):
            home = store.private_dir(store.account_home(name))
            (home/'config.toml').write_text(config)
            store.write(home/'auth.json', {'auth_mode': 'chatgpt', 'tokens': {'id_token': token(name), 'access_token': token(name), 'refresh_token': 'fake-refresh', 'account_id': name}, 'last_refresh': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        store.write(store.root()/'config.json', {'accounts': {'a': {}, 'b': {}}})
        work_home = store.private_dir(self.path/'codex')
        (work_home/'config.toml').write_text(config)
        self.work = self.path/'work'
        self.work.mkdir()

    def tearDown(self):
        self.release.set()
        try:
            broker.request('stop')
        except (OSError, ValueError):
            pass
        for _ in range(100):
            if not broker.socket_path().exists():
                break
            time.sleep(.05)
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def wait(self, client, predicate):
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            message = client.events.get(timeout=20)
            if predicate(message):
                return message
        self.fail('No matching Codex event')

    def test_switch_during_one_turn_preserves_tool_and_history(self):
        broker.ensure()
        snapshot = broker.request('status')
        self.assertTrue(snapshot['accounts']['a']['windows'], snapshot)
        broker.request('pin', account='a')
        with Session(cwd=str(self.work)) as session:
            client = session.connection('cxgear-test')
            thread = client.call('thread/start', {'cwd': str(self.work), 'model': 'mock-model', 'dynamicTools': [{'name': 'cxgear_pause', 'description': 'Inert test tool', 'inputSchema': {'type': 'object', 'properties': {}}}]})['thread']['id']
            self.mode = 'tool'
            turn = client.call('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': 'Remember CXGEAR-42 and call the tool.', 'text_elements': []}]})['turn']['id']
            call = self.wait(client, lambda x: x.get('method') == 'item/tool/call')
            # Fresh native quota polling must trigger automatic rotation, even
            # within the normal cooldown when the active account is exhausted.
            broker.request('auto')
            self.used['a'] = 98
            broker.request('refresh')
            deadline = time.monotonic()+10
            while session.account != 'b' and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual(session.account, 'b')
            client.send({'id': call['id'], 'result': {'contentItems': [{'type': 'inputText', 'text': 'Tool completed once.'}], 'success': True}})
            finished = self.wait(client, lambda x: x.get('method') == 'turn/completed')
            self.assertEqual(finished['params']['turn']['id'], turn)
            self.assertEqual(finished['params']['turn']['status'], 'completed')
            self.assertEqual([r['account'] for r in self.requests], ['a', 'b'])
            self.assertIn('CXGEAR-42', json.dumps(self.requests[-1]['input']))
            self.assertIn('Tool completed once.', json.dumps(self.requests[-1]['input']))
            client.close()

    def test_exec_json_output_file_and_resume(self):
        out = self.path/'result.txt'
        command = [sys.executable, '-m', 'cxgear', 'codex', 'exec', '--skip-git-repo-check', '--json', '-C', str(self.work), '-o', str(out), 'Remember CXGEAR-99.']
        result = subprocess.run(command, input='', text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(events[0]['type'], 'thread.started')
        self.assertEqual(events[-1]['type'], 'turn.completed')
        self.assertIn('Answer', out.read_text())
        thread = events[0]['thread_id']
        broker.request('pin', account='b')
        result = subprocess.run(command[:-1]+['resume', thread, 'Recall the marker.'], input='', text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
        self.assertIn('CXGEAR-99', json.dumps(self.requests[-1]['input']))
        self.assertEqual(self.requests[-1]['account'], 'b')
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(events[-1]['usage']['input_tokens'], 20)

    def test_schema_and_failure_exit(self):
        schema = self.path/'schema.json'
        expected = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer'], 'additionalProperties': False}
        schema.write_text(json.dumps(expected))
        command = [sys.executable, '-m', 'cxgear', 'codex', 'exec', '--skip-git-repo-check', '--json', '-C', str(self.work), '--output-schema', str(schema), 'Answer.']
        result = subprocess.run(command, input='', text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests[-1]['schema']['format']['schema'], expected)
        self.mode = 'failure'
        result = subprocess.run(command, input='', text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])['type'], 'turn.failed')

    def test_interrupt_exits_without_replaying(self):
        self.mode = 'wait'
        process = subprocess.Popen([sys.executable, '-m', 'cxgear', 'codex', 'exec', '--skip-git-repo-check', '--json', '-C', str(self.work), 'Wait.'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(self.started.wait(20))
            process.terminate()
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 143, stdout+stderr)
            self.assertEqual(len(self.requests), 1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_interactive_native_ui_and_termination(self):
        with (self.path/'codex/config.toml').open('a') as f:
            f.write('\n[projects."'+str(self.work)+'"]\ntrust_level="trusted"\n')
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 120, 0, 0))
        process = subprocess.Popen([sys.executable, '-m', 'cxgear', 'codex', '--no-alt-screen', '-C', str(self.work), 'Say hello.'], stdin=slave, stdout=slave, stderr=slave, env=dict(os.environ, TERM='xterm-256color'), start_new_session=True)
        os.close(slave)
        output = b''
        try:
            deadline = time.monotonic()+25
            while time.monotonic() < deadline and process.poll() is None:
                if select.select([master], [], [], .2)[0]:
                    chunk = os.read(master, 65536)
                    output += chunk
                    if b'\x1b[6n' in chunk:
                        os.write(master, b'\x1b[1;1R')
                    if b'\x1b[c' in chunk:
                        os.write(master, b'\x1b[?1;2c')
                if b'Answer 1 from a' in output:
                    break
            self.assertIn(b'Answer 1 from a', output)
            self.assertIn(b'OpenAI Codex', output)
            process.terminate()
            self.assertEqual(process.wait(timeout=15), 143)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
