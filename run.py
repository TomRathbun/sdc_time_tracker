"""Entry point — run with: python run.py"""

import os
import uvicorn

if __name__ == "__main__":
    ssl_keyfile = "certs/key.pem"
    ssl_certfile = "certs/cert.pem"

    kwargs = {
        "app": "app.main:app",
        "host": "0.0.0.0",
        "port": 8888,
        "reload": True,
    }

    # Enable HTTPS if certs exist
    if os.path.exists(ssl_certfile) and os.path.exists(ssl_keyfile):
        kwargs["ssl_keyfile"] = ssl_keyfile
        kwargs["ssl_certfile"] = ssl_certfile
        print(f"🔒 Starting HTTPS server on https://0.0.0.0:8888")
    else:
        print(f"⚠  No certs found, starting HTTP server on http://0.0.0.0:8888")
        print(f"   Run 'uv run python gen_cert.py' to generate certs for HTTPS")

    uvicorn.run(**kwargs)
