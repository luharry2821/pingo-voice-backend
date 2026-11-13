"""Command-line entry point: python -m server starts uvicorn with the FastAPI app (server/app.py).

The Lovable browser app connects to /ws.
"""

import argparse
import logging
import os

import uvicorn

from server.config import bind_address, load_dotenv, load_settings, normalize_target
from server.app import create_app


def main(argv=None):
    # Read .env (if present) before argparse so PINGO_CONFIG and the key are visible.
    """Parse the command line, load settings and the API key, then run the server.

    Settings come from --config (default configs/baseline.yaml), env vars and
    --set overrides; --log and --target-language override those.
    """
    load_dotenv()
    parser = argparse.ArgumentParser(description="Pingo voice tutor backend")
    parser.add_argument("--config", default=os.environ.get("PINGO_CONFIG", "configs/baseline.yaml"))
    parser.add_argument("--log", default=None, help="JSONL path. Appended to, never overwritten.")
    parser.add_argument("--target-language", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings(args.config, args.set)
    if args.log:
        settings["log"]["path"] = args.log
    if args.target_language:
        settings["target_language"] = normalize_target(args.target_language)
    # The key stays server-side; it is never sent to the browser.
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    # Server mode: listen where the config/PORT/--host/--port say.
    host, port = bind_address(settings, host=args.host, port=args.port)
    uvicorn.run(create_app(settings, api_key), host=host, port=port)


if __name__ == "__main__":
    main()
