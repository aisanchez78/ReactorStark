import io
import json
import os
import sys
import tempfile
import threading
import unittest
import argparse
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs
from urllib.error import HTTPError
from urllib.request import Request, urlopen as http_urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def load_example_config(operation):
    env_path = Path(__file__).resolve().parents[1] / ".env.example"
    environment = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, separator, value = line.partition("=")
            if separator:
                environment[name.strip()] = value.strip()

    with patch.dict(os.environ, environment, clear=True):
        return main.Config.from_environment(operation)


class ThingSpeakClientTests(unittest.TestCase):
    def setUp(self):
        self.client = main.ThingSpeakClient(load_example_config("write"))

    def test_environment_file_loads_values_without_overriding_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "THINGSPEAK_CHANNEL_ID=1234567\nTHINGSPEAK_WRITE_API_KEY=FROM_FILE\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"THINGSPEAK_WRITE_API_KEY": "FROM_ENV"}, clear=True):
                with patch("main.ENV_FILE", env_path):
                    main._load_environment_file()

                self.assertEqual(os.environ["THINGSPEAK_CHANNEL_ID"], "1234567")
                self.assertEqual(os.environ["THINGSPEAK_WRITE_API_KEY"], "FROM_ENV")

    def test_web_panel_reads_and_writes_through_local_api(self):
        config = main.Config("1234567", write_api_key="WRITE", read_api_key="READ")
        feeds = [{"created_at": "2026-10-31T12:00:00Z", "field1": "52.4"}]
        client = Mock()
        client.config = config
        client.read.return_value = feeds
        client.write.return_value = 42

        with (
            patch("main._load_environment_file"),
            patch("main.Config.from_environment", return_value=config),
            patch("main.ThingSpeakClient", return_value=client),
        ):
            server = main.create_web_server(0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with http_urlopen(f"{base_url}/") as response:
                    page = response.read().decode("utf-8")
                self.assertIn('id="write-form"', page)

                with http_urlopen(f"{base_url}/api/feeds?results=20") as response:
                    result = json.loads(response.read())
                self.assertEqual(result["channel_id"], "1234567")
                self.assertEqual(result["feeds"], feeds)
                client.read.assert_called_once_with(20)

                values = {
                    "field1": 52.4,
                    "field2": 96.8,
                    "field3": 35.2,
                    "field4": 118.6,
                    "field5": 42.3,
                }
                request = Request(
                    f"{base_url}/api/write",
                    data=json.dumps(values).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with http_urlopen(request) as response:
                    result = json.loads(response.read())
                self.assertEqual(result, {"entry_id": 42})
                client.write.assert_called_once_with((52.4, 96.8, 35.2, 118.6, 42.3))

                values["field2"] = 101
                request = Request(
                    f"{base_url}/api/write",
                    data=json.dumps(values).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    http_urlopen(request)
                self.assertEqual(error.exception.code, 400)
                client.write.assert_called_once()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    @patch("main.urlopen")
    def test_write_sends_all_five_fields_and_returns_entry_id(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"entry_id": 42}'

        entry_id = self.client.write((52.4, 96.8, 35.2, 118.6, 42.3))

        self.assertEqual(entry_id, 42)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.thingspeak.com/update.json")
        self.assertEqual(
            parse_qs(request.data.decode()),
            {
                "api_key": [self.client.config.write_api_key],
                "field1": ["52.4"],
                "field2": ["96.8"],
                "field3": ["35.2"],
                "field4": ["118.6"],
                "field5": ["42.3"],
            },
        )

    @patch("main.urlopen")
    def test_read_returns_feeds_and_requests_requested_count(self, urlopen):
        client = main.ThingSpeakClient(load_example_config("read"))
        feeds = [{"created_at": "2026-10-31T12:00:00Z", "field1": "52.4"}]
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"feeds": feeds}
        ).encode()

        result = client.read(10)

        self.assertEqual(result, feeds)
        request_url = urlopen.call_args.args[0].full_url
        self.assertIn(f"/channels/{client.config.channel_id}/feeds.json?", request_url)
        self.assertIn(f"api_key={client.config.read_api_key}", request_url)
        self.assertIn("results=10", request_url)

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_channel_configuration_is_reported(self):
        with self.assertRaisesRegex(main.ThingSpeakError, "THINGSPEAK_CHANNEL_ID"):
            main.Config.from_environment("read")

    def test_write_rejects_zero_entry_id(self):
        with patch("main.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"entry_id": 0}'
            with self.assertRaisesRegex(main.ThingSpeakError, "rechazó la escritura"):
                self.client.write(main.SAMPLES[0])

    def test_sample_interval_respects_free_account_limit(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            main.parse_interval("14")
        self.assertEqual(main.parse_interval("15"), 15.0)

    def test_write_rejects_non_finite_measurements(self):
        args = main.build_parser().parse_args(
            [
                "write",
                "--field1",
                "nan",
                "--field2",
                "96.8",
                "--field3",
                "35.2",
                "--field4",
                "118.6",
                "--field5",
                "42.3",
            ]
        )
        with self.assertRaisesRegex(main.ThingSpeakError, "número finito"):
            main._values_from_args(args)

    def test_preview_does_not_require_channel_secrets(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main.main(["preview"]), 0)


if __name__ == "__main__":
    unittest.main()
