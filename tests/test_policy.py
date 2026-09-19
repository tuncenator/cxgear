import unittest
from cxgear.policy import choose, parse_limits


NOW = 1800000000


def usage(weekly=10, hours=120, short=None, at=NOW):
    windows = [{"minutes": 10080, "used": weekly, "reset": NOW+hours*3600}]
    if short is not None:
        windows.append({"minutes": 300, "used": short, "reset": NOW+3600})
    return {"at": at, "windows": windows}


class PolicyTest(unittest.TestCase):
    def decision(self, a, b, **kw):
        return choose({"a": {}, "b": {}}, {"a": a, "b": b}, "a", None, NOW, **kw)

    def test_weekly_and_short_exhaustion(self):
        self.assertEqual(self.decision(usage(97), usage(10)).account, "b")
        self.assertEqual(self.decision(usage(short=93), usage(short=5)).account, "b")

    def test_earlier_reset_prevents_wasted_allowance(self):
        self.assertEqual(self.decision(usage(0, 168), usage(70, 12)).account, "b")

    def test_cooldown_and_deadband(self):
        self.assertEqual(self.decision(usage(80), usage(10), last_switch=NOW-10).account, "a")
        self.assertEqual(self.decision(usage(11), usage(10)).account, "a")

    def test_stale_expired_and_dead_are_not_headroom(self):
        for bad in (usage(0, at=NOW-181), usage(0, hours=-1), {**usage(0), "error": "reauthenticate"}):
            self.assertEqual(self.decision(usage(97), bad).account, "a")

    def test_pin_is_respected_even_when_capped(self):
        d = choose({"a": {}, "b": {}}, {"a": usage(99), "b": usage(0)}, "a", "a", NOW)
        self.assertEqual(d.account, "a")

    def test_weekly_only_and_short_only(self):
        self.assertEqual(self.decision(usage(97), usage(20)).account, "b")
        short = {"at": NOW, "windows": [{"minutes": 300, "used": 5, "reset": NOW+1000}]}
        self.assertEqual(self.decision(usage(97), short).account, "b")

    def test_relative_capacity_and_explicit_extra_usage(self):
        cfg = {"a": {"capacity": 1}, "b": {"capacity": 5}}
        self.assertEqual(choose(cfg, {"a": usage(10), "b": usage(10)}, "a", None, NOW).account, "b")
        self.assertIsNone(choose(cfg, {"a": usage(100), "b": usage(100)}, None, None, NOW).account)
        cfg["b"]["extra_usage"] = True
        self.assertEqual(choose(cfg, {"a": usage(100), "b": usage(100)}, "a", None, NOW).account, "b")

    def test_missing_and_malformed_limit_is_never_zero(self):
        for value in (None, True, -1, 101, float("nan")):
            reading = parse_limits({"rateLimits": {"secondary": {"usedPercent": value, "windowDurationMins": 10080, "resetsAt": NOW+300}}}, NOW)
            self.assertEqual(reading["windows"], [])
