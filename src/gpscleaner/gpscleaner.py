import math
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
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


class GPSSampleRateUpsampler:
    """
    Increases the number of track points in a GPS recording to a target sample rate
    (positions per second) by inserting linearly interpolated points between existing ones.

    For each consecutive pair of timestamped track points whose time gap exceeds
    1/target_sample_rate seconds, additional points are inserted so that consecutive
    kept points are at most 1/target_sample_rate seconds apart.

    Usage:
        upsampler = GPSSampleRateUpsampler(recording, sample_rate)
        upsampler.start()
    """

    def __init__(self, recording: Path, target_sample_rate: float) -> None:
        """
        Parameters
        ----------
        recording : Path
            Path to the GPX file whose sample rate should be increased.
        target_sample_rate : float
            Target number of positions per second. Must be greater than the current
            sample rate of the recording.
        """
        self._recording = recording
        self._target_sample_rate = target_sample_rate

    def start(self) -> None:
        """
        Run the upsampling:
        1. Parse the recording GPX.
        2. For each consecutive pair of timestamped points, compute the time gap.
        3. Insert interpolated points so consecutive points are at most
           1/target_sample_rate seconds apart.
        4. Write the result to a new file next to the original.
        """
        ET.register_namespace("", GPX_NAMESPACE)

        recording_tree = ET.parse(self._recording)
        recording_root = recording_tree.getroot()

        timed_count = sum(
            1 for trkpt in recording_root.iter(f"{{{GPX_NAMESPACE}}}trkpt")
            if trkpt.find(f"{{{GPX_NAMESPACE}}}time") is not None
        )
        if timed_count < 2:
            print("Not enough track points with timestamps to upsample.")
            return

        interval = 1.0 / self._target_sample_rate
        total_inserted = 0

        for trkseg in recording_root.iter(f"{{{GPX_NAMESPACE}}}trkseg"):
            old_trkpts = [c for c in trkseg if c.tag == f"{{{GPX_NAMESPACE}}}trkpt"]
            new_trkpts: list[ET.Element] = []

            for j, trkpt in enumerate(old_trkpts):
                new_trkpts.append(trkpt)
                if j + 1 >= len(old_trkpts):
                    continue

                a = trkpt
                b = old_trkpts[j + 1]
                time_a_el = a.find(f"{{{GPX_NAMESPACE}}}time")
                time_b_el = b.find(f"{{{GPX_NAMESPACE}}}time")
                if time_a_el is None or time_b_el is None:
                    continue

                t_a = datetime.fromisoformat(time_a_el.text.replace("Z", "+00:00"))
                t_b = datetime.fromisoformat(time_b_el.text.replace("Z", "+00:00"))
                time_gap = (t_b - t_a).total_seconds()
                n_new = max(0, math.ceil(time_gap / interval) - 1)
                if n_new == 0:
                    continue

                a_lat = float(a.attrib["lat"])
                a_lon = float(a.attrib["lon"])
                b_lat = float(b.attrib["lat"])
                b_lon = float(b.attrib["lon"])
                a_ele_el = a.find(f"{{{GPX_NAMESPACE}}}ele")
                b_ele_el = b.find(f"{{{GPX_NAMESPACE}}}ele")
                has_ele = a_ele_el is not None and b_ele_el is not None

                for k in range(1, n_new + 1):
                    fraction = k * interval / time_gap

                    new_trkpt = ET.Element(f"{{{GPX_NAMESPACE}}}trkpt")
                    new_trkpt.attrib["lat"] = f"{a_lat + fraction * (b_lat - a_lat):.15f}"
                    new_trkpt.attrib["lon"] = f"{a_lon + fraction * (b_lon - a_lon):.15f}"

                    if has_ele:
                        a_ele = float(a_ele_el.text)
                        b_ele = float(b_ele_el.text)
                        ele_el = ET.SubElement(new_trkpt, f"{{{GPX_NAMESPACE}}}ele")
                        ele_el.text = f"{a_ele + fraction * (b_ele - a_ele):.6f}"

                    new_time = t_a + k * timedelta(seconds=interval)
                    time_el = ET.SubElement(new_trkpt, f"{{{GPX_NAMESPACE}}}time")
                    time_el.text = new_time.isoformat().replace("+00:00", "Z")

                    new_trkpts.append(new_trkpt)
                    total_inserted += 1

            for trkpt in old_trkpts:
                trkseg.remove(trkpt)
            for trkpt in new_trkpts:
                trkseg.append(trkpt)

        output_path = self._recording.parent / (
            self._recording.stem + f"_sample-rate={self._target_sample_rate}" + self._recording.suffix
        )
        recording_tree.write(output_path, xml_declaration=True, encoding="utf-8")

        print(f"Done. {timed_count} track points upsampled to {timed_count + total_inserted}.")
        print(f"Output written to: {output_path}")


class GPSSampleRateResampler:
    """
    Resamples a GPS recording to a target sample rate (positions per second) by
    combining reduction and interpolation in a single pass per track segment.

    For each track point that falls within the same time bucket as the previously
    kept point, the point is dropped (reduction). After selecting the kept points,
    any gap between consecutive kept points that is larger than 1/target_sample_rate
    seconds is filled with linearly interpolated points (upsampling).

    This correctly handles recordings from navigation devices that use adaptive
    sampling (dense near turns, sparse on straight sections): dense sections are
    thinned and sparse sections are filled in the same pass — without relying on
    an average sample rate to pick a single direction.

    Usage:
        resampler = GPSSampleRateResampler(recording, sample_rate)
        resampler.start()
    """

    def __init__(self, recording: Path, target_sample_rate: float) -> None:
        """
        Parameters
        ----------
        recording : Path
            Path to the GPX file to resample.
        target_sample_rate : float
            Target number of positions per second.
        """
        self._recording = recording
        self._target_sample_rate = target_sample_rate

    def start(self) -> None:
        """
        Run the combined resample pass:
        1. Parse the recording GPX.
        2. Find the first timestamped point as the bucket origin.
        3. For each segment, drop points in already-covered buckets and fill
           gaps larger than 1/target_sample_rate with interpolated points.
        4. Write the result to a new file next to the original.
        """
        ET.register_namespace("", GPX_NAMESPACE)

        recording_tree = ET.parse(self._recording)
        recording_root = recording_tree.getroot()

        origin: datetime | None = None
        for trkpt in recording_root.iter(f"{{{GPX_NAMESPACE}}}trkpt"):
            time_el = trkpt.find(f"{{{GPX_NAMESPACE}}}time")
            if time_el is not None and time_el.text is not None:
                origin = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
                break

        if origin is None:
            print("No track points with timestamps found.")
            return

        interval = 1.0 / self._target_sample_rate
        total_timed = 0
        total_removed = 0
        total_inserted = 0

        for trkseg in recording_root.iter(f"{{{GPX_NAMESPACE}}}trkseg"):
            old_trkpts = [c for c in trkseg if c.tag == f"{{{GPX_NAMESPACE}}}trkpt"]
            new_trkpts: list[ET.Element] = []
            last_kept: tuple[ET.Element, datetime] | None = None
            last_bucket = -1

            for trkpt in old_trkpts:
                time_el = trkpt.find(f"{{{GPX_NAMESPACE}}}time")
                if time_el is None or time_el.text is None:
                    new_trkpts.append(trkpt)
                    continue

                total_timed += 1
                point_time = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
                bucket = int((point_time - origin).total_seconds() / interval)

                if bucket <= last_bucket:
                    total_removed += 1
                    continue

                # Gap-filling: insert interpolated points if the gap to the previous
                # kept point is larger than the target interval
                if last_kept is not None:
                    gap = (point_time - last_kept[1]).total_seconds()
                    n_insert = max(0, math.ceil(gap / interval) - 1)
                    if n_insert > 0:
                        a = last_kept[0]
                        b = trkpt
                        a_lat = float(a.attrib["lat"])
                        a_lon = float(a.attrib["lon"])
                        b_lat = float(b.attrib["lat"])
                        b_lon = float(b.attrib["lon"])
                        a_ele_el = a.find(f"{{{GPX_NAMESPACE}}}ele")
                        b_ele_el = b.find(f"{{{GPX_NAMESPACE}}}ele")
                        has_ele = a_ele_el is not None and b_ele_el is not None

                        for k in range(1, n_insert + 1):
                            fraction = k * interval / gap
                            new_pt = ET.Element(f"{{{GPX_NAMESPACE}}}trkpt")
                            new_pt.attrib["lat"] = f"{a_lat + fraction * (b_lat - a_lat):.15f}"
                            new_pt.attrib["lon"] = f"{a_lon + fraction * (b_lon - a_lon):.15f}"

                            if has_ele:
                                a_ele = float(a_ele_el.text)
                                b_ele = float(b_ele_el.text)
                                ele_el = ET.SubElement(new_pt, f"{{{GPX_NAMESPACE}}}ele")
                                ele_el.text = f"{a_ele + fraction * (b_ele - a_ele):.6f}"

                            new_time = last_kept[1] + k * timedelta(seconds=interval)
                            new_time_el = ET.SubElement(new_pt, f"{{{GPX_NAMESPACE}}}time")
                            new_time_el.text = new_time.isoformat().replace("+00:00", "Z")

                            new_trkpts.append(new_pt)
                            total_inserted += 1

                new_trkpts.append(trkpt)
                last_kept = (trkpt, point_time)
                last_bucket = bucket

            for trkpt in old_trkpts:
                trkseg.remove(trkpt)
            for trkpt in new_trkpts:
                trkseg.append(trkpt)

        output_path = self._recording.parent / (
            self._recording.stem + f"_sample-rate={self._target_sample_rate}" + self._recording.suffix
        )
        recording_tree.write(output_path, xml_declaration=True, encoding="utf-8")

        total_out = total_timed - total_removed + total_inserted
        print(
            f"Done. {total_timed} track points resampled to {total_out} "
            f"({total_removed} removed, {total_inserted} inserted)."
        )
        print(f"Output written to: {output_path}")


class GPSRetimer:
    """
    Assigns timestamps to track points that have none, based on their distance
    from the surrounding timestamped anchor points (constant-speed assumption).

    For each gap of points without <time>, the class computes cumulative Haversine
    distances from the last timestamped point before the gap to the first after it.
    Each point's timestamp is interpolated proportionally to its distance within
    that segment. If all points in a segment are at the same location (total
    distance is zero), timestamps are distributed evenly in time instead.

    When sample_rate is given, additional track points are inserted within each
    gap section (between the anchor points) by linear interpolation so that the
    time interval between consecutive kept points does not exceed 1/sample_rate.
    Existing gap points are always kept. Points outside gap sections are never
    modified or supplemented.

    The first and last track points of the recording must have timestamps;
    otherwise processing is aborted with an error message.

    Usage:
        retimer = GPSRetimer(recording, overwrite, sample_rate)
        retimer.start()
    """

    def __init__(
        self,
        recording: Path,
        overwrite: bool = False,
        sample_rate: float | None = None,
    ) -> None:
        """
        Parameters
        ----------
        recording : Path
            Path to the GPX file containing track points without timestamps.
        overwrite : bool
            If True, the original recording is overwritten in place.
            If False (default), the result is written to a new file with the
            suffix "_retimed" added before the file extension.
        sample_rate : float | None
            If given, additional interpolated points are inserted in gap sections
            so that consecutive points are at most 1/sample_rate seconds apart.
        """
        self._recording = recording
        self._overwrite = overwrite
        self._sample_rate = sample_rate

    def start(self) -> None:
        """
        Run the retiming process:
        1. Parse the recording GPX.
        2. Verify the first and last track points have timestamps.
        3. Find all gaps (consecutive runs of track points without <time>).
        4. For each gap, interpolate timestamps from the surrounding anchors.
        5. If sample_rate is set, insert additional interpolated points in gap zones.
        6. Write the result to a new file (or overwrite the original).
        """
        ET.register_namespace("", GPX_NAMESPACE)

        tree = ET.parse(self._recording)
        root = tree.getroot()

        all_trkpts: list[ET.Element] = list(root.iter(f"{{{GPX_NAMESPACE}}}trkpt"))

        if not all_trkpts:
            print("No track points found.")
            return

        def has_time(trkpt: ET.Element) -> bool:
            el = trkpt.find(f"{{{GPX_NAMESPACE}}}time")
            return el is not None and el.text is not None

        def get_time(trkpt: ET.Element) -> datetime:
            el = trkpt.find(f"{{{GPX_NAMESPACE}}}time")
            return datetime.fromisoformat(el.text.replace("Z", "+00:00"))

        if not has_time(all_trkpts[0]):
            print("Error: The first track point has no timestamp. Aborting.")
            return

        if not has_time(all_trkpts[-1]):
            print("Error: The last track point has no timestamp. Aborting.")
            return

        # Identify all gaps: consecutive runs of track points without <time>
        gaps: list[tuple[int, int]] = []  # (first_index, last_index) inclusive
        i = 0
        while i < len(all_trkpts):
            if not has_time(all_trkpts[i]):
                gap_start = i
                while i < len(all_trkpts) and not has_time(all_trkpts[i]):
                    i += 1
                gaps.append((gap_start, i - 1))
            else:
                i += 1

        if not gaps:
            print("No track points without timestamps found.")
            return

        total_assigned = 0

        # Episode 1: assign timestamps to gap points
        for gap_start, gap_end in gaps:
            anchor_start_pt = all_trkpts[gap_start - 1]
            anchor_end_pt   = all_trkpts[gap_end + 1]

            anchor_start_time = get_time(anchor_start_pt)
            anchor_end_time   = get_time(anchor_end_pt)
            time_span = anchor_end_time - anchor_start_time

            # Build segment including both anchors for distance calculation
            segment = (
                [anchor_start_pt]
                + all_trkpts[gap_start : gap_end + 1]
                + [anchor_end_pt]
            )

            # Cumulative Haversine distances along the segment
            cumulative: list[float] = [0.0]
            for j in range(1, len(segment)):
                prev_pt = segment[j - 1]
                curr_pt = segment[j]
                d = _haversine_distance(
                    float(prev_pt.attrib["lat"]),
                    float(prev_pt.attrib["lon"]),
                    float(curr_pt.attrib["lat"]),
                    float(curr_pt.attrib["lon"]),
                )
                cumulative.append(cumulative[-1] + d)

            total_dist = cumulative[-1]
            n_gap = gap_end - gap_start + 1

            for i_gap, trkpt in enumerate(all_trkpts[gap_start : gap_end + 1]):
                # In the segment array the gap points start at index 1
                seg_idx = i_gap + 1

                if total_dist == 0:
                    # All points at the same location — distribute evenly in time
                    fraction = (i_gap + 1) / (n_gap + 1)
                else:
                    fraction = cumulative[seg_idx] / total_dist

                new_time = anchor_start_time + fraction * time_span

                time_el = ET.Element(f"{{{GPX_NAMESPACE}}}time")
                time_el.text = new_time.isoformat().replace("+00:00", "Z")

                # Insert <time> after <ele> if present, otherwise at position 0
                ele_el = trkpt.find(f"{{{GPX_NAMESPACE}}}ele")
                if ele_el is not None:
                    trkpt.insert(list(trkpt).index(ele_el) + 1, time_el)
                else:
                    trkpt.insert(0, time_el)

            total_assigned += n_gap

        total_inserted = 0

        # Episode 2: insert interpolated points within gap zones (only if sample_rate given)
        if self._sample_rate is not None:
            interval = 1.0 / self._sample_rate

            # Use object identity to mark which trkpts belong to gap zones
            # (anchor_start and anchor_end inclusive)
            gap_zone_ids: set[int] = set()
            for gap_start, gap_end in gaps:
                for idx in range(gap_start - 1, gap_end + 2):
                    gap_zone_ids.add(id(all_trkpts[idx]))

            for trkseg in root.iter(f"{{{GPX_NAMESPACE}}}trkseg"):
                old_trkpts = [
                    c for c in trkseg if c.tag == f"{{{GPX_NAMESPACE}}}trkpt"
                ]
                new_trkpts: list[ET.Element] = []

                for j, trkpt in enumerate(old_trkpts):
                    new_trkpts.append(trkpt)
                    if (
                        j + 1 < len(old_trkpts)
                        and id(trkpt) in gap_zone_ids
                        and id(old_trkpts[j + 1]) in gap_zone_ids
                    ):
                        inserted = self._interpolate_points(
                            trkpt, old_trkpts[j + 1], interval, get_time
                        )
                        new_trkpts.extend(inserted)
                        total_inserted += len(inserted)

                # Rebuild trkseg in-place
                for trkpt in old_trkpts:
                    trkseg.remove(trkpt)
                for trkpt in new_trkpts:
                    trkseg.append(trkpt)

        if self._overwrite:
            output_path = self._recording
        else:
            output_path = self._recording.parent / (
                self._recording.stem + "_retimed" + self._recording.suffix
            )

        tree.write(output_path, xml_declaration=True, encoding="utf-8")

        msg = f"Done. {total_assigned} timestamps assigned."
        if self._sample_rate is not None:
            msg += f" {total_inserted} points inserted."
        print(msg)
        print(f"Output written to: {output_path}")

    def _interpolate_points(
        self,
        a: ET.Element,
        b: ET.Element,
        interval: float,
        get_time,
    ) -> list[ET.Element]:
        """
        Return a list of new track points to insert between a and b so that
        consecutive points are at most interval seconds apart.

        Positions (lat, lon, ele) are linearly interpolated between a and b.
        Timestamps are evenly spaced starting at a.time + interval.
        If the time gap between a and b is at most interval, returns [].
        """
        a_time = get_time(a)
        b_time = get_time(b)
        time_gap = (b_time - a_time).total_seconds()
        n_new = max(0, math.ceil(time_gap / interval) - 1)
        if n_new == 0:
            return []

        a_lat = float(a.attrib["lat"])
        a_lon = float(a.attrib["lon"])
        b_lat = float(b.attrib["lat"])
        b_lon = float(b.attrib["lon"])

        a_ele_el = a.find(f"{{{GPX_NAMESPACE}}}ele")
        b_ele_el = b.find(f"{{{GPX_NAMESPACE}}}ele")
        has_ele = a_ele_el is not None and b_ele_el is not None

        result: list[ET.Element] = []
        for k in range(1, n_new + 1):
            fraction = k * interval / time_gap

            trkpt = ET.Element(f"{{{GPX_NAMESPACE}}}trkpt")
            trkpt.attrib["lat"] = f"{a_lat + fraction * (b_lat - a_lat):.15f}"
            trkpt.attrib["lon"] = f"{a_lon + fraction * (b_lon - a_lon):.15f}"

            if has_ele:
                a_ele = float(a_ele_el.text)
                b_ele = float(b_ele_el.text)
                ele_el = ET.SubElement(trkpt, f"{{{GPX_NAMESPACE}}}ele")
                ele_el.text = f"{a_ele + fraction * (b_ele - a_ele):.6f}"

            new_time = a_time + k * timedelta(seconds=interval)
            time_el = ET.SubElement(trkpt, f"{{{GPX_NAMESPACE}}}time")
            time_el.text = new_time.isoformat().replace("+00:00", "Z")

            result.append(trkpt)

        return result


def _parse_timed_trackpoints(
    gpx_file: Path,
) -> list[tuple[float, float, datetime]]:
    """Return a list of (lat, lon, datetime) for all track points with a <time> element."""
    root = ET.parse(gpx_file).getroot()
    result: list[tuple[float, float, datetime]] = []
    for trkpt in root.iter(f"{{{GPX_NAMESPACE}}}trkpt"):
        time_el = trkpt.find(f"{{{GPX_NAMESPACE}}}time")
        if time_el is not None and time_el.text is not None:
            result.append((
                float(trkpt.attrib["lat"]),
                float(trkpt.attrib["lon"]),
                datetime.fromisoformat(time_el.text.replace("Z", "+00:00")),
            ))
    return result


def compare_tracks(
    recording: Path,
    reference: Path,
    max_time_diff: float,
    interval: float | None = None,
) -> None:
    """
    Compare two GPS tracks by matching points with similar timestamps and
    printing their distances as an aligned table to stdout.

    For each reference point (or each interval step when interval is given),
    the recording point with the smallest time difference within max_time_diff
    is found. The distance between the two points is computed using the
    Haversine formula. Rows with no match within the tolerance have empty
    distance and timestamp (original) columns.

    Parameters
    ----------
    recording : Path
        The GPS recording to compare against the reference.
    reference : Path
        The reference GPS track that drives the comparison.
    max_time_diff : float
        Maximum allowed time difference in seconds (interpreted as ±).
    interval : float | None
        If given, check every this many seconds starting from the first
        reference timestamp; otherwise every reference point is compared.
    """
    ref_pts = _parse_timed_trackpoints(reference)
    rec_pts = _parse_timed_trackpoints(recording)

    if not ref_pts:
        print("Reference track contains no timed track points.")
        return
    if not rec_pts:
        print("Recording contains no timed track points.")
        return

    # Determine which reference points serve as check points
    if interval is None:
        check_pts = ref_pts
    else:
        check_pts = []
        t = ref_pts[0][2]
        end_time = ref_pts[-1][2]
        while t <= end_time + timedelta(seconds=interval / 2):
            nearest = min(ref_pts, key=lambda p: abs((p[2] - t).total_seconds()))
            check_pts.append(nearest)
            t += timedelta(seconds=interval)

    # Table column widths — TS_WIDTH=32 accommodates microsecond precision timestamps
    TS_WIDTH = 32
    DIST_WIDTH = 12
    SEP = "   "

    print(
        f"{'Timestamp (Reference)':<{TS_WIDTH}}{SEP}"
        f"{'Distance (m)':>{DIST_WIDTH}}{SEP}"
        f"Timestamp (Original)"
    )

    for ref_lat, ref_lon, ref_time in check_pts:
        # Find the recording point with the smallest time difference within tolerance
        best_match: tuple[float, float, datetime] | None = None
        best_diff = float("inf")
        for rec_lat, rec_lon, rec_time in rec_pts:
            diff = abs((rec_time - ref_time).total_seconds())
            if diff <= max_time_diff and diff < best_diff:
                best_diff = diff
                best_match = (rec_lat, rec_lon, rec_time)

        ts_ref = ref_time.isoformat()
        if best_match is not None:
            rec_lat, rec_lon, rec_time = best_match
            dist = _haversine_distance(ref_lat, ref_lon, rec_lat, rec_lon)
            print(
                f"{ts_ref:<{TS_WIDTH}}{SEP}"
                f"{dist:>{DIST_WIDTH}.2f}{SEP}"
                f"{rec_time.isoformat()}"
            )
        else:
            print(f"{ts_ref:<{TS_WIDTH}}")
