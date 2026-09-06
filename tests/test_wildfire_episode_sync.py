import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from wildfire_episode_metrics import parse_wildfire_episode_metrics  # noqa: E402


class WildfireEpisodeSyncTests(unittest.TestCase):
    def test_requested_episode_area_and_dates(self):
        payload = {
            "properties": {
                "episodes": [
                    {
                        "episodeid": 9,
                        "severitydata": {"severitytext": "Green impact for forestfire in 15813 ha"},
                        "fromdate": "2026-08-27T00:00:00",
                        "todate": "2026-09-04T00:00:00",
                    },
                    {
                        "episodeid": 10,
                        "severitydata": {"severitytext": "Green impact for forestfire in 16194 ha"},
                        "fromdate": "2026-08-27T00:00:00",
                        "todate": "2026-09-05T00:00:00",
                    },
                ]
            }
        }
        got = parse_wildfire_episode_metrics(payload, 10)
        self.assertEqual(got["burned_area_ha"], 16194)
        self.assertEqual(got["start_date"], "2026-08-27")
        self.assertEqual(got["last_detection"], "2026-09-05")
        self.assertEqual(got["duration_days"], 9)

    def test_explicit_ha_wins(self):
        payload = {
            "episodeid": 11,
            "ha": "16,250",
            "severitydata": {"severitytext": "Green impact for forestfire in 16194 ha"},
        }
        got = parse_wildfire_episode_metrics(payload, 11)
        self.assertEqual(got["burned_area_ha"], 16250)
        self.assertEqual(got["burned_area_source_field"], "ha")

    def test_episode_payload_without_episode_id_still_parses(self):
        payload = {
            "severitydata": {"severitytext": "Green impact for forestfire in 12,345 ha"},
            "fromDate": "2026-08-01",
            "toDate": "2026-08-06",
        }
        got = parse_wildfire_episode_metrics(payload, 7)
        self.assertEqual(got["burned_area_ha"], 12345)
        self.assertEqual(got["duration_days"], 5)


if __name__ == "__main__":
    unittest.main()
