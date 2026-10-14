"""NOAA NCEI normals: parsing, flag filtering, archive extraction and the fetcher (synthetic data, no network)."""
import datetime as dt
import io
import tarfile

import pytest

from pipeline.climate import http as chttp
from pipeline.climate import noaa_normals as nn

HEADER = ("STATION,NAME,LATITUDE,LONGITUDE,ELEVATION,ANN-PRCP-NORMAL,meas_flag_ANN-PRCP-NORMAL,comp_flag_ANN-PRCP-NORMAL,"
          "years_ANN-PRCP-NORMAL,ANN-SNOW-NORMAL,meas_flag_ANN-SNOW-NORMAL,comp_flag_ANN-SNOW-NORMAL,years_ANN-SNOW-NORMAL")


def station_csv(sid="USC00000001", name="FIXTURE PEAK, CO US", lat=" 39.5000", lon="-106.0000", elev="2900.0",
                snow="   250.4", meas=" ", comp="S", years="29"):
    return f'{HEADER}\n{sid},"{name}            ",{lat},{lon},{elev},  20.1, ,S,30,{snow},{meas},{comp},{years}\n'


def test_parse_station_csv_reads_the_snow_normal_and_flags():
    st = nn.parse_station_csv(station_csv())
    assert st == nn.StationNormal("USC00000001", "FIXTURE PEAK, CO US", 39.5, -106.0, 2900.0, 250.4, "", "S", 29)


def test_parse_station_without_the_snow_column_is_none():
    text = "STATION,NAME,LATITUDE,LONGITUDE,ELEVATION,ANN-PRCP-NORMAL\nUSC00000002,X,1,2,3,4\n"
    assert nn.parse_station_csv(text) is None
    assert nn.parse_station_csv("") is None


@pytest.mark.parametrize("snow", ["", "   ", "-9999.0", "abc"])
def test_missing_snow_values_parse_as_none(snow):
    assert nn.parse_station_csv(station_csv(snow=snow)).ann_snow_in is None


ACCEPT = (["S", "R", "P"], ["", "X"])


@pytest.mark.parametrize("comp, meas, snow, keep", [
    ("S", "", "250.4", True),
    ("R", "", "80", True),
    ("P", "", "12", True),
    ("E", "", "12", False),      # estimated from neighbors: dropped
    ("S", "M", "12", False),     # missing
    ("S", "Y", "12", False),     # insufficient values
    ("S", "Z", "12", False),     # inconsistent
    ("P", "X", "0.0", True),     # nonzero rounded to zero: a real near zero normal
    ("S", "", "0.0", True),      # zero snowfall in the South is real data
    ("S", "", "", False),        # no value
])
def test_usable_follows_the_dataset_flags(comp, meas, snow, keep):
    st = nn.parse_station_csv(station_csv(comp=comp, meas=meas, snow=snow))
    assert nn.usable(st, *ACCEPT) is keep


def test_filter_usable_keeps_southern_zero_stations():
    rows = [nn.parse_station_csv(station_csv(sid=f"USC0000000{i}", snow=v, comp=c, meas=m))
            for i, (v, c, m) in enumerate([("0.0", "S", ""), ("0.0", "P", "X"), ("5", "E", ""), ("40", "P", "")])]
    kept = nn.filter_usable(rows, *ACCEPT)
    assert [s.station_id for s in kept] == ["USC00000000", "USC00000001", "USC00000003"]


def _archive(tmp_path, members):
    path = tmp_path / "normals.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_extract_archive_keeps_state_stations_with_the_column(tmp_path):
    path = _archive(tmp_path, {
        "USC00000002.csv": station_csv(sid="USC00000002"),
        "USW00000001.csv": station_csv(sid="USW00000001", snow="0.0", meas="X", comp="P"),
        "RQC00000001.csv": station_csv(sid="RQC00000001"),          # Puerto Rico: territory, left out
        "USC00000003.csv": "STATION,NAME,LATITUDE,LONGITUDE,ELEVATION\nUSC00000003,X,1,2,3\n",  # no snow column
        "README.txt": "not a station",
    })
    rows, files = nn.extract_archive(path)
    assert files == 4
    assert [r.station_id for r in rows] == ["USC00000002", "USW00000001"]


def test_extract_round_trip(tmp_path):
    rows = [nn.parse_station_csv(station_csv(sid="USC00000009", name='ODD, NAME WITH COMMA')),
            nn.parse_station_csv(station_csv(sid="US1COXX0001", snow="", elev=""))]
    src = {"source": {"id": nn.SOURCE_ID}, "confidence": "published"}
    nn.write_extract(rows, tmp_path, src)
    back, meta = nn.load_extract(tmp_path)
    assert back == rows and meta == src


LISTING = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>noaa-normals-pds</Name>
<Contents><Key>normals-annualseasonal/1991-2020/archive/us-climate-normals_1991-2020_v1.0.1_annualseasonal_multivariate_by-station_c20230404.tar.gz</Key>
<LastModified>2026-07-19T13:49:11.000Z</LastModified><ETag>&quot;abc&quot;</ETag><Size>1234</Size></Contents>
<Contents><Key>normals-annualseasonal/1991-2020/archive/other.csv</Key><Size>1</Size></Contents></ListBucketResult>"""


def test_parse_listing_finds_the_by_station_archive():
    item = nn.parse_listing(LISTING)
    assert item["key"].endswith("by-station_c20230404.tar.gz") and item["size"] == 1234 and item["etag"] == "abc"


def test_fetch_writes_the_extract_and_source_record(tmp_path, monkeypatch):
    archive = _archive(tmp_path, {"USC00000002.csv": station_csv(sid="USC00000002")})
    calls = []

    def fake_get_bytes(url, params=None, **kw):
        calls.append((url, params))
        return LISTING.replace(b"<Size>1234</Size>", f"<Size>{archive.stat().st_size}</Size>".encode())

    def fake_download(url, dest, **kw):
        calls.append((url, None))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive.read_bytes())
        return dest

    monkeypatch.setattr(nn.http, "get_bytes", fake_get_bytes)
    monkeypatch.setattr(nn.http, "download", fake_download)
    out = tmp_path / "noaa"
    res = nn.fetch(out, today=dt.date(2026, 10, 13))
    assert res["rows"] == 1 and res["station_files"] == 1
    assert calls[0][0] == nn.BUCKET_URL and calls[0][1]["prefix"] == nn.ARCHIVE_PREFIX
    assert calls[1][0].startswith("https://noaa-normals-pds.s3.amazonaws.com/normals-annualseasonal/")
    rows, meta = nn.load_extract(out)
    assert rows[0].ann_snow_in == 250.4
    assert meta["source"]["id"] == nn.SOURCE_ID and meta["confidence"] == "published"
    assert meta["license"] == "public domain" and meta["source"]["accessed_date"] == "2026-10-13"
    # A second fetch reuses the download when its size matches.
    nn.fetch(out, today=dt.date(2026, 10, 13))
    assert sum(1 for u, _ in calls if u != nn.BUCKET_URL) == 1


def test_requests_carry_a_generic_user_agent_without_an_email(monkeypatch):
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return Resp(b"payload")

    monkeypatch.setattr(chttp.urllib.request, "urlopen", fake_urlopen)
    assert chttp.get_bytes("https://example.org/x") == b"payload"
    ua = seen[0].get_header("User-agent")
    assert ua == chttp.USER_AGENT and "@" not in ua and "mailto" not in ua.lower()


def test_download_streams_to_the_destination(tmp_path, monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(chttp.urllib.request, "urlopen", lambda req, timeout=None: Resp(b"x" * 5000))
    dest = chttp.download("https://example.org/a.tar.gz", tmp_path / "d" / "a.tar.gz")
    assert dest.read_bytes() == b"x" * 5000 and not (tmp_path / "d" / "a.tar.gz.part").exists()


def test_committed_extract_is_compact_and_sourced():
    rows, meta = nn.load_extract(nn.DEFAULT_DIR)
    assert len(rows) > 5000
    assert meta["source"]["id"] == nn.SOURCE_ID and meta["confidence"] == "published"
    assert all(nn.US_STATION.match(r.station_id) for r in rows)
    assert (nn.DEFAULT_DIR / nn.EXTRACT_NAME).stat().st_size < 600_000
