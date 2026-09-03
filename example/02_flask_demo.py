# -*- coding:utf-8 -*-

"""
Example: Flask zero-config mDNS integration.
Run with:
    python 02_flask_demo.py
"""

from flask import Flask, jsonify
from yyds_mdns import MDNS

app = Flask(__name__)

# Register Flask app: Accessible at http://flask-demo.local:5000
mdns = MDNS(app, name="flask-demo", port=5000)


@app.route("/")
def index():
    return jsonify(
        {
            "status": "ok",
            "message": "Hello from yyds-mdns Flask server!",
            "access_via": mdns.url,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
