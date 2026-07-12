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

from pyngrok import ngrok
from ytnoti import AsyncYouTubeNotifier
from ytnoti.models.video import Video

from config import BASE_DIR, load_settings
from providers import translator
from providers.fans import FansClient, FansNotification, FansAuthError, FansAPIError
from providers.ig import InstagramClient, InstagramLoginError, InstagramMedia
from providers.fans import FansSessionStore
from providers.twitter import TwitterClient, TwitterTweet, TwitterLoginError
from state import IG_STATE_FILE, FANS_STATE_FILE, YT_STATE_FILE, TWITTER_STATE_FILE, FansStateStore, InstagramStateStore, YouTubeStateStore, TwitterStateStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
for noisy in ("instagrapi", "private_request", "pyngrok", "httpx"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class DiscordWebhookError(RuntimeError):
    pass


async def build_discord_payload(role_id: str, media: InstagramMedia) -> dict[str, object]:
    raw = media.caption_text.strip()
    caption = await translator.translate(raw) if raw else "(no caption)"
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


async def build_fans_discord_payload(
    role_id: str,
    notif: FansNotification,
    body: str | None = None,
    author: str | None = None,
) -> dict[str, object]:
    raw = (body or notif.message or "").strip()
    content = await translator.translate(raw) if raw else "(no message)"
    caption = content[:2000] or "(no message)"
    if author:
        caption = f"{author}: {caption}"
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


async def build_twitter_discord_payload(role_id: str, tweet: TwitterTweet) -> dict[str, object]:
    import re
    raw = tweet.text.strip()
    raw = re.sub(r"https://t\.co/\w+", "", raw).strip()
    text = (await translator.translate(raw))[:2000] if raw else "(no content)"
    return {
        "content": f"```{text}```\n<@&{role_id}>",
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
                        "label": "Xem trên Twitter",
                        "url": tweet.url,
                    }
                ],
            }
        ],
    }


async def build_yt_discord_payload(role_id: str, video: YouTubeVideo) -> dict[str, object]:
    raw = video.title.strip()
    title = (await translator.translate(raw))[:2000] if raw else "(no title)"
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
        logger.info("Đã chuyển %s sang %s", path.name, new_path.name)
        return new_path
    except Exception as exc:
        logger.warning("Chuyển HEIC %s thất bại: %s", path.name, exc)
        return path


def _crop_black_bars(path: Path, threshold: int = 15) -> Path:
    try:
        from PIL import Image

        img = Image.open(path)
        gray = img.convert("L")
        w, h = gray.size
        pix = gray.get_flattened_data()

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
            "Đã cắt viền đen từ %s: %sx%s -> %sx%s",
            path.name, w, h, right - left, bottom - top,
        )
    except Exception as exc:
        logger.warning("Cắt viền đen cho %s thất bại: %s", path.name, exc)
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
            logger.info("Đã tải YouTube thumbnail %s cho %s (dung lượng=%s)", size, video_id, output_path.stat().st_size)
            return temp_dir, [output_path]
        except Exception as exc:
            logger.warning("YouTube thumbnail %s không có cho %s: %s", size, video_id, exc)
            continue
    logger.warning("Không có YouTube thumbnail nào cho %s", video_id)
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
        logger.info("Đang tải file %s/%s cho %s", index, len(urls), log_label)
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
                "Đã tải file %s/%s cho %s thành %s (%s bytes)",
                index,
                len(urls),
                log_label,
                output_path.name,
                output_path.stat().st_size,
            )
        except Exception as exc:
            logger.warning("Tải %s thất bại: %s", url, exc)
            continue

    return temp_dir, downloaded_files


def send_error_alert(webhook_url: str | None, message: str) -> None:
    if not webhook_url:
        return
    try:
        requests.post(
            webhook_url,
            json={"content": f"```\n{message[:1800]}\n```"},
            timeout=30,
        )
    except Exception as exc:
        logger.warning("Gửi error alert thất bại: %s", exc)


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
        for i in range(0, max(len(attachment_paths), 1), MAX_FILES_PER_WEBHOOK)
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
                "Đang gửi Discord webhook chunk %s/%s với %s file đính kèm",
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
                "Discord webhook chunk %s/%s đã gửi thành công (status %s)",
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
    error_webhook_url: str | None = None,
) -> None:
    state_store.load()
    logger.info("Bắt đầu poll Instagram, chu kỳ %s giây", interval_seconds)

    while True:
        try:
            recent_medias = await asyncio.to_thread(instagram.recent_medias)
            if not recent_medias:
                logger.info("Không có media Instagram nào cho @%s", target_username)
                continue

            if not state_store.is_initialized():
                state_store.mark_seen(recent_medias[0].pk)
                state_store.save()
                logger.info("Đã khởi tạo state Instagram với media pk=%s", state_store.last_seen_pk)
                continue

            new_medias: list[InstagramMedia] = []
            for media in recent_medias:
                if media.pk == state_store.last_seen_pk:
                    break
                new_medias.append(media)

            if not new_medias:
                logger.info("Không có media Instagram mới cho @%s", target_username)
            else:
                logger.info("Tìm thấy %s media Instagram mới cho @%s", len(new_medias), target_username)

            for media in new_medias:
                logger.info(
                    "Media Instagram mới: pk=%s url_count=%s type=%s product_type=%s",
                    media.pk,
                    len(media.media_urls),
                    media.media_type,
                    media.product_type,
                )
                payload = await build_discord_payload(role_id, media)
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
                        logger.info("Đã dọn file tạm cho pk=%s", media.pk)
                state_store.mark_seen(media.pk)
                state_store.save()
                logger.info("Đã gửi Discord webhook cho Instagram media pk %s", media.pk)
        except InstagramLoginError as exc:
            logger.error("Đăng nhập Instagram thất bại, dừng task: %s", exc)
            send_error_alert(error_webhook_url, f"Instagram @{target_username} login failed: {exc}")
            return
        except DiscordWebhookError as exc:
            logger.error("Gửi Discord webhook thất bại: %s", exc)
        except Exception:
            logger.exception("Instagram lỗi, dừng task")
            send_error_alert(error_webhook_url, f"Instagram @{target_username} stopped: unexpected error")
            return

        await asyncio.sleep(interval_seconds)


async def poll_fans(
    fans: FansClient,
    state_store: FansStateStore,
    webhook_url: str,
    role_id: str,
    target_group: str,
    interval_seconds: int,
    thread_id: str | None = None,
    error_webhook_url: str | None = None,
) -> None:
    state_store.load()
    logger.info("Bắt đầu poll Fans cho %s, chu kỳ %s giây", target_group, interval_seconds)

    while True:
        try:
            notifs = await asyncio.to_thread(fans.recent_notifications)
            if not notifs:
                logger.info("Không có thông báo Fans nào cho %s", target_group)
                continue

            if not state_store.is_initialized():
                state_store.mark_seen(notifs[0].id)
                state_store.save()
                logger.info(
                    "Đã khởi tạo state Fans với thông báo mới nhất %s cho %s",
                    notifs[0].id, target_group,
                )
                continue

            new_notifs: list[FansNotification] = []
            for n in notifs:
                if n.id == state_store.last_seen_id:
                    break
                new_notifs.append(n)

            if not new_notifs:
                logger.info("Không có thông báo Fans mới cho %s", target_group)
            else:
                logger.info("Tìm thấy %s thông báo Fans mới cho %s", len(new_notifs), target_group)

            for notif in new_notifs:
                    logger.info(
                        "Thông báo Fans mới: id=%s category=%s group=%s",
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
                    author: str | None = None
                    attachment_urls: list[str] = []
                    if slug:
                        try:
                            post = fans.get_post_detail(slug)
                            if post:
                                body = post.get("body")
                                post_time = post.get("firstActivatedAt")
                                member = post.get("member") or {}
                                artist = member.get("artist") or {}
                                code = artist.get("code", "")
                                if code:
                                    author = code[0].upper() + code[1:]
                                atts = post.get("attachments") or []
                                for a in atts:
                                    u = a.get("url")
                                    if u:
                                        attachment_urls.append(u)
                        except Exception as exc:
                            logger.warning("Lấy chi tiết bài Fans slug=%s thất bại: %s", slug, exc)

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

                        payload = await build_fans_discord_payload(role_id, notif, body, author)
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
                        logger.error("Gửi Discord webhook thất bại: %s", exc)
                        continue
                    finally:
                        if temp_dir is not None:
                            shutil.rmtree(temp_dir, ignore_errors=True)

                    state_store.mark_seen(notif.id)
                    state_store.save()
                    logger.info(
                        "Đã gửi Discord webhook cho Fans notification id=%s với %s file đính kèm",
                        notif.id,
                        len(attachment_urls),
                    )

        except FansAuthError as exc:
            logger.error("Xác thực Fans thất bại, dừng task: %s", exc)
            send_error_alert(error_webhook_url, f"Fans {target_group} auth failed: {exc}")
            return
        except FansAPIError as exc:
            logger.error("Fans API lỗi: %s", exc)
        except Exception:
            logger.exception("Fans lỗi, dừng task")
            send_error_alert(error_webhook_url, f"Fans {target_group} stopped: unexpected error")
            return

        await asyncio.sleep(interval_seconds)


async def poll_twitter(
    twitter: TwitterClient,
    state_store: TwitterStateStore,
    webhook_url: str,
    role_id: str,
    target_username: str,
    interval_seconds: int,
    thread_id: str | None = None,
    error_webhook_url: str | None = None,
) -> None:
    state_store.load()
    logger.info("Bắt đầu poll Twitter cho @%s, chu kỳ %s giây", target_username, interval_seconds)

    try:
        await twitter.authenticate()
    except TwitterLoginError as exc:
        logger.error("Twitter không được xác thực, bỏ qua: %s", exc)
        send_error_alert(error_webhook_url, f"Twitter @{target_username} auth failed: {exc}")
        return

    while True:
        try:
            tweets = await twitter.recent_tweets()
            if not tweets:
                logger.info("Không có tweet Twitter nào cho @%s", target_username)
                await asyncio.sleep(interval_seconds)
                continue

            if not state_store.is_initialized():
                state_store.mark_seen(tweets[0].id)
                state_store.save()
                logger.info("Đã khởi tạo state Twitter với tweet id=%s", state_store.last_seen_id)
                await asyncio.sleep(interval_seconds)
                continue

            new_tweets: list[TwitterTweet] = []
            for t in tweets:
                if t.id == state_store.last_seen_id:
                    break
                new_tweets.append(t)

            if not new_tweets:
                logger.info("Không có tweet Twitter mới cho @%s", target_username)
            else:
                logger.info("Tìm thấy %s tweet Twitter mới cho @%s", len(new_tweets), target_username)

            for tweet in new_tweets:
                logger.info(
                    "Tweet Twitter mới: id=%s media_count=%s",
                    tweet.id,
                    len(tweet.media_urls),
                )
                payload = await build_twitter_discord_payload(role_id, tweet)
                temp_dir: Path | None = None
                try:
                    if tweet.media_urls:
                        temp_dir, paths = await asyncio.to_thread(
                            download_urls,
                            tweet.media_urls[:10],
                            prefix=f"twitter-{tweet.id}-",
                            log_label=f"tweet={tweet.id}",
                        )
                    else:
                        paths = []

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
                    logger.error("Gửi Discord webhook thất bại: %s", exc)
                    continue
                finally:
                    if temp_dir is not None:
                        shutil.rmtree(temp_dir, ignore_errors=True)

                state_store.mark_seen(tweet.id)
                state_store.save()
                logger.info("Đã gửi Discord webhook cho Twitter tweet %s", tweet.id)
        except Exception:
            logger.warning("Twitter lỗi, dừng task", exc_info=True)
            send_error_alert(error_webhook_url, f"Twitter @{target_username} stopped: unexpected error")
            return

        await asyncio.sleep(interval_seconds)


def _normalize_channel_id(cid: str) -> str:
    cid = cid.strip()
    if cid.startswith("UU"):
        return "UC" + cid[2:]
    return cid


async def _handle_yt_video(
    video: Video,
    webhook_url: str,
    role_id: str,
    thread_id: str | None,
    state_store: YouTubeStateStore,
) -> None:
    channel_id = video.channel.id
    if not state_store.is_new(channel_id, video.id):
        return

    logger.info("Video YouTube mới: id=%s title=%s channel=%s", video.id, video.title[:80], video.channel.name)

    yt_title = await translator.translate(video.title)
    payload = {
        "content": f"```{yt_title[:2000]}```\n<@&{role_id}>",
        "allowed_mentions": {"parse": [], "roles": [role_id]},
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

    temp_dir: Path | None = None
    try:
        temp_dir, paths = await asyncio.to_thread(_download_yt_thumbnail, video.id)
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
        logger.error("Gửi Discord webhook thất bại: %s", exc)
        return
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)

    state_store.mark_seen(channel_id, video.id)
    state_store.save()
    logger.info("Đã gửi Discord webhook cho YouTube video %s", video.id)



async def run_all(settings) -> None:
    translator.init(settings.gemini_api_key, BASE_DIR / "prompt.txt")
    tasks = []

    ig_client = InstagramClient(
        target_username=settings.ig_target,
        session_file=settings.instagram_session_file,
        username=settings.ig_username or "",
        password=settings.ig_password or "",
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
            error_webhook_url=settings.error_webhook_url,
        )
    )

    if settings.fans_target:
        try:
            fans_session = FansSessionStore().load()
            fans_client = FansClient(session=fans_session, target_group=settings.fans_target)
        except FansAuthError:
            fans_client = None
            fans_session = FansSessionStore()
        if fans_client is not None:
            if not settings.fans_webhook_url or not settings.fans_role_id:
                logger.warning("Thiếu FANS_WEBHOOK hoặc FANS_ROLE trong .env — bỏ qua Fans")
            else:
                fans_state = FansStateStore(FANS_STATE_FILE).load()
                tasks.append(
                    poll_fans(
                        fans=fans_client,
                        state_store=fans_state,
                        webhook_url=settings.fans_webhook_url,
                        role_id=settings.fans_role_id,
                        target_group=settings.fans_target,
                        interval_seconds=settings.fans_poll_interval,
                        thread_id=settings.fans_thread_id,
                        error_webhook_url=settings.error_webhook_url,
                    )
                )
        else:
            logger.info("Không tìm thấy Fans session, bỏ qua poll Fans (chạy: python -m login)")

    if settings.yt_targets:
        if not settings.yt_webhook_url or not settings.yt_role_id:
            logger.warning("Thiếu YT_WEBHOOK hoặc YT_ROLE trong .env — bỏ qua YouTube")
        elif not settings.ngrok_token:
            logger.warning("Cần NGROK_TOKEN để dùng YouTube push notification — bỏ qua YouTube")
        else:
            yt_state = YouTubeStateStore(YT_STATE_FILE).load()
            tasks.append(
                _yt_push_notifier(
                    ngrok_token=settings.ngrok_token,
                    yt_targets=settings.yt_targets,
                    yt_webhook=settings.yt_webhook_url,
                    yt_role=settings.yt_role_id,
                    yt_thread_id=settings.yt_thread_id,
                    yt_state=yt_state,
                    error_webhook_url=settings.error_webhook_url,
                )
            )

    if settings.twitter_target:
        if not settings.twitter_auth_token:
            logger.warning("Cần TWITTER_AUTH_TOKEN trong .env để dùng Twitter — bỏ qua")
        elif not settings.twitter_webhook_url or not settings.twitter_role_id:
            logger.warning("Thiếu TWITTER_WEBHOOK hoặc TWITTER_ROLE trong .env — bỏ qua Twitter")
        else:
            twitter_client = TwitterClient(
                target_username=settings.twitter_target,
                auth_token=settings.twitter_auth_token,
            )
            twitter_state = TwitterStateStore(TWITTER_STATE_FILE).load()
            tasks.append(
                poll_twitter(
                    twitter=twitter_client,
                    state_store=twitter_state,
                    webhook_url=settings.twitter_webhook_url,
                    role_id=settings.twitter_role_id,
                    target_username=settings.twitter_target,
                    interval_seconds=settings.twitter_poll_interval,
                    thread_id=settings.twitter_thread_id,
                    error_webhook_url=settings.error_webhook_url,
                )
            )

    if not tasks:
        logger.info("Không có tác vụ nào để chạy")
        return

    await asyncio.gather(*tasks)


async def _yt_push_notifier(
    ngrok_token: str,
    yt_targets: tuple[str, ...],
    yt_webhook: str,
    yt_role: str,
    yt_thread_id: str | None,
    yt_state: YouTubeStateStore,
    error_webhook_url: str | None = None,
) -> None:
    channel_ids = [_normalize_channel_id(c) for c in yt_targets]
    logger.info("Bắt đầu YouTube notifier với %s kênh", len(yt_targets))

    for attempt in range(3):
        ngrok.set_auth_token(ngrok_token)
        notifier = AsyncYouTubeNotifier()

        @notifier.upload()
        async def on_yt_upload(video: Video) -> None:
            await _handle_yt_video(video, yt_webhook, yt_role, yt_thread_id, yt_state)

        yt_task = asyncio.create_task(notifier.run())

        ok = False
        try:
            start = asyncio.get_event_loop().time()
            while not notifier.is_ready:
                if yt_task.done():
                    try:
                        yt_task.result()
                    except Exception as exc:
                        if attempt < 2:
                            logger.warning("YouTube notifier lỗi lần %s: %s — thử lại...", attempt + 1, exc)
                        else:
                            logger.warning("YouTube notifier thất bại sau 3 lần: %s", exc)
                            send_error_alert(error_webhook_url, f"YouTube notifier failed after 3 attempts: {exc}")
                    break
                if asyncio.get_event_loop().time() - start > 30:
                    raise asyncio.TimeoutError
                await asyncio.sleep(1)
            else:
                try:
                    await notifier.subscribe(channel_ids)
                except Exception as exc:
                    if attempt < 2:
                        logger.warning("YouTube subscribe lỗi lần %s: %s — thử lại...", attempt + 1, exc)
                    else:
                        logger.warning("YouTube subscribe thất bại sau 3 lần: %s", exc)
                        send_error_alert(error_webhook_url, f"YouTube subscribe failed after 3 attempts: {exc}")
                    break

                logger.info("YouTube notifier đã sẵn sàng")
                ok = True
                await yt_task
        except asyncio.TimeoutError:
            if attempt < 2:
                logger.warning("YouTube push không khả dụng (ngrok) sau 30s lần %s — thử lại...", attempt + 1)
            else:
                logger.warning("YouTube push không khả dụng (ngrok) sau 30s — bỏ qua")
                send_error_alert(error_webhook_url, "YouTube push unavailable (ngrok timeout) after 3 attempts")

        if ok:
            return

        notifier.stop()
        yt_task.cancel()
        try:
            await yt_task
        except (asyncio.CancelledError, Exception):
            pass

        if attempt < 2:
            await asyncio.sleep(3)


def main() -> None:
    settings = load_settings()
    asyncio.run(run_all(settings))


if __name__ == "__main__":
    main()
