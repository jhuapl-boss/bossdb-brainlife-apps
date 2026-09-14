import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

import export_tiff


class FakeBounds:
    def __init__(self, minimum, maximum):
        self.minpt = minimum
        self.maxpt = maximum


class FakeVolume:
    def __init__(self, data, minimum=(0, 0, 0)):
        self.data = np.asarray(data)
        self.dtype = self.data.dtype
        self.chunk_size = (2, 2, 4)
        self.resolution = (4, 4, 40)
        maximum = tuple(start + size for start, size in zip(minimum, self.data.shape))
        self.bounds = FakeBounds(minimum, maximum)
        self.minimum = minimum
        self.requests = []

    def __getitem__(self, selection):
        self.requests.append(selection)
        local = tuple(
            slice(part.start - offset, part.stop - offset)
            for part, offset in zip(selection, self.minimum)
        )
        return self.data[local][..., np.newaxis]


def make_config(**overrides):
    values = {
        "channel": "bossdb://collection/experiment/channel",
        "x_start": 1,
        "y_start": 0,
        "z_start": 1,
        "x_stop": 4,
        "y_stop": 2,
        "z_stop": 3,
        "resolution": 0,
        "export_as_volume": False,
    }
    values.update(overrides)
    return export_tiff.validate_config(values)


class ConfigTests(unittest.TestCase):
    def test_valid_config_normalizes_integral_numbers(self):
        config = make_config(x_stop=4.0)
        self.assertEqual(config.x_stop, 4)
        self.assertEqual(config.shape_xyz, (3, 2, 2))

    def test_rejects_missing_or_reversed_bounds(self):
        with self.assertRaisesRegex(ValueError, "x_stop must be an integer"):
            make_config(x_stop=None)
        with self.assertRaisesRegex(ValueError, "z_stop must be greater"):
            make_config(z_stop=1)

    def test_rejects_non_boolean_mode(self):
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            make_config(export_as_volume=1)

    def test_parse_bossdb_uri(self):
        self.assertEqual(
            export_tiff.parse_bossdb_uri("bossdb://collection/experiment/channel"),
            ("collection", "experiment", "channel"),
        )
        for invalid in (
            "boss://collection/experiment/channel",
            "bossdb://collection/experiment",
            "bossdb://collection/experiment/channel/extra",
            "bossdb://collection/experiment/channel?token=nope",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                export_tiff.parse_bossdb_uri(invalid)


class ResolveTests(unittest.TestCase):
    def test_resolves_public_cloudvol_metadata(self):
        requested = []

        def fetcher(url):
            requested.append(url)
            return {
                "public": True,
                "storage_type": "cloudvol",
                "bucket": "bossdb-open-data",
                "cv_path": "collection/experiment/channel",
            }

        channel = export_tiff.resolve_channel(
            "bossdb://collection/experiment/channel", json_fetcher=fetcher
        )
        self.assertEqual(
            requested,
            ["https://api.bossdb.io/v1/collection/collection/experiment/experiment/channel/channel"],
        )
        self.assertEqual(
            channel.cloudpath,
            "s3://bossdb-open-data/collection/experiment/channel",
        )

    def test_rejects_private_or_non_precomputed_channels(self):
        with self.assertRaisesRegex(ValueError, "not public"):
            export_tiff.resolve_channel(
                "bossdb://c/e/ch", json_fetcher=lambda _: {"public": False}
            )
        with self.assertRaisesRegex(ValueError, "not stored as a precomputed"):
            export_tiff.resolve_channel(
                "bossdb://c/e/ch",
                json_fetcher=lambda _: {"public": True, "storage_type": "boss"},
            )


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.data = np.arange(5 * 4 * 4, dtype=np.uint16).reshape(5, 4, 4)

    def test_volume_export_is_zyx(self):
        volume = FakeVolume(self.data)
        config = make_config(export_as_volume=True)
        with tempfile.TemporaryDirectory() as directory:
            report = export_tiff.export_cutout(volume, config, Path(directory))
            actual = tifffile.imread(Path(directory) / "volume.tif")

        expected = self.data[1:4, 0:2, 1:3].transpose(2, 1, 0)
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(report["shape_zyx"], [2, 2, 3])
        self.assertEqual(len(volume.requests), 1)

    def test_slice_export_downloads_in_source_z_chunks(self):
        volume = FakeVolume(self.data)
        config = make_config()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            report = export_tiff.export_cutout(volume, config, output)
            names = sorted(path.name for path in output.glob("*.tif"))
            first = tifffile.imread(output / names[0])
            second = tifffile.imread(output / names[1])

        self.assertEqual(names, ["slice_z000001.tif", "slice_z000002.tif"])
        np.testing.assert_array_equal(first, self.data[1:4, 0:2, 1].T)
        np.testing.assert_array_equal(second, self.data[1:4, 0:2, 2].T)
        self.assertEqual(report["output"]["file_count"], 2)
        self.assertEqual(report["voxel_size_nm_xyz"], [4.0, 4.0, 40.0])
        self.assertEqual(len(volume.requests), 1)
        self.assertEqual(volume.requests[0][2].stop - volume.requests[0][2].start, 2)

    def test_rejects_cutout_outside_selected_mip(self):
        volume = FakeVolume(self.data)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "outside mip bounds"):
                export_tiff.export_cutout(volume, make_config(x_stop=6), Path(directory))

    def test_run_writes_brainlife_products(self):
        volume = FakeVolume(self.data)
        old_working_directory = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                report = export_tiff.run(
                    make_config(export_as_volume=True),
                    json_fetcher=lambda _: {
                        "public": True,
                        "storage_type": "cloudvol",
                        "bucket": "bossdb-open-data",
                        "cv_path": "collection/experiment/channel",
                    },
                    volume_factory=lambda cloudpath, mip: volume,
                )
                sidecar = json.loads(Path("outputs/export.json").read_text())
                product = json.loads(Path("product.json").read_text())
        finally:
            os.chdir(old_working_directory)

        self.assertEqual(sidecar, report)
        self.assertEqual(sidecar["source"], "bossdb://collection/experiment/channel")
        self.assertEqual(product["brainlife"][0]["type"], "success")


if __name__ == "__main__":
    unittest.main()
