import unittest
from cxgear.policy import choose, parse_limits


NOW = 1800000000


def usage(weekly=10, hours=120, short=None, at=NOW):
    windows = [{"minutes": 10080, "used": weekly, "reset": NOW+hours*3600 if hours is not None else None}]
    if short is not None:
        windows.append({"minutes": 300, "used": short, "reset": NOW+3600})
    return {"at": at, "windows": windows}


class PolicyTest(unittest.TestCase):
    def decision(self, a, b, current="a", extra=False, **kw):
        cfg = {"a": {"capacity": 20}, "b": {"capacity": 5, "extra_usage": extra}}
        return choose(cfg, {"a": a, "b": b}, current, None, NOW, **kw)

    def test_ccgear_reference_cases(self):
        # Expected selections checked against ccgear bc63b69 src/policy.py.
        # a occupies Max's drain-first role; b has explicit extra-usage opt-in.
        cases = [
            ("41 vs 100 remaining", usage(59, 121), usage(0, 168), "a", "b"),
            ("short cap", usage(short=90), usage(short=79), "a", "b"),
            ("short cap can escape to risky target", usage(short=90), usage(short=89), "a", "b"),
            ("short cap cannot escape to weekly cap", usage(short=90), usage(96, short=0), "a", "a"),
            ("weekly cap can escape to short cap", usage(96, short=0), usage(0, short=100), "a", "b"),
            ("short cap order precedes weekly escape", usage(96, short=97), usage(0, short=100), "a", "a"),
            ("both short full drain first", usage(short=97), usage(short=90), "b", "a"),
            ("both short full extra fallback", usage(short=98), usage(short=90), "a", "b"),
            ("both weekly full drain first", usage(98), usage(96), "b", "a"),
            ("both weekly full extra fallback", usage(99), usage(96), "a", "b"),
            ("perishable overrides higher pressure", usage(0, 72), usage(90, 24), "a", "b"),
            ("perishable requires 3x runway", usage(0, 71), usage(90, 24), "a", "a"),
            ("perishable target cannot be risky", usage(0, 72), usage(90, 24, short=80), "a", "a"),
            ("normal target cannot be risky", usage(80), usage(0, short=80), "a", "a"),
            ("deadband holds", usage(11), usage(10), "a", "a"),
            ("deadband strict boundary", usage(50, 1), usage(48, 1), "a", "b"),
            ("dormant target preferred", usage(10), usage(0, None), "a", "b"),
            ("dormant target risky", usage(10), usage(0, None, short=80), "a", "a"),
            ("both dormant hold", usage(0, None), usage(0, None), "b", "b"),
            ("one minute pressure floor", usage(99, .001), usage(98, .01), "b", "b"),
        ]
        for label, a, b, current, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(self.decision(a, b, current, extra=True).account, expected)

    def test_plan_capacity_never_weights_selection(self):
        for weights in ((20, 5), (5, 20), (1, 1000)):
            cfg = {"a": {"capacity": weights[0]}, "b": {"capacity": weights[1]}}
            readings = {"a": usage(59, 121), "b": usage(0, 168)}
            self.assertEqual(choose(cfg, readings, "a", None, NOW).account, "b")

    def test_no_time_based_cooldown(self):
        self.assertEqual(self.decision(usage(80), usage(10), last_switch=NOW-1).account, "b")

    def test_stale_expired_missing_and_dead_are_not_headroom(self):
        for bad in (usage(0, at=NOW-181), usage(0, hours=-1), {"at": NOW, "windows": []}, {**usage(0), "error": "reauthenticate"}):
            self.assertEqual(self.decision(usage(97), bad).account, "a")
            self.assertEqual(self.decision(bad, usage(100)).account, "b")
            self.assertEqual(self.decision(bad, bad).account, "a")

    def test_pin_is_respected_even_when_capped_or_dead(self):
        for a in (usage(99), {**usage(0), "error": "reauthenticate"}):
            d = choose({"a": {}, "b": {}}, {"a": a, "b": usage(0)}, "a", "a", NOW)
            self.assertEqual(d.account, "a")

    def test_weekly_only_and_short_only(self):
        self.assertEqual(self.decision(usage(97), usage(20)).account, "b")
        short = {"at": NOW, "windows": [{"minutes": 300, "used": 5, "reset": NOW+1000}]}
        self.assertEqual(self.decision(usage(97), short).account, "b")

    def test_extra_usage_requires_opt_in(self):
        self.assertIsNone(self.decision(usage(100), usage(100), current=None).account)
        self.assertEqual(self.decision(usage(100), usage(100)).account, "a")
        self.assertEqual(self.decision(usage(100), usage(100), extra=True).account, "b")
        self.assertEqual(self.decision(usage(short=100), usage(short=100)).account, "a")

    def test_missing_and_malformed_limit_is_never_zero(self):
        for value in (None, True, -1, 101, float("nan")):
            reading = parse_limits({"rateLimits": {"secondary": {"usedPercent": value, "windowDurationMins": 10080, "resetsAt": NOW+300}}}, NOW)
            self.assertEqual(reading["windows"], [])
        self.assertEqual(parse_limits({}, NOW)["windows"], [])

    def test_explicit_zero_with_null_reset_is_dormant(self):
        def parse(used):
            return parse_limits({"rateLimits": {"secondary": {"usedPercent": used, "windowDurationMins": 10080, "resetsAt": None}}}, NOW)
        dormant = parse(0)
        self.assertEqual(self.decision(usage(10), dormant).account, "b")
        self.assertEqual(parse(10)["windows"], [])
