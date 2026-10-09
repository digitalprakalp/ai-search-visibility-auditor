from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
from collections import deque
from datetime import datetime

app = Flask(__name__)
CORS(app)

HEADERS = {
    "User-Agent": "AIVA-AI-Search-Visibility-Auditor/1.0"
}

MAX_PAGES = 15
REQUEST_TIMEOUT = 12


def normalize_url(url):
    parsed = urlparse(url)

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc

    path = parsed.path or "/"

    if path != "/" and path.endswith("/"):
        path = path[:-1]

    return f"{scheme}://{netloc}{path}"


def same_domain(url1, url2):
    return urlparse(url1).netloc.lower() == urlparse(url2).netloc.lower()


def fetch_page(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        content_type = response.headers.get("Content-Type", "")

        if response.status_code >= 400:
            return None

        if "text/html" not in content_type.lower():
            return None

        return {
            "url": response.url,
            "status_code": response.status_code,
            "html": response.text,
            "content_type": content_type
        }

    except Exception:
        return None


def extract_page_data(url, html):
    soup = BeautifulSoup(html, "html.parser")

    # Remove elements that don't represent main readable content
    for element in soup([
        "script",
        "style",
        "noscript",
        "svg",
        "iframe"
    ]):
        element.decompose()

    title = ""
    if soup.title:
        title = soup.title.get_text(" ", strip=True)

    meta_description = ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})

    if meta:
        meta_description = meta.get("content", "").strip()

    headings = {
        "h1": [x.get_text(" ", strip=True) for x in soup.find_all("h1")],
        "h2": [x.get_text(" ", strip=True) for x in soup.find_all("h2")],
        "h3": [x.get_text(" ", strip=True) for x in soup.find_all("h3")]
    }

    paragraphs = [
        x.get_text(" ", strip=True)
        for x in soup.find_all("p")
        if x.get_text(" ", strip=True)
    ]

    text = soup.get_text(" ", strip=True)

    words = re.findall(r"\b\w+\b", text)

    # Questions from headings and text
    questions = []

    for heading_level in ["h1", "h2", "h3"]:
        for heading in headings[heading_level]:
            if "?" in heading:
                questions.append(heading)

    question_sentences = re.findall(
        r"[^.!?]*\?",
        text
    )

    for question in question_sentences:
        question = re.sub(r"\s+", " ", question).strip()

        if len(question) > 15 and len(question) < 300:
            questions.append(question)

   # Remove duplicate questions after normalizing whitespace,
# numbering and capitalization.
unique_questions = []
seen_questions = set()

for question in questions:
    normalized = re.sub(r"\s+", " ", question).strip()
    normalized = re.sub(r"^[\d\s.]+", "", normalized)
    normalized = normalized.casefold().rstrip(" .")

    if normalized and normalized not in seen_questions:
        seen_questions.add(normalized)
        unique_questions.append(question.strip())

questions = unique_questions
    # Links
    links = []

    for a in soup.find_all("a", href=True):
        href = a.get("href")

        if href.startswith("#"):
            continue

        absolute = urljoin(url, href)

        parsed = urlparse(absolute)

        if parsed.scheme in ["http", "https"]:
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

            if same_domain(clean, url):
                links.append(clean)

    links = list(dict.fromkeys(links))

    # Canonical
    canonical = None

    canonical_tag = soup.find(
        "link",
        attrs={"rel": lambda value: value and "canonical" in value}
    )

    if canonical_tag:
        canonical = canonical_tag.get("href")

    
    # Structured data: extract and validate JSON-LD
    import json

    schemas = []
    schema_types = []
    schema_errors = []

    for script in soup.find_all(
        "script",
        attrs={"type": re.compile(r"application/ld\+json", re.I)}
    ):
        schema_text = script.get_text(strip=True)

        if not schema_text:
            continue

        try:
            schema_data = json.loads(schema_text)
            schemas.append(schema_data)

            def collect_schema_types(item):
                if isinstance(item, dict):
                    item_type = item.get("@type")

                    if isinstance(item_type, str):
                        schema_types.append(item_type)
                    elif isinstance(item_type, list):
                        schema_types.extend(
                            value for value in item_type
                            if isinstance(value, str)
                        )

                    graph = item.get("@graph")

                    if isinstance(graph, list):
                        for entry in graph:
                            collect_schema_types(entry)

                elif isinstance(item, list):
                    for entry in item:
                        collect_schema_types(entry)

            collect_schema_types(schema_data)

        except (json.JSONDecodeError, TypeError):
            schema_errors.append("Invalid JSON-LD block")

    schema_types = sorted(set(schema_types))


    for script in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
    ):
        if script.string:
            schemas.append(script.string.strip())

    # Images
    images = len(soup.find_all("img"))

    images_without_alt = len([
        img for img in soup.find_all("img")
        if not img.get("alt")
    ])

    # Lists
    list_count = len(soup.find_all(["ul", "ol"]))

    # Tables
    table_count = len(soup.find_all("table"))

    return {
        "url": url,
        "title": title,
        "meta_description": meta_description,

        "headings": headings,

        "paragraphs": paragraphs,
        "text": text[:50000],

        "word_count": len(words),

        "questions": questions,

        "links": links,

        "canonical": canonical,

        
        "schemas": schemas,
        "schema_types": schema_types,
        "schema_errors": schema_errors,


        "images": images,
        "images_without_alt": images_without_alt,

        "lists": list_count,
        "tables": table_count
    }


def crawl_website(start_url):
    start_url = normalize_url(start_url)

    domain = urlparse(start_url).netloc

    queue = deque([start_url])
    visited = set()

    pages = []

    while queue and len(pages) < MAX_PAGES:

        current_url = queue.popleft()

        if current_url in visited:
            continue

        visited.add(current_url)

        if not same_domain(current_url, start_url):
            continue

        result = fetch_page(current_url)

        if not result:
            continue

        page_data = extract_page_data(
            current_url,
            result["html"]
        )

        pages.append(page_data)

        # Add internal links to crawler queue
        for link in page_data["links"]:

            if link in visited:
                continue

            if not same_domain(link, start_url):
                continue

            parsed = urlparse(link)

            # Avoid obvious non-page resources
            excluded_extensions = [
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".svg",
                ".webp",
                ".pdf",
                ".zip",
                ".mp4",
                ".mp3"
            ]

            if any(
                parsed.path.lower().endswith(ext)
                for ext in excluded_extensions
            ):
                continue

            queue.append(link)

    return pages


def check_resource(base_url, path):
    url = urljoin(base_url, path)

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        return {
            "url": url,
            "status_code": response.status_code,
            "available": response.status_code == 200,
            "content_length": len(response.text),
            "content": response.text[:30000]
        }

    except Exception as e:
        return {
            "url": url,
            "status_code": None,
            "available": False,
            "content_length": 0,
            "content": "",
            "error": str(e)
        }


def collect_technical_data(start_url):

    robots = check_resource(
        start_url,
        "/robots.txt"
    )

    sitemap = check_resource(
        start_url,
        "/sitemap.xml"
    )

    llms = check_resource(
        start_url,
        "/llms.txt"
    )

    llms_full = check_resource(
        start_url,
        "/llms-full.txt"
    )

    return {
        "robots_txt": robots,
        "sitemap_xml": sitemap,
        "llms_txt": llms,
        "llms_full_txt": llms_full
    }


@app.route("/")
def home():

    return jsonify({
        "message": "AI Search Visibility Auditor API",
        "name": "AIVA",
        "status": "running"
    })


@app.route("/audit", methods=["POST"])
def audit():

    data = request.get_json(silent=True) or {}

    url = data.get("url")

    if not url:
        return jsonify({
            "error": "URL is required"
        }), 400

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:

        parsed = urlparse(url)

        if not parsed.netloc:
            return jsonify({
                "error": "Invalid URL"
            }), 400

        pages = crawl_website(url)

        technical = collect_technical_data(url)

        total_words = sum(
            page["word_count"]
            for page in pages
        )

        total_questions = sum(
            len(page["questions"])
            for page in pages
        )

        total_schemas = sum(
            len(page["schemas"])
            for page in pages
        )

        return jsonify({

            "status": "success",

            "website": {
                "url": url,
                "domain": parsed.netloc,
                "pages_analyzed": len(pages),
                "total_words": total_words,
                "total_questions": total_questions,
                "total_schemas": total_schemas
            },

            "crawl": {
                "pages": pages
            },

            "technical": technical,

            "crawl_timestamp": datetime.utcnow().isoformat() + "Z"

        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
