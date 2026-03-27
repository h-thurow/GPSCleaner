import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.gpscleaner.cli import app
from src.gpscleaner.gpscleaner import GPSCleaner, GPSDistanceReducer, GPSRetimer, GPSSampleRateReducer, _get_timestamp_by_coord, _get_timestamp_by_index, _haversine_distance, _interpolate_positions, compare_tracks

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

ZUCCO_NO_TIMESTAMPS = FIXTURES_DIR / "Zucco_di_Manavello-sections_without_timestamps.GPX"
ZUCCO_RETIMED       = FIXTURES_DIR / "Zucco_di_Manavello-sections_without_timestamps_retimed.GPX"

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
            "clean", "--recording", str(CAM_RECORDING),
            "--sample-rate", "1",
            "--start", "2026-01-03T11:48:46Z",
        ])
        assert result.exit_code != 0
        assert "--sample-rate" in result.output

    def test_sample_rate_with_end_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
            "--sample-rate", "1",
            "--end", "2026-01-03T12:00:00Z",
        ])
        assert result.exit_code != 0
        assert "--sample-rate" in result.output

    def test_sample_rate_with_reference_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
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
            "clean", "--recording", str(CAM_RECORDING),
            "--start-point", "100",
            "--end-point", "200",
            "--reference", str(CAM_REFERENCE),
        ])
        assert CAM_CLEANED.exists()

    def test_trackpoint_count_is_preserved(self):
        original_count = count_trackpoints(CAM_RECORDING)
        runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
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
            "clean", "--recording", str(CAM_RECORDING),
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
            "clean", "--recording", str(CAM_RECORDING),
            "--start-point", "100",
            "--reference", str(CAM_REFERENCE),
        ])
        assert result.exit_code != 0
        assert "--end-point" in result.output

    def test_start_point_with_start_time_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
            "--start-point", "100",
            "--end-point", "200",
            "--start", "2026-01-03T11:48:46Z",
            "--reference", str(CAM_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_invalid_index_zero_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
            "--start-point", "0",
            "--end-point", "200",
            "--reference", str(CAM_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_invalid_index_too_large_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
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


class TestGPSDistanceReducer:

    @pytest.fixture(autouse=True)
    def cleanup_distance_output(self):
        """Remove distance-reduced output files before each test."""
        for f in FIXTURES_DIR.glob("*_distance=*.gpx"):
            f.unlink()
        for f in FIXTURES_DIR.glob("*_distance=*.GPX"):
            f.unlink()
        yield

    def cam_distance_output(self, distance: float) -> Path:
        return FIXTURES_DIR / f"1_CAM_20260103124845_0011_D_distance={distance}.gpx"

    def recording_distance_output(self, distance: float) -> Path:
        return FIXTURES_DIR / f"260322-recording_distance={distance}.GPX"

    def test_output_file_is_created(self):
        # CAM has avg ~0.05 m between points; threshold 1 m causes large reduction
        GPSDistanceReducer(CAM_RECORDING, 1.0).start()
        assert self.cam_distance_output(1.0).exists()

    def test_original_file_is_not_modified(self):
        original_content = CAM_RECORDING.read_bytes()
        GPSDistanceReducer(CAM_RECORDING, 1.0).start()
        assert CAM_RECORDING.read_bytes() == original_content

    def test_trackpoint_count_is_reduced_cam(self):
        original_count = count_trackpoints(CAM_RECORDING)
        GPSDistanceReducer(CAM_RECORDING, 1.0).start()
        assert count_trackpoints(self.cam_distance_output(1.0)) < original_count / 5

    def test_trackpoint_count_is_reduced_recording(self):
        # 260322 has avg ~1.27 m between points; threshold 3 m reduces count
        original_count = count_trackpoints(RECORDING)
        GPSDistanceReducer(RECORDING, 3.0).start()
        assert count_trackpoints(self.recording_distance_output(3.0)) < original_count

    def test_nothing_to_do_when_gaps_already_large_enough(self, capsys, tmp_path):
        # Three points ~10 m apart — all gaps well above the 3 m threshold
        gpx = (
            "<?xml version='1.0' encoding='utf-8'?>"
            '<gpx xmlns="http://www.topografix.com/GPX/1/1">'
            "<trk><trkseg>"
            '<trkpt lat="47.000000" lon="9.000000"/>'
            '<trkpt lat="47.000090" lon="9.000000"/>'
            '<trkpt lat="47.000180" lon="9.000000"/>'
            "</trkseg></trk></gpx>"
        )
        gpx_file = tmp_path / "sparse.gpx"
        gpx_file.write_text(gpx)
        GPSDistanceReducer(gpx_file, 3.0).start()
        captured = capsys.readouterr()
        assert "Nothing to do" in captured.out
        assert not (tmp_path / "sparse_distance=3.0.gpx").exists()

    def test_distance_with_start_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
            "--distance", "1",
            "--start", "2026-01-03T11:48:46Z",
        ])
        assert result.exit_code != 0
        assert "--distance" in result.output

    def test_distance_with_sample_rate_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
            "--distance", "1",
            "--sample-rate", "1",
        ])
        assert result.exit_code != 0
        assert "--distance" in result.output

    def test_distance_with_reference_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
            "--distance", "1",
            "--reference", str(TARGET),
        ])
        assert result.exit_code != 0
        assert "--distance" in result.output

    def test_distance_with_start_coord_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording", str(CAM_RECORDING),
            "--distance", "1",
            "--start-coord", "45.0,9.0",
            "--end-coord", "45.1,9.1",
        ])
        assert result.exit_code != 0
        assert "--distance" in result.output


class TestGPSCleanerByCoord:

    @pytest.fixture(autouse=True)
    def cleanup_zucco_cleaned(self):
        """Remove the Zucco cleaned output file before each test."""
        if ZUCCO_CLEANED.exists():
            ZUCCO_CLEANED.unlink()
        yield

    def test_output_file_is_created(self):
        runner.invoke(app, [
            "clean", "--recording", str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--end-coord",   ZUCCO_END_COORD,
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert ZUCCO_CLEANED.exists()

    def test_trackpoint_count_is_preserved(self):
        original_count = count_trackpoints(ZUCCO_RECORDING)
        runner.invoke(app, [
            "clean", "--recording", str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--end-coord",   ZUCCO_END_COORD,
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert count_trackpoints(ZUCCO_CLEANED) == original_count

    def test_start_coord_without_end_coord_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording",        str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert result.exit_code != 0
        assert "--end-coord" in result.output

    def test_start_coord_with_start_time_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording",        str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--end-coord",   ZUCCO_END_COORD,
            "--start",       "2026-01-01T00:00:00Z",
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_start_coord_with_start_point_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording",        str(ZUCCO_RECORDING),
            "--start-coord", ZUCCO_START_COORD,
            "--end-coord",   ZUCCO_END_COORD,
            "--start-point", "100",
            "--reference",   str(ZUCCO_REFERENCE),
        ])
        assert result.exit_code != 0

    def test_invalid_coord_format_raises_error(self):
        result = runner.invoke(app, [
            "clean", "--recording",        str(ZUCCO_RECORDING),
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


class TestCompare:

    def test_output_contains_header(self, capsys):
        compare_tracks(RECORDING, RECORDING, max_time_diff=1.0, interval=60.0)
        out = capsys.readouterr().out
        assert "Timestamp (Reference)" in out
        assert "Distance (m)" in out
        assert "Timestamp (Original)" in out

    def test_self_compare_has_zero_distance(self, capsys):
        compare_tracks(RECORDING, RECORDING, max_time_diff=1.0, interval=60.0)
        lines = capsys.readouterr().out.splitlines()
        data_lines = [l for l in lines[1:] if l.strip()]
        assert len(data_lines) > 0
        assert all("0.00" in line for line in data_lines)

    def test_interval_reduces_row_count(self, capsys):
        compare_tracks(RECORDING, RECORDING, max_time_diff=1.0)
        all_count = len([l for l in capsys.readouterr().out.splitlines() if l.strip()])

        compare_tracks(RECORDING, RECORDING, max_time_diff=1.0, interval=60.0)
        interval_count = len([l for l in capsys.readouterr().out.splitlines() if l.strip()])

        assert interval_count < all_count

    def test_no_match_leaves_distance_empty(self, capsys):
        # ZUCCO (Dec 2025) and RECORDING (Mar 2026) have no overlapping timestamps;
        # use a large interval to limit output to a few rows
        compare_tracks(RECORDING, ZUCCO_RECORDING, max_time_diff=1.0, interval=3600.0)
        lines = capsys.readouterr().out.splitlines()
        data_lines = [l for l in lines[1:] if l.strip()]
        assert len(data_lines) > 0
        # Rows with no match contain only the timestamp — nothing after column 1
        for line in data_lines:
            assert line.rstrip() == line[:32].rstrip()

    def test_cli_compare_produces_output(self):
        result = runner.invoke(app, [
            "compare",
            "--recording", str(RECORDING),
            "--reference", str(RECORDING),
            "--max-time-diff", "1",
            "--interval", "60",
        ])
        assert result.exit_code == 0
        assert "Timestamp (Reference)" in result.output

    def test_cli_missing_max_time_diff_raises_error(self):
        result = runner.invoke(app, [
            "compare",
            "--recording", str(RECORDING),
            "--reference", str(TARGET),
        ])
        assert result.exit_code != 0


class TestGPSRetimer:

    @pytest.fixture(autouse=True)
    def cleanup_retimed(self):
        """Remove the retimed output file before each test."""
        if ZUCCO_RETIMED.exists():
            ZUCCO_RETIMED.unlink()
        yield
        if ZUCCO_RETIMED.exists():
            ZUCCO_RETIMED.unlink()

    def test_output_file_is_created(self):
        GPSRetimer(ZUCCO_NO_TIMESTAMPS).start()
        assert ZUCCO_RETIMED.exists()

    def test_original_file_is_not_modified(self):
        original_bytes = ZUCCO_NO_TIMESTAMPS.read_bytes()
        GPSRetimer(ZUCCO_NO_TIMESTAMPS).start()
        assert ZUCCO_NO_TIMESTAMPS.read_bytes() == original_bytes

    def test_all_points_have_timestamps_after_retime(self):
        GPSRetimer(ZUCCO_NO_TIMESTAMPS).start()
        root = ET.parse(ZUCCO_RETIMED).getroot()
        for trkpt in root.iter(f"{{{GPX_NS}}}trkpt"):
            time_el = trkpt.find(f"{{{GPX_NS}}}time")
            assert time_el is not None and time_el.text is not None

    def test_timestamps_outside_gap_are_unchanged(self):
        GPSRetimer(ZUCCO_NO_TIMESTAMPS).start()
        root_orig   = ET.parse(ZUCCO_NO_TIMESTAMPS).getroot()
        root_retimed = ET.parse(ZUCCO_RETIMED).getroot()

        orig_trkpts   = list(root_orig.iter(f"{{{GPX_NS}}}trkpt"))
        retimed_trkpts = list(root_retimed.iter(f"{{{GPX_NS}}}trkpt"))

        assert len(orig_trkpts) == len(retimed_trkpts)

        for orig, retimed in zip(orig_trkpts, retimed_trkpts):
            orig_time_el = orig.find(f"{{{GPX_NS}}}time")
            if orig_time_el is None or orig_time_el.text is None:
                continue  # gap point — skip
            retimed_time_el = retimed.find(f"{{{GPX_NS}}}time")
            assert retimed_time_el is not None
            assert orig_time_el.text == retimed_time_el.text

    def test_gap_timestamps_are_monotonically_increasing(self):
        GPSRetimer(ZUCCO_NO_TIMESTAMPS).start()
        root = ET.parse(ZUCCO_RETIMED).getroot()
        trkpts = list(root.iter(f"{{{GPX_NS}}}trkpt"))

        # The gap in Zucco_no_timestamps is at indices 5602-5613 (12 points)
        gap_times = []
        for trkpt in trkpts[5602:5614]:
            time_el = trkpt.find(f"{{{GPX_NS}}}time")
            assert time_el is not None
            gap_times.append(datetime.fromisoformat(time_el.text.replace("Z", "+00:00")))

        for j in range(1, len(gap_times)):
            assert gap_times[j] > gap_times[j - 1]

    def test_gap_timestamps_are_proportional_to_distance(self):
        GPSRetimer(ZUCCO_NO_TIMESTAMPS).start()
        root = ET.parse(ZUCCO_RETIMED).getroot()
        trkpts = list(root.iter(f"{{{GPX_NS}}}trkpt"))

        # Anchors: index 5601 (before gap) and 5614 (after gap)
        # Gap points: indices 5602-5613
        def ts(trkpt):
            el = trkpt.find(f"{{{GPX_NS}}}time")
            return datetime.fromisoformat(el.text.replace("Z", "+00:00"))

        anchor_start = ts(trkpts[5601])
        anchor_end   = ts(trkpts[5614])
        time_span = (anchor_end - anchor_start).total_seconds()

        # Build segment [5601, 5602, ..., 5614] and compute cumulative distances
        segment = trkpts[5601:5615]
        cumulative = [0.0]
        for j in range(1, len(segment)):
            prev, curr = segment[j - 1], segment[j]
            cumulative.append(cumulative[-1] + _haversine_distance(
                float(prev.attrib["lat"]), float(prev.attrib["lon"]),
                float(curr.attrib["lat"]), float(curr.attrib["lon"]),
            ))
        total_dist = cumulative[-1]

        tolerance_seconds = 1e-6  # 1 microsecond

        for i_gap, trkpt in enumerate(trkpts[5602:5614]):
            seg_idx = i_gap + 1
            expected_fraction = cumulative[seg_idx] / total_dist
            actual_seconds = (ts(trkpt) - anchor_start).total_seconds()
            actual_fraction = actual_seconds / time_span
            assert abs(actual_fraction - expected_fraction) * time_span <= tolerance_seconds

    def test_overwrite_modifies_original(self, tmp_path):
        copy = tmp_path / ZUCCO_NO_TIMESTAMPS.name
        copy.write_bytes(ZUCCO_NO_TIMESTAMPS.read_bytes())
        original_bytes = copy.read_bytes()

        GPSRetimer(copy, overwrite=True).start()

        assert copy.read_bytes() != original_bytes
        retimed_copy = tmp_path / (copy.stem + "_retimed" + copy.suffix)
        assert not retimed_copy.exists()

    def test_first_point_without_timestamp_raises_error(self, capsys, tmp_path):
        gpx = (
            "<?xml version='1.0' encoding='utf-8'?>"
            '<gpx xmlns="http://www.topografix.com/GPX/1/1">'
            "<trk><trkseg>"
            '<trkpt lat="47.0" lon="9.0"><ele>400.0</ele></trkpt>'
            '<trkpt lat="47.1" lon="9.0"><ele>400.0</ele><time>2026-01-01T10:00:00Z</time></trkpt>'
            "</trkseg></trk></gpx>"
        )
        gpx_file = tmp_path / "no_first_time.gpx"
        gpx_file.write_text(gpx)
        GPSRetimer(gpx_file).start()
        captured = capsys.readouterr()
        assert "first" in captured.out.lower()
        assert not (tmp_path / "no_first_time_retimed.gpx").exists()

    def test_last_point_without_timestamp_raises_error(self, capsys, tmp_path):
        gpx = (
            "<?xml version='1.0' encoding='utf-8'?>"
            '<gpx xmlns="http://www.topografix.com/GPX/1/1">'
            "<trk><trkseg>"
            '<trkpt lat="47.0" lon="9.0"><ele>400.0</ele><time>2026-01-01T10:00:00Z</time></trkpt>'
            '<trkpt lat="47.1" lon="9.0"><ele>400.0</ele></trkpt>'
            "</trkseg></trk></gpx>"
        )
        gpx_file = tmp_path / "no_last_time.gpx"
        gpx_file.write_text(gpx)
        GPSRetimer(gpx_file).start()
        captured = capsys.readouterr()
        assert "last" in captured.out.lower()
        assert not (tmp_path / "no_last_time_retimed.gpx").exists()

    def test_no_gaps_prints_message(self, capsys):
        # ZUCCO_RECORDING has no missing timestamps
        GPSRetimer(ZUCCO_RECORDING).start()
        captured = capsys.readouterr()
        assert "No track points without timestamps found" in captured.out

    def test_cli_retime_succeeds(self):
        result = runner.invoke(app, [
            "retime", "--recording", str(ZUCCO_NO_TIMESTAMPS),
        ])
        assert result.exit_code == 0

    def test_cli_plot_raises_error(self):
        result = runner.invoke(app, [
            "retime", "--recording", str(ZUCCO_NO_TIMESTAMPS), "--plot",
        ])
        assert result.exit_code == 1
        assert "--plot" in result.output


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
