# yyds-mdns: Zero-Config mDNS Service Broadcaster & Discovery for Python

<p align="center">
  <a href="https://pypi.org/project/yyds-mdns/"><img src="https://img.shields.io/pypi/v/yyds-mdns.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/yyds-mdns/"><img src="https://img.shields.io/pypi/pyversions/yyds-mdns.svg" alt="Python Versions"></a>
  <a href="https://github.com/yyds-fast/yyds-mdns/blob/main/LICENSE"><img src="https://img.shields.io/github/license/yyds-fast/yyds-mdns.svg" alt="License"></a>
</p>

`yyds-mdns` is a lightweight, zero-configuration mDNS (Multicast DNS / Zeroconf) service registration and discovery library tailored for Python Web frameworks (FastAPI, Flask, Starlette, etc.) and standalone services.

Solve the annoyance of constantly changing DHCP IP addresses during local development, IoT, Raspberry Pi, or private server deployments.

Map `http://192.168.x.x:8000` to `http://my-service.local:8000` with just **one line of code**!

---

## ✨ Features

- ⚡ **One-Line Integration**: Seamless lifespan hook for FastAPI / Starlette and Flask extensions.
- 🌐 **Real Hostname Resolution**: Assembles both DNS-SD and A/AAAA host records so LAN browsers can directly navigate to `.local` domains.
- 🧠 **Smart Interface & IP Detection**: Automatically probes the OS outbound routing table and filters out loopback, unconfigured link-local, and Docker virtual bridges.
- 🛡️ **Graceful Shutdown (Goodbye Packet)**: Automatically broadcasts TTL=0 goodbye packets upon SIGINT/SIGTERM or normal exit to avoid stale DNS caching on clients.
- 🔒 **Multi-Worker Process Lock**: Safe under `uvicorn --workers 4` or Gunicorn deployments by ensuring only one process holds the broadcast.
- 🔍 **Lightweight Resolver & Scanner**: Built-in `MDNSResolver` to scan LAN services or resolve other `.local` devices.
- 💻 **Standalone CLI Tool**: Provides `yyds-mdns run` for non-Python applications (e.g. Ollama, MinIO, Docker containers).

---

## 📦 Installation

```bash
pip install yyds-mdns
```

No framework-specific plugins needed. It natively works out-of-the-box with any installed ASGI or WSGI framework.

---

## 🚀 Quick Start: Universal Experience
 
Whether you use **FastAPI**, **Flask**, **Django**, **Sanic**, or standalone Python scripts, **you only ever need to import a single entrypoint**:

```python
from yyds_mdns import MDNS
```

---

### 1. FastAPI (One-Liner)

```python
import uvicorn
from fastapi import FastAPI
from yyds_mdns import MDNS

app = FastAPI()

# One line to register: Accessible at http://my-api.local:8000
MDNS(app, name="my-api", port=8000)

@app.get("/")
def read_root():
    return {"message": "Hello from mDNS!"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

### 2. Flask

```python
from flask import Flask
from yyds_mdns import MDNS

app = Flask(__name__)

# Register Flask service: Accessible at http://flask-app.local:5000
MDNS(app, name="flask-app", port=5000)

@app.route("/")
def index():
    return "Hello Flask from mDNS!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

---

### 3. Django (In `wsgi.py` or `asgi.py`)

```python
from django.core.wsgi import get_wsgi_application
from yyds_mdns import MDNS

application = get_wsgi_application()

# Accessible at http://my-django.local:8000
MDNS(application, name="my-django", port=8000)
```

---

### 4. Universal Python Mode (Context Manager & Decorator)

Works with any TCP server, Sanic, Tornado, or standard library `http.server`:

```python
from yyds_mdns import MDNS

# Synchronous context manager
with MDNS(name="custom-node", port=9090) as server:
    print(f"Service live on {server.url} (IP: {server.ip})")
    run_server()

# Asynchronous context manager
async with MDNS(name="async-service", port=8080):
    await run_async_server()

# Function decorator
@MDNS(name="my-job", port=8888)
def serve():
    run_server()
```

---

### 4. Service Discovery & Resolution Client

```python
from yyds_mdns import MDNSResolver

# 1. Discover all active HTTP services in LAN
services = MDNSResolver.discover(service_type="_http._tcp.local.", timeout=2.0)
for s in services:
    print(s["name"], "->", s["url"], s["ip"])

# 2. Resolve a specific .local host
res = MDNSResolver.resolve("my-api")
if res:
    print(f"IP: {res['ip']}, Port: {res['port']}")
```

---

### 5. Command-Line Interface (CLI)

Map any running port to a local domain without modifying code:

```bash
# Broadcast a local port (e.g. Ollama or Docker container)
yyds-mdns run --name my-ollama --port 11434

# Discover services in LAN
yyds-mdns scan

# Resolve a specific service
yyds-mdns resolve my-api

# Resolve and open directly in default browser
yyds-mdns open my-api

# Show detected LAN IP
yyds-mdns ip
```

---

## ⚙️ Configuration Parameters

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | `None` (auto-inferred) | Domain/service prefix, inferred from `app.title`, `app.name`, or filename |
| `port` | `int` | `None` (auto-inferred) | Service port, resolved from `PORT` env or framework defaults (Flask 5000, ASGI 8000) |
| `ip` | `str` | `None` (auto-detect) | Explicit IPv4 address override |
| `interface` | `str` | `None` (smart choice) | Explicit network interface binding (e.g. `"eno1"`, `"eth0"`) |
| `protocol` | `str` | `"_http._tcp.local."` | DNS-SD service type, e.g. `"_https._tcp.local."` |
| `properties` | `dict` | `None` | Key-value pairs stored in DNS TXT record |
| `use_worker_lock`| `bool` | `True` | Prevents multi-worker broadcast collision |
| `verbose` | `bool` | `True` | Whether to print startup banner and shutdown notice to terminal |

---

## 🛠️ Testing & Building

Aligned with the `yyds-fast` repository standard:

```bash
# Run unit tests
python -m unittest discover -s tests -v

# Run build & package verification
./build.sh
```

---

## 📄 License

Licensed under the [MIT License](LICENSE).
Copyright (c) 2026 yyds-fast.
