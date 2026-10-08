from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime
import re
import json

app = Flask(__name__)
CORS(app)

USER_AGENT = "AIVA-AI-Search-Auditor/2.0"


# ---------------------------------------------------------
# BASIC HELPERS
# ---------------------------------------------------------

def get_url(url):
    headers = {
        "User-Agent": USER_AGENT
    }

    return requests.get(
        url,
        headers=headers,
        timeout=15,
        allow_redirects=True
    )


def valid_url(url):
    parsed = urlparse(url)

    return (
        parsed.scheme in ["http", "https"]
        and bool(parsed.netloc)
    )


def absolute_url(base, value):
    return urljoin(base, value)


# ---------------------------------------------------------
# FILE CHECKS
# ---------------------------------------------------------

def check_file(base_url, filename):

    parsed = urlparse(base_url)

    file_url = (
        f"{parsed.scheme}://{parsed.netloc}/{filename}"
    )

    try:

        response = requests.get(
            file_url,
            headers={"User-Agent": USER_AGENT},
            timeout=10
        )

        return {
            "exists": response.status_code == 200,
            "url": file_url,
            "status_code": response.status_code,
            "content": response.text[:20000]
            if response.status_code == 200
            else ""
        }

    except Exception:

        return {
            "exists": False,
            "url": file_url,
            "status_code": 0,
            "content": ""
        }


# ---------------------------------------------------------
# QUESTION DETECTION
# ---------------------------------------------------------

def detect_questions(soup):

    questions = []

    # Question headings
    for heading in soup.find_all(
        ["h2", "h3", "h4"]
    ):

        text = heading.get_text(
            " ",
            strip=True
        )

        if "?" in text:
            questions.append(text)

    # FAQ-style questions
    for element in soup.find_all(
        ["summary", "dt"]
    ):

        text = element.get_text(
            " ",
            strip=True
        )

        if "?" in text:
            questions.append(text)

    # Remove duplicates
    unique = []

    for question in questions:

        if question not in unique:
            unique.append(question)

    return unique[:50]


# ---------------------------------------------------------
# STRUCTURED DATA
# ---------------------------------------------------------

def analyze_schema(soup):

    schemas = []

    for script in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
    ):

        try:

            data = json.loads(
                script.string or script.get_text()
            )

            if isinstance(data, list):
                schemas.extend(data)

            else:
                schemas.append(data)

        except Exception:
            continue

    schema_types = []

    def extract_type(item):

        if isinstance(item, dict):

            item_type = item.get("@type")

            if isinstance(item_type, list):
                schema_types.extend(item_type)

            elif item_type:
                schema_types.append(
                    str(item_type)
                )

            for value in item.values():

                if isinstance(value, dict):
                    extract_type(value)

                elif isinstance(value, list):

                    for child in value:
                        extract_type(child)

    for schema in schemas:
        extract_type(schema)

    return {
        "count": len(schemas),
        "types": sorted(
            list(set(schema_types))
        )
    }


# ---------------------------------------------------------
# ENTITY SIGNALS
# ---------------------------------------------------------

def analyze_entities(soup, schema):

    text = soup.get_text(
        " ",
        strip=True
    )

    links = []

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link.get("href", "")
        label = link.get_text(
            " ",
            strip=True
        )

        links.append({
            "label": label,
            "href": href
        })

    has_about = any(
        "about" in (
            item["href"] +
            item["label"]
        ).lower()
        for item in links
    )

    has_contact = any(
        "contact" in (
            item["href"] +
            item["label"]
        ).lower()
        for item in links
    )

    organization_schema = (
        "Organization" in schema["types"]
        or "LocalBusiness" in schema["types"]
    )

    person_schema = "Person" in schema["types"]

    logo_present = bool(
        soup.find(
            "img",
            alt=re.compile(
                r"logo",
                re.I
            )
        )
    )

    return {
        "organization_schema": organization_schema,
        "person_schema": person_schema,
        "about_page_signal": has_about,
        "contact_page_signal": has_contact,
        "logo_signal": logo_present
    }


# ---------------------------------------------------------
# CONTENT QUALITY SIGNALS
# ---------------------------------------------------------

def analyze_content(soup, text):

    words = re.findall(
        r"\b[\w'-]+\b",
        text
    )

    word_count = len(words)

    h1 = soup.find_all("h1")
    h2 = soup.find_all("h2")
    h3 = soup.find_all("h3")

    paragraphs = soup.find_all("p")

    lists = soup.find_all(
        ["ul", "ol"]
    )

    images = soup.find_all("img")

    images_with_alt = [
        img for img in images
        if img.get("alt", "").strip()
    ]

    links = soup.find_all(
        "a",
        href=True
    )

    external_links = []

    for link in links:

        href = link.get("href", "")

        if href.startswith("http"):
            external_links.append(href)

    # Signals of evidence/original information
    statistics = len(
        re.findall(
            r"\b\d+(?:\.\d+)?%\b",
            text
        )
    )

    citation_signals = len(
        re.findall(
            r"\baccording to\b|\bstudy\b|\bresearch\b|\bsource\b|\breport\b",
            text,
            re.I
        )
    )

    first_hand_signals = len(
        re.findall(
            r"\bour experience\b|\bour data\b|\bwe found\b|\bour research\b|\bcase study\b",
            text,
            re.I
        )
    )

    generic_signals = len(
        re.findall(
            r"\bbest solution\b|\btop solution\b|\bultimate guide\b|\bworld[- ]class\b|\bleading solution\b",
            text,
            re.I
        )
    )

    return {
        "word_count": word_count,
        "h1_count": len(h1),
        "h2_count": len(h2),
        "h3_count": len(h3),
        "paragraph_count": len(paragraphs),
        "list_count": len(lists),
        "image_count": len(images),
        "images_with_alt": len(images_with_alt),
        "internal_link_count": len(links),
        "external_link_count": len(external_links),
        "statistics": statistics,
        "citation_signals": citation_signals,
        "first_hand_signals": first_hand_signals,
        "generic_signals": generic_signals
    }


# ---------------------------------------------------------
# FRESHNESS
# ---------------------------------------------------------

def analyze_freshness(soup):

    dates = []

    for tag in soup.find_all(
        ["time", "meta"]
    ):

        value = (
            tag.get("datetime")
            or tag.get("content")
        )

        if value:
            dates.append(value)

    detected_date = None

    for value in dates:

        match = re.search(
            r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})",
            value
        )

        if match:

            try:

                detected_date = datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3))
                )

                break

            except Exception:
                pass

    if not detected_date:

        return {
            "date_found": False,
            "age_days": None
        }

    age_days = (
        datetime.utcnow() -
        detected_date
    ).days

    return {
        "date_found": True,
        "age_days": age_days
    }


# ---------------------------------------------------------
# MAIN PAGE ANALYSIS
# ---------------------------------------------------------

def analyze_page(url):

    response = get_url(url)

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    title = (
        soup.title.get_text(
            " ",
            strip=True
        )
        if soup.title
        else ""
    )

    meta = soup.find(
        "meta",
        attrs={
            "name": re.compile(
                "^description$",
                re.I
            )
        }
    )

    meta_description = (
        meta.get("content", "").strip()
        if meta
        else ""
    )

    h1_tags = soup.find_all("h1")

    canonical = soup.find(
        "link",
        rel=lambda value:
        value and "canonical" in value
    )

    canonical_url = (
        canonical.get("href", "").strip()
        if canonical
        else ""
    )

    schema = analyze_schema(soup)

    questions = detect_questions(soup)

    for tag in soup(
        ["script", "style", "noscript"]
    ):
        tag.decompose()

    text = soup.get_text(
        " ",
        strip=True
    )

    content = analyze_content(
        soup,
        text
    )

    entities = analyze_entities(
        soup,
        schema
    )

    freshness = analyze_freshness(
        soup
    )

    # Internal links
    parsed_base = urlparse(
        response.url
    )

    internal_links = []

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link.get(
            "href",
            ""
        ).strip()

        if not href:
            continue

        absolute = absolute_url(
            response.url,
            href
        )

        parsed_link = urlparse(
            absolute
        )

        if (
            parsed_link.netloc
            == parsed_base.netloc
        ):
            internal_links.append(
                absolute
            )

    return {

        "status_code":
            response.status_code,

        "final_url":
            response.url,

        "https":
            response.url.startswith(
                "https://"
            ),

        "title":
            title,

        "title_length":
            len(title),

        "meta_description":
            meta_description,

        "meta_description_length":
            len(meta_description),

        "h1":
            h1_tags[0].get_text(
                " ",
                strip=True
            )
            if h1_tags
            else "",

        "h1_count":
            len(h1_tags),

        "canonical":
            canonical_url,

        "schema":
            schema,

        "questions":
            questions,

        "question_count":
            len(questions),

        "content":
            content,

        "entities":
            entities,

        "freshness":
            freshness,

        "internal_links":
            len(internal_links)
    }


# ---------------------------------------------------------
# SCORING
# ---------------------------------------------------------

def calculate_scores(data):

    # ------------------------------------
    # 1. TECHNICAL ACCESSIBILITY / 10
    # ------------------------------------

    technical = 0

    if data["https"]:
        technical += 2

    if data["status_code"] == 200:
        technical += 2

    if data["robots"]["exists"]:
        technical += 2

    if data["sitemap"]["exists"]:
        technical += 2

    if data["canonical"]:
        technical += 1

    if data["content"]["word_count"] > 100:
        technical += 1

    technical = min(
        technical,
        10
    )

    # ------------------------------------
    # 2. HELPFUL & ORIGINAL CONTENT / 18
    # ------------------------------------

    content_score = 0

    words = data["content"]["word_count"]

    if words >= 1500:
        content_score += 5

    elif words >= 900:
        content_score += 4

    elif words >= 500:
        content_score += 3

    elif words >= 250:
        content_score += 1

    if data["content"]["h2_count"] >= 3:
        content_score += 2

    if data["content"]["h3_count"] >= 2:
        content_score += 1

    if data["content"]["list_count"] >= 2:
        content_score += 1

    if data["content"]["statistics"] >= 2:
        content_score += 2

    if data["content"]["citation_signals"] >= 2:
        content_score += 2

    if data["content"]["first_hand_signals"] >= 1:
        content_score += 3

    # Generic marketing language reduces differentiation
    if data["content"]["generic_signals"] >= 5:
        content_score -= 2

    content_score = max(
        0,
        min(content_score, 18)
    )

    # ------------------------------------
    # 3. QUESTION & INTENT COVERAGE / 12
    # ------------------------------------

    question_score = 0

    question_count = data[
        "question_count"
    ]

    if question_count >= 8:
        question_score = 12

    elif question_count >= 5:
        question_score = 9

    elif question_count >= 3:
        question_score = 6

    elif question_count >= 1:
        question_score = 3

    # ------------------------------------
    # 4. ENTITY UNDERSTANDING / 12
    # ------------------------------------

    entity_score = 0

    entities = data["entities"]

    if entities["organization_schema"]:
        entity_score += 3

    if entities["person_schema"]:
        entity_score += 1

    if entities["about_page_signal"]:
        entity_score += 2

    if entities["contact_page_signal"]:
        entity_score += 2

    if entities["logo_signal"]:
        entity_score += 1

    if data["title"]:
        entity_score += 1

    if data["h1"]:
        entity_score += 1

    if data["meta_description"]:
        entity_score += 1

    entity_score = min(
        entity_score,
        12
    )

    # ------------------------------------
    # 5. TOPICAL COVERAGE / 12
    # ------------------------------------

    topical_score = 0

    h2 = data["content"]["h2_count"]
    h3 = data["content"]["h3_count"]

    if h2 >= 8:
        topical_score += 6

    elif h2 >= 5:
        topical_score += 4

    elif h2 >= 3:
        topical_score += 2

    if h3 >= 6:
        topical_score += 4

    elif h3 >= 3:
        topical_score += 2

    if data["question_count"] >= 3:
        topical_score += 2

    topical_score = min(
        topical_score,
        12
    )

    # ------------------------------------
    # 6. CITATION & EVIDENCE / 10
    # ------------------------------------

    citation_score = 0

    if data["content"]["statistics"] >= 1:
        citation_score += 2

    if data["content"]["citation_signals"] >= 1:
        citation_score += 2

    if data["content"]["external_link_count"] >= 3:
        citation_score += 2

    if data["content"]["first_hand_signals"] >= 1:
        citation_score += 3

    if data["freshness"]["date_found"]:
        citation_score += 1

    citation_score = min(
        citation_score,
        10
    )

    # ------------------------------------
    # 7. STRUCTURED DATA / 8
    # ------------------------------------

    schema_score = 0

    schema_types = data[
        "schema"
    ]["types"]

    if len(schema_types) >= 1:
        schema_score += 2

    if "Organization" in schema_types:
        schema_score += 2

    if "Article" in schema_types:
        schema_score += 1

    if "BreadcrumbList" in schema_types:
        schema_score += 1

    if "FAQPage" in schema_types:
        schema_score += 1

    if "WebSite" in schema_types:
        schema_score += 1

    schema_score = min(
        schema_score,
        8
    )

    # ------------------------------------
    # 8. FRESHNESS / 7
    # ------------------------------------

    freshness_score = 0

    freshness = data[
        "freshness"
    ]

    if freshness["date_found"]:

        age = freshness["age_days"]

        if age <= 90:
            freshness_score = 7

        elif age <= 180:
            freshness_score = 5

        elif age <= 365:
            freshness_score = 3

        else:
            freshness_score = 1

    else:

        freshness_score = 2

    # ------------------------------------
    # 9. INTERNAL DISCOVERABILITY / 6
    # ------------------------------------

    architecture_score = 0

    links = data["internal_links"]

    if links >= 20:
        architecture_score = 6

    elif links >= 10:
        architecture_score = 5

    elif links >= 5:
        architecture_score = 3

    elif links >= 2:
        architecture_score = 1

    # ------------------------------------
    # 10. LLM ACCESSIBILITY / 5
    # ------------------------------------

    llm_score = 0

    if data["llms"]["exists"]:
        llm_score += 2

    if data["llms"]["content"]:
        llm_score += 1

    if data["robots"]["exists"]:
        llm_score += 1

    if data["sitemap"]["exists"]:
        llm_score += 1

    llm_score = min(
        llm_score,
        5
    )

    # ------------------------------------
    # TOTAL
    # ------------------------------------

    total = (
        technical
        + content_score
        + question_score
        + entity_score
        + topical_score
        + citation_score
        + schema_score
        + freshness_score
        + architecture_score
        + llm_score
    )

    # Prevent unrealistically high scores
    # until deeper AI analysis is available.

    if entity_score < 6:
        total = min(total, 84)

    if content_score < 10:
        total = min(total, 84)

    if question_score < 6:
        total = min(total, 89)

    if citation_score < 5:
        total = min(total, 89)

    total = min(
        total,
        89
    )

    return {

        "total": total,

        "technical_accessibility":
            technical,

        "helpful_original_content":
            content_score,

        "question_intent_coverage":
            question_score,

        "entity_understanding":
            entity_score,

        "topical_coverage":
            topical_score,

        "citation_evidence_readiness":
            citation_score,

        "structured_data":
            schema_score,

        "freshness":
            freshness_score,

        "internal_discoverability":
            architecture_score,

        "llm_accessibility":
            llm_score
    }


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

@app.route("/")
def home():

    return jsonify({

        "name":
            "AIVA",

        "message":
            "AI Search Visibility Auditor API",

        "version":
            "2.0",

        "status":
            "running"
    })


@app.route(
    "/audit",
    methods=["POST"]
)
def audit():

    body = request.get_json(
        silent=True
    ) or {}

    url = body.get(
        "url",
        ""
    ).strip()

    if not url:

        return jsonify({
            "error":
                "Website URL is required"
        }), 400

    if not url.startswith(
        ("http://", "https://")
    ):

        url = "https://" + url

    if not valid_url(url):

        return jsonify({
            "error":
                "Invalid website URL"
        }), 400

    try:

        data = analyze_page(url)

        parsed = urlparse(url)

        data["robots"] = check_file(
            url,
            "robots.txt"
        )

        data["sitemap"] = check_file(
            url,
            "sitemap.xml"
        )

        data["llms"] = check_file(
            url,
            "llms.txt"
        )

        scores = calculate_scores(
            data
        )

        return jsonify({

            "success":
                True,

            "url":
                url,

            "score":
                scores["total"],

            "score_breakdown":
                scores,

            "analysis":
                data

        })

    except requests.exceptions.RequestException:

        return jsonify({
            "error":
                "Unable to access the website"
        }), 502

    except Exception as error:

        return jsonify({
            "error":
                str(error)
        }), 500


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
