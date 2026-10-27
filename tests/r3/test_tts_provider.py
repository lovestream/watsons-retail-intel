import os
import unittest
from unittest import mock

from skills.generate_periodic_report.generate_periodic_report import (
    _rate_to_speed, _siliconflow_tts_chunk)


class TtsProviderTest(unittest.TestCase):
    def test_rate_to_speed_is_bounded(self):
        self.assertAlmostEqual(_rate_to_speed("+55%"), 1.55)
        self.assertAlmostEqual(_rate_to_speed("-15%"), 0.85)
        self.assertAlmostEqual(_rate_to_speed("+200%"), 2.0)

    def test_siliconflow_requires_key_and_never_uses_old_audio(self):
        with mock.patch.dict(os.environ, {"siliconflow_api_key": ""}, clear=False):
            self.assertFalse(_siliconflow_tts_chunk("测试", "+55%", "/tmp/x.mp3"))


if __name__ == "__main__":
    unittest.main()
