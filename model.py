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
    "5:4": (5, 4),
    "4:5": (4, 5),
}

QUALITY_LONG_SIDE = {
    "1K": 1024,
    "2K": 2048,
    "4K": 4096,
}


def get_target_size(aspect_ratio, quality):
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


def save_output_image_from_bytes(image_bytes, aspect_ratio="1:1", quality="2K"):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    width, height = get_target_size(aspect_ratio, quality)

    img = img.resize((width, height), Image.LANCZOS)

    output_dir = os.path.join(os.path.dirname(__file__), "..", "outputs")
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, f"result_{uuid.uuid4().hex}.jpg")
    img.save(output_path, format="JPEG", quality=95)

    return output_path, width, height


def run_gemini_nanobanana_edit(prompt, image_paths, api_key, aspect_ratio, quality, model_name):
    if model_name == "flash":
        model = "gemini-3.1-flash-image-preview"
    else:
        model = "gemini-3-pro-image-preview"

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

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

    payload = {"contents": [{"parts": parts}]}

    res = requests.post(endpoint, json=payload)
    res.raise_for_status()
    data = res.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError("Gemini nevratil obrazok")

    parts = candidates[0]["content"].get("parts", [])

    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            image_bytes = base64.b64decode(inline["data"])
            return save_output_image_from_bytes(image_bytes, aspect_ratio, quality)

    raise ValueError("Gemini nevratil image data")


def run_wavespeed_edit(prompt, image_paths, api_key, aspect_ratio, quality, model_name):

    if model_name == "seedream-5.0":
        model = "bytedance/seedream-v5.0-lite/edit"
    else:
        model = "bytedance/seedream-v4.5/edit"

    submit_url = f"https://api.wavespeed.ai/api/v3/{model}"

    width, height = get_target_size(aspect_ratio, quality)
    size = f"{width}*{height}"

    payload = {
        "prompt": prompt,
        "images": [],
        "size": size,
        "enable_sync_mode": True
    }

    for path in image_paths:
        payload["images"].append(path)

    res = requests.post(
        submit_url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    res.raise_for_status()
    data = res.json()

    outputs = data.get("data", {}).get("outputs", [])

    if not outputs:
        raise ValueError("Wavespeed nevratil obrazok")

    img = requests.get(outputs[0]).content

    return save_output_image_from_bytes(img, aspect_ratio, quality)


def run_nanobanana_edit(
    prompt,
    image_paths,
    api_key,
    provider,
    iphone_style,
    aspect_ratio,
    quality,
    model_name,
    safety_threshold,
):

    model_name = model_name.lower()

    # NanoBanana → Gemini
    if model_name in ["flash", "pro"]:
        return run_gemini_nanobanana_edit(
            prompt,
            image_paths,
            api_key,
            aspect_ratio,
            quality,
            model_name,
        )

    # Seedream → WaveSpeed
    return run_wavespeed_edit(
        prompt,
        image_paths,
        api_key,
        aspect_ratio,
        quality,
        model_name,
    )
