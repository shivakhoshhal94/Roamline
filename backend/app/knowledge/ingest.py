"""Stream current Wikivoyage articles into auditable destination sections."""
import bz2
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import math
from pathlib import Path
import re
import mwxml
import mwparserfromhell as wiki
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import delete, select, func
from app.db.models import Destination
from app.knowledge.models import KnowledgePage, KnowledgeChunk
from app.services.geoapify import normalized_name, distance_km

PARSER_VERSION = "wikivoyage-sections-v1"
SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1400, chunk_overlap=120, separators=["\n\n", "\n", ". ", " ", ""])
LISTINGS = {"see", "do", "eat", "drink", "buy", "sleep", "listing"}


def digest_file(path):
    with Path(path).open("rb") as source:
        import hashlib
        return hashlib.file_digest(source, "sha256").hexdigest()


def coordinates(code):
    for template in code.filter_templates():
        if str(template.name).strip().lower() == "geo":
            try:
                lat, lon = float(str(template.get(1).value).strip()), float(str(template.get(2).value).strip())
                if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon
            except (ValueError, TypeError):
                pass
    return None


def city_lookup(cities):
    lookup = defaultdict(dict)
    for city in cities:
        for name in {city.name, city.ascii_name}:
            lookup[normalized_name(name)][city.geoname_id] = city
    return lookup


def match_city(title, code, lookup):
    # District guides remain associated with their canonical city. Coordinates
    # disambiguate same-name cities worldwide; title alone never establishes identity.
    root = title.split("/", 1)[0].split(" (", 1)[0]
    point = coordinates(code)
    if point is None:
        return None
    matches = [(distance_km(city.latitude, city.longitude, *point), city)
               for city in lookup.get(normalized_name(root), {}).values()]
    matches = [(distance, city) for distance, city in matches if distance <= 25]
    return matches[0] if len(matches) == 1 else None


def clean_section(code):
    code = wiki.parse(str(code))
    for template in list(code.filter_templates(recursive=False)):
        name = str(template.name).strip().lower()
        replacement = ""
        if name in LISTINGS:
            replacement = ". ".join(str(template.get(key).value).strip() for key in ["name", "alt", "content"] if template.has(key))
        code.replace(template, replacement)
    for link in reversed(list(code.filter_wikilinks())):
        if str(link.title).strip().lower().startswith(("file:", "image:", "category:")):
            code.remove(link)
    for tag in reversed(list(code.filter_tags())):
        if str(tag.tag).lower() in {"ref", "references", "gallery", "table"}:
            code.remove(tag)
    text = code.strip_code(normalize=True, collapse=True)
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip(" *#;:") for line in text.splitlines() if line.strip()).strip()


def section_chunks(title, code):
    result = []
    for section in code.get_sections(include_lead=True, include_headings=True, flat=True):
        headings = section.filter_headings()
        heading = str(headings[0].title).strip() if headings else "Overview"
        if heading.lower() in {"go next", "external links", "related", "references"}:
            continue
        text = clean_section(section)
        if len(text) < 60:
            continue
        for part in SPLITTER.split_text(text):
            if len(part) >= 60:
                result.append((heading, title + " — " + heading + "\n" + part))
    return result


def ingest_page(session, city, title, page_id, revision_id, revised_at, wikicode, dump_hash, distance):
    content_hash = sha256((PARSER_VERSION + wikicode).encode()).hexdigest()
    page = session.scalar(select(KnowledgePage).where(KnowledgePage.source == "wikivoyage",
        KnowledgePage.language == "en", KnowledgePage.source_page_id == page_id).with_for_update())
    if page and page.revision_id > revision_id:
        return "older", 0
    if page and page.content_hash == content_hash and page.destination_id == city.geoname_id:
        page.canonical_city, page.country_code, page.continent_code = city.name, city.country_code, city.continent_code
        page.revision_id, page.revised_at = revision_id, revised_at
        page.source_url = "https://en.wikivoyage.org/w/index.php?oldid=" + str(revision_id)
        page.dump_sha256 = dump_hash
        return "unchanged", 0
    chunks = section_chunks(title, wiki.parse(wikicode))
    if not chunks:
        return "empty", 0
    if page is None:
        page = KnowledgePage(source="wikivoyage", language="en", source_page_id=page_id)
        session.add(page)
    else:
        session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.page_id == page.id))
    page.destination_id, page.canonical_city = city.geoname_id, city.name
    page.country_code, page.continent_code = city.country_code, city.continent_code
    page.title, page.revision_id, page.revised_at = title, revision_id, revised_at
    page.source_url = "https://en.wikivoyage.org/w/index.php?oldid=" + str(revision_id)
    page.license = "CC BY-SA 4.0"
    page.dump_sha256, page.content_hash = dump_hash, content_hash
    page.parser_version, page.match_method, page.match_distance_km = PARSER_VERSION, "name-and-geo-within-25km", distance
    page.ingested_at = datetime.now(timezone.utc)
    session.flush()
    for index, (heading, text) in enumerate(chunks):
        values = dict(page_id=page.id, section=heading, chunk_index=index, text=text,
                      content_hash=sha256(text.encode()).hexdigest())
        if session.get_bind().dialect.name == "postgresql":
            values["search_vector"] = func.to_tsvector("english", text)
        session.add(KnowledgeChunk(**values))
    return "written", len(chunks)


def ingest_dump(factory, path, max_pages=None):
    path = Path(path)
    dump_hash = digest_file(path)
    with factory() as session:
        cities = session.scalars(select(Destination)).all()
        lookup = city_lookup(cities)
    report = {"source_sha256": dump_hash, "pages_processed": 0, "matched_pages": 0,
              "written_pages": 0, "unchanged_pages": 0, "chunks_written": 0, "unmatched_pages": 0}
    opener = bz2.open if path.suffix == ".bz2" else open
    with opener(path, "rb") as source:
        dump = mwxml.Dump.from_file(source)
        for page in dump:
            latest = None
            for revision in page:
                if latest is None or revision.id > latest.id:
                    latest = revision
            if page.namespace != 0 or getattr(page, "redirect", None) or latest is None or not latest.text:
                continue
            report["pages_processed"] += 1
            root = page.title.split("/", 1)[0].split(" (", 1)[0]
            match = match_city(page.title, wiki.parse(latest.text), lookup) if normalized_name(root) in lookup else None
            if match:
                distance, city = match
                # Each page replacement is atomic. Restarting safely skips hashes
                # already committed, including after a truncated/corrupt later page.
                with factory.begin() as session:
                    status, count = ingest_page(session, city, page.title, page.id, latest.id,
                        datetime.fromisoformat(str(latest.timestamp).replace("Z", "+00:00")), latest.text, dump_hash, distance)
                report["matched_pages"] += 1
                report["written_pages"] += status == "written"
                report["unchanged_pages"] += status == "unchanged"
                report["chunks_written"] += count
            else:
                report["unmatched_pages"] += 1
            if max_pages and report["pages_processed"] >= max_pages:
                break
    return report
