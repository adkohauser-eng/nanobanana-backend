import os
import io
import time
import base64
import uuid
import requests
from PIL import Image

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
    "3:2": (3, 2),
    "2:3": (2, 3),
}

QUALITY_LONG_SIDE = {
    "1K": 1024,
    "2K": 2048,
    "4K": 4096,
}


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


def save_output_image_from_bytes(image_bytes, aspect_ratio="1:1", quality="2K"):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    target_width, target_height = get_target_size(aspect_ratio, quality)
    img = resize_output_image(img, target_width, target_height)

    output_dir = os.path.join(os.path.dirname(__file__), "..", "outputs")
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, f"result_{uuid.uuid4().hex}.jpg")
    img.save(output_path, format="JPEG", quality=95)

    return output_path, target_width, target_height


def local_image_to_public_url(path):
    base_url = os.getenv("PUBLIC_BASE_URL", "https://nanobanana-backend-dn2n.onrender.com").rstrip("/")
    filename = os.path.basename(path)
    return f"{base_url}/outputs/{filename}"


def normalize_model_for_provider(provider, model_name):
    provider = (provider or "wavespeed").strip().lower()
    model_name = (model_name or "seedream-4.5").strip().lower()

    if provider == "wavespeed":
        if model_name in ["seedream-4", "seedream-4.5", "4.5"]:
            return "bytedance/seedream-v4.5/edit"
        if model_name in ["seedream-5", "seedream-5.0", "seedream-5.0-lite", "5.0", "5"]:
            return "bytedance/seedream-v5.0-lite/edit"
        return "bytedance/seedream-v4.5/edit"

    if provider == "sjinn":
        if model_name in ["seedream-4", "seedream-4.5", "4.5"]:
            return "seedream-v4-5-api"
        if model_name in ["seedream-5", "seedream-5.0", "seedream-v5-lite", "seedream-5.0-lite", "5.0", "5"]:
            return "seedream-v5-lite-api"
        return "seedream-v4-5-api"

    raise ValueError("Nepodporovany provider.")


def build_wavespeed_size(aspect_ratio, quality):
    width, height = get_target_size(aspect_ratio, quality)
    return f"{width}*{height}", width, height


def extract_wavespeed_output_url(response_json):
    data = response_json.get("data", {}) or {}
    outputs = data.get("outputs", []) or []

    if outputs:
        return outputs[0]

    raise ValueError(f"Wavespeed nevratil output URL. Odpoved: {response_json}")


def wait_for_wavespeed_result(get_url, api_key, timeout_seconds=180, poll_interval=3):
    start_time = time.time()

    while True:
        if time.time() - start_time > timeout_seconds:
            raise TimeoutError("Wavespeed timeout pri cakani na vysledok.")

        response = requests.get(
            get_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        prediction = data.get("data", {}) or {}
        status = prediction.get("status", "")

        if status == "completed":
            outputs = prediction.get("outputs", []) or []
            if not outputs:
                raise ValueError(f"Wavespeed task je completed, ale nema outputs. Odpoved: {data}")
            return outputs[0], prediction.get("model", "")

        if status == "failed":
            error = prediction.get("error", "Wavespeed task failed.")
            raise RuntimeError(error)

        time.sleep(poll_interval)


def run_wavespeed_edit(
    prompt,
    image_paths,
    api_key,
    aspect_ratio="1:1",
    quality="2K",
    model_name="seedream-4.5",
):
    resolved_model = normalize_model_for_provider("wavespeed", model_name)
    submit_url = f"https://api.wavespeed.ai/api/v3/{resolved_model}"

    size_value, target_width, target_height = build_wavespeed_size(aspect_ratio, quality)
    public_image_urls = [local_image_to_public_url(path) for path in image_paths]

    payload = {
        "prompt": prompt,
        "images": public_image_urls,
        "size": size_value,
        "enable_sync_mode": False,
        "enable_base64_output": False,
    }

    response = requests.post(
        submit_url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()

    prediction = data.get("data", {}) or {}
    outputs = prediction.get("outputs", []) or []
    status = prediction.get("status", "")
    result_url = None

    if outputs:
        result_url = outputs[0]
    elif prediction.get("urls", {}).get("get"):
        result_url, resolved_model_name = wait_for_wavespeed_result(
            prediction["urls"]["get"],
            api_key,
            timeout_seconds=240,
            poll_interval=3,
        )
        image_response = requests.get(result_url, timeout=120)
        image_response.raise_for_status()
        output_path, _, _ = save_output_image_from_bytes(
            image_response.content,
            aspect_ratio=aspect_ratio,
            quality=quality,
        )
        return output_path, target_width, target_height, resolved_model_name
    elif status == "completed":
        result_url = extract_wavespeed_output_url(data)
    else:
        raise ValueError(f"Wavespeed nevratil outputs ani result URL. Odpoved: {data}")

    image_response = requests.get(result_url, timeout=120)
    image_response.raise_for_status()

    output_path, _, _ = save_output_image_from_bytes(
        image_response.content,
        aspect_ratio=aspect_ratio,
        quality=quality,
    )

    return output_path, target_width, target_height, resolved_model


def wait_for_sjinn_result(task_id, api_key, timeout_seconds=240, poll_interval=5):
    start_time = time.time()
    url = "https://sjinn.ai/api/un-api/query_tool_task_status"

    while True:
        if time.time() - start_time > timeout_seconds:
            raise TimeoutError("SJinn timeout pri cakani na vysledok.")

        response = requests.post(
            url,
            json={"task_id": task_id},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("success", False):
            raise RuntimeError(data.get("errorMsg", "SJinn query zlyhal."))

        result = data.get("data", {}) or {}
        status = result.get("status")

        if status == 1:
            output_urls = result.get("output_urls", []) or []
            if not output_urls:
                raise ValueError(f"SJinn task je completed, ale nema output_urls. Odpoved: {data}")
            return output_urls[0], result.get("task_type", "")

        if status == -1:
            raise RuntimeError(result.get("error", data.get("errorMsg", "SJinn task failed.")))

        time.sleep(poll_interval)


def run_sjinn_edit(
    prompt,
    image_paths,
    api_key,
    aspect_ratio="1:1",
    quality="2K",
    model_name="seedream-4.5",
):
    resolved_model = normalize_model_for_provider("sjinn", model_name)
    submit_url = "https://sjinn.ai/api/un-api/create_tool_task"

    public_image_urls = [local_image_to_public_url(path) for path in image_paths]
    target_width, target_height = get_target_size(aspect_ratio, quality)

    input_payload = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio if aspect_ratio in ASPECT_RATIO_MAP else "auto",
    }

    if public_image_urls:
        input_payload["image_list"] = public_image_urls

    payload = {
        "tool_type": resolved_model,
        "input": input_payload,
    }

    response = requests.post(
        submit_url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()

    if not data.get("success", False):
        raise RuntimeError(data.get("errorMsg", "SJinn create task zlyhal."))

    task_id = data.get("data", {}).get("task_id")
    if not task_id:
        raise ValueError(f"SJinn nevratil task_id. Odpoved: {data}")

    result_url, resolved_tool = wait_for_sjinn_result(
        task_id,
        api_key,
        timeout_seconds=300,
        poll_interval=5,
    )

    image_response = requests.get(result_url, timeout=120)
    image_response.raise_for_status()

    output_path, _, _ = save_output_image_from_bytes(
        image_response.content,
        aspect_ratio=aspect_ratio,
        quality=quality,
    )

    return output_path, target_width, target_height, resolved_tool


def run_nanobanana_edit(
    prompt,
    image_paths,
    api_key,
    provider="wavespeed",
    iphone_style=False,
    aspect_ratio="1:1",
    quality="2K",
    model_name="seedream-4.5",
    safety_threshold="BLOCK_ONLY_HIGH",
):
    if not api_key:
        raise ValueError("Chyba API key.")

    final_prompt = build_hidden_prompt(
        prompt,
        iphone_style=iphone_style,
        aspect_ratio=aspect_ratio,
        quality=quality,
    )

    provider = (provider or "wavespeed").strip().lower()

    if provider == "wavespeed":
        return run_wavespeed_edit(
            prompt=final_prompt,
            image_paths=image_paths,
            api_key=api_key,
            aspect_ratio=aspect_ratio,
            quality=quality,
            model_name=model_name,
        )

    if provider == "sjinn":
        return run_sjinn_edit(
            prompt=final_prompt,
            image_paths=image_paths,
            api_key=api_key,
            aspect_ratio=aspect_ratio,
            quality=quality,
            model_name=model_name,
        )

    raise ValueError("Nepodporovany provider. Pouzi wavespeed alebo sjinn.")
