"""Entry point — run with: uv run python run.py [--ssl | --no-ssl]"""

import argparse
import os
import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SDC Time Tracker server")
    parser.add_argument(
        "--ssl", dest="ssl", action="store_true", default=None,
        help="Force HTTPS (requires certs in certs/)",
    )
    parser.add_argument(
        "--no-ssl", dest="ssl", action="store_false",
        help="Force HTTP (no SSL)",
    )
    args = parser.parse_args()

    ssl_keyfile = "certs/key.pem"
    ssl_certfile = "certs/cert.pem"
    certs_exist = os.path.exists(ssl_certfile) and os.path.exists(ssl_keyfile)

    # Determine SSL mode: flag wins, otherwise auto-detect from certs
    use_ssl = args.ssl if args.ssl is not None else certs_exist

    kwargs = {
        "app": "app.main:app",
        "host": "0.0.0.0",
        "port": 8888,
        "reload": True,
    }

    if use_ssl:
        if not certs_exist:
            print("❌ SSL requested but certs not found!")
            print("   Run 'uv run python gen_cert.py' to generate them.")
            exit(1)
        kwargs["ssl_keyfile"] = ssl_keyfile
        kwargs["ssl_certfile"] = ssl_certfile
        print("🔒 Starting HTTPS server on https://0.0.0.0:8888")
    else:
        print("🌐 Starting HTTP server on http://0.0.0.0:8888")

    uvicorn.run(**kwargs)
