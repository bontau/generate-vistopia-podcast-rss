#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
import argparse
import copy
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET


DEFAULT_RSS_URL = "https://api.vistopia.com.cn/rss/program/11.xml"
DEFAULT_ARTICLE_LIST_URL = "https://api.vistopia.com.cn/api/v1/content/article_list?content_id=11&count=1001"
DEFAULT_IMAGE_URL = "http://cdn.vistopia.com.cn/img/podcast-bafen.jpg"

NSMAP = {
    "atom": "http://www.w3.org/2005/Atom/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
}

ARTICLE_ID_RE = re.compile(r"article_id=(\d+)")


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


def synthesize_item(article: dict, image_url: str, author: str, subtitle: str) -> ET.Element:
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

    ET.SubElement(item, f"{itunes}explicit").text = "no"
    ET.SubElement(item, f"{itunes}episodeType").text = "full"
    ET.SubElement(item, f"{itunes}duration").text = format_duration_hhmmss(article.get("duration"))
    ET.SubElement(item, "link").text = share_url

    desc = f"该条目来自官网目录。网页链接：{share_url}"
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
    for article in target_articles:
        aid = article.get("article_id")
        if mode == "complete" and aid in existing_item_by_id:
            new_channel.append(clone_element(existing_item_by_id[aid]))
            reused_count += 1
            continue
        new_channel.append(synthesize_item(article, image_url=image_url, author=author, subtitle=subtitle))
        generated_count += 1

    tree = ET.ElementTree(new_root)
    ET.indent(tree, space="    ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
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
