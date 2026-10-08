from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re

app = Flask(__name__)
CORS(app)

USER_AGENT = "AIVA-AI-Search-Auditor/1.0"


def fetch_page(url):
    headers = {
        "User-Agent": USER_AGENT
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=15,
        allow_redirects=True
    )

    return response


def check_url(url):
    parsed = urlparse(url)

    if parsed.scheme not in ["http", "https"]:
        return False

    if not parsed.netloc:
        return False

    return True


def analyze_page(url):
    response = fetch_page(url)

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else ""

    meta_description = soup.find(
        "meta",
        attrs={"name": re.compile("^description$", re.I)}
    )

    meta_description = (
        meta_description.get("content", "").strip()
        if meta_description
        else ""
    )

    h1_tags = soup.find_all("h1")

    canonical = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value
    )

    canonical_url = (
        canonical.get("href", "").strip()
        if canonical
        else ""
    )

    json_ld = soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
    )

    links = soup.find_all("a", href=True)

    parsed_base = urlparse(response.url)

    internal_links = []

    for link in links:
        href = link.get("href", "").strip()

        if not href:
            continue

        absolute_url = urljoin(response.url, href)
        parsed_link = urlparse(absolute_url)

        if parsed_link.netloc == parsed_base.netloc:
            internal_links.append(absolute_url)

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)

    words = re.findall(r"\b[\w'-]+\b", text)

    return {
        "status_code": response.status_code,
        "final_url": response.url,
        "https": response.url.startswith("https://"),
        "title": title,
        "title_length": len(title),
        "meta_description": meta_description,
        "meta_description_length": len(meta_description),
        "h1_count": len(h1_tags),
        "h1": h1_tags[0].get_text(" ", strip=True)
        if h1_tags
        else "",
        "canonical": canonical_url,
        "schema_count": len(json_ld),
        "internal_links": len(internal_links),
        "word_count": len(words)
    }


def check_robots(url):
    parsed = urlparse(url)

    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        response = requests.get(
            robots_url,
            headers={"User-Agent": USER_AGENT},
            timeout=10
        )

        return {
            "exists": response.status_code == 200,
            "url": robots_url
        }

    except Exception:
        return {
            "exists": False,
            "url": robots_url
        }


def check_sitemap(url):
    parsed = urlparse(url)

    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"

    try:
        response = requests.get(
            sitemap_url,
            headers={"User-Agent": USER_AGENT},
            timeout=10
        )

        return {
            "exists": response.status_code == 200,
            "url": sitemap_url
        }

    except Exception:
        return {
            "exists": False,
            "url": sitemap_url
        }


def calculate_score(data):
    score = 0

    # HTTPS
    if data["https"]:
        score += 10

    # Title
    if 30 <= data["title_length"] <= 65:
        score += 10
    elif data["title_length"] > 0:
        score += 5

    # Meta description
    if 70 <= data["meta_description_length"] <= 160:
        score += 10
    elif data["meta_description_length"] > 0:
        score += 5

    # H1
    if data["h1_count"] == 1:
        score += 10
    elif data["h1_count"] > 0:
        score += 5

    # Canonical
    if data["canonical"]:
        score += 10

    # Structured data
    if data["schema_count"] > 0:
        score += 15

    # Content
    if data["word_count"] >= 1000:
        score += 15
    elif data["word_count"] >= 500:
        score += 10
    elif data["word_count"] > 250:
        score += 5

    # Internal links
    if data["internal_links"] >= 10:
        score += 10
    elif data["internal_links"] >= 5:
        score += 5

    # Robots
    if data["robots"]["exists"]:
        score += 5

    # Sitemap
    if data["sitemap"]["exists"]:
        score += 5

    return min(score, 100)


@app.route("/")
def home():
    return jsonify({
        "name": "AIVA",
        "message": "AI Search Visibility Auditor API",
        "status": "running"
    })


@app.route("/audit", methods=["POST"])
def audit():

    body = request.get_json(silent=True) or {}

    url = body.get("url", "").strip()

    if not url:
        return jsonify({
            "error": "Website URL is required"
        }), 400

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if not check_url(url):
        return jsonify({
            "error": "Invalid website URL"
        }), 400

    try:

        page_data = analyze_page(url)

        page_data["robots"] = check_robots(url)
        page_data["sitemap"] = check_sitemap(url)

        score = calculate_score(page_data)

        return jsonify({
            "success": True,
            "url": url,
            "score": score,
            "data": page_data
        })

    except requests.exceptions.RequestException:
        return jsonify({
            "error": "Unable to access the website"
        }), 502

    except Exception as error:
        return jsonify({
            "error": str(error)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
