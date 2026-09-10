# Diskwala Web Extractor — Python/Flask

A small website that accepts a Diskwala URL and calls the Diskwala extraction API from the server.

## 1. Install Python

Use Python 3.10+.

## 2. Create a virtual environment

Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 3. Install packages

```bash
pip install -r requirements.txt
```

## 4. Add your API key

Copy `.env.example` to `.env` and put your purchased API key in:

```text
DISKWALA_API_KEY=YOUR_REAL_KEY
```

The Python app reads the key from the environment. It is NOT exposed to browser JavaScript.

## 5. Run

```bash
python app.py
```

Open:

http://127.0.0.1:5000

## API flow

Browser:
POST /api/extract
{
  "url": "https://www.diskwala.com/video/..."
}

Python server:
POST https://api.teraboxdl.site/api/v1/diskwala/extract
Authorization: Bearer YOUR_API_KEY

The server then returns the provider's download/stream URLs to your browser.

## Important

Use the API only for content you are authorized to access/download and follow the provider's terms and applicable copyright law.

For production, run behind HTTPS and a production WSGI server such as Gunicorn. Do not commit `.env` or your API key to Git.
