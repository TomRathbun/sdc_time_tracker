"""BEOD checkbox is offered only after 5h of work."""

from __future__ import annotations

import unittest

from app.config import BEOD_MINIMUM_HOURS, BEOD_OPTION_MIN_HOURS
from app.services.time_calc import show_beod_option


class BeodOptionTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(BEOD_OPTION_MIN_HOURS, 5.0)
        self.assertEqual(BEOD_MINIMUM_HOURS, 6.0)

    def test_hidden_before_five_hours(self):
        self.assertFalse(show_beod_option(0))
        self.assertFalse(show_beod_option(4.99))

    def test_shown_at_five_hours(self):
        self.assertTrue(show_beod_option(5.0))
        self.assertTrue(show_beod_option(5.5))
        self.assertTrue(show_beod_option(8.0))

    def test_hidden_if_already_claimed(self):
        self.assertFalse(show_beod_option(8.0, already_claimed=True))


if __name__ == "__main__":
    unittest.main()
