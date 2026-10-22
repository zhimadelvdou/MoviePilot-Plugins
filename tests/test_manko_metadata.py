"""Synthetic offline regressions for Manko runtime and artwork projection."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET


CORE_PATH = Path(__file__).resolve().parents[1] / "plugins.v3" / "xiaojiejiescraping" / "core.py"
SPEC = importlib.util.spec_from_file_location("manko_metadata_core_under_test", CORE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load plugin core from {CORE_PATH}")
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


ALLOWED_HOSTS = ("images.example.test", "art.example.test")
COVER = "https://images.example.test/cover.jpg"
BACKDROP = "https://art.example.test/backdrop.jpg"


def raw_record(**overrides):
    record = {
        "_id": "fixture-native-765",
        "number": "TEST-765",
        "title": "TEST-765 synthetic title",
        "title_display_name": [{"language": "en", "title": "TEST-765 synthetic title"}],
        "discription": [{"language": "en", "overview": "Synthetic plot only."}],
        "share_date": "2026-10-21T00:00:00Z",
        "cover_image": COVER,
        "backdrop_images": [BACKDROP],
        "video_duration": 120,
    }
    record.update(overrides)
    return record


def project(record):
    return core.project_record(record, "TEST-765", [], ALLOWED_HOSTS)


class TestMankoMetadata(unittest.TestCase):
    def test_project_record_maps_video_duration_minutes_to_runtime(self):
        info = project(raw_record())

        self.assertEqual(info.get("runtime"), 120)

    def test_project_record_uses_first_valid_allowlisted_backdrop_separate_from_cover(self):
        record = raw_record(backdrop_images=[
            "http://art.example.test/insecure.jpg",
            "https://unapproved.example/backdrop.jpg",
            BACKDROP,
            "https://art.example.test/second.jpg",
        ])

        info = project(record)

        self.assertEqual(info.get("poster_path"), COVER)
        self.assertEqual(info.get("backdrop_path"), BACKDROP)

    def test_project_record_skips_cover_repeated_at_start_of_backdrop_list(self):
        info = project(raw_record(backdrop_images=[COVER, BACKDROP]))

        self.assertEqual(info.get("backdrop_path"), BACKDROP)

    def test_project_record_does_not_reuse_cover_when_backdrop_is_invalid(self):
        info = project(raw_record(backdrop_images=["http://art.example.test/insecure.jpg"]))

        self.assertEqual(info.get("poster_path"), COVER)
        self.assertIsNone(info.get("backdrop_path"))

    def test_image_map_uses_independent_poster_and_fanart_sources(self):
        images = core.image_map(raw_record(), ALLOWED_HOSTS)

        self.assertEqual(images.get("poster.jpg"), COVER)
        self.assertEqual(images.get("fanart.jpg"), BACKDROP)
        self.assertNotEqual(images["poster.jpg"], images["fanart.jpg"])

    def test_image_map_omits_fanart_instead_of_reusing_cover_when_backdrop_is_invalid(self):
        images = core.image_map(raw_record(backdrop_images=["http://art.example.test/insecure.jpg"]), ALLOWED_HOSTS)

        self.assertEqual(images, {"poster.jpg": COVER})

    def test_image_map_omits_fanart_when_backdrop_only_repeats_cover(self):
        images = core.image_map(raw_record(backdrop_images=[COVER]), ALLOWED_HOSTS)

        self.assertEqual(images, {"poster.jpg": COVER})

    def test_render_nfo_writes_projected_runtime_as_minutes(self):
        info = project(raw_record())
        nfo = ET.fromstring(core.render_nfo(info, "TEST-765"))

        self.assertEqual(nfo.findtext("runtime"), "120")

    def test_invalid_duration_values_are_not_projected_as_runtime(self):
        for value in (True, 0, -1, 10_000, "120", 120.0, None):
            with self.subTest(value=value):
                info = project(raw_record(video_duration=value))
                self.assertIsNone(info.get("runtime"))

    def test_verified_official_minutes_supply_runtime_only_when_manko_duration_is_missing(self):
        info = project(raw_record(video_duration=None, runtime_minutes=120))

        self.assertEqual(info.get("runtime"), 120)

    def test_normalized_director_and_production_company_fields_reach_nfo(self):
        info = project(raw_record(
            directors=[{"name": "Synthetic Director"}, {"name": "Bad" * 201}, "invalid", {"name": "Second Director\x01"}],
            production_companies=[{"name": "Synthetic Studio"}, {"name": "Second Studio"}],
        ))
        nfo = ET.fromstring(core.render_nfo(info, "TEST-765"))

        self.assertEqual(info.get("directors"), [{"name": "Synthetic Director"}, {"name": "Second Director"}])
        self.assertEqual(info.get("production_companies"), [{"name": "Synthetic Studio"}, {"name": "Second Studio"}])
        self.assertNotIn("director", info)
        self.assertNotIn("studio", info)
        self.assertEqual([node.text for node in nfo.findall("director")], ["Synthetic Director", "Second Director"])
        self.assertEqual([node.text for node in nfo.findall("studio")], ["Synthetic Studio", "Second Studio"])

    def test_projection_and_nfo_rendering_do_not_mutate_raw_provider_record(self):
        record = raw_record()
        original = copy.deepcopy(record)

        info = project(record)
        core.render_nfo(info, "TEST-765")

        self.assertEqual(record, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
