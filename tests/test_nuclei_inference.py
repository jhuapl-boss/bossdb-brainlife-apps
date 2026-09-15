import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import nuclei_inference as inference


class FakeBounds:
    def __init__(self, minimum, maximum):
        self.minpt = minimum
        self.maxpt = maximum


class FakeVolume:
    def __init__(self, data, minimum=(0, 0, 0)):
        self.data = np.asarray(data)
        self.dtype = self.data.dtype
        self.resolution = (32, 32, 40)
        maximum = tuple(start + size for start, size in zip(minimum, self.data.shape))
        self.bounds = FakeBounds(minimum, maximum)
        self.minimum = minimum

    def __getitem__(self, selection):
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
        "model": inference.MODEL_NAME,
        "threshold": 0.5,
        "output_path": "outputs",
    }
    values.update(overrides)
    return inference.validate_config(values)


class ConfigTests(unittest.TestCase):
    def test_accepts_brainlife_string_numbers_and_internal_fields(self):
        config = inference.validate_config(
            {
                "channel": "bossdb://chandok2026/human_boutons/8_nm_volume_upsampled",
                "x_start": "28672",
                "y_start": "28672",
                "z_start": "30",
                "x_stop": "29672",
                "y_stop": "29672",
                "z_stop": "40",
                "resolution": 0,
                "model": inference.MODEL_NAME,
                "threshold": "0.5",
                "output_path": "outputs",
                "_inputs": [{"id": "input"}],
                "_outputs": [{"id": "outputs"}],
            }
        )
        self.assertEqual(config.shape_xyz, (1000, 1000, 10))
        self.assertEqual(config.threshold, 0.5)

    def test_rejects_invalid_threshold_or_model(self):
        for value in (-0.1, 1.1, True, "nan"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_config(threshold=value)
        with self.assertRaisesRegex(ValueError, "unsupported model"):
            make_config(model="unknown")

    def test_output_path_must_stay_in_working_directory(self):
        for value in ("", ".", "../elsewhere", "/tmp/output"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_config(output_path=value)

    def test_rejects_missing_or_reversed_bounds(self):
        with self.assertRaisesRegex(ValueError, "x_stop must be an integer"):
            make_config(x_stop=None)
        with self.assertRaisesRegex(ValueError, "z_stop must be greater"):
            make_config(z_stop=1)

    def test_parse_bossdb_uri(self):
        self.assertEqual(
            inference.parse_bossdb_uri("bossdb://collection/experiment/channel"),
            ("collection", "experiment", "channel"),
        )
        with self.assertRaises(ValueError):
            inference.parse_bossdb_uri("boss://collection/experiment/channel")


class DataTests(unittest.TestCase):
    def test_download_transposes_xyz_to_zyx(self):
        data = np.arange(5 * 4 * 4, dtype=np.uint8).reshape(5, 4, 4)
        actual = inference.download_cutout_zyx(FakeVolume(data), make_config())
        expected = data[1:4, 0:2, 1:3].transpose(2, 1, 0)
        np.testing.assert_array_equal(actual, expected)

    def test_minmax_matches_connectomics_inference_transform(self):
        raw = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
        normalized, metadata = inference.normalize_em(raw)
        self.assertEqual(float(normalized.min()), 0.0)
        self.assertEqual(float(normalized.max()), 1.0)
        self.assertEqual(metadata["method"], "min-max")
        with self.assertRaisesRegex(ValueError, "no intensity variation"):
            inference.normalize_em(np.ones((2, 2, 2), dtype=np.uint8))

    def test_connectomics_runtime_matches_trial_inference(self):
        config = inference.build_connectomics_inference_config(
            "cuda", window_zyx=(4, 6, 8), overlap=0.25, batch_size=2
        )
        self.assertEqual(config.inference.sliding_window.window_size, [4, 6, 8])
        self.assertEqual(config.inference.sliding_window.blending, "bump")
        self.assertEqual(config.inference.sliding_window.sw_device, "cuda")
        self.assertEqual(config.inference.sliding_window.output_device, "cpu")
        self.assertTrue(config.inference.test_time_augmentation.patch_first_local)
        self.assertEqual(config.inference.test_time_augmentation.flip_axes, "all")
        self.assertEqual(
            config.inference.model.channel_activations,
            [{"channels": ":", "activation": "sigmoid"}],
        )

    def test_sliding_tta_restores_each_flip(self):
        try:
            from connectomics.inference.manager import InferenceManager  # noqa: F401
        except ImportError:
            self.skipTest("PyTorch Connectomics runtime is not installed")

        class IdentityModel:
            def __call__(self, tensor):
                return tensor

        image = np.linspace(-2, 2, 3 * 5 * 6, dtype=np.float32).reshape(3, 5, 6)
        actual = inference.predict_probabilities(
            image,
            IdentityModel(),
            "cpu",
            window_zyx=(4, 4, 4),
            overlap=0.5,
            batch_size=2,
        )
        expected = 1.0 / (1.0 + np.exp(-image))
        np.testing.assert_allclose(actual, expected, atol=2e-6)


class ModelAssetTests(unittest.TestCase):
    def test_selects_best_validation_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("last.ckpt", "010-0.2000.ckpt", "020-0.1000.ckpt"):
                (root / name).write_bytes(b"checkpoint")
            self.assertEqual(inference.find_checkpoint(root).name, "020-0.1000.ckpt")

    def test_extracts_lightning_wrapped_model_weights(self):
        checkpoint = {
            "state_dict": {
                "model.model.conv.weight": "weight",
                "model.model.conv.bias": "bias",
                "loss_functions.0.buffer": "ignored",
            }
        }
        state = inference.select_model_state_dict(
            checkpoint, ("conv.weight", "conv.bias")
        )
        self.assertEqual(state, {"conv.weight": "weight", "conv.bias": "bias"})

    def test_extracts_weights_for_connectomics_model_wrapper(self):
        checkpoint = {
            "state_dict": {
                "model.model.conv.weight": "weight",
                "model.model.conv.bias": "bias",
            }
        }
        state = inference.select_model_state_dict(
            checkpoint, ("model.conv.weight", "model.conv.bias")
        )
        self.assertEqual(
            state,
            {"model.conv.weight": "weight", "model.conv.bias": "bias"},
        )


class OutputTests(unittest.TestCase):
    def test_writes_readable_precomputed_segmentation(self):
        try:
            from cloudvolume import CloudVolume
        except ImportError:
            self.skipTest("cloudvolume is not installed")
        segmentation = np.zeros((2, 3, 4), dtype=np.uint32)
        segmentation[:, 1:, 2:] = 1
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "layer"
            inference.write_precomputed_segmentation(
                output,
                segmentation,
                starts_xyz=(10, 20, 30),
                resolution_xyz=(32, 32, 40),
            )
            volume = CloudVolume(output.resolve().as_uri(), mip=0, progress=False)
            actual = np.asarray(volume[10:14, 20:23, 30:32])[..., 0]
            info = json.loads((output / "info").read_text())
        np.testing.assert_array_equal(actual, segmentation.transpose(2, 1, 0))
        self.assertEqual(info["type"], "segmentation")
        self.assertEqual(info["scales"][0]["voxel_offset"], [10, 20, 30])

    def test_run_writes_provenance_and_product(self):
        data = np.arange(5 * 4 * 4, dtype=np.uint8).reshape(5, 4, 4)
        volume = FakeVolume(data)
        old_cwd = os.getcwd()
        old_model_root = os.environ.get("NUCLEI_MODEL_ROOT")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                model_root = Path(directory) / "models"
                model_dir = model_root / inference.MODEL_DIRECTORY
                model_dir.mkdir(parents=True)
                (model_dir / "best.ckpt").write_bytes(b"test checkpoint")
                (model_dir / "config.yaml").write_text("model: test\n")
                os.environ["NUCLEI_MODEL_ROOT"] = str(model_root)
                with mock.patch.object(
                    inference, "choose_device", return_value=("cpu", "test")
                ):
                    report = inference.run(
                        make_config(),
                        json_fetcher=lambda _: {
                            "public": True,
                            "storage_type": "cloudvol",
                            "bucket": "bossdb-open-data",
                            "cv_path": "collection/experiment/channel",
                        },
                        volume_factory=lambda cloudpath, mip: volume,
                        model_loader=lambda checkpoint, device: object(),
                        predictor=lambda image, model, device: np.full(
                            image.shape, 0.75, dtype=np.float32
                        ),
                        output_writer=lambda path, data, **kwargs: path.mkdir(
                            parents=True, exist_ok=True
                        ),
                    )
                sidecar = json.loads(Path("outputs/inference.json").read_text())
                product = json.loads(Path("product.json").read_text())
        finally:
            os.chdir(old_cwd)
            if old_model_root is None:
                os.environ.pop("NUCLEI_MODEL_ROOT", None)
            else:
                os.environ["NUCLEI_MODEL_ROOT"] = old_model_root
        self.assertEqual(sidecar, report)
        self.assertEqual(report["output"]["foreground_voxels"], 12)
        self.assertEqual(product["brainlife"][0]["type"], "success")


if __name__ == "__main__":
    unittest.main()
