import json
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from PIL import Image, UnidentifiedImageError


class EventMediaStorageError(Exception):
    pass


def _pin_media(image, title, description, media_label):
    token = settings.PINATA_JWT
    if not token:
        raise ImproperlyConfigured(
            f"PINATA_JWT must be configured to upload {media_label} images."
        )

    gateway = settings.PINATA_GATEWAY_URL
    gateway_parts = urlsplit(gateway)
    if gateway_parts.scheme != "https" or not gateway_parts.netloc:
        raise ImproperlyConfigured("PINATA_GATEWAY_URL must be an HTTPS gateway URL.")

    image_format_content_types = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }
    try:
        image.seek(0)
        with Image.open(image) as decoded_image:
            image_format = decoded_image.format
            decoded_image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise EventMediaStorageError(f"Uploaded {media_label} image is invalid.") from exc

    image_content_type = image_format_content_types.get(image_format)
    if image_content_type is None:
        raise EventMediaStorageError(f"{media_label.title()} image must be JPEG, PNG, or WebP.")

    headers = {"Authorization": f"Bearer {token}"}
    image.seek(0)
    try:
        image_response = httpx.post(
            "https://api.pinata.cloud/pinning/pinFileToIPFS",
            headers=headers,
            files={"file": (image.name, image, image_content_type)},
            timeout=30,
        )
        image_response.raise_for_status()
        image_cid = image_response.json().get("IpfsHash")
        if not image_cid:
            raise EventMediaStorageError("Pinata did not return an image CID.")

        image_uri = f"{gateway}/ipfs/{image_cid}"
        metadata = {
            "name": title,
            "symbol": "NFTICKET",
            "description": description,
            "image": image_uri,
            "properties": {
                "category": "image",
                "files": [{"uri": image_uri, "type": image_content_type}],
            },
        }
        metadata_response = httpx.post(
            "https://api.pinata.cloud/pinning/pinJSONToIPFS",
            headers={**headers, "Content-Type": "application/json"},
            content=json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        metadata_response.raise_for_status()
        metadata_cid = metadata_response.json().get("IpfsHash")
        if not metadata_cid:
            raise EventMediaStorageError("Pinata did not return a metadata CID.")
    except httpx.HTTPStatusError as exc:
        raise EventMediaStorageError(
            f"Pinata rejected a {media_label} image upload (HTTP {exc.response.status_code})."
        ) from exc
    except httpx.HTTPError as exc:
        raise EventMediaStorageError(f"Could not upload {media_label} media to Pinata.") from exc
    except ValueError as exc:
        raise EventMediaStorageError("Pinata returned an invalid response.") from exc

    metadata_uri = f"{gateway}/ipfs/{metadata_cid}"
    if len(metadata_uri.encode("utf-8")) > 200:
        raise EventMediaStorageError(
            "The configured Pinata gateway produces a metadata URI longer than "
            "the Solana program's 200-byte limit."
        )
    return image_uri, metadata_uri


def pin_event_media(image, title, description):
    return _pin_media(image, title, description, "event")


def pin_ticket_type_media(image, title, description):
    return _pin_media(image, title, description, "ticket")
