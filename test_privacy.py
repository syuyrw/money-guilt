"""Tests for privacy: masking, idle detection and the auto-hide lock.

Run with:  python -m unittest test_privacy -v
"""
import os
import subprocess
import sys
import unittest

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

import privacy  # noqa: E402
from privacy import MASK, PrivacyState, mask_numbers  # noqa: E402


class Masking(unittest.TestCase):
    def test_every_kind_of_figure_becomes_the_same_placeholder(self):
        cases = {
            'Instead of wasting $346.98 this year': f'Instead of wasting {MASK} this year',
            'Out of $352.38 total spending': f'Out of {MASK} total spending',
            'wasted over 3 purchases': f'wasted over {MASK} purchases',
            '$1,268.20': MASK,
            '98.5%': MASK,
            '7.2': MASK,
            '£40': MASK,
        }
        for text, expected in cases.items():
            self.assertEqual(mask_numbers(text), expected, text)

    def test_no_digit_survives(self):
        for text in ('$0.99', '1,000,000', 'wasted 12 of 30 (40%)', '$5 + $10.50',
                     '€12,50'):
            self.assertFalse(any(c.isdigit() for c in mask_numbers(text)), text)

    def test_the_placeholder_length_does_not_reveal_the_size(self):
        self.assertEqual(mask_numbers('$5'), mask_numbers('$5,000,000.99'))

    def test_text_without_numbers_is_untouched(self):
        self.assertEqual(mask_numbers('Money spent on wasteful purchases'),
                         'Money spent on wasteful purchases')

    def test_empty_and_none(self):
        self.assertEqual(mask_numbers(''), '')
        self.assertEqual(mask_numbers(None), '')


class IdleReading(unittest.TestCase):
    def test_parses_nanoseconds_into_seconds(self):
        out = '    | |   "HIDIdleTime" = 33734629666\n'
        self.assertAlmostEqual(privacy.parse_idle(out), 33.734629666)

    def test_unparseable_output_is_none(self):
        for bad in ('', None, 'nothing useful', '"HIDIdleTime" = abc'):
            self.assertIsNone(privacy.parse_idle(bad), repr(bad))

    def test_runs_the_expected_command(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen['cmd'] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout='"HIDIdleTime" = 2000000000')
        self.assertEqual(privacy.idle_seconds(fake_run), 2.0)
        self.assertEqual(seen['cmd'], [privacy.IOREG, '-c', 'IOHIDSystem'])
        self.assertTrue(os.path.isabs(privacy.IOREG))

    def test_failures_return_none_instead_of_raising(self):
        def missing(cmd, **kw):
            raise FileNotFoundError('ioreg')

        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 5)
        self.assertIsNone(privacy.idle_seconds(missing))
        self.assertIsNone(privacy.idle_seconds(slow))

    def test_the_real_reading_is_a_sane_number_on_this_machine(self):
        if sys.platform != 'darwin':
            self.skipTest('macOS only')
        value = privacy.idle_seconds()
        self.assertIsNotNone(value)
        self.assertGreaterEqual(value, 0)


class AutoHide(unittest.TestCase):
    def state(self, idle, **kw):
        self.idle = idle
        return PrivacyState(idle_fn=lambda: self.idle, **kw)

    def test_locks_once_idle_reaches_the_limit(self):
        s = self.state(299, idle_limit=300)
        self.assertFalse(s.check_idle())
        self.assertFalse(s.masked)
        self.idle = 300
        self.assertTrue(s.check_idle())
        self.assertTrue(s.masked)

    def test_activity_alone_never_reveals(self):
        """The property the lock exists for: nudging the mouse must not unmask."""
        s = self.state(600, idle_limit=300)
        s.check_idle()
        self.idle = 0                      # someone touches the mouse
        s.check_idle()
        self.assertTrue(s.masked, 'input alone must not clear the lock')

    def test_a_deliberate_reveal_clears_it(self):
        s = self.state(600, idle_limit=300)
        s.check_idle()
        self.assertTrue(s.reveal())
        self.assertFalse(s.masked)

    def test_relocks_after_a_reveal_if_idle_again(self):
        s = self.state(600, idle_limit=300)
        s.check_idle(); s.reveal()
        self.idle = 10
        self.assertFalse(s.check_idle())
        self.idle = 400
        self.assertTrue(s.check_idle())

    def test_disabled_when_auto_hide_is_off_or_limit_is_zero(self):
        self.assertFalse(self.state(9999, auto_hide=False).check_idle())
        self.assertFalse(self.state(9999, idle_limit=0).check_idle())

    def test_turning_auto_hide_off_releases_an_existing_lock(self):
        s = self.state(600, idle_limit=300)
        s.check_idle()
        s.set_auto_hide(False)
        self.assertFalse(s.masked)

    def test_unreadable_idle_time_does_not_lock(self):
        s = self.state(None, idle_limit=300)
        self.assertFalse(s.check_idle())
        self.assertFalse(s.masked)

    def test_manual_hiding_is_independent_of_the_lock(self):
        s = self.state(0, manual=True)
        self.assertTrue(s.masked)
        s.reveal()
        self.assertTrue(s.masked, 'revealing the idle lock must not undo manual hiding')
        s.manual = False
        self.assertFalse(s.masked)


class CaptureExclusion(unittest.TestCase):
    def test_refuses_a_null_view_instead_of_crashing(self):
        self.assertFalse(privacy.set_capture_excluded(0, True))
        self.assertFalse(privacy.set_capture_excluded(None, True))


if __name__ == '__main__':
    unittest.main()
