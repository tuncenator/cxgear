import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cxgear import install, store


class DurableInstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cxgear-install-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home/'.local/bin'
        self.bin.mkdir(parents=True)
        self.native = self.home/'native-current'
        self.native.write_text('#!/bin/sh\necho native-old\n')
        self.native.chmod(0o700)
        self.target = self.bin/'codex'
        self.target.symlink_to(self.native)
        self.venv = self.home/'venv/bin'
        self.venv.mkdir(parents=True)
        self.wrapper = self.venv/'cxgear-codex'
        self.wrapper.write_text('#!/bin/sh\necho managed\n')
        self.wrapper.chmod(0o700)
        self.startup = 'export PATH="'+str(self.bin)+':$PATH"\n# existing shell configuration\n'
        (self.home/'.zshrc').write_text(self.startup)
        (self.home/'.bashrc').write_text(self.startup)
        (self.home/'.bash_profile').write_text('# existing login config\n')
        for context in (
            patch.dict(os.environ, {'HOME': str(self.home), 'ZDOTDIR': str(self.home), 'CXGEAR_HOME': str(self.home/'state')}),
            patch('pathlib.Path.home', return_value=self.home),
            patch('sys.executable', str(self.venv/'python')),
            patch('cxgear.accounts.binary', return_value=str(self.native)),
        ):
            context.start()
            self.addCleanup(context.stop)

    def shell_selection(self, shell):
        shell_bin = shutil.which(shell)
        if not shell_bin:
            self.skipTest(shell+' not installed')
        env = dict(os.environ, PATH=str(self.bin)+':/usr/bin:/bin')
        args = ([shell_bin, '-ic', 'command -v codex; codex'] if shell == 'zsh' else
                [shell_bin, '--noprofile', '--rcfile', str(self.home/'.bashrc'), '-ic', 'command -v codex; codex'])
        run = subprocess.run(args, env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('env.sh', run.stderr)
        return run.stdout.strip().splitlines()

    def test_native_update_cannot_replace_private_launcher(self):
        launcher = install.install()
        self.assertEqual(self.target.resolve(), self.native)
        for shell in (name for name in ('zsh','bash') if shutil.which(name)):
            self.assertEqual(self.shell_selection(shell), [str(launcher), 'managed'])
        # Simulate updater replacing its own command, including version changes.
        newer = self.home/'native-new'
        newer.write_text('#!/bin/sh\necho native-new\n'); newer.chmod(0o700)
        self.target.unlink(); self.target.symlink_to(newer)
        self.native.write_text('#!/bin/sh\necho native-updated\n')
        for shell in (name for name in ('zsh','bash') if shutil.which(name)):
            self.assertEqual(self.shell_selection(shell), [str(launcher), 'managed'])
        install.install()
        self.assertEqual((self.home/'.zshrc').read_text().count('# >>> cxgear >>>'), 1)
        with (self.home/'.zshrc').open('a') as f:
            f.write('# later user edit\n')
        install.uninstall()
        self.assertEqual(self.target.resolve(), newer)
        self.assertEqual((self.home/'.zshrc').read_text(), self.startup+'# later user edit\n')
        self.assertEqual((self.home/'.bash_profile').read_text(), '# existing login config\n')
        self.assertFalse((self.home/'.zprofile').exists())
        self.assertFalse(launcher.exists())

    def test_shell_block_is_home_relative(self):
        # A startup file synced between hosts with different home paths gets one block.
        with patch.dict(os.environ, {'CXGEAR_HOME': str(self.home/'.local/share/cxgear')}):
            install.install()
            text = (self.home/'.zshrc').read_text()
        self.assertIn('"$HOME"/.local/share/cxgear/env.sh', text)
        self.assertNotIn(str(self.home)+'/.local/share/cxgear/env.sh', text)

    def test_existing_block_stays_in_place(self):
        (self.home/'.zshrc').write_text('a\n# >>> cxgear >>>\n. /old/env.sh\n# <<< cxgear <<<\nb\n')
        install.install()
        lines = (self.home/'.zshrc').read_text().splitlines()
        self.assertEqual((lines[0], lines[1], lines[-1]), ('a', '# >>> cxgear >>>', 'b'))
        self.assertNotIn('/old/env.sh', '\n'.join(lines))

    def test_shell_startup_ignores_missing_state(self):
        # Startup files may be synced to hosts where cxgear is not installed.
        install.install()
        shutil.rmtree(store.root())
        for shell in (name for name in ('zsh','bash') if shutil.which(name)):
            self.assertEqual(self.shell_selection(shell), [str(self.target), 'native-old'])

    def test_migration_restores_native_command_and_preserves_backup(self):
        install.install(self.bin)
        self.assertEqual(self.target.resolve(), self.wrapper)
        launcher = install.install()
        self.assertEqual(self.target.resolve(), self.native)
        self.assertFalse((self.bin/'.codex-before-cxgear').exists())
        self.assertEqual(launcher.resolve(), self.wrapper)
        self.assertEqual(store.read(store.root()/'install.json')['mode'], 'path')
        install.uninstall()
        self.assertEqual(self.target.resolve(), self.native)

    def test_migration_accepts_native_update_but_not_unrelated_replacement(self):
        install.install(self.bin)
        self.target.unlink(); self.target.symlink_to(self.native)
        install.install()
        self.assertEqual(self.target.resolve(), self.native)
        install.uninstall()
        install.install(self.bin)
        unrelated = self.home/'unrelated'; unrelated.write_text('leave alone')
        self.target.unlink(); self.target.symlink_to(unrelated)
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            install.install()
        self.assertEqual(self.target.resolve(), unrelated)
        self.assertFalse((self.home/'.zprofile').exists())
