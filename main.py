#!/usr/bin/env python3
"""CLI para consultar y actualizar el canal ThingSpeak de la actividad."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


API_BASE = "https://api.thingspeak.com"
ENV_FILE = Path(__file__).with_name(".env")
WEB_PAGE = Path(__file__).with_name("web") / "index.html"
MINIMUM_INTERVAL_SECONDS = 15
FIELDS = (
    "Carga del núcleo (%)",
    "Estabilidad del campo (0-100)",
    "Flujo de refrigerante (0-100)",
    "Latencia de JARVIS (ms)",
    "Consumo de repulsores (W)",
)
SAMPLES = (
    (52.4, 96.8, 35.2, 118.6, 42.3),
    (58.7, 94.3, 41.9, 126.4, 46.8),
    (63.9, 91.5, 48.6, 162.8, 51.7),
    (69.2, 88.1, 56.3, 134.9, 57.9),
    (74.6, 84.7, 63.5, 148.2, 63.4),
    (79.8, 81.2, 71.8, 178.5, 69.6),
    (84.3, 77.6, 79.4, 196.7, 76.2),
    (88.9, 73.8, 86.7, 214.3, 82.5),
    (92.5, 70.4, 92.1, 231.6, 88.7),
    (95.8, 67.9, 96.4, 249.8, 94.1),
)


class ThingSpeakError(Exception):
    """Error de configuración o de comunicación con ThingSpeak."""


def _load_environment_file() -> None:
    if not ENV_FILE.exists():
        return

    for line_number, raw_line in enumerate(ENV_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not name.strip():
            raise ThingSpeakError(f"Configuración no válida en .env, línea {line_number}.")
        os.environ.setdefault(name.strip(), value.strip())


@dataclass(frozen=True)
class Config:
    channel_id: str
    write_api_key: str | None = None
    read_api_key: str | None = None

    @classmethod
    def from_environment(cls, operation: str) -> Config:
        channel_id = os.getenv("THINGSPEAK_CHANNEL_ID", "").strip()
        if not channel_id:
            raise ThingSpeakError(
                "Falta THINGSPEAK_CHANNEL_ID. Configura las variables del archivo .env."
            )
        if not channel_id.isdecimal():
            raise ThingSpeakError("THINGSPEAK_CHANNEL_ID debe contener solo dígitos.")

        write_key = os.getenv("THINGSPEAK_WRITE_API_KEY", "").strip() or None
        read_key = os.getenv("THINGSPEAK_READ_API_KEY", "").strip() or None
        if operation in {"write", "samples"} and not write_key:
            raise ThingSpeakError("Falta THINGSPEAK_WRITE_API_KEY para esta operación.")
        if operation == "read" and not read_key:
            raise ThingSpeakError("Falta THINGSPEAK_READ_API_KEY para esta operación.")
        return cls(channel_id, write_key, read_key)


class ThingSpeakClient:
    def __init__(self, config: Config, timeout: float = 15.0) -> None:
        self.config = config
        self.timeout = timeout

    def write(self, values: tuple[float, float, float, float, float]) -> int:
        write_key = self.config.write_api_key
        if not write_key:
            raise ThingSpeakError("Falta la clave API de escritura.")
        fields = {f"field{i}": value for i, value in enumerate(values, start=1)}
        form = {"api_key": write_key, **fields}
        payload = self._request(
            f"{API_BASE}/update.json",
            data=urlencode(form).encode("ascii"),
        )
        try:
            response = json.loads(payload)
            entry_id = int(response["entry_id"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ThingSpeakError("ThingSpeak devolvió una respuesta de escritura no válida.") from exc
        if entry_id <= 0:
            raise ThingSpeakError(
                "ThingSpeak rechazó la escritura (entry_id 0). Comprueba la clave y "
                "el intervalo mínimo del canal."
            )
        return entry_id

    def read(self, results: int) -> list[dict[str, Any]]:
        read_key = self.config.read_api_key
        if not read_key:
            raise ThingSpeakError("Falta la clave API de lectura.")
        query = urlencode({"api_key": read_key, "results": results})
        url = f"{API_BASE}/channels/{self.config.channel_id}/feeds.json?{query}"
        payload = self._request(url)
        try:
            response = json.loads(payload)
            feeds = response["feeds"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ThingSpeakError("ThingSpeak devolvió una respuesta de lectura no válida.") from exc
        if not isinstance(feeds, list):
            raise ThingSpeakError("ThingSpeak devolvió una lista de mediciones no válida.")
        return feeds

    def _request(self, url: str, data: bytes | None = None) -> str:
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(url, data=data, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            raise ThingSpeakError(
                f"ThingSpeak respondió con HTTP {exc.code}. Comprueba el canal y las claves API."
            ) from exc
        except URLError as exc:
            raise ThingSpeakError(
                "No se pudo conectar con ThingSpeak. Comprueba la conexión a Internet."
            ) from exc
        except TimeoutError as exc:
            raise ThingSpeakError("La conexión con ThingSpeak agotó el tiempo de espera.") from exc


def parse_interval(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("El intervalo debe ser un número de segundos.") from exc
    if interval < MINIMUM_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(
            f"El intervalo mínimo configurado es {MINIMUM_INTERVAL_SECONDS} segundos."
        )
    return interval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lee y escribe las mediciones del reactor experimental en ThingSpeak."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    read_parser = commands.add_parser("read", help="Leer las últimas mediciones del canal.")
    read_parser.add_argument(
        "--results", type=int, default=10, help="Número de mediciones que se mostrarán (1-8000)."
    )

    write_parser = commands.add_parser("write", help="Enviar una medición al canal.")
    for field, label in enumerate(FIELDS, start=1):
        write_parser.add_argument(
            f"--field{field}",
            type=float,
            required=True,
            metavar="VALOR",
            help=label.replace("%", "%%"),
        )

    samples_parser = commands.add_parser(
        "samples", help="Enviar las diez muestras del enunciado, respetando el intervalo."
    )
    samples_parser.add_argument(
        "--interval",
        type=parse_interval,
        default=float(MINIMUM_INTERVAL_SECONDS),
        help=f"Segundos entre escrituras (mínimo {MINIMUM_INTERVAL_SECONDS}).",
    )

    commands.add_parser("preview", help="Mostrar las diez muestras sin conectarse a ThingSpeak.")

    web_parser = commands.add_parser("web", help="Abrir el panel web de monitorización.")
    web_parser.add_argument("--port", type=int, default=8000, help="Puerto del servidor web.")
    return parser


def _validate_values(values: tuple[float, ...]) -> tuple[float, float, float, float, float]:
    if len(values) != 5:
        raise ThingSpeakError("Se requieren exactamente cinco valores.")
    for i, value in enumerate(values, start=1):
        if not math.isfinite(value):
            raise ThingSpeakError(f"field{i} debe ser un número finito.")
    for i, value in enumerate(values[:3], start=1):
        if not 0 <= value <= 100:
            raise ThingSpeakError(f"field{i} debe estar entre 0 y 100.")
    if values[3] < 0:
        raise ThingSpeakError("field4 (latencia) no puede ser negativo.")
    if values[4] < 0:
        raise ThingSpeakError("field5 (consumo) no puede ser negativo.")
    return values


def _values_from_args(args: argparse.Namespace) -> tuple[float, float, float, float, float]:
    values = tuple(getattr(args, f"field{i}") for i in range(1, 6))
    return _validate_values(values)


def create_web_server(port: int) -> ThreadingHTTPServer:
    _load_environment_file()
    config = Config.from_environment("read")
    if not config.write_api_key:
        raise ThingSpeakError("Falta THINGSPEAK_WRITE_API_KEY para esta operación.")
    client = ThingSpeakClient(config)

    class ThingSpeakRequestHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            response = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(response)

        def do_GET(self) -> None:
            parsed_url = urlsplit(self.path)
            if parsed_url.path in {"/", "/index.html"}:
                try:
                    response = WEB_PAGE.read_bytes()
                except OSError:
                    self._send_json(500, {"error": "No se pudo cargar la página del panel."})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(response)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(response)
                return

            if parsed_url.path != "/api/feeds":
                self._send_json(404, {"error": "Ruta no encontrada."})
                return

            query = parse_qs(parsed_url.query)
            try:
                result_values = query.get("results", ["10"])
                if len(result_values) != 1:
                    raise ValueError
                results = int(result_values[0])
                if not 1 <= results <= 8000:
                    raise ValueError
            except ValueError:
                self._send_json(400, {"error": "results debe estar entre 1 y 8000."})
                return

            try:
                feeds = client.read(results)
            except ThingSpeakError as exc:
                self._send_json(502, {"error": str(exc)})
                return
            self._send_json(200, {"feeds": feeds, "channel_id": client.config.channel_id})

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/api/write":
                self._send_json(404, {"error": "Ruta no encontrada."})
                return
            if self.headers.get_content_type() != "application/json":
                self._send_json(415, {"error": "El contenido debe ser JSON."})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"error": "Longitud de petición no válida."})
                return
            if not 1 <= content_length <= 16_384:
                self._send_json(413, {"error": "Tamaño de petición no válido."})
                return
            try:
                payload = json.loads(self.rfile.read(content_length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(400, {"error": "El cuerpo debe contener JSON válido."})
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "Se esperaba un objeto con cinco valores."})
                return

            raw_values = [payload.get(f"field{i}") for i in range(1, 6)]
            if any(type(value) not in (int, float) for value in raw_values):
                self._send_json(400, {"error": "Completa los cinco campos con valores numéricos."})
                return
            try:
                values = tuple(float(value) for value in raw_values)
                values = _validate_values(values)
            except (ThingSpeakError, OverflowError) as exc:
                message = str(exc) or "Los valores deben ser números finitos."
                self._send_json(400, {"error": message})
                return
            try:
                entry_id = client.write(values)
            except ThingSpeakError as exc:
                self._send_json(502, {"error": str(exc)})
                return
            self._send_json(200, {"entry_id": entry_id})

    return ThreadingHTTPServer(("127.0.0.1", port), ThingSpeakRequestHandler)


def _print_feeds(feeds: list[dict[str, Any]]) -> None:
    if not feeds:
        print("El canal todavía no contiene mediciones.")
        return
    print(f"Mediciones recibidas: {len(feeds)}")
    for feed in feeds:
        print(f"\nFecha UTC: {feed.get('created_at', 'sin fecha')}")
        for index, label in enumerate(FIELDS, start=1):
            print(f"  field{index} — {label}: {feed.get(f'field{index}', 'sin dato')}")


def run(args: argparse.Namespace) -> int:
    if args.command == "preview":
        print("Muestras del enunciado (sin envío):")
        for index, row in enumerate(SAMPLES, start=1):
            values = ", ".join(f"field{i}={value}" for i, value in enumerate(row, start=1))
            print(f"{index:02d}. {values}")
        return 0

    if args.command == "web":
        server = create_web_server(args.port)
        print(f"Panel disponible en http://127.0.0.1:{args.port} (Ctrl+C para detener).")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor detenido.")
        finally:
            server.server_close()
        return 0

    if args.command == "read" and not 1 <= args.results <= 8000:
        raise ThingSpeakError("--results debe estar entre 1 y 8000.")

    _load_environment_file()
    config = Config.from_environment(args.command)
    client = ThingSpeakClient(config)
    if args.command == "read":
        _print_feeds(client.read(args.results))
    elif args.command == "write":
        entry_id = client.write(_values_from_args(args))
        print(f"Medición guardada correctamente. entry_id={entry_id}")
    else:
        print(f"Enviando {len(SAMPLES)} muestras al canal {config.channel_id}.")
        for index, row in enumerate(SAMPLES):
            entry_id = client.write(row)
            print(f"Muestra {index + 1}/{len(SAMPLES)} guardada. entry_id={entry_id}")
            if index < len(SAMPLES) - 1:
                time.sleep(args.interval)
        print("Carga completada.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except ThingSpeakError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error al iniciar el servidor web: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
