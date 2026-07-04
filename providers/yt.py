from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests


logger = logging.getLogger(__name__)


RSS_BASE = "https://www.youtube.com/feeds/videos.xml"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


@dataclass(frozen=True)
class YouTubeVideo:
    video_id: str
    title: str
    url: str
    channel_name: str
    channel_id: str
    thumbnail_url: str
    published: str


class YouTubeRSS:
    @staticmethod
    def _feed_url(channel_or_playlist_id: str) -> str:
        cid = channel_or_playlist_id.strip()
        if cid.startswith("UC"):
            return f"{RSS_BASE}?channel_id={cid}"
        return f"{RSS_BASE}?playlist_id={cid}"

    def fetch_channel(self, channel_id: str) -> list[YouTubeVideo]:
        url = self._feed_url(channel_id)
        logger.info("Fetching YouTube RSS for %s", channel_id)
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        root = ET.fromstring(response.content)
        entries = root.findall("atom:entry", NS)

        videos: list[YouTubeVideo] = []
        for entry in entries:
            video_id_el = entry.find("yt:videoId", NS)
            if video_id_el is None or not video_id_el.text:
                continue
            video_id = video_id_el.text.strip()

            title_el = entry.find("atom:title", NS)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            link_el = entry.find("atom:link", NS)
            url = link_el.attrib.get("href", "") if link_el is not None else ""
            if not url:
                url = f"https://www.youtube.com/watch?v={video_id}"

            published_el = entry.find("atom:published", NS)
            published = published_el.text.strip() if published_el is not None and published_el.text else ""

            author_el = entry.find("atom:author", NS)
            channel_name = ""
            if author_el is not None:
                name_el = author_el.find("atom:name", NS)
                if name_el is not None and name_el.text:
                    channel_name = name_el.text.strip()

            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

            videos.append(YouTubeVideo(
                video_id=video_id,
                title=title,
                url=url,
                channel_name=channel_name,
                channel_id=channel_id,
                thumbnail_url=thumbnail_url,
                published=published,
            ))

        logger.info(
            "Found %s video(s) for channel %s (%s)",
            len(videos), channel_id, channel_name or "?",
        )
        return videos
