import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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
