import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cxgear import accounts, install, store
from cxgear.exec import parse, prompt_text, Unsupported
from cxgear.launcher import command, launch, native_auth_requested, native
from cxgear.output import Output, item_event


class CompatibilityTest(unittest.TestCase):
    def test_exec_resume_and_flags_keep_meaning(self):
        x = parse(['-c', 'model_reasoning_effort="high"', '--json', 'resume', '1234', '--output-last-message', '/tmp/result', '--dangerously-bypass-approvals-and-sandbox', 'Continue'])
        self.assertEqual((x.mode, x.thread, x.prompt), ('resume', '1234', 'Continue'))
        self.assertTrue(x.json)
        self.assertTrue(x.dangerously_bypass_approvals_and_sandbox)
        self.assertEqual(parse(['resume', '--last', 'Continue']).prompt, 'Continue')

    def test_unknown_options_do_not_silently_change_permissions(self):
        with self.assertRaises(Unsupported):
            parse(['--new-sandbox-option', 'hi'])

    def test_command_detection_does_not_mistake_option_values(self):
        self.assertEqual(command(['-m', 'exec', 'hello']), (None, None))
        self.assertEqual(command(['-c', 'x="resume"', 'exec', 'hello']), ('exec', 2))
        self.assertEqual(command(['--', 'exec']), (None, None))

    def test_piped_input_is_preserved(self):
        with patch('sys.stdin', io.StringIO('data')):
            self.assertEqual(prompt_text('Inspect'), 'Inspect\n\n<stdin>\ndata\n</stdin>')
        with patch('sys.stdin', io.StringIO('full prompt')):
            self.assertEqual(prompt_text('-'), 'full prompt')

    def test_public_json_projection(self):
        x = item_event({'type': 'commandExecution', 'id': '1', 'command': 'false', 'status': 'failed', 'exitCode': 1, 'aggregatedOutput': 'failed'})
        self.assertEqual((x['type'], x['status'], x['exit_code']), ('command_execution', 'failed', 1))
        self.assertIsNone(item_event({'type': 'rawReasoning', 'id': '2', 'text': 'private'}))
        out = Output(True)
        out.usage = {'inputTokens': 130, 'outputTokens': 20}
        with patch('sys.stdout', io.StringIO()) as stream:
            out.completed()
            self.assertEqual(json.loads(stream.getvalue())['usage']['input_tokens'], 130)

    def test_unconfigured_and_api_key_runs_are_native(self):
        with patch('cxgear.store.config', return_value={'accounts': {}}), patch('cxgear.launcher.native', side_effect=SystemExit(9)) as native:
            with self.assertRaises(SystemExit):
                launch(['exec', '--json', 'hi'])
            native.assert_called_once_with(['exec', '--json', 'hi'])

    def test_private_state_and_path_validation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CXGEAR_HOME': tmp}):
            path = store.root()/'state.json'
            store.write(path, {'active': 'work'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                store.account_home('../outside')

    def test_global_openai_key_does_not_bypass_chatgpt_rotation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CODEX_HOME': tmp, 'OPENAI_API_KEY': 'unrelated-key'}, clear=True):
            store.write(Path(tmp)/'auth.json', {'auth_mode': 'chatgpt', 'tokens': {}})
            with patch('cxgear.store.config', return_value={'accounts': {'work': {}}}), patch('cxgear.launcher.run', return_value=0) as run, patch('cxgear.launcher.native') as upstream:
                self.assertEqual(launch(['exec', '--json', 'hi']), 0)
                run.assert_called_once()
                upstream.assert_not_called()

    def test_explicit_native_auth_remains_native(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CODEX_HOME': tmp}, clear=True):
            store.write(Path(tmp)/'auth.json', {'auth_mode': 'apikey', 'OPENAI_API_KEY': 'fixture'})
            self.assertTrue(native_auth_requested())
            store.write(Path(tmp)/'auth.json', {'auth_mode': 'chatgpt', 'tokens': {}})
            for key in ('CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'OPENAI_IDENTITY_TOKEN_FILE'):
                with patch.dict(os.environ, {key: 'fixture'}):
                    self.assertTrue(native_auth_requested())

    def test_native_fallback_does_not_disable_rotation_for_children(self):
        with patch.dict(os.environ, {}, clear=True), patch('cxgear.accounts.binary', return_value='/native/codex'), patch('os.execve') as execute:
            native(['--version'])
            self.assertNotIn('CXGEAR_BYPASS', execute.call_args.args[2])

    def test_installer_preserves_and_restores_native(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CXGEAR_HOME': tmp+'/state'}):
            bin_dir = Path(tmp)/'bin';bin_dir.mkdir()
            executable = bin_dir/'codex';executable.write_text('native')
            env = Path(tmp)/'venv/bin';env.mkdir(parents=True)
            wrapper = env/'cxgear-codex';wrapper.write_text('wrapper')
            with patch('sys.executable', str(env/'python')), patch('cxgear.accounts.binary', return_value=str(executable)):
                install.install(bin_dir)
                self.assertEqual(executable.resolve(), wrapper)
                install.uninstall()
                self.assertFalse(executable.is_symlink())
                self.assertEqual(executable.read_text(), 'native')
