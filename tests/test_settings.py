from __future__ import annotations

import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from vspider.settings import local_datetime_fromtimestamp, local_timezone


class SettingsTests(unittest.TestCase):
    def test_default_timezone_is_china(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            instant = local_datetime_fromtimestamp(0)
            self.assertEqual(local_timezone().utcoffset(instant), timedelta(hours=8))
            self.assertEqual(instant.hour, 8)

    def test_invalid_timezone_falls_back_to_utc_plus_eight(self) -> None:
        with patch.dict(os.environ, {"VSPIDER_TIMEZONE": "invalid/test-zone"}):
            zone = local_timezone()
            self.assertEqual(zone.utcoffset(None), timedelta(hours=8))
            self.assertEqual(local_datetime_fromtimestamp(0).hour, 8)


if __name__ == "__main__":
    unittest.main()
