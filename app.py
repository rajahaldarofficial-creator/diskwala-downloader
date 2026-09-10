import os
import json
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

API_URL = os.getenv(
    "DISKWALA_API_URL",
    "https://api.teraboxdl.site/api/v1/diskwala/extract",
)
API_KEY = os.getenv("DISKWALA_API_KEY", "")
TIMEOUT = int(os.getenv("DISKWALA_TIMEOUT", "30"))

ALLOWED_HOSTS = {"diskwala.com", "www.diskwala.com"}


def valid_diskwala_url(value: str) -> bool:
    try:
        p = urlparse(value.strip())
        return p.scheme in {"http", "https"} and p.hostname in ALLOWED_HOSTS
    except Exception:
        return False


def extract_links(data):
    """Recursively find download/stream URLs and metadata in the full API response."""
    result = {
        "download_url": None,
        "stream_url": None,
        "file_name": None,
        "thumbnail": None,
    }

    download_keys = {
        "download_url", "downloadurl", "direct_url", "direct_link",
        "download_link", "download", "cdn_url", "url", "link",
    }
    stream_keys = {
        "stream_url", "streamurl", "hls_url", "hls", "m3u8",
        "m3u8_url", "stream",
    }
    name_keys = {"name", "file_name", "filename", "title"}
    thumbnail_keys = {"thumbnail", "thumbnail_url", "thumb", "thumb_url"}
    all_urls = []

    def scan(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_lower = str(key).strip().lower()

                if isinstance(value, str):
                    value = value.strip()
                    if not value:
                        continue

                    # Some APIs put JSON inside a string field.
                    if value[:1] in ("{", "["):
                        try:
                            scan(json.loads(value))
                            continue
                        except Exception:
                            pass

                    if value.startswith(("http://", "https://")):
                        all_urls.append(value)

                    if key_lower in stream_keys and result["stream_url"] is None:
                        result["stream_url"] = value
                    elif key_lower in download_keys and result["download_url"] is None:
                        result["download_url"] = value
                    elif key_lower in name_keys and result["file_name"] is None:
                        result["file_name"] = value
                    elif key_lower in thumbnail_keys and result["thumbnail"] is None:
                        result["thumbnail"] = value

                elif isinstance(value, (dict, list)):
                    scan(value)

        elif isinstance(obj, list):
            for item in obj:
                scan(item)

    scan(data)

    # Fallback when provider changes field names.
    for value in all_urls:
        lower = value.lower()
        if result["stream_url"] is None and (
            ".m3u8" in lower or "m3u8" in lower or "/hls" in lower or "stream" in lower
        ):
            result["stream_url"] = value
            break

    for value in all_urls:
        lower = value.lower()
        if result["download_url"] is None and value != result["stream_url"] and not (
            "thumbnail" in lower or "thumb" in lower or ".m3u8" in lower
            or "m3u8" in lower or "/hls" in lower
        ):
            result["download_url"] = value
            break

    return result


def safe_debug(obj):
    """Redact URLs/tokens before printing provider response to the terminal."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                out[key] = "[URL REDACTED]"
            elif isinstance(value, (dict, list)):
                out[key] = safe_debug(value)
            else:
                out[key] = value
        return out
    if isinstance(obj, list):
        return [safe_debug(x) for x in obj]
    return obj


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/extract")
def extract():
    if not API_KEY:
        return jsonify({
            "ok": False,
            "error": "DISKWALA_API_KEY is not configured on the server."
        }), 500

    body = request.get_json(silent=True) or {}
    url = str(body.get("url", "")).strip()

    if not valid_diskwala_url(url):
        return jsonify({
            "ok": False,
            "error": "Please enter a valid Diskwala URL."
        }), 400

    # Your Diskwala dashboard specifies X-API-Key authentication.
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.post(
            API_URL,
            headers=headers,
            json={"url": url},
            timeout=TIMEOUT,
        )
    except requests.Timeout:
        return jsonify({"ok": False, "error": "API request timed out."}), 504
    except requests.RequestException as exc:
        return jsonify({
            "ok": False,
            "error": f"Could not connect to the extraction API: {exc}"
        }), 502

    try:
        data = response.json()
    except ValueError:
        return jsonify({
            "ok": False,
            "error": f"Provider returned HTTP {response.status_code} with a non-JSON response."
        }), 502

    # Safe terminal diagnostic. Actual URLs are hidden.
    print("\n========== DISKWALA API DEBUG ==========")
    print("HTTP STATUS:", response.status_code)
    print("RESPONSE:")
    print(json.dumps(safe_debug(data), indent=2, ensure_ascii=False))
    print("========================================\n", flush=True)

    if not response.ok:
        provider_error = data.get("error") if isinstance(data, dict) else None
        if not provider_error and isinstance(data, dict):
            provider_error = data.get("message")
        if not provider_error:
            provider_error = f"Provider returned HTTP {response.status_code}."
        return jsonify({"ok": False, "error": provider_error}), response.status_code

    links = extract_links(data)

    if not links["download_url"] and not links["stream_url"]:
        return jsonify({
            "ok": False,
            "error": "Extraction succeeded, but no download/stream URL was found.",
            "provider_response": data,
        }), 502

    return jsonify({
        "ok": True,
        **links,
        "provider_response": data,
    })


@app.get("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("RUNNING APP:", os.path.abspath(__file__))
    print("API URL:", API_URL)
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
