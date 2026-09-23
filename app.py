import os
import json
from urllib.parse import urlparse, quote

import requests
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    Response,
)
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

API_URL = os.getenv(
    "DISKWALA_API_URL",
    "https://api.teraboxdl.site/api/v1/diskwala/extract",
)

API_KEY = os.getenv("DISKWALA_API_KEY", "")
TIMEOUT = int(os.getenv("DISKWALA_TIMEOUT", "30"))

ALLOWED_HOSTS = {
    "diskwala.com",
    "www.diskwala.com",
}

# Provider media hosts allowed for proxying.
#
# Your current Diskwala response uses dragonplayer.online
# for the direct media URL.
#
# If the provider changes its media host later, add it here
# or set DISKWALA_PROXY_HOSTS in Render environment variables.
DEFAULT_PROXY_HOSTS = {
    "dragonplayer.online",
    "www.dragonplayer.online",
}

env_proxy_hosts = os.getenv("DISKWALA_PROXY_HOSTS", "").strip()

if env_proxy_hosts:
    PROXY_ALLOWED_HOSTS = {
        host.strip().lower()
        for host in env_proxy_hosts.split(",")
        if host.strip()
    }
else:
    PROXY_ALLOWED_HOSTS = DEFAULT_PROXY_HOSTS


def valid_diskwala_url(value: str) -> bool:
    try:
        p = urlparse(value.strip())

        return (
            p.scheme in {"http", "https"}
            and p.hostname in ALLOWED_HOSTS
        )

    except Exception:
        return False


def valid_proxy_url(value: str) -> bool:
    """
    Only allow HTTPS media URLs from known provider media hosts.
    This prevents the proxy endpoint from becoming an open SSRF proxy.
    """
    try:
        p = urlparse(value.strip())

        hostname = (p.hostname or "").lower()

        return (
            p.scheme == "https"
            and hostname in PROXY_ALLOWED_HOSTS
        )

    except Exception:
        return False


def extract_links(data):
    """Recursively find download/stream URLs and metadata."""

    result = {
        "download_url": None,
        "stream_url": None,
        "file_name": None,
        "thumbnail": None,
    }

    download_keys = {
        "download_url",
        "downloadurl",
        "direct_url",
        "direct_link",
        "download_link",
        "download",
        "cdn_url",
        "url",
        "link",
    }

    stream_keys = {
        "stream_url",
        "streamurl",
        "hls_url",
        "hls",
        "m3u8",
        "m3u8_url",
        "stream",
    }

    name_keys = {
        "name",
        "file_name",
        "filename",
        "title",
    }

    thumbnail_keys = {
        "thumbnail",
        "thumbnail_url",
        "thumb",
        "thumb_url",
    }

    all_urls = []

    def scan(obj):
        if isinstance(obj, dict):

            for key, value in obj.items():

                key_lower = str(key).strip().lower()

                if isinstance(value, str):

                    value = value.strip()

                    if not value:
                        continue

                    # Some APIs put JSON inside a string.
                    if value[:1] in ("{", "["):
                        try:
                            scan(json.loads(value))
                            continue
                        except Exception:
                            pass

                    if value.startswith(("http://", "https://")):
                        all_urls.append(value)

                    if (
                        key_lower in stream_keys
                        and result["stream_url"] is None
                    ):
                        result["stream_url"] = value

                    elif (
                        key_lower in download_keys
                        and result["download_url"] is None
                    ):
                        result["download_url"] = value

                    elif (
                        key_lower in name_keys
                        and result["file_name"] is None
                    ):
                        result["file_name"] = value

                    elif (
                        key_lower in thumbnail_keys
                        and result["thumbnail"] is None
                    ):
                        result["thumbnail"] = value

                elif isinstance(value, (dict, list)):
                    scan(value)

        elif isinstance(obj, list):

            for item in obj:
                scan(item)

    scan(data)

    # Fallback for HLS/stream URLs.
    for value in all_urls:

        lower = value.lower()

        if result["stream_url"] is None and (
            ".m3u8" in lower
            or "m3u8" in lower
            or "/hls" in lower
            or "stream" in lower
        ):
            result["stream_url"] = value
            break

    # Fallback for download URL.
    for value in all_urls:

        lower = value.lower()

        if (
            result["download_url"] is None
            and value != result["stream_url"]
            and not (
                "thumbnail" in lower
                or "thumb" in lower
                or ".m3u8" in lower
                or "m3u8" in lower
                or "/hls" in lower
            )
        ):
            result["download_url"] = value
            break

    return result


def safe_debug(obj):
    """Redact URLs/tokens before printing provider response."""

    if isinstance(obj, dict):

        out = {}

        for key, value in obj.items():

            if (
                isinstance(value, str)
                and value.startswith(("http://", "https://"))
            ):
                out[key] = "[URL REDACTED]"

            elif isinstance(value, (dict, list)):
                out[key] = safe_debug(value)

            else:
                out[key] = value

        return out

    if isinstance(obj, list):
        return [safe_debug(x) for x in obj]

    return obj


def make_proxy_url(endpoint, source_url):
    """
    Convert provider media URL into our own Render proxy URL.
    """

    return (
        request.host_url.rstrip("/")
        + endpoint
        + "?url="
        + quote(source_url, safe="")
    )


def proxy_request(source_url, allow_range=True):
    """
    Open provider URL and return a streaming Flask response.

    Supports HTTP Range requests for video playback/seeking.
    """

    if not valid_proxy_url(source_url):
        return None, (
            jsonify({
                "ok": False,
                "error": "Media URL host is not allowed."
            }),
            403,
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    if allow_range:
        range_header = request.headers.get("Range")

        if range_header:
            headers["Range"] = range_header

    try:
        upstream = requests.get(
            source_url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=TIMEOUT,
        )

    except requests.Timeout:
        return None, (
            jsonify({
                "ok": False,
                "error": "Media server timed out."
            }),
            504,
        )

    except requests.RequestException as exc:
        print(
            "MEDIA PROXY ERROR:",
            str(exc),
            flush=True,
        )

        return None, (
            jsonify({
                "ok": False,
                "error": "Could not connect to media server."
            }),
            502,
        )

    if not upstream.ok and upstream.status_code != 206:

        status = upstream.status_code

        upstream.close()

        return None, (
            jsonify({
                "ok": False,
                "error": f"Media server returned HTTP {status}."
            }),
            502,
        )

    response_headers = {}

    copy_headers = {
        "Content-Type",
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "ETag",
        "Last-Modified",
        "Cache-Control",
    }

    for key in copy_headers:

        value = upstream.headers.get(key)

        if value:
            response_headers[key] = value

    def generate():

        try:

            for chunk in upstream.iter_content(
                chunk_size=64 * 1024
            ):

                if chunk:
                    yield chunk

        finally:
            upstream.close()

    return Response(
        generate(),
        status=upstream.status_code,
        headers=response_headers,
        direct_passthrough=True,
    ), None


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/extract")
def extract():

    if not API_KEY:

        return jsonify({
            "ok": False,
            "error": (
                "DISKWALA_API_KEY is not configured "
                "on the server."
            ),
        }), 500

    body = request.get_json(silent=True) or {}

    url = str(
        body.get("url", "")
    ).strip()

    if not valid_diskwala_url(url):

        return jsonify({
            "ok": False,
            "error": "Please enter a valid Diskwala URL.",
        }), 400

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

        return jsonify({
            "ok": False,
            "error": "API request timed out.",
        }), 504

    except requests.RequestException as exc:

        return jsonify({
            "ok": False,
            "error": (
                "Could not connect to the extraction API: "
                f"{exc}"
            ),
        }), 502

    try:

        data = response.json()

    except ValueError:

        return jsonify({
            "ok": False,
            "error": (
                f"Provider returned HTTP "
                f"{response.status_code} "
                "with a non-JSON response."
            ),
        }), 502

    print(
        "\n========== DISKWALA API DEBUG =========="
    )

    print(
        "HTTP STATUS:",
        response.status_code,
    )

    print("RESPONSE:")

    print(
        json.dumps(
            safe_debug(data),
            indent=2,
            ensure_ascii=False,
        )
    )

    print(
        "========================================\n",
        flush=True,
    )

    if not response.ok:

        provider_error = (
            data.get("error")
            if isinstance(data, dict)
            else None
        )

        if (
            not provider_error
            and isinstance(data, dict)
        ):
            provider_error = data.get("message")

        if not provider_error:
            provider_error = (
                f"Provider returned HTTP "
                f"{response.status_code}."
            )

        return jsonify({
            "ok": False,
            "error": provider_error,
        }), response.status_code

    links = extract_links(data)

    if (
        not links["download_url"]
        and not links["stream_url"]
    ):

        return jsonify({
            "ok": False,
            "error": (
                "Extraction succeeded, but no "
                "download/stream URL was found."
            ),
            "provider_response": data,
        }), 502

    #
    # If provider gives only a download URL,
    # use that URL for streaming as well.
    #
    original_media_url = (
        links["stream_url"]
        or links["download_url"]
    )

    #
    # Build our own proxy URLs.
    #
    proxy_download_url = None
    proxy_stream_url = None
    proxy_thumbnail_url = None

    if links["download_url"]:

        if valid_proxy_url(
            links["download_url"]
        ):

            proxy_download_url = make_proxy_url(
                "/api/download",
                links["download_url"],
            )

    if original_media_url:

        if valid_proxy_url(
            original_media_url
        ):

            proxy_stream_url = make_proxy_url(
                "/api/stream",
                original_media_url,
            )

    if links["thumbnail"]:

        if valid_proxy_url(
            links["thumbnail"]
        ):

            proxy_thumbnail_url = make_proxy_url(
                "/api/thumbnail",
                links["thumbnail"],
            )
        else:
            #
            # If thumbnail host is different from the
            # media host, keep the original URL.
            #
            proxy_thumbnail_url = links["thumbnail"]

    #
    # If provider URL isn't on our proxy allowlist,
    # keep original download URL so extraction still works.
    #
    final_download_url = (
        proxy_download_url
        or links["download_url"]
    )

    final_stream_url = (
        proxy_stream_url
        or links["stream_url"]
        or final_download_url
    )

    return jsonify({
        "ok": True,

        "download_url": final_download_url,

        "stream_url": final_stream_url,

        "file_name": links["file_name"],

        "thumbnail": proxy_thumbnail_url,

    })


@app.get("/api/stream")
def stream_media():

    source_url = request.args.get(
        "url",
        "",
        type=str,
    ).strip()

    if not source_url:

        return jsonify({
            "ok": False,
            "error": "Missing media URL.",
        }), 400

    response, error = proxy_request(
        source_url,
        allow_range=True,
    )

    if error:
        return error

    return response


@app.get("/api/download")
def download_media():

    source_url = request.args.get(
        "url",
        "",
        type=str,
    ).strip()

    if not source_url:

        return jsonify({
            "ok": False,
            "error": "Missing download URL.",
        }), 400

    response, error = proxy_request(
        source_url,
        allow_range=False,
    )

    if error:
        return error

    file_name = request.args.get(
        "filename",
        "LinkWala_Download.mp4",
        type=str,
    ).strip()

    #
    # Prevent dangerous header characters.
    #
    file_name = (
        file_name
        .replace("\r", "")
        .replace("\n", "")
        .replace('"', "")
    )

    response.headers["Content-Disposition"] = (
        f'attachment; filename="{file_name}"'
    )

    return response


@app.get("/api/thumbnail")
def thumbnail_media():

    source_url = request.args.get(
        "url",
        "",
        type=str,
    ).strip()

    if not source_url:

        return jsonify({
            "ok": False,
            "error": "Missing thumbnail URL.",
        }), 400

    response, error = proxy_request(
        source_url,
        allow_range=False,
    )

    if error:
        return error

    response.headers["Cache-Control"] = (
        "public, max-age=3600"
    )

    return response


@app.get("/health")
def health():

    return jsonify({
        "ok": True,
    })


if __name__ == "__main__":

    print(
        "RUNNING APP:",
        os.path.abspath(__file__),
    )

    print(
        "API URL:",
        API_URL,
    )

    print(
        "PROXY HOSTS:",
        sorted(PROXY_ALLOWED_HOSTS),
    )

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv("PORT", "5000")
        ),
        debug=(
            os.getenv(
                "FLASK_DEBUG",
                "0",
            )
            == "1"
        ),
    )