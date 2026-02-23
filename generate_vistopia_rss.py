#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
import argparse
import copy
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime


DEFAULT_RSS_URL = "https://api.vistopia.com.cn/rss/program/11.xml"
DEFAULT_ARTICLE_LIST_URL = "https://api.vistopia.com.cn/api/v1/content/article_list?content_id=11&count=1001"
DEFAULT_IMAGE_URL = "http://cdn.vistopia.com.cn/img/podcast-bafen.jpg"

NSMAP = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "sy": "http://purl.org/rss/1.0/modules/syndication/",
    "admin": "http://webns.net/mvcb/",
    "atom": "http://www.w3.org/2005/Atom/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "googleplay": "http://www.google.com/schemas/play-podcasts/1.0",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "fireside": "http://fireside.fm/modules/rss/fireside",
}

ARTICLE_ID_RE = re.compile(r"article_id=(\d+)")
ARTICLE_DATE_RE = re.compile(r"(?<!\d)(\d{4})\.(\d{1,2})\.(\d{1,2})(?!\d)")
COURSE_CONTENT_RE = re.compile(
    r'<div class="course-content">\s*(.*?)\s*</div>\s*</div>\s*<script',
    re.S,
)
RSS_OPEN_TAG_RE = re.compile(r"<rss\b[^>]*>")
XMLNS_ATTR_RE = re.compile(r'\s+xmlns:[A-Za-z0-9_-]+="[^"]*"')
VERSION_ATTR_RE = re.compile(r'\s+version="[^"]*"')
ENCODING_ATTR_RE = re.compile(r'\s+encoding="[^"]*"')
RSS_OPEN_TAG_CANONICAL = (
    '<rss xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:sy="http://purl.org/rss/1.0/modules/syndication/" '
    'xmlns:admin="http://webns.net/mvcb/" '
    'xmlns:atom="http://www.w3.org/2005/Atom/" '
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
    'xmlns:googleplay="http://www.google.com/schemas/play-podcasts/1.0" '
    'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
    'xmlns:fireside="http://fireside.fm/modules/rss/fireside" '
    'version="2.0" encoding="UTF-8">'
)


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "vistopia-rss-generator/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def clone_element(el: ET.Element) -> ET.Element:
    return copy.deepcopy(el)


def article_id_from_item(item: ET.Element) -> str | None:
    link = (item.findtext("link") or "").strip()
    match = ARTICLE_ID_RE.search(link)
    return match.group(1) if match else None


def format_duration_hhmmss(duration_seconds: str | int | None) -> str:
    try:
        sec = int(float(duration_seconds or 0))
    except (TypeError, ValueError):
        sec = 0
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def kb_to_bytes(kb: str | int | None) -> str:
    try:
        return str(max(0, int(float(kb or 0)) * 1024))
    except (TypeError, ValueError):
        return "0"


def parse_pubdate(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def to_rfc822_beijing(date_ymd: str) -> str | None:
    """Convert YYYY.MM.DD to RFC-822 at 00:00:00 in Beijing timezone."""
    try:
        parts = date_ymd.split(".")
        if len(parts) != 3:
            return None
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        dt = datetime(y, m, d, 0, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    except ValueError:
        return None
    return format_datetime(dt, usegmt=False)


def fetch_article_date_from_page(article_id: str, timeout: int = 6) -> str | None:
    """Extract date like YYYY.MM.DD from https://www.vistopia.com.cn/article/{id}."""
    if not article_id:
        return None
    url = f"https://www.vistopia.com.cn/article/{article_id}"
    try:
        html = fetch_bytes(url, timeout=timeout).decode("utf-8", errors="ignore")
    except Exception:
        return None
    matches = ARTICLE_DATE_RE.findall(html)
    if not matches:
        return None
    # Usually only one date exists. If multiple, use the first deterministic match.
    y, m, d = matches[0]
    return f"{y}.{m}.{d}"

def extract_course_content_html(article_content_html: str) -> str | None:
    match = COURSE_CONTENT_RE.search(article_content_html)
    if not match:
        return None
    return match.group(1).strip()


def html_to_plain_text(fragment: str) -> str:
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", fragment, flags=re.I)
    text = re.sub(r"</\s*(p|div|h1|h2|h3|h4|h5|h6|li)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def fetch_article_body_text(article: dict, timeout: int = 12) -> str:
    content_url = (article.get("content_url") or "").strip()
    if not content_url:
        return ""
    try:
        page = fetch_bytes(content_url, timeout=timeout).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    fragment = extract_course_content_html(page)
    if not fragment:
        return ""
    return html_to_plain_text(fragment)


def normalize_rss_header_in_file(path: str) -> None:
    with open(path, "r", encoding="utf-8") as f:
        xml = f.read()
    m = RSS_OPEN_TAG_RE.search(xml)
    if not m:
        return
    old_tag = m.group(0)
    cleaned = XMLNS_ATTR_RE.sub("", old_tag)
    cleaned = VERSION_ATTR_RE.sub("", cleaned)
    cleaned = ENCODING_ATTR_RE.sub("", cleaned)
    # Force exactly one canonical rss open tag.
    new_xml = xml[:m.start()] + RSS_OPEN_TAG_CANONICAL + xml[m.end():]
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_xml)


def build_pubdate_map(
    target_articles: list[dict],
    original_items: list[ET.Element],
    prefer_article_page_date: bool,
) -> dict[str, str]:
    known_dates: list[datetime] = []
    for it in original_items:
        d = parse_pubdate(it.findtext("pubDate"))
        if d is not None:
            known_dates.append(d.astimezone(timezone.utc))

    if known_dates:
        # Keep synthetic dates older than existing feed dates to avoid ordering surprises.
        base = min(known_dates) - timedelta(minutes=len(target_articles))
    else:
        # Fallback when source feed has no parseable dates.
        base = datetime(2018, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    out: dict[str, str] = {}
    article_page_cache: dict[str, str | None] = {}
    if prefer_article_page_date:
        unique_ids = sorted({str(a.get("article_id", "")) for a in target_articles if str(a.get("article_id", ""))})
        total = len(unique_ids)
        print(f"[pubDate] scraping article pages: {total} episode(s)", file=sys.stderr)
        done = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            fut_map = {ex.submit(fetch_article_date_from_page, aid): aid for aid in unique_ids}
            for fut in as_completed(fut_map):
                aid = fut_map[fut]
                try:
                    article_page_cache[aid] = fut.result()
                except Exception:
                    article_page_cache[aid] = None
                done += 1
                d = article_page_cache[aid]
                if d:
                    print(f"[pubDate] {done}/{total} article_id={aid} -> {d}", file=sys.stderr)
                else:
                    print(f"[pubDate] {done}/{total} article_id={aid} -> (no date, fallback)", file=sys.stderr)
    else:
        print("[pubDate] skipping article page scraping; using fallback pubDate values", file=sys.stderr)
    for idx, article in enumerate(target_articles):
        aid = str(article.get("article_id", ""))
        real_pub = None
        if prefer_article_page_date:
            real_pub = to_rfc822_beijing(article_page_cache[aid] or "")
        out[aid] = real_pub or format_datetime(base + timedelta(minutes=idx), usegmt=False)
    return out


def synthesize_item(
    article: dict,
    image_url: str,
    author: str,
    subtitle: str,
    pub_date: str,
    article_body_text: str,
) -> ET.Element:
    itunes = f"{{{NSMAP['itunes']}}}"
    content = f"{{{NSMAP['content']}}}"

    item = ET.Element("item")
    ET.SubElement(item, "title").text = article.get("title", "")
    ET.SubElement(item, f"{itunes}author").text = author
    ET.SubElement(item, f"{itunes}subtitle").text = subtitle
    img = ET.SubElement(item, f"{itunes}image")
    img.set("href", image_url)

    enclosure = ET.SubElement(item, "enclosure")
    enclosure.set(
        "url",
        article.get("media_key_full_url")
        or article.get("optional_media_key_full_url")
        or "",
    )
    enclosure.set("type", "audio/mpeg")
    enclosure.set("length", kb_to_bytes(article.get("media_size")))

    share_url = article.get("share_url") or f"https://shop.vistopia.com.cn/article?article_id={article.get('article_id','')}"
    guid = ET.SubElement(item, "guid")
    guid.set("isPermaLink", "false")
    guid.text = share_url

    ET.SubElement(item, "pubDate").text = pub_date
    ET.SubElement(item, f"{itunes}explicit").text = "no"
    ET.SubElement(item, f"{itunes}episodeType").text = "full"
    duration_text = format_duration_hhmmss(article.get("duration"))
    ET.SubElement(item, f"{itunes}duration").text = duration_text
    ET.SubElement(item, "link").text = share_url

    desc = article_body_text or ""
    ET.SubElement(item, "description").text = desc
    ET.SubElement(item, f"{itunes}keywords").text = "八分"
    ET.SubElement(item, f"{content}encoded").text = desc
    ET.SubElement(item, f"{itunes}summary").text = desc
    return item


def build_feed(
    mode: str,
    rss_xml: bytes,
    article_list_json: bytes,
    output_path: str,
    self_link: str | None,
    image_url: str,
    author: str,
    subtitle: str,
    prefer_article_page_date: bool,
) -> tuple[int, int, int]:
    for prefix, uri in NSMAP.items():
        ET.register_namespace(prefix, uri)

    original_root = ET.fromstring(rss_xml)
    original_channel = original_root.find("channel")
    if original_channel is None:
        raise RuntimeError("Original RSS has no <channel>.")

    article_list_payload = json.loads(article_list_json.decode("utf-8"))
    if article_list_payload.get("status") != "success":
        raise RuntimeError(f"Article list API status is not success: {article_list_payload.get('status')}")
    all_articles = article_list_payload["data"]["article_list"]

    original_items = original_channel.findall("item")
    existing_item_by_id: dict[str, ET.Element] = {}
    for item in original_items:
        aid = article_id_from_item(item)
        if aid:
            existing_item_by_id[aid] = item

    existing_ids = set(existing_item_by_id.keys())
    if mode == "missing":
        target_articles = [a for a in all_articles if a.get("article_id") not in existing_ids]
    else:
        target_articles = list(all_articles)

    new_root = ET.Element(original_root.tag, original_root.attrib)
    # Keep header close to the original feed, including declared namespaces.
    new_root.set("version", "2.0")
    new_root.set("encoding", "UTF-8")
    for prefix, uri in NSMAP.items():
        new_root.set(f"xmlns:{prefix}", uri)
    new_channel = ET.SubElement(new_root, "channel")

    for child in list(original_channel):
        if child.tag == "item":
            continue
        new_channel.append(clone_element(child))

    title_el = new_channel.find("title")
    if title_el is not None and title_el.text:
        suffix = "（缺失补全）" if mode == "missing" else "（完整镜像）"
        title_el.text = f"{title_el.text}{suffix}"

    desc_el = new_channel.find("description")
    if desc_el is not None:
        note = "仅包含原始 RSS 缺失条目。" if mode == "missing" else "完整镜像（含原始 RSS + 官网条目）。"
        desc_el.text = ((desc_el.text or "").rstrip() + "\n\n" + note).strip()

    channel_link = new_channel.find("link")
    if channel_link is not None:
        channel_link.text = "https://www.vistopia.com.cn/detail/11"

    atom_link = new_channel.find(f"{{{NSMAP['atom']}}}link")
    if atom_link is not None and self_link:
        atom_link.set("href", self_link)

    generated_count = 0
    reused_count = 0
    synth_targets = []
    for article in target_articles:
        aid = str(article.get("article_id", ""))
        if mode == "complete" and aid in existing_item_by_id:
            continue
        synth_targets.append(article)
    pubdate_map = build_pubdate_map(
        target_articles=synth_targets,
        original_items=original_items,
        prefer_article_page_date=prefer_article_page_date,
    )
    article_body_map: dict[str, str] = {}
    if synth_targets:
        print(f"[description] fetching article body: {len(synth_targets)} episode(s)", file=sys.stderr)
        done = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            fut_map = {ex.submit(fetch_article_body_text, article): str(article.get("article_id", "")) for article in synth_targets}
            for fut in as_completed(fut_map):
                aid = fut_map[fut]
                try:
                    article_body_map[aid] = fut.result()
                except Exception:
                    article_body_map[aid] = ""
                done += 1
                has = "ok" if article_body_map[aid] else "empty"
                print(f"[description] {done}/{len(synth_targets)} article_id={aid} -> {has}", file=sys.stderr)

    for article in target_articles:
        aid = str(article.get("article_id", ""))
        if mode == "complete" and aid in existing_item_by_id:
            new_channel.append(clone_element(existing_item_by_id[aid]))
            reused_count += 1
            continue
        new_channel.append(
            synthesize_item(
                article,
                image_url=image_url,
                author=author,
                subtitle=subtitle,
                pub_date=pubdate_map.get(aid, format_datetime(datetime.now(timezone.utc), usegmt=False)),
                article_body_text=article_body_map.get(aid, ""),
            )
        )
        generated_count += 1

    tree = ET.ElementTree(new_root)
    ET.indent(tree, space="    ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    normalize_rss_header_in_file(output_path)
    return len(all_articles), len(existing_ids), generated_count + reused_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Vistopia podcast RSS feed in missing-only or complete mode."
    )
    parser.add_argument(
        "mode",
        choices=["missing", "complete"],
        help="missing: only episodes absent in original RSS; complete: all episodes from website API.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output file path. Defaults to rss-program-11-missing-only.xml or rss-program-11-complete.xml.",
    )
    parser.add_argument("--rss-url", default=DEFAULT_RSS_URL, help="Original RSS feed URL.")
    parser.add_argument("--article-list-url", default=DEFAULT_ARTICLE_LIST_URL, help="Website episode-list API URL.")
    parser.add_argument(
        "--self-link",
        default=None,
        help="Optional atom:link self href in output feed.",
    )
    parser.add_argument("--image-url", default=DEFAULT_IMAGE_URL, help="Podcast cover image URL for synthesized items.")
    parser.add_argument("--author", default="梁文道", help="itunes:author for synthesized items.")
    parser.add_argument(
        "--subtitle",
        default="梁文道 · 八分 | 看理想播客",
        help="itunes:subtitle for synthesized items.",
    )
    parser.add_argument(
        "--no-article-page-date",
        action="store_true",
        help="Disable fetching date from article page; always use synthetic pubDate for generated items.",
    )
    parser.add_argument(
        "--skip-pubdate-scrape",
        action="store_true",
        help="Skip pubDate scraping from article pages to reduce requests (use fallback dates for generated items).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output
    if not output:
        output = (
            "rss-program-11-missing-only.xml"
            if args.mode == "missing"
            else "rss-program-11-complete.xml"
        )

    try:
        rss_xml = fetch_bytes(args.rss_url)
        article_json = fetch_bytes(args.article_list_url)
        all_count, rss_count, written_count = build_feed(
            mode=args.mode,
            rss_xml=rss_xml,
            article_list_json=article_json,
            output_path=output,
            self_link=args.self_link,
            image_url=args.image_url,
            author=args.author,
            subtitle=args.subtitle,
            prefer_article_page_date=not (args.no_article_page_date or args.skip_pubdate_scrape),
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"mode={args.mode}")
    print(f"website_episode_count={all_count}")
    print(f"original_rss_episode_count={rss_count}")
    print(f"output_items={written_count}")
    print(f"output_file={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
