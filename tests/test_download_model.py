import io
import unittest
from unittest import mock

import download_model


class DownloadModelTests(unittest.TestCase):
    def test_parses_prefix_with_spaces(self):
        self.assertEqual(
            download_model.parse_s3_uri(
                "s3://bossdb-neuvue-datalake/public/models/20260825_191045 trial 1"
            ),
            (
                "bossdb-neuvue-datalake",
                "public/models/20260825_191045 trial 1/",
            ),
        )

    def test_rejects_bucket_without_prefix(self):
        with self.assertRaises(ValueError):
            download_model.parse_s3_uri("s3://bucket")

    def test_decodes_s3_space_and_literal_plus_keys(self):
        response = io.BytesIO(
            b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <IsTruncated>false</IsTruncated>
  <Contents><Key>models/trial+1/config.yaml</Key><Size>12</Size></Contents>
  <Contents><Key>models/a%2Bb.ckpt</Key><Size>34</Size></Contents>
</ListBucketResult>"""
        )
        with mock.patch.object(download_model, "open_url", return_value=response):
            objects = download_model.list_objects("bucket", "models/")
        self.assertEqual(
            [entry["key"] for entry in objects],
            ["models/trial 1/config.yaml", "models/a+b.ckpt"],
        )

    def test_selects_config_and_lowest_validation_loss(self):
        entries = [
            {"key": "models/config.yaml", "size": 1},
            {"key": "models/checkpoints/last.ckpt", "size": 2},
            {"key": "models/checkpoints/epoch=070-val_loss=1.0096.ckpt", "size": 3},
            {"key": "models/checkpoints/epoch=092-val_loss=1.0085.ckpt", "size": 4},
            {"key": "models/prediction/raw_x8.h5", "size": 5},
        ]
        selected = download_model.select_model_objects(entries)
        self.assertEqual(
            [entry["key"] for entry in selected],
            [
                "models/config.yaml",
                "models/checkpoints/epoch=092-val_loss=1.0085.ckpt",
            ],
        )


if __name__ == "__main__":
    unittest.main()
