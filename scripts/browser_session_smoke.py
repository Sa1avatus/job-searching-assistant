from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet

from app.browser.engine import PlaywrightEngine
from app.browser.session_store import EncryptedBrowserStateStore


class SmokePageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"<!doctype html><title>browser session smoke</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


async def run_smoke() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), SmokePageHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with TemporaryDirectory() as temporary_directory:
            artifact_directory = Path(temporary_directory)
            store = EncryptedBrowserStateStore(
                artifact_directory,
                encryption_key=Fernet.generate_key().decode("ascii"),
                max_state_bytes=2_097_152,
            )
            async with PlaywrightEngine(
                artifact_directory=artifact_directory / "browser"
            ) as first_engine:
                first_page = await first_engine.new_page()
                assert (await first_engine.navigate(first_page, origin)).is_successful
                await first_page.evaluate("localStorage.setItem('recovery-proof', 'restored')")
                await first_page.context.add_cookies(
                    [{"name": "session-proof", "value": "restored", "url": origin}]
                )
                encrypted_state_path = store.save(await first_engine.storage_state())

            restored_state = store.load(encrypted_state_path)
            async with PlaywrightEngine(
                artifact_directory=artifact_directory / "browser-restored",
                storage_state=restored_state,
            ) as restored_engine:
                restored_page = await restored_engine.new_page()
                assert (await restored_engine.navigate(restored_page, origin)).is_successful
                local_storage_value = await restored_page.evaluate(
                    "localStorage.getItem('recovery-proof')"
                )
                cookies = await restored_page.context.cookies(origin)
                assert local_storage_value == "restored"
                assert any(
                    cookie["name"] == "session-proof" and cookie["value"] == "restored"
                    for cookie in cookies
                )
            print("browser session recovery smoke: ok")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


if __name__ == "__main__":
    asyncio.run(run_smoke())
