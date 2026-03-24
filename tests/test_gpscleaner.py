import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.gpscleaner.gpscleaner import GPSCleaner, _interpolate_positions

FIXTURES_DIR = Path(__file__).parent / "fixtures"

RECORDING = FIXTURES_DIR / "260322-recording.GPX"
OUTPUT = FIXTURES_DIR / "260322-recording_cleaned.GPX"
# RECORDING = FIXTURES_DIR / "260322-recording-garmin.connect-export.gpx"
# OUTPUT = FIXTURES_DIR / "260322-recording-garmin.connect-export_cleaned.gpx"

TARGET = FIXTURES_DIR / "260322-reference.GPX"

START_TIME_UTC = datetime(2026, 3, 22, 15, 6, 8, tzinfo=timezone.utc)
END_TIME_UTC = datetime(2026, 3, 22, 15, 30, 50, tzinfo=timezone.utc)

GPX_NS = "http://www.topografix.com/GPX/1/1"


@pytest.fixture(autouse=True)
def cleanup_output():
    """Remove the output file before each test so previous results don't interfere."""
    if OUTPUT.exists():
        OUTPUT.unlink()
    yield


def count_trackpoints(gpx_file: Path) -> int:
    root = ET.parse(gpx_file).getroot()
    return len(list(root.iter(f"{{{GPX_NS}}}trkpt")))


def get_trackpoints_in_window(gpx_file: Path) -> list[ET.Element]:
    root = ET.parse(gpx_file).getroot()
    result = []
    for trkpt in root.iter(f"{{{GPX_NS}}}trkpt"):
        time_el = trkpt.find(f"{{{GPX_NS}}}time")
        if time_el is not None and time_el.text is not None:
            point_time = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
            if START_TIME_UTC <= point_time <= END_TIME_UTC:
                result.append(trkpt)
    return result


class TestGPSCleaner:

    def test_output_file_is_created(self):
        GPSCleaner(START_TIME_UTC, END_TIME_UTC, RECORDING, TARGET).start()
        assert OUTPUT.exists()

    def test_original_file_is_not_modified(self):
        original_content = RECORDING.read_bytes()
        GPSCleaner(START_TIME_UTC, END_TIME_UTC, RECORDING, TARGET).start()
        assert RECORDING.read_bytes() == original_content

    def test_trackpoint_count_is_preserved(self):
        original_count = count_trackpoints(RECORDING)
        GPSCleaner(START_TIME_UTC, END_TIME_UTC, RECORDING, TARGET).start()
        assert count_trackpoints(OUTPUT) == original_count

    def test_points_in_window_have_new_coordinates(self):
        original_points = get_trackpoints_in_window(RECORDING)
        original_lats = [float(p.attrib["lat"]) for p in original_points]

        GPSCleaner(START_TIME_UTC, END_TIME_UTC, RECORDING, TARGET).start()

        cleaned_points = get_trackpoints_in_window(OUTPUT)
        cleaned_lats = [float(p.attrib["lat"]) for p in cleaned_points]

        assert len(cleaned_lats) == len(original_lats)
        assert cleaned_lats != original_lats

    def test_points_outside_window_are_unchanged(self):
        root_original = ET.parse(RECORDING).getroot()
        all_original = list(root_original.iter(f"{{{GPX_NS}}}trkpt"))

        GPSCleaner(START_TIME_UTC, END_TIME_UTC, RECORDING, TARGET).start()

        root_cleaned = ET.parse(OUTPUT).getroot()
        all_cleaned = list(root_cleaned.iter(f"{{{GPX_NS}}}trkpt"))

        assert len(all_original) == len(all_cleaned)

        for orig, cleaned in zip(all_original, all_cleaned):
            time_el = orig.find(f"{{{GPX_NS}}}time")
            if time_el is None or time_el.text is None:
                continue
            point_time = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
            if START_TIME_UTC <= point_time <= END_TIME_UTC:
                continue  # skip points inside the window
            assert orig.attrib["lat"] == cleaned.attrib["lat"]
            assert orig.attrib["lon"] == cleaned.attrib["lon"]


class TestInterpolatePositions:

    def test_empty_count_returns_empty_list(self):
        points = [(52.0, 13.0, 40.0), (52.1, 13.1, 41.0)]
        assert _interpolate_positions(points, 0) == []

    def test_single_point_returns_repeated(self):
        points = [(52.0, 13.0, 40.0)]
        result = _interpolate_positions(points, 3)
        assert len(result) == 3
        assert all(r == (52.0, 13.0, 40.0) for r in result)

    def test_first_and_last_match_endpoints(self):
        points = [(52.0, 13.0, 40.0), (52.1, 13.0, 40.0), (52.2, 13.0, 40.0)]
        result = _interpolate_positions(points, 3)
        assert result[0][0] == pytest.approx(52.0)
        assert result[-1][0] == pytest.approx(52.2, abs=1e-6)

    def test_count_one_returns_midpoint(self):
        # Two symmetric points — midpoint should be exactly the middle
        points = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
        result = _interpolate_positions(points, 1)
        assert len(result) == 1
        assert result[0][0] == pytest.approx(0.5, abs=0.01)
