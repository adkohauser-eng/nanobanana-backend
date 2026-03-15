import os
import io
import base64
import uuid
import requests
from PIL import Image

MODEL_MAP = {
    "flash": "gemini-3.1-flash-image-preview",
    "pro": "gemini-3-pro-image-preview",
}

HARM_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)

IPHONE_14_PRO_MAX = {
    "make": "Apple",
    "model": "iPhone 14 Pro Max",
    "software": "iOS 17.6.1",
    "focal_length": "24mm equivalent",
    "f_number": "f/1.78",
    "lens_model": "iPhone 14 Pro Max back triple camera 6.86mm f/1.78",
}

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


def encode_image(path):
    ext = os.path.splitext(path)[1].lower()
    mime_type = "image/png"

    if ext in [".jpg", ".jpeg"]:
        mime_type = "image/jpeg"
    elif ext == ".webp":
        mime_type = "image/webp"

    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")

    return mime_type, data


def build_hidden_prompt(user_prompt, iphone_style=False, aspect_ratio="1:1", quality="2K"):
    base_rules = (
        "Preserve the person's identity, face, hairstyle, outfit, proportions, "
        "scene consistency, and overall realism. "
        "Only apply the requested edit. "
        "Keep the image photorealistic."
    )

    aspect_rules = (
        f" Final image aspect ratio should be {aspect_ratio}. "
        f"Compose the frame naturally for a {aspect_ratio} photo."
    )

    quality_rules = (
        f" Target output quality: {quality}. "
        "High detail, clean texture, realistic lighting, realistic skin, realistic materials."
    )

    iphone_rules = ""
    if iphone_style:
        iphone_rules = (
            f" Shot on {IPHONE_14_PRO_MAX['model']}. "
            f"{IPHONE_14_PRO_MAX['lens_model']}. "
            f"{IPHONE_14_PRO_MAX['focal_length']}, aperture {IPHONE_14_PRO_MAX['f_number']}. "
            "Apple computational photography. "
            "Natural smartphone photo look. "
            "Realistic HDR, realistic skin texture, realistic dynamic range, "
            "natural mobile sharpening, subtle smartphone noise, authentic phone camera rendering."
        )

    return f"{user_prompt}. {base_rules}{aspect_rules}{quality_rules}{iphone_rules}"


def apply_gemini_safety(payload, threshold):
    payload["safetySettings"] = [
        {"category": category, "threshold": threshold}
        for category in HARM_CATEGORIES
    ]


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

    return max(64, int(width)), max(64, int(height))


def resize_output_image(img, target_width, target_height):
    return img.resize((target_width, target_height), Image.LANCZOS)


def extract_image_data(api_response_json):
    candidates = api_response_json.get("candidates", [])
    if not candidates:
        raise ValueError(f"API nevratila candidates. Odpoved: {api_response_json}")

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])

    for part in parts:
        inline_data = part.get("inlineData") or part.get("inline_data")
        if inline_data and "data" in inline_data:
            return inline_data["data"]

    raise ValueError(f"V odpovedi API sa nenasli obrazove data. Odpoved: {api_response_json}")


def run_nanobanana_edit(
    prompt,
    image_paths,
    api_key,
    iphone_style=False,
    aspect_ratio="1:1",
    quality="2K",
    model_name="flash",
    safety_threshold="BLOCK_ONLY_HIGH",
):
    if not api_key:
        raise ValueError("Chyba GEMINI_API_KEY.")

    final_prompt = build_hidden_prompt(
        prompt,
        iphone_style=iphone_style,
        aspect_ratio=aspect_ratio,
        quality=quality
    )

    resolved_model = MODEL_MAP.get(model_name, MODEL_MAP["flash"])
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{resolved_model}:generateContent"

    parts = []

    for img_path in image_paths:
        mime_type, encoded_data = encode_image(img_path)
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": encoded_data
            }
        })

    parts.append({"text": final_prompt})

    payload = {
        "contents": [
            {
                "parts": parts
            }
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"]
        }
    }

    apply_gemini_safety(payload, safety_threshold)

    response = requests.post(
        f"{api_url}?key={api_key}",
        json=payload,
        timeout=180
    )
    response.raise_for_status()
    data = response.json()

    image_data = extract_image_data(data)
    image_bytes = base64.b64decode(image_data)

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    target_width, target_height = get_target_size(aspect_ratio, quality)
    img = resize_output_image(img, target_width, target_height)

    output_dir = os.path.join(os.path.dirname(__file__), "..", "outputs")
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, f"result_{uuid.uuid4().hex}.jpg")
    img.save(output_path, format="JPEG", quality=95)

    return output_path, target_width, target_height, resolved_model
