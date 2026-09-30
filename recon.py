"""One-off recon script (Phase 1). Not part of the spider.
Fetches the SRP page via Zyte API with plain httpResponseBody first,
saves raw HTML for manual inspection.
"""
import base64
import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

ZYTE_API_KEY = os.environ["ZYTE_API_KEY"]
ZYTE_URL = "https://api.zyte.com/v1/extract"

SRP_URL = "https://properties.cityinfoservices.com/properties-for-rent-in-bangalore/srp"


def fetch(url, browser_html=False, extra=None):
    payload = {"url": url}
    if browser_html:
        payload["browserHtml"] = True
    else:
        payload["httpResponseBody"] = True
    if extra:
        payload.update(extra)
    resp = requests.post(
        ZYTE_URL,
        auth=(ZYTE_API_KEY, ""),
        json=payload,
        timeout=120,
    )
    print(f"HTTP {resp.status_code} for {url} (browserHtml={browser_html})", file=sys.stderr)
    resp.raise_for_status()
    data = resp.json()
    if browser_html:
        html = data.get("browserHtml", "")
    else:
        html = base64.b64decode(data["httpResponseBody"]).decode("utf-8", errors="replace")
    return html, data


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "httpResponseBody"
    url = sys.argv[2] if len(sys.argv) > 2 else SRP_URL
    outfile = sys.argv[3] if len(sys.argv) > 3 else "debug/srp_http.html"

    html, data = fetch(url, browser_html=(mode == "browserHtml"))
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved {len(html)} chars to {outfile}")
    # dump non-html keys for inspection
    meta = {k: v for k, v in data.items() if k not in ("httpResponseBody", "browserHtml")}
    print(json.dumps(meta, indent=2)[:3000])
