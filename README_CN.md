# yyds-mdns: 极简零配置的局域网 mDNS 服务广播与发现库

<p align="center">
  <a href="https://pypi.org/project/yyds-mdns/"><img src="https://img.shields.io/pypi/v/yyds-mdns.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/yyds-mdns/"><img src="https://img.shields.io/pypi/pyversions/yyds-mdns.svg" alt="Python Versions"></a>
  <a href="https://github.com/yyds-fast/yyds-mdns/blob/main/LICENSE"><img src="https://img.shields.io/github/license/yyds-fast/yyds-mdns.svg" alt="License"></a>
</p>

`yyds-mdns` 是专为 Python Web 框架（FastAPI, Flask 等）及独立服务打造的**一行代码、零侵入、零配置** mDNS (Multicast DNS / Zeroconf) 服务注册与发现库。

解决在家庭/局域网开发、树莓派、嵌入式 IoT、私有服务器部署时，因路由器 DHCP 动态分配 IP 导致的频繁查 IP、改配置的痛点。

只需一行代码，自动将 `http://192.168.x.x:8000` 映射为 `http://my-service.local:8000`！

---

## ✨ 核心特性

- ⚡ **一行代码集成**：针对 FastAPI / Starlette / Flask 深度适配，无缝挂载生命周期。
- 🌐 **真正的主机名解析**：不仅支持 DNS-SD 服务广播，且严格组装 A/AAAA 记录，局域网内浏览器可直接键入域名访问。
- 🧠 **智能网卡与 IP 探测**：自适应出口路由探测，自动过滤 `127.0.0.1`、Docker 虚机桥接网卡及未配置的链路本地地址。
- 🛡️ **优雅退出与 Goodbye 广播**：在应用关闭或捕获 `SIGINT` / `SIGTERM` / Ctrl+C 时，自动向局域网广播 TTL=0 注销包，杜绝 DNS 缓存污染。
- 🔒 **多 Worker 防冲突锁**：支持 `uvicorn --workers 4` 或 `gunicorn` 多进程部署，自动防止多 Worker 同名广播冲突。
- 🔍 **轻量客户端解析器**：内置 `MDNSResolver`，可快速在 Python 代码中扫描局域网服务或解析指定 `.local` 设备。
- 💻 **独立 CLI 命令行工具**：提供 `yyds-mdns run`，让任意非 Python 服务（如 Ollama、MinIO、Docker 端口）即刻拥有局域网域名。

---

## 📦 安装

```bash
pip install yyds-mdns
```

无需安装任何特定框架的适配插件，天然零侵入支持你项目中已安装的任意 ASGI 或 WSGI 框架。

---

## 🚀 快速上手：大一统使用体验

无论你使用的是 **FastAPI**、**Flask**、**Django**、**Sanic** 还是纯 Python 服务，**全局统一仅需导入一个接口**：

```python
from yyds_mdns import MDNS
```

---

### 1. FastAPI 模式（一行挂载）

```python
import uvicorn
from fastapi import FastAPI
from yyds_mdns import MDNS

app = FastAPI()

# 一行代码挂载：启动后局域网设备直接访问 http://my-api.local:8000
MDNS(app, name="my-api", port=8000)


@app.get("/")
def read_root():
    return {"message": "Hello from mDNS!"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

> **原理**：自动挂载 FastAPI 现代的 `lifespan` 异步上下文。若原有业务已有自定义 lifespan，会自动无缝合并。

---

### 2. Flask 模式

```python
from flask import Flask
from yyds_mdns import MDNS

app = Flask(__name__)

# 初始化 Flask：局域网设备直接访问 http://flask-app.local:5000
MDNS(app, name="flask-app", port=5000)


@app.route("/")
def index():
    return "Hello Flask from mDNS!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

---

### 3. Django 模式（在 wsgi.py 或 asgi.py 中一行挂载）

```python
from django.core.wsgi import get_wsgi_application
from yyds_mdns import MDNS

application = get_wsgi_application()

# 一行挂载：局域网设备直接访问 http://my-django.local:8000
MDNS(application, name="my-django", port=8000)
```

---

### 4. 通用 Python 模式（适用于任意服务 / 上下文管理器 / 装饰器）

适用于 Sanic、Tornado、标准库 `http.server` 或自定义 TCP 服务：

```python
from yyds_mdns import MDNS

# 方式 1：同步上下文管理器（离开上下文自动广播 Goodbye 包下线）
with MDNS(name="my-tcp-node", port=9090) as server:
    print(f"Service running on {server.url} (LAN IP: {server.ip})")
    run_my_server()

# 方式 2：异步上下文管理器
async with MDNS(name="async-service", port=8080):
    await run_async_server()


# 方式 3：函数/协程装饰器
@MDNS(name="decorated-job", port=8888)
def run_job():
    serve_traffic()
```

---

### 4. 客户端局域网服务发现与域名解析

```python
from yyds_mdns import MDNSResolver

# 1. 扫描局域网所有在线的 HTTP 服务
services = MDNSResolver.discover(service_type="_http._tcp.local.", timeout=2.0)
for s in services:
    print(s["name"], "->", s["url"], s["ip"])

# 2. 精确解析指定的 .local 服务
res = MDNSResolver.resolve("my-api")
if res:
    print(f"Found IP: {res['ip']}, Port: {res['port']}")
```

---

### 5. CLI 命令行工具

无需编写任何 Python 代码，即可直接把本地运行的任意端口映射为 mDNS 局域网域名：

```bash
# 1. 广播本地端口（如 Ollama 或本地 Web 站点）
yyds-mdns run --name my-ollama --port 11434

# 2. 扫描局域网 mDNS 服务
yyds-mdns scan

# 3. 快速解析域名
yyds-mdns resolve my-api

# 4. 解析并在系统默认浏览器中一键打开
yyds-mdns open my-api

# 5. 查看当前被智能选中的局域网 IP
yyds-mdns ip
```

---

## ⚙️ 高级配置参数

`MDNS(app, ...)` 及 `MDNSServer(...)` 支持以下可选参数：

| 参数名 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `name` | `str` | `None` (智能推导) | 域名/服务前缀，省略时自动根据 `app.title`、`app.name` 或运行脚本名推导 |
| `port` | `int` | `None` (智能推导) | 服务对外端口，省略时自动从 `PORT` 环境变量读取或框架默认（Flask 5000, ASGI 8000） |
| `ip` | `str` | `None` (自动探测) | 手动指定广播的 IPv4 地址 |
| `interface` | `str` | `None` (智能优选) | 手动指定绑定的物理网卡名（如 `"eno1"`、`"eth0"`） |
| `protocol` | `str` | `"_http._tcp.local."` | DNS-SD 服务类型，如 `"_https._tcp.local."` |
| `properties` | `dict` | `None` | 附加在 TXT 记录中的元数据字典 |
| `use_worker_lock`| `bool` | `True` | 是否启用多 Worker 进程互斥锁，避免冲突 |
| `verbose` | `bool` | `True` | 是否在终端输出高亮访问域名提示与注销通知 |

---

## 🛠️ 本地构建与测试

本项目对齐 `yyds-` 家族统一规范：

```bash
# 运行单元测试
python -m unittest discover -s tests -v

# 一键构建与打包检查
./build.sh
```

---

## 📄 开源许可证

本项目采用 [MIT License](LICENSE) 授权协议。
Copyright (c) 2026 yyds-fast.
