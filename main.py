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
from state import STATE_FILE, FANS_STATE_FILE, BotState, FansStateStore, StateStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
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


def state_from_media(media: InstagramMedia) -> BotState:
    return BotState(
        last_seen_media_pk=media.pk,
        last_seen_media_code=media.code,
    )


def is_media_after_state(media: InstagramMedia, state: BotState) -> bool:
    if not state.last_seen_media_pk:
        return True
    try:
        return int(media.pk) > int(state.last_seen_media_pk)
    except (ValueError, TypeError):
        return False


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


def download_urls(
    urls: list[str],
    prefix: str,
    log_label: str = "",
) -> tuple[Path, list[Path]]:
    temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    downloaded_files: list[Path] = []

    try:
        for index, url in enumerate(urls, start=1):
            logger.info("Downloading file %s/%s for %s", index, len(urls), log_label)
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()

            extension = extension_from_response(url, response.headers.get("content-type"))
            output_path = temp_dir / f"{index:02d}{extension}"
            with output_path.open("wb") as file_handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        file_handle.write(chunk)

            output_path = _convert_heic(output_path)
            downloaded_files.append(output_path)
            logger.info(
                "Downloaded file %s/%s for %s as %s (%s bytes)",
                index,
                len(urls),
                log_label,
                output_path.name,
                output_path.stat().st_size,
            )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return temp_dir, downloaded_files


MAX_FILES_PER_WEBHOOK = 10


def send_discord_webhook(
    webhook_url: str,
    payload: dict[str, object],
    attachment_paths: list[Path],
) -> None:
    chunks = [
        attachment_paths[i : i + MAX_FILES_PER_WEBHOOK]
        for i in range(0, len(attachment_paths), MAX_FILES_PER_WEBHOOK)
    ]

    for chunk_idx, chunk in enumerate(chunks):
        file_handles: list[object] = []
        files: list[tuple[str, tuple[str, object, str]]] = []

        try:
            for index, path in enumerate(chunk):
                file_handle = path.open("rb")
                file_handles.append(file_handle)
                mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files.append((f"files[{index}]", (path.name, file_handle, mime_type)))

            chunk_payload = payload if chunk_idx == 0 else {"content": ""}
            logger.info(
                "Sending Discord webhook chunk %s/%s with %s attachment(s)",
                chunk_idx + 1,
                len(chunks),
                len(files),
            )
            response = requests.post(
                f"{webhook_url}?wait=true&with_components=true",
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
    state_store: StateStore,
    webhook_url: str,
    role_id: str,
    target_username: str,
    interval_seconds: int,
) -> None:
    state = state_store.load()
    logger.info("Instagram polling started with %s second interval", interval_seconds)

    while True:
        try:
            recent_medias = await asyncio.to_thread(instagram.recent_medias)
            if not recent_medias:
                logger.info("No Instagram media found for @%s", target_username)
            elif state.last_seen_media_pk is None:
                state = state_from_media(recent_medias[-1])
                state_store.save(state)
                logger.info("Initialized Instagram state with media pk=%s code=%s", state.last_seen_media_pk, state.last_seen_media_code)
            else:
                new_medias = [media for media in recent_medias if is_media_after_state(media, state)]
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
                        await asyncio.to_thread(send_discord_webhook, webhook_url, payload, attachment_paths)
                    finally:
                        if temp_dir is not None:
                            shutil.rmtree(temp_dir, ignore_errors=True)
                            logger.info("Cleaned temporary media files for pk=%s", media.pk)
                    state = state_from_media(media)
                    state_store.save(state)
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
) -> None:
    state_store.load()
    logger.info("Fans polling started for %s with %s second interval", target_group, interval_seconds)

    while True:
        try:
            notifs = await asyncio.to_thread(fans.recent_notifications)
            if not notifs:
                logger.info("No Fans notifications found for %s", target_group)
            elif not state_store.is_initialized():
                for n in notifs:
                    state_store.mark_seen(n.id)
                state_store.save()
                logger.info("Initialized Fans state with %s notification(s)", len(notifs))
            else:
                new_notifs = [n for n in notifs if state_store.is_new(n.id)]
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
                        await asyncio.to_thread(send_discord_webhook, webhook_url, payload, paths)
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


async def run_all(settings) -> None:
    tasks = []

    ig_client = InstagramClient(
        target_username=settings.ig_target,
        session_file=settings.instagram_session_file,
        sessionid=settings.ig_sessionid,
        proxy=settings.ig_proxy,
    )
    ig_state = StateStore(STATE_FILE)
    tasks.append(
        poll_instagram(
            instagram=ig_client,
            state_store=ig_state,
            webhook_url=settings.webhook_url,
            role_id=settings.role_id,
            target_username=settings.ig_target,
            interval_seconds=settings.poll_interval,
        )
    )

    if settings.fans_token and settings.fans_client_uuid and settings.fans_guid and settings.fans_target:
        fans_client = FansClient(
            token=settings.fans_token,
            client_uuid=settings.fans_client_uuid,
            guid=settings.fans_guid,
            target_group=settings.fans_target,
        )
        fans_webhook = settings.fans_webhook_url or settings.webhook_url
        fans_role = settings.fans_role_id or settings.role_id
        fans_state = FansStateStore(FANS_STATE_FILE)
        tasks.append(
            poll_fans(
                fans=fans_client,
                state_store=fans_state,
                webhook_url=fans_webhook,
                role_id=fans_role,
                target_group=settings.fans_target,
                interval_seconds=settings.fans_poll_interval,
            )
        )
    else:
        logger.info("Fans credentials not provided, skipping Fans polling")

    await asyncio.gather(*tasks)


def main() -> None:
    settings = load_settings()
    asyncio.run(run_all(settings))


if __name__ == "__main__":
    main()
