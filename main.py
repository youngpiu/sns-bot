from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests

from config import load_settings
from providers.fans import FansClient, FansNotification, FansAuthError, FansAPIError
from providers.ig import InstagramClient, InstagramLoginError, InstagramMedia
from providers.yt import YouTubeRSS, YouTubeVideo
from state import IG_STATE_FILE, FANS_STATE_FILE, YT_STATE_FILE, FansStateStore, InstagramStateStore, YouTubeStateStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
for noisy in ("instagrapi", "private_request"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class DiscordWebhookError(RuntimeError):
    pass


def build_discord_payload(role_id: str, media: InstagramMedia) -> dict[str, object]:
    caption = media.caption_text.strip() or "(no caption)"
    return {
        "content": f"```{caption}```\n<@&{role_id}>",
        "allowed_mentions": {
            "parse": [],
            "roles": [role_id],
        },
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Xem trên Instagram",
                        "url": media.url,
                    }
                ],
            }
        ],
    }


def build_fans_discord_payload(
    role_id: str,
    notif: FansNotification,
    body: str | None = None,
) -> dict[str, object]:
    content = body or notif.message or "(no message)"
    caption = content.strip()[:2000] or "(no message)"
    return {
        "content": f"```{caption}```\n<@&{role_id}>",
        "allowed_mentions": {
            "parse": [],
            "roles": [role_id],
        },
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Xem trên Fans",
                        "url": notif.url,
                    }
                ],
            }
        ],
    }


def build_yt_discord_payload(role_id: str, video: YouTubeVideo) -> dict[str, object]:
    title = video.title.strip()[:2000] or "(no title)"
    return {
        "content": f"```{title}```\n<@&{role_id}>",
        "allowed_mentions": {
            "parse": [],
            "roles": [role_id],
        },
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Xem trên YouTube",
                        "url": video.url,
                    }
                ],
            }
        ],
    }


def extension_from_response(url: str, content_type: str | None) -> str:
    parsed_path = urlparse(url).path
    path_extension = Path(parsed_path).suffix
    if path_extension:
        return path_extension

    guessed_extension = mimetypes.guess_extension(content_type or "")
    return guessed_extension or ".bin"


def download_media_files(media: InstagramMedia) -> tuple[Path, list[Path]]:
    return download_urls(
        urls=media.media_urls[:10],
        prefix=f"sns-bot-{media.pk}-",
        log_label=f"pk={media.pk}",
    )


def _convert_heic(path: Path) -> Path:
    ext = path.suffix.lower()
    if ext not in (".heic", ".heif"):
        return path
    try:
        from PIL import Image
        import pillow_heif

        pillow_heif.register_heif_opener()
        img = Image.open(path)
        new_path = path.with_suffix(".jpg")
        img.save(new_path, "JPEG", quality=95)
        path.unlink()
        logger.info("Converted %s to %s", path.name, new_path.name)
        return new_path
    except Exception as exc:
        logger.warning("Failed to convert HEIC %s: %s", path.name, exc)
        return path


def _crop_black_bars(path: Path, threshold: int = 15) -> Path:
    try:
        from PIL import Image

        img = Image.open(path)
        gray = img.convert("L")
        w, h = gray.size
        pix = list(gray.getdata())

        top = 0
        for y in range(h):
            if max(pix[y * w : (y + 1) * w]) > threshold:
                top = y
                break

        bottom = h
        for y in range(h - 1, -1, -1):
            if max(pix[y * w : (y + 1) * w]) > threshold:
                bottom = y + 1
                break

        left = 0
        for x in range(w):
            if max(pix[y * w + x] for y in range(h)) > threshold:
                left = x
                break

        right = w
        for x in range(w - 1, -1, -1):
            if max(pix[y * w + x] for y in range(h)) > threshold:
                right = x + 1
                break

        if left == 0 and top == 0 and right == w and bottom == h:
            return path

        img_rgb = img.convert("RGB")
        cropped = img_rgb.crop((left, top, right, bottom))
        cropped.save(path, "JPEG", quality=95)
        logger.info(
            "Cropped black bars from %s: %sx%s -> %sx%s",
            path.name, w, h, right - left, bottom - top,
        )
    except Exception as exc:
        logger.warning("Failed to crop black bars for %s: %s", path.name, exc)
    return path


def _download_yt_thumbnail(video_id: str) -> tuple[Path, list[Path]]:
    temp_dir = Path(tempfile.mkdtemp(prefix=f"yt-{video_id}-"))
    for size in ("maxresdefault", "sddefault", "hqdefault", "mqdefault"):
        url = f"https://i.ytimg.com/vi/{video_id}/{size}.jpg"
        try:
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
            extension = extension_from_response(url, response.headers.get("content-type"))
            output_path = temp_dir / f"01{extension}"
            with output_path.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        fh.write(chunk)
            output_path = _convert_heic(output_path)
            output_path = _crop_black_bars(output_path)
            logger.info("Downloaded YouTube thumbnail %s for %s (size=%s)", size, video_id, output_path.stat().st_size)
            return temp_dir, [output_path]
        except Exception as exc:
            logger.warning("YouTube thumbnail %s not available for %s: %s", size, video_id, exc)
            continue
    logger.warning("No YouTube thumbnail available for %s", video_id)
    shutil.rmtree(temp_dir, ignore_errors=True)
    return temp_dir, []


def download_urls(
    urls: list[str],
    prefix: str,
    log_label: str = "",
) -> tuple[Path, list[Path]]:
    temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    downloaded_files: list[Path] = []

    for index, url in enumerate(urls, start=1):
        logger.info("Downloading file %s/%s for %s", index, len(urls), log_label)
        try:
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()

            extension = extension_from_response(url, response.headers.get("content-type"))
            output_path = temp_dir / f"{index:02d}{extension}"
            with output_path.open("wb") as file_handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        file_handle.write(chunk)

            output_path = _convert_heic(output_path)
            output_path = _crop_black_bars(output_path)
            downloaded_files.append(output_path)
            logger.info(
                "Downloaded file %s/%s for %s as %s (%s bytes)",
                index,
                len(urls),
                log_label,
                output_path.name,
                output_path.stat().st_size,
            )
        except Exception as exc:
            logger.warning("Failed to download %s: %s", url, exc)
            continue

    return temp_dir, downloaded_files


MAX_FILES_PER_WEBHOOK = 10


def _without_role_mention(payload: dict[str, object], role_id: str) -> dict[str, object]:
    p = dict(payload)
    if isinstance(p.get("content"), str):
        p["content"] = p["content"].replace(f"\n<@&{role_id}>", "\u3164")
    p["allowed_mentions"] = {"parse": [], "roles": []}
    return p


def send_discord_webhook(
    webhook_url: str,
    payload: dict[str, object],
    attachment_paths: list[Path],
) -> None:
    chunks = [
        attachment_paths[i : i + MAX_FILES_PER_WEBHOOK]
        for i in range(0, len(attachment_paths), MAX_FILES_PER_WEBHOOK)
    ]

    has_components = bool(payload.get("components"))

    for chunk_idx, chunk in enumerate(chunks):
        file_handles: list[object] = []
        files: list[tuple[str, tuple[str, object, str]]] = []

        try:
            for index, path in enumerate(chunk):
                file_handle = path.open("rb")
                file_handles.append(file_handle)
                mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files.append((f"files[{index}]", (path.name, file_handle, mime_type)))

            if len(chunks) == 1:
                chunk_payload = payload
            elif chunk_idx == 0:
                chunk_payload = dict(payload)
                chunk_payload.pop("components", None)
            elif chunk_idx == len(chunks) - 1 and has_components:
                chunk_payload = {"content": "", "components": payload["components"]}
            else:
                chunk_payload = {"content": ""}
            logger.info(
                "Sending Discord webhook chunk %s/%s with %s attachment(s)",
                chunk_idx + 1,
                len(chunks),
                len(files),
            )
            response = requests.post(webhook_url,
                data={"payload_json": json.dumps(chunk_payload, ensure_ascii=False)},
                files=files or None,
                timeout=120,
            )
            if response.status_code >= 400:
                raise DiscordWebhookError(
                    f"Discord webhook failed with status {response.status_code}: {response.text}"
                )
            logger.info(
                "Discord webhook chunk %s/%s accepted with status %s",
                chunk_idx + 1,
                len(chunks),
                response.status_code,
            )
        finally:
            for file_handle in file_handles:
                file_handle.close()


async def poll_instagram(
    instagram: InstagramClient,
    state_store: InstagramStateStore,
    webhook_url: str,
    role_id: str,
    target_username: str,
    interval_seconds: int,
    thread_id: str | None = None,
) -> None:
    state_store.load()
    logger.info("Instagram polling started with %s second interval", interval_seconds)

    while True:
        try:
            recent_medias = await asyncio.to_thread(instagram.recent_medias)
            if not recent_medias:
                logger.info("No Instagram media found for @%s", target_username)
                continue

            if not state_store.is_initialized():
                state_store.mark_seen(recent_medias[0].pk)
                state_store.save()
                logger.info("Initialized Instagram state with media pk=%s", state_store.last_seen_pk)
                continue

            new_medias: list[InstagramMedia] = []
            for media in recent_medias:
                if media.pk == state_store.last_seen_pk:
                    break
                new_medias.append(media)

            if not new_medias:
                logger.info("No new Instagram media for @%s", target_username)
            else:
                logger.info("Found %s new Instagram media item(s) for @%s", len(new_medias), target_username)

            for media in new_medias:
                logger.info(
                    "New Instagram media detected pk=%s url_count=%s type=%s product_type=%s",
                    media.pk,
                    len(media.media_urls),
                    media.media_type,
                    media.product_type,
                )
                payload = build_discord_payload(role_id, media)
                temp_dir: Path | None = None
                try:
                    temp_dir, attachment_paths = await asyncio.to_thread(download_media_files, media)
                    base_url = f"{webhook_url}?wait=true&with_components=true"
                    send_tasks = [
                        asyncio.to_thread(send_discord_webhook, base_url, payload, attachment_paths),
                    ]
                    if thread_id:
                        thread_url = f"{webhook_url}?wait=true&with_components=true&thread_id={thread_id}"
                        thread_payload = _without_role_mention(payload, role_id)
                        send_tasks.append(
                            asyncio.to_thread(send_discord_webhook, thread_url, thread_payload, attachment_paths),
                        )
                    await asyncio.gather(*send_tasks)
                finally:
                    if temp_dir is not None:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        logger.info("Cleaned temporary media files for pk=%s", media.pk)
                state_store.mark_seen(media.pk)
                state_store.save()
                logger.info("Sent Discord webhook notification for Instagram media pk %s", media.pk)
        except InstagramLoginError as exc:
            logger.error("Instagram login failed: %s", exc)
        except DiscordWebhookError as exc:
            logger.error("Discord webhook send failed: %s", exc)
        except Exception:
            logger.exception("Instagram polling cycle failed")

        await asyncio.sleep(interval_seconds)


async def poll_fans(
    fans: FansClient,
    state_store: FansStateStore,
    webhook_url: str,
    role_id: str,
    target_group: str,
    interval_seconds: int,
    thread_id: str | None = None,
) -> None:
    state_store.load()
    logger.info("Fans polling started for %s with %s second interval", target_group, interval_seconds)

    while True:
        try:
            notifs = await asyncio.to_thread(fans.recent_notifications)
            rt = fans.auth.get_refresh_token()
            if rt and rt != state_store.refresh_token:
                state_store.set_refresh_token(rt)
                state_store.save()
            if not notifs:
                logger.info("No Fans notifications found for %s", target_group)
                continue

            if not state_store.is_initialized():
                state_store.mark_seen(notifs[0].id)
                state_store.save()
                logger.info(
                    "Initialized Fans state with newest notification %s for %s",
                    notifs[0].id, target_group,
                )
                continue

            new_notifs: list[FansNotification] = []
            for n in notifs:
                if n.id == state_store.last_seen_id:
                    break
                new_notifs.append(n)

            if not new_notifs:
                logger.info("No new Fans notifications for %s", target_group)
            else:
                logger.info("Found %s new Fans notification(s) for %s", len(new_notifs), target_group)

            for notif in new_notifs:
                    logger.info(
                        "New Fans notification id=%s category=%s group=%s",
                        notif.id,
                        notif.category,
                        notif.group_name,
                    )

                    slug = None
                    try:
                        path = urlparse(notif.url).path.rstrip("/")
                        slug = path.split("/")[-1]
                    except Exception:
                        pass

                    body = None
                    post_time = None
                    attachment_urls: list[str] = []
                    if slug:
                        try:
                            post = fans.get_post_detail(slug)
                            if post:
                                body = post.get("body")
                                post_time = post.get("firstActivatedAt")
                                atts = post.get("attachments") or []
                                for a in atts:
                                    u = a.get("url")
                                    if u:
                                        attachment_urls.append(u)
                        except Exception as exc:
                            logger.warning("Failed to fetch Fans post detail for slug=%s: %s", slug, exc)

                    temp_dir: Path | None = None
                    try:
                        if attachment_urls:
                            temp_dir, paths = await asyncio.to_thread(
                                download_urls,
                                attachment_urls,
                                prefix=f"fans-{notif.id}-",
                                log_label=f"notif={notif.id}",
                            )
                        else:
                            paths = []

                        payload = build_fans_discord_payload(role_id, notif, body)
                        base_url = f"{webhook_url}?wait=true&with_components=true"
                        send_tasks = [
                            asyncio.to_thread(send_discord_webhook, base_url, payload, paths),
                        ]
                        if thread_id:
                            thread_url = f"{webhook_url}?wait=true&with_components=true&thread_id={thread_id}"
                            thread_payload = _without_role_mention(payload, role_id)
                            send_tasks.append(
                                asyncio.to_thread(send_discord_webhook, thread_url, thread_payload, paths),
                            )
                        await asyncio.gather(*send_tasks)
                    except DiscordWebhookError as exc:
                        logger.error("Discord webhook send failed: %s", exc)
                        continue
                    finally:
                        if temp_dir is not None:
                            shutil.rmtree(temp_dir, ignore_errors=True)

                    state_store.mark_seen(notif.id)
                    state_store.save()
                    logger.info(
                        "Sent Discord webhook for Fans notification id=%s with %s attachment(s)",
                        notif.id,
                        len(attachment_urls),
                    )

        except FansAuthError as exc:
            logger.error("Fans auth failed: %s", exc)
        except FansAPIError as exc:
            logger.error("Fans API error: %s", exc)
        except Exception:
            logger.exception("Fans polling cycle failed")

        await asyncio.sleep(interval_seconds)


async def poll_youtube(
    rss_client: YouTubeRSS,
    state_store: YouTubeStateStore,
    webhook_url: str,
    role_id: str,
    channel_ids: list[str],
    interval_seconds: int,
    thread_id: str | None = None,
) -> None:
    state_store.load()
    logger.info(
        "YouTube polling started for %s channel(s) with %s second interval",
        len(channel_ids), interval_seconds,
    )

    while True:
        try:
            for channel_id in channel_ids:
                videos = await asyncio.to_thread(rss_client.fetch_channel, channel_id)
                if not videos:
                    continue

                if not state_store.is_initialized():
                    state_store.mark_seen(videos[0].video_id)
                    state_store.save()
                    logger.info(
                        "Initialized YouTube state with newest video %s for channel %s",
                        videos[0].video_id, channel_id,
                    )
                    continue

                new_videos: list[YouTubeVideo] = []
                for v in videos:
                    if v.video_id == state_store.last_seen_id:
                        break
                    new_videos.append(v)

                if not new_videos:
                    logger.info("No new YouTube videos for channel %s", channel_id)
                else:
                    logger.info(
                        "Found %s new YouTube video(s) for channel %s",
                        len(new_videos), channel_id,
                    )

                for video in new_videos:
                        logger.info(
                            "New YouTube video id=%s title=%s channel=%s",
                            video.video_id, video.title[:80], video.channel_name,
                        )
                        payload = build_yt_discord_payload(role_id, video)
                        temp_dir: Path | None = None
                        try:
                            temp_dir, paths = await asyncio.to_thread(
                                _download_yt_thumbnail, video.video_id,
                            )

                            base_url = f"{webhook_url}?wait=true&with_components=true"
                            send_tasks = [
                                asyncio.to_thread(send_discord_webhook, base_url, payload, paths),
                            ]
                            if thread_id:
                                thread_url = f"{webhook_url}?wait=true&with_components=true&thread_id={thread_id}"
                                thread_payload = _without_role_mention(payload, role_id)
                                send_tasks.append(
                                    asyncio.to_thread(
                                        send_discord_webhook, thread_url, thread_payload, paths,
                                    ),
                                )
                            await asyncio.gather(*send_tasks)
                        except DiscordWebhookError as exc:
                            logger.error("Discord webhook send failed: %s", exc)
                            continue
                        finally:
                            if temp_dir is not None:
                                shutil.rmtree(temp_dir, ignore_errors=True)

                        state_store.mark_seen(video.video_id)
                        state_store.save()
                        logger.info("Sent Discord webhook for YouTube video %s", video.video_id)

        except requests.RequestException as exc:
            logger.error("YouTube RSS fetch failed: %s", exc)
        except Exception:
            logger.exception("YouTube polling cycle failed")

        await asyncio.sleep(interval_seconds)


async def run_all(settings) -> None:
    tasks = []

    ig_client = InstagramClient(
        target_username=settings.ig_target,
        session_file=settings.instagram_session_file,
        sessionid=settings.ig_sessionid,
        proxy=settings.ig_proxy,
    )
    ig_state = InstagramStateStore(IG_STATE_FILE)
    tasks.append(
        poll_instagram(
            instagram=ig_client,
            state_store=ig_state,
            webhook_url=settings.webhook_url,
            role_id=settings.role_id,
            target_username=settings.ig_target,
            interval_seconds=settings.poll_interval,
            thread_id=settings.ig_thread_id,
        )
    )

    if settings.fans_token and settings.fans_target:
        fans_state = FansStateStore(FANS_STATE_FILE).load()
        cu = fans_state.client_uuid or settings.fans_client_uuid
        g = fans_state.guid or settings.fans_guid
        fans_client = FansClient(
            token=settings.fans_token,
            client_uuid=cu,
            guid=g,
            target_group=settings.fans_target,
            refresh_token=fans_state.refresh_token or settings.fans_refresh_token,
        )
        # persist auto-generated client_uuid/guid back to state
        if fans_state.client_uuid != fans_client.auth._client_uuid:
            fans_state.set_client_uuid(fans_client.auth._client_uuid)
        if fans_state.guid != fans_client.auth._guid:
            fans_state.set_guid(fans_client.auth._guid)
        if fans_state.client_uuid is not None or fans_state.guid is not None:
            fans_state.save()
        fans_webhook = settings.fans_webhook_url or settings.webhook_url
        fans_role = settings.fans_role_id or settings.role_id
        tasks.append(
            poll_fans(
                fans=fans_client,
                state_store=fans_state,
                webhook_url=fans_webhook,
                role_id=fans_role,
                target_group=settings.fans_target,
                interval_seconds=settings.fans_poll_interval,
                thread_id=settings.fans_thread_id,
            )
        )
    else:
        logger.info("Fans credentials not provided, skipping Fans polling")

    if settings.yt_targets:
        yt_client = YouTubeRSS()
        yt_webhook = settings.yt_webhook_url or settings.webhook_url
        yt_role = settings.yt_role_id or settings.role_id
        yt_state = YouTubeStateStore(YT_STATE_FILE)
        tasks.append(
            poll_youtube(
                rss_client=yt_client,
                state_store=yt_state,
                webhook_url=yt_webhook,
                role_id=yt_role,
                channel_ids=list(settings.yt_targets),
                interval_seconds=settings.yt_poll_interval,
                thread_id=settings.yt_thread_id,
            )
        )
    else:
        logger.info("YouTube targets not provided, skipping YouTube polling")

    await asyncio.gather(*tasks)


def main() -> None:
    settings = load_settings()
    asyncio.run(run_all(settings))


if __name__ == "__main__":
    main()
