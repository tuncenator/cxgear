import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cxgear import store
from cxgear.status import describe, enrich


class StatusTest(unittest.TestCase):
    def snapshot(self):
        return {'active': 'work', 'accounts': {'work': {'at': 1000, 'plan': 'pro', 'label': 'Work', 'windows': [{'minutes': 10080, 'used': 59, 'reset': 605800}]}}}

    def test_fresh_quota_has_bars_and_remaining_allowance(self):
        output = describe(self.snapshot(), now=1000)
        self.assertIn('Work (work) · Pro · ACTIVE', output)
        self.assertIn('59% used · 41% left', output)
        self.assertIn('█', output)
        self.assertIn('Short window: not reported', output)
        self.assertIn('not enabled', output)
        self.assertNotIn('\033', output)

    def test_stale_and_reset_readings_never_show_available_headroom(self):
        for now in (1200, 606000):
            output = describe(self.snapshot(), now=now)
            self.assertIn('current allowance unknown', output)
            self.assertNotIn('% left', output)
            self.assertNotIn('█', output)

    def test_missing_usage_is_not_zero(self):
        snapshot = self.snapshot()
        snapshot['accounts']['work']['windows'] = []
        output = describe(snapshot, now=1000)
        self.assertIn('Usage unavailable', output)
        self.assertNotIn('0% used', output)

    def test_metadata_excludes_credentials_and_checks_actual_launcher(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CXGEAR_HOME': tmp}), patch('cxgear.accounts.credentials', return_value={'chatgptPlanType': 'pro', 'accessToken': 'DO-NOT-EXPOSE', 'chatgptAccountId': 'PRIVATE-ID'}):
            store.write(store.root()/'config.json', {'accounts': {'work': {'label': 'Work'}}})
            target = Path(tmp)/'codex'
            wrapper = Path(tmp)/'wrapper'
            wrapper.write_text('wrapper')
            target.symlink_to(wrapper)
            store.write(store.root()/'install.json', {'target': str(target), 'wrapper': str(wrapper)})
            result = enrich(self.snapshot())
            self.assertTrue(result['launcher_installed'])
            self.assertEqual(result['accounts']['work']['plan'], 'pro')
            self.assertNotIn('DO-NOT-EXPOSE', json.dumps(result))
            self.assertNotIn('PRIVATE-ID', json.dumps(result))
            target.unlink()
            self.assertFalse(enrich(self.snapshot())['launcher_installed'])
