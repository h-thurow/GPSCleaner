import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.gpscleaner.cli import app
from src.gpscleaner.gpscleaner import GPSCleaner, GPSSampleRateReducer, _get_timestamp_by_coord, _get_timestamp_by_index, _interpolate_positions

FIXTURES_DIR = Path(__file__).parent / "fixtures"

RECORDING = FIXTURES_DIR / "260322-recording.GPX"
OUTPUT = FIXTURES_DIR / "260322-recording_cleaned.GPX"

TARGET = FIXTURES_DIR / "260322-reference.GPX"

ZUCCO_RECORDING = FIXTURES_DIR / "Zucco di Manavello.GPX"
ZUCCO_REFERENCE = FIXTURES_DIR / "Zucco di Manavello reference.GPX"
ZUCCO_CLEANED   = FIXTURES_DIR / "Zucco di Manavello_cleaned.GPX"

# Coordinates of points 5603 and 7080 in Zucco di Manavello.GPX
ZUCCO_START_COORD = "45.924565242603421,9.340607235208154"
ZUCCO_END_COORD   = "45.923309549689293,9.34313572011888"

CAM_RECORDING = FIXTURES_DIR / "1_CAM_20260103124845_0011_D.gpx"
CAM_REFERENCE = FIXTURES_DIR / "4_CAM_20260103124845_0011_D-reference.GPX"
CAM_CLEANED   = FIXTURES_DIR / "1_CAM_20260103124845_0011_D_cleaned.gpx"


def cam_output(sample_rate: float) -> Path:
    return FIXTURES_DIR / f"1_CAM_20260103124845_0011_D_sample-rate={sample_rate}.gpx"

START_TIME_UTC = datetime(2026, 3, 22, 15, 6, 8, tzinfo=timezone.utc)
END_TIME_UTC = datetime(2026, 3, 22, 15, 30, 50, tzinfo=timezone.utc)

GPX_NS = "http://www.topografix.com/GPX/1/1"

runner = CliRunner()


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


class TestGPSSampleRateReducer:

    @pytest.fixture(autouse=True)
    def cleanup_cam_output(self):
        """Remove the CAM output file before each test."""
        for f in FIXTURES_DIR.glob("1_CAM_20260103124845_0011_D_sample-rate=*.gpx"):
            f.unlink()
        yield

    def test_output_file_is_created(self):
        # 1_CAM has ~25 positions/second; reduce to 1/s
        GPSSampleRateReducer(CAM_RECORDING, 1.0).start()
        assert cam_output(1.0).exists()

    def test_original_file_is_not_modified(self):
        original_content = CAM_RECORDING.read_bytes()
        GPSSampleRateReducer(CAM_RECORDING, 1.0).start()
        assert CAM_RECORDING.read_bytes() == original_content

    def test_trackpoint_count_is_reduced(self):
        # 1_CAM has ~25 positions/second; reducing to 1/s should keep roughly 1/25
        original_count = count_trackpoints(CAM_RECORDING)
        GPSSampleRateReducer(CAM_RECORDING, 1.0).start()
        reduced_count = count_trackpoints(cam_output(1.0))
        assert reduced_count < original_count
        assert reduced_count < original_count / 10

    def test_hint_when_sample_rate_cannot_be_reduced(self, capsys):
        # 260322-recording.GPX has ~1 position/second; requesting 2/s is impossible
        GPSSampleRateReducer(RECORDING, 2.0).start()
        captured = capsys.readouterr()
        assert "Cannot reduce" in captured.out
        assert not OUTPUT.exists()

    def test_sample_rate_with_start_raises_error(self):
        result = runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--sample-rate", "1",
            "--start", "2026-01-03T11:48:46Z",
        ])
        assert result.exit_code != 0
        assert "--sample-rate" in result.output

    def test_sample_rate_with_end_raises_error(self):
        result = runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--sample-rate", "1",
            "--end", "2026-01-03T12:00:00Z",
        ])
        assert result.exit_code != 0
        assert "--sample-rate" in result.output

    def test_sample_rate_with_reference_raises_error(self):
        result = runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--sample-rate", "1",
            "--reference", str(TARGET),
        ])
        assert result.exit_code != 0
        assert "--sample-rate" in result.output


class TestGPSCleanerByIndex:

    @pytest.fixture(autouse=True)
    def cleanup_cam_cleaned(self):
        """Remove the CAM cleaned output file before each test."""
        if CAM_CLEANED.exists():
            CAM_CLEANED.unlink()
        yield

    def test_output_file_is_created(self):
        runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--start-point", "100",
            "--end-point", "200",
            "--reference", str(CAM_REFERENCE),
        ])
        assert CAM_CLEANED.exists()

    def test_trackpoint_count_is_preserved(self):
        original_count = count_trackpoints(CAM_RECORDING)
        runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--start-point", "100",
            "--end-point", "200",
            "--reference", str(CAM_REFERENCE),
        ])
        assert count_trackpoints(CAM_CLEANED) == original_count

    def test_points_in_index_window_have_new_coordinates(self):
        root_original = ET.parse(CAM_RECORDING).getroot()
        all_original = list(root_original.iter(f"{{{GPX_NS}}}trkpt"))
        original_lats = [float(p.attrib["lat"]) for p in all_original[99:200]]

        runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--start-point", "100",
            "--end-point", "200",
            "--reference", str(CAM_REFERENCE),
        ])

        root_cleaned = ET.parse(CAM_CLEANED).getroot()
        all_cleaned = list(root_cleaned.iter(f"{{{GPX_NS}}}trkpt"))
        cleaned_lats = [float(p.attrib["lat"]) for p in all_cleaned[99:200]]

        assert cleaned_lats != original_lats

    def test_start_point_without_end_point_raises_error(self):
        result = runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--start-point", "100",
            "--reference", str(CAM_REFERENCE),
        ])
        assert result.exit_code != 0
        assert "--end-point" in result.output

    def test_start_point_with_start_time_raises_error(self):
        result = runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--start-point", "100",
            "--end-point", "200",
            "--start", "2026-01-03T11:48:46Z",
            "--reference", str(CAM_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_invalid_index_zero_raises_error(self):
        result = runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--start-point", "0",
            "--end-point", "200",
            "--reference", str(CAM_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_invalid_index_too_large_raises_error(self):
        result = runner.invoke(app, [
            "--orig", str(CAM_RECORDING),
            "--start-point", "100",
            "--end-point", "999999",
            "--reference", str(CAM_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_get_timestamp_by_index_returns_correct_time(self):
        # First point has a known timestamp
        ts = _get_timestamp_by_index(CAM_RECORDING, 1)
        assert ts.year == 2026
        assert ts.month == 1
        assert ts.day == 3

    def test_get_timestamp_by_index_raises_on_invalid_index(self):
        with pytest.raises(ValueError):
            _get_timestamp_by_index(CAM_RECORDING, 0)
        with pytest.raises(ValueError):
            _get_timestamp_by_index(CAM_RECORDING, 999999)


class TestGPSCleanerByCoord:

    @pytest.fixture(autouse=True)
    def cleanup_zucco_cleaned(self):
        """Remove the Zucco cleaned output file before each test."""
        if ZUCCO_CLEANED.exists():
            ZUCCO_CLEANED.unlink()
        yield

    def test_output_file_is_created(self):
        runner.invoke(app, [
            "--orig", str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--end-coord",   ZUCCO_END_COORD,
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert ZUCCO_CLEANED.exists()

    def test_trackpoint_count_is_preserved(self):
        original_count = count_trackpoints(ZUCCO_RECORDING)
        runner.invoke(app, [
            "--orig", str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--end-coord",   ZUCCO_END_COORD,
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert count_trackpoints(ZUCCO_CLEANED) == original_count

    def test_start_coord_without_end_coord_raises_error(self):
        result = runner.invoke(app, [
            "--orig",        str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert result.exit_code != 0
        assert "--end-coord" in result.output

    def test_start_coord_with_start_time_raises_error(self):
        result = runner.invoke(app, [
            "--orig",        str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--end-coord",   ZUCCO_END_COORD,
            "--start",       "2026-01-01T00:00:00Z",
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_start_coord_with_start_point_raises_error(self):
        result = runner.invoke(app, [
            "--orig",        str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--end-coord",   ZUCCO_END_COORD,
            "--start-point", "100",
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_invalid_coord_format_raises_error(self):
        result = runner.invoke(app, [
            "--orig",        str(ZUCCO_RECORDING),
            "--start-coord", "not-a-coord",
            "--end-coord",   ZUCCO_END_COORD,
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_get_timestamp_by_coord_returns_correct_time(self):
        lat, lon = 45.924565242603421, 9.340607235208154
        ts = _get_timestamp_by_coord(ZUCCO_RECORDING, lat, lon)
        assert ts.year == 2025
        assert ts.month == 12
        assert ts.day == 31

    def test_get_timestamp_by_coord_raises_on_unknown_coord(self):
        with pytest.raises(ValueError):
            _get_timestamp_by_coord(ZUCCO_RECORDING, 0.0, 0.0)


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
