import math
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# The XML namespace used in GPX 1.1 files
GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the distance in meters between two GPS coordinates
    using the Haversine formula.
    """
    earth_radius_meters = 6_371_000

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (math.sin(delta_lat / 2) ** 2
         + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius_meters * c


def _interpolate_positions(
    target_points: list[tuple[float, float, float | None]],
    count: int,
) -> list[tuple[float, float, float | None]]:
    """
    Distribute `count` evenly spaced positions along the path defined by
    `target_points`. Each target point is a tuple of (lat, lon, ele).

    Returns a list of `count` interpolated (lat, lon, ele) tuples.
    If count is 0, returns an empty list.
    If count is 1, returns the midpoint of the path.
    """
    if count == 0:
        return []

    if len(target_points) == 1:
        return [target_points[0]] * count

    # Compute cumulative distances along the target path
    cumulative_distances: list[float] = [0.0]
    for i in range(1, len(target_points)):
        prev_lat, prev_lon, _ = target_points[i - 1]
        curr_lat, curr_lon, _ = target_points[i]
        segment_length = _haversine_distance(prev_lat, prev_lon, curr_lat, curr_lon)
        cumulative_distances.append(cumulative_distances[-1] + segment_length)

    total_distance = cumulative_distances[-1]

    result: list[tuple[float, float, float | None]] = []

    for i in range(count):
        # Target distance along the path for this point
        if count == 1:
            target_distance = total_distance / 2
        else:
            target_distance = i * total_distance / (count - 1)

        # Find the segment that contains this target distance
        segment_index = 0
        for j in range(1, len(cumulative_distances)):
            if cumulative_distances[j] >= target_distance:
                segment_index = j - 1
                break
            segment_index = j - 1  # last segment if target_distance == total_distance

        # Interpolation factor within the segment (0.0 to 1.0)
        segment_start_distance = cumulative_distances[segment_index]
        segment_end_distance = cumulative_distances[segment_index + 1]
        segment_length = segment_end_distance - segment_start_distance

        if segment_length == 0:
            factor = 0.0
        else:
            factor = (target_distance - segment_start_distance) / segment_length

        # Linearly interpolate lat, lon, and ele between the two segment endpoints
        start_lat, start_lon, start_ele = target_points[segment_index]
        end_lat, end_lon, end_ele = target_points[segment_index + 1]

        interpolated_lat = start_lat + factor * (end_lat - start_lat)
        interpolated_lon = start_lon + factor * (end_lon - start_lon)

        if start_ele is not None and end_ele is not None:
            interpolated_ele: float | None = start_ele + factor * (end_ele - start_ele)
        else:
            interpolated_ele = start_ele if start_ele is not None else end_ele

        result.append((interpolated_lat, interpolated_lon, interpolated_ele))

    return result


def _get_timestamp_by_index(recording: Path, index: int) -> datetime:
    """
    Return the timestamp of the track point at the given 1-based index.

    Parameters
    ----------
    recording : Path
        Path to the GPX file.
    index : int
        1-based index of the track point (1 = first point).

    Raises
    ------
    ValueError
        If the index is less than 1 or exceeds the number of track points
        with a timestamp.
    """
    root = ET.parse(recording).getroot()
    timed_points: list[datetime] = []
    for trkpt in root.iter(f"{{{GPX_NAMESPACE}}}trkpt"):
        time_element = trkpt.find(f"{{{GPX_NAMESPACE}}}time")
        if time_element is not None and time_element.text is not None:
            timed_points.append(
                datetime.fromisoformat(time_element.text.replace("Z", "+00:00"))
            )

    if index < 1 or index > len(timed_points):
        raise ValueError(
            f"Index {index} is out of range. "
            f"The recording has {len(timed_points)} track points with timestamps."
        )

    return timed_points[index - 1]


def _get_timestamp_by_coord(recording: Path, lat: float, lon: float) -> datetime:
    """
    Return the timestamp of the track point whose lat/lon attributes match
    (lat, lon) exactly (float comparison).

    Raises
    ------
    ValueError
        If no track point matches the given coordinates, or if more than one
        track point matches (ambiguous — e.g. a looping track).
    """
    root = ET.parse(recording).getroot()
    matches: list[datetime] = []
    for trkpt in root.iter(f"{{{GPX_NAMESPACE}}}trkpt"):
        if float(trkpt.attrib["lat"]) == lat and float(trkpt.attrib["lon"]) == lon:
            time_el = trkpt.find(f"{{{GPX_NAMESPACE}}}time")
            if time_el is not None and time_el.text is not None:
                matches.append(
                    datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
                )
    if len(matches) == 0:
        raise ValueError(
            f"No track point with lat={lat}, lon={lon} found in the recording."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Coordinate lat={lat}, lon={lon} matches {len(matches)} track points "
            f"— cannot determine the point unambiguously."
        )
    return matches[0]


class GPSCleaner:
    """
    Replaces GPS track points in a recording that fall within a given time
    window with evenly distributed positions from a reference (target) track.

    Usage:
        cleaner = GPSCleaner(start_time, end_time, recording, target)
        cleaner.start()
    """

    def __init__(
        self,
        start_time: datetime,
        end_time: datetime,
        recording: Path,
        target: Path,
    ) -> None:
        """
        Parameters
        ----------
        start_time : datetime
            The point in time where the recording begins to deviate from the
            actual route. Must be timezone-aware (e.g. UTC).
        end_time : datetime
            The point in time where the recording returns to the actual route.
            Must be timezone-aware (e.g. UTC).
        recording : Path
            Path to the GPX file containing the recorded track with deviations.
        target : Path
            Path to the GPX file containing the actual route for the given
            time window. Only positions (lat/lon/ele) are used from this file.
        """
        self._start_time = start_time
        self._end_time = end_time
        self._recording = recording
        self._target = target

    def start(self) -> None:
        """
        Run the cleaning process:
        1. Parse the recording GPX.
        2. Find all track points within [start_time, end_time].
        3. Distribute evenly spaced positions from the target along those points.
        4. Write the corrected track to a new file next to the original.
        """
        # Register the GPX namespace so ElementTree preserves the prefix
        ET.register_namespace("", GPX_NAMESPACE)

        recording_tree = ET.parse(self._recording)
        recording_root = recording_tree.getroot()

        # Collect all track points whose <time> element falls in the window
        affected_elements: list[ET.Element] = []
        for trkpt in recording_root.iter(f"{{{GPX_NAMESPACE}}}trkpt"):
            time_element = trkpt.find(f"{{{GPX_NAMESPACE}}}time")
            if time_element is None or time_element.text is None:
                continue
            point_time = datetime.fromisoformat(
                time_element.text.replace("Z", "+00:00")
            )
            if self._start_time <= point_time <= self._end_time:
                affected_elements.append(trkpt)

        if not affected_elements:
            print("No track points found in the given time window. Nothing to do.")
            return

        # Parse the target GPX and extract all (lat, lon, ele) positions
        target_tree = ET.parse(self._target)
        target_root = target_tree.getroot()

        target_points: list[tuple[float, float, float | None]] = []
        for trkpt in target_root.iter(f"{{{GPX_NAMESPACE}}}trkpt"):
            lat = float(trkpt.attrib["lat"])
            lon = float(trkpt.attrib["lon"])
            ele_element = trkpt.find(f"{{{GPX_NAMESPACE}}}ele")
            ele = float(ele_element.text) if ele_element is not None and ele_element.text is not None else None
            target_points.append((lat, lon, ele))

        # Calculate evenly distributed replacement positions along the target path
        replacement_positions = _interpolate_positions(target_points, len(affected_elements))

        # Replace lat/lon/ele of each affected track point in-place
        for trkpt_element, (new_lat, new_lon, new_ele) in zip(affected_elements, replacement_positions):
            trkpt_element.attrib["lat"] = f"{new_lat:.15f}"
            trkpt_element.attrib["lon"] = f"{new_lon:.15f}"

            if new_ele is not None:
                ele_element = trkpt_element.find(f"{{{GPX_NAMESPACE}}}ele")
                if ele_element is not None:
                    ele_element.text = f"{new_ele:.6f}"

        # Write the corrected recording to a new file next to the original
        output_path = self._recording.parent / (
            self._recording.stem + "_cleaned" + self._recording.suffix
        )
        recording_tree.write(output_path, xml_declaration=True, encoding="utf-8")

        print(f"Done. {len(affected_elements)} track points replaced.")
        print(f"Output written to: {output_path}")


class GPSDistanceReducer:
    """
    Reduces the number of track points in a GPS recording so that consecutive
    kept points are at least min_distance metres apart.

    A point is removed only if the next point (after removal) would still be
    within min_distance of the current anchor, so no gap exceeds the threshold.
    Points are never added. Sections already sparser than min_distance are
    left unchanged.

    Usage:
        reducer = GPSDistanceReducer(recording, min_distance)
        reducer.start()
    """

    def __init__(self, recording: Path, min_distance: float) -> None:
        """
        Parameters
        ----------
        recording : Path
            Path to the GPX file whose point density should be reduced.
        min_distance : float
            Minimum distance in metres between consecutive kept points.
        """
        self._recording = recording
        self._min_distance = min_distance

    def start(self) -> None:
        """
        Run the distance-based reduction:
        1. Parse the recording GPX.
        2. Apply the min-distance algorithm to determine which points to keep.
        3. If no points are removed, print a message and return without writing.
        4. Otherwise remove the unneeded points and write a new file.
        """
        ET.register_namespace("", GPX_NAMESPACE)

        recording_tree = ET.parse(self._recording)
        recording_root = recording_tree.getroot()

        all_trkpts: list[ET.Element] = list(
            recording_root.iter(f"{{{GPX_NAMESPACE}}}trkpt")
        )

        if len(all_trkpts) < 2:
            print("Not enough track points to reduce.")
            return

        kept = self._reduce(all_trkpts)

        if len(kept) == len(all_trkpts):
            print(
                f"Nothing to do: no consecutive points are closer than "
                f"{self._min_distance} m."
            )
            return

        kept_ids = {id(pt) for pt in kept}
        for trkseg in recording_root.iter(f"{{{GPX_NAMESPACE}}}trkseg"):
            for trkpt in list(trkseg):
                if trkpt.tag == f"{{{GPX_NAMESPACE}}}trkpt" and id(trkpt) not in kept_ids:
                    trkseg.remove(trkpt)

        output_path = self._recording.parent / (
            self._recording.stem + f"_distance={self._min_distance}" + self._recording.suffix
        )
        recording_tree.write(output_path, xml_declaration=True, encoding="utf-8")

        print(f"Done. {len(all_trkpts)} track points reduced to {len(kept)}.")
        print(f"Output written to: {output_path}")

    def _reduce(self, all_trkpts: list[ET.Element]) -> list[ET.Element]:
        """
        Return the list of track points to keep, applying the min-distance rule.
        The first and last points are always kept.
        """
        kept = [all_trkpts[0]]
        i = 1
        while i < len(all_trkpts):
            anchor = kept[-1]
            anchor_lat = float(anchor.attrib["lat"])
            anchor_lon = float(anchor.attrib["lon"])

            while i < len(all_trkpts):
                curr = all_trkpts[i]
                curr_lat = float(curr.attrib["lat"])
                curr_lon = float(curr.attrib["lon"])
                dist_to_curr = _haversine_distance(
                    anchor_lat, anchor_lon, curr_lat, curr_lon
                )

                if dist_to_curr >= self._min_distance:
                    # Far enough — keep and make new anchor
                    kept.append(curr)
                    i += 1
                    break

                # Too close — remove curr only if the point after it is also
                # within min_distance of the anchor (look-ahead guard)
                if i + 1 < len(all_trkpts):
                    nxt = all_trkpts[i + 1]
                    dist_to_next = _haversine_distance(
                        anchor_lat, anchor_lon,
                        float(nxt.attrib["lat"]), float(nxt.attrib["lon"]),
                    )
                    if dist_to_next <= self._min_distance:
                        i += 1  # skip curr
                    else:
                        # Removing curr would leave a gap > min_distance — keep it
                        kept.append(curr)
                        i += 1
                        break
                else:
                    # curr is the last point — always keep it
                    kept.append(curr)
                    i += 1
                    break

        return kept


class GPSSampleRateReducer:
    """
    Reduces the number of track points in a GPS recording to a target sample rate
    (positions per second). Points are removed by keeping only those that are
    at least 1/target_sample_rate seconds apart from the previously kept point.

    Usage:
        reducer = GPSSampleRateReducer(recording, sample_rate)
        reducer.start()
    """

    def __init__(self, recording: Path, target_sample_rate: float) -> None:
        """
        Parameters
        ----------
        recording : Path
            Path to the GPX file whose sample rate should be reduced.
        target_sample_rate : float
            Target number of positions per second. Must be less than the current
            sample rate of the recording; otherwise a hint is printed and no output
            file is created.
        """
        self._recording = recording
        self._target_sample_rate = target_sample_rate

    def start(self) -> None:
        """
        Run the density reduction:
        1. Parse the recording GPX.
        2. Calculate the current sample rate.
        3. If target sample rate >= current sample rate, print a hint and return.
        4. Keep only track points that are at least 1/target_sample_rate seconds apart.
        5. Write the reduced track to a new file next to the original.
        """
        ET.register_namespace("", GPX_NAMESPACE)

        recording_tree = ET.parse(self._recording)
        recording_root = recording_tree.getroot()

        # Collect all track points that have a <time> element
        all_trkpts: list[tuple[ET.Element, datetime]] = []
        for trkpt in recording_root.iter(f"{{{GPX_NAMESPACE}}}trkpt"):
            time_element = trkpt.find(f"{{{GPX_NAMESPACE}}}time")
            if time_element is None or time_element.text is None:
                continue
            point_time = datetime.fromisoformat(
                time_element.text.replace("Z", "+00:00")
            )
            all_trkpts.append((trkpt, point_time))

        if len(all_trkpts) < 2:
            print("Not enough track points with timestamps to calculate sample rate.")
            return

        total_duration = (all_trkpts[-1][1] - all_trkpts[0][1]).total_seconds()
        if total_duration <= 0:
            print("Cannot calculate sample rate: all track points have the same timestamp.")
            return

        current_sample_rate = (len(all_trkpts) - 1) / total_duration

        if self._target_sample_rate >= current_sample_rate:
            print(
                f"Cannot reduce: recording sample rate is {current_sample_rate:.4f} positions/second."
            )
            return

        # Keep the first point in each time bucket of width 1/target_sample_rate.
        # Buckets are measured from the recording's first timestamp, so drift is
        # bounded by at most one sample period regardless of recording length.
        min_interval = 1.0 / self._target_sample_rate
        start_time = all_trkpts[0][1]
        kept_trkpts: list[ET.Element] = []
        last_bucket = -1

        for trkpt, point_time in all_trkpts:
            bucket = int((point_time - start_time).total_seconds() / min_interval)
            if bucket > last_bucket:
                kept_trkpts.append(trkpt)
                last_bucket = bucket

        # Remove all track points not in the kept set from their parent <trkseg>
        kept_ids = {id(trkpt) for trkpt in kept_trkpts}
        for trkseg in recording_root.iter(f"{{{GPX_NAMESPACE}}}trkseg"):
            for trkpt in list(trkseg):
                if trkpt.tag == f"{{{GPX_NAMESPACE}}}trkpt" and id(trkpt) not in kept_ids:
                    trkseg.remove(trkpt)

        output_path = self._recording.parent / (
            self._recording.stem + f"_sample-rate={self._target_sample_rate}" + self._recording.suffix
        )
        recording_tree.write(output_path, xml_declaration=True, encoding="utf-8")

        print(f"Done. {len(all_trkpts)} track points reduced to {len(kept_trkpts)}.")
        print(f"Output written to: {output_path}")
