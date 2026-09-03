# -*- coding:utf-8 -*-

"""
Example: FastAPI zero-config mDNS integration.
Run with:
    python 01_fastapi_demo.py
    # or: uvicorn 01_fastapi_demo:app --port 8000
"""

import uvicorn
from fastapi import FastAPI
from yyds_mdns import MDNS

app = FastAPI(title="mDNS FastAPI Demo")

# One line to register mDNS: Accessible at http://fastapi-demo.local:8000
MDNS(app, name="fastapi-demo", port=8000)


@app.get("/")
def index():
    return {
        "status": "ok",
        "message": "Hello from yyds-mdns FastAPI server!",
        "access_via": "http://fastapi-demo.local:8000",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
