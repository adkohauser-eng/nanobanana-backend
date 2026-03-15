import os
import io
import time
import uuid
import base64
import requests
from PIL import Image

ASPECT_RATIO_MAP = {
    "1:1": (1, 1),
    "16:9": (16, 9),
    "9:16": (9, 16),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "3:2": (3, 2),
    "2:3": (2, 3),
}

QUALITY_LONG_SIDE = {
    "1K": 1024,
    "2K": 2048,
    "4K": 4096,
}


def get_target_size(aspect_ratio="1:1", quality="2K"):
    if aspect_ratio not in ASPECT_RATIO_MAP:
        aspect_ratio = "1:1"

    if quality not in QUALITY_LONG_SIDE:
        quality = "2K"

    rw, rh = ASPECT_RATIO_MAP[aspect_ratio]
    long_side = QUALITY_LONG_SIDE[quality]

    if rw >= rh:
        width = long_side
        height = round(long_side * rh / rw)
    else:
        height = long_side
        width = round(long_side * rw / rh)

    return int(width), int(height)


def save_output_image_from_bytes(image_bytes, aspect_ratio="1:1", quality="2K", width=None, height=None):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    if width and height:
        target_width = int(width)
        target_height = int(height)
    else:
        target_width, target_height = get_target_size(aspect_ratio, quality)

    img = img.resize((target_width, target_height), Image.LANCZOS)

    output_dir = os.path.join(os.path.dirname(__file__), "..", "outputs")
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, f"result_{uuid.uuid4().hex}.jpg")
    img.save(output_path, format="JPEG", quality=95)

    return output_path, target_width, target_height


def local_image_to_public_url(path):
    base_url = os.getenv("PUBLIC_BASE_URL", "https://nanobanana-backend-dn2n.onrender.com").rstrip("/")
    filename = os.path.basename(path)
    return f"{base_url}/outputs/{filename}"


def map_nanobanana_model(model_name):
    model_name = (model_name or "flash").strip().lower()

    if model_name == "flash":
        return "gemini-3.1-flash-image-preview"

    if model_name == "pro":
        return "gemini-3-pro-image-preview"

    raise ValueError("Nepodporovany NanoBanana model.")


def map_seedream_model(model_name):
    model_name = (model_name or "seedream-4.5").strip().lower()

    if model_name == "seedream-4.5":
        return "bytedance/seedream-v4.5/edit"

    if model_name == "seedream-5.0":
        return "bytedance/seedream-v5.0-lite/edit"

    raise ValueError("Nepodporovany Seedream model.")


def run_gemini_nanobanana_edit(
    prompt,
    image_paths,
    api_key,
    aspect_ratio="1:1",
    quality="2K",
    model_name="flash",
):
    resolved_model = map_nanobanana_model(model_name)
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{resolved_model}:generateContent"

    parts = [{"text": prompt}]

    for path in image_paths:
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")

        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": encoded,
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {
                "aspectRatio": aspect_ratio,
                "imageSize": quality,
            },
        },
    }

    response = requests.post(
        endpoint,
        json=payload,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()

    candidates = data.get("candidates", []) or []
    if not candidates:
        raise ValueError(f"Gemini nevratil candidates. Odpoved: {data}")

    content = candidates[0].get("content", {}) or {}
    response_parts = content.get("parts", []) or []

    image_b64 = None

    for part in response_parts:
        inline_data = part.get("inlineData") or part.get("inline_data")
        if inline_data and inline_data.get("data"):
            image_b64 = inline_data["data"]
            break

    if not image_b64:
        raise ValueError(f"Gemini nevratil image data. Odpoved: {data}")

    image_bytes = base64.b64decode(image_b64)

    output_path, target_width, target_height = save_output_image_from_bytes(
        image_bytes,
        aspect_ratio=aspect_ratio,
        quality=quality,
    )

    return output_path, target_width, target_height, resolved_model


def run_wavespeed_edit(
    prompt,
    image_paths,
    api_key,
    aspect_ratio="1:1",
    quality="2K",
    model_name="seedream-4.5",
    width=None,
    height=None,
):
    resolved_model = map_seedream_model(model_name)
    submit_url = f"https://api.wavespeed.ai/api/v3/{resolved_model}"

    if width and height:
        out_width = int(width)
        out_height = int(height)
    else:
        out_width, out_height = get_target_size(aspect_ratio, quality)

    size_value = f"{out_width}*{out_height}"

    public_image_urls = [local_image_to_public_url(path) for path in image_paths]

    payload = {
        "prompt": prompt,
        "images": public_image_urls,
        "size": size_value,
        "enable_sync_mode": True,
        "enable_base64_output": False,
    }

    response = requests.post(
        submit_url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()

    outputs = data.get("data", {}).get("outputs", []) or []

    if not outputs:
        raise ValueError(f"Wavespeed nevratil obrazok. Odpoved: {data}")

    result_url = outputs[0]

    image_response = requests.get(result_url, timeout=120)
    image_response.raise_for_status()

    output_path, _, _ = save_output_image_from_bytes(
        image_response.content,
        width=out_width,
        height=out_height,
    )

    return output_path, out_width, out_height, resolved_model


def run_nanobanana_edit(
    prompt,
    image_paths,
    api_key,
    provider="gemini",
    iphone_style=False,
    aspect_ratio="1:1",
    quality="2K",
    model_name="flash",
    safety_threshold="BLOCK_ONLY_HIGH",
    width=None,
    height=None,
):
    if not api_key:
        raise ValueError("Chyba API key.")

    normalized_model = (model_name or "").strip().lower()

    if normalized_model in ["flash", "pro"]:
        return run_gemini_nanobanana_edit(
            prompt=prompt,
            image_paths=image_paths,
            api_key=api_key,
            aspect_ratio=aspect_ratio,
            quality=quality,
            model_name=normalized_model,
        )

    return run_wavespeed_edit(
        prompt=prompt,
        image_paths=image_paths,
        api_key=api_key,
        aspect_ratio=aspect_ratio,
        quality=quality,
        model_name=normalized_model,
        width=width,
        height=height,
    )
