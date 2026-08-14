import argparse
import base64
import copy
import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


DEFAULT_NEGATIVE = (
    "low quality, blurry, smeared motion, warped anatomy, distorted object shape, identity drift, "
    "composition drift, background replacement, changing subject, duplicate subject, cartoon, CGI, "
    "plastic texture, over-sharpened, flicker, exposure change, luminosity change, text, subtitles, "
    "watermark, borders, frame artifacts, cuts, transitions, scene change, camera shake"
)




def ensure_api_workflow(workflow, workflow_path):
    if isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list) and isinstance(workflow.get("links"), list):
        raise SystemExit(
            "Workflow UI detecte, pas un Export(API): "
            f"{workflow_path}\nDans ComfyUI, ouvre ce workflow puis utilise File > Export (API), "
            "et charge ce fichier API dans l'app."
        )
    if not isinstance(workflow, dict):
        raise SystemExit(f"Workflow API invalide: {workflow_path}")

def nodes_by_class(workflow, class_type):
    return [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


def find_save_node(workflow):
    saves = nodes_by_class(workflow, "SaveVideo")
    if not saves:
        raise KeyError("No SaveVideo node found in workflow")
    return saves[-1]


def find_load_image_node(workflow):
    images = nodes_by_class(workflow, "LoadImage")
    return images[0] if images else (None, None)


def is_negative_text_node(node):
    title = (node.get("_meta", {}).get("title") or "").lower()
    raw_text = node.get("inputs", {}).get("text", "")
    text = raw_text.lower() if isinstance(raw_text, str) else ""
    if "positive" in title:
        return False
    if "negative" in title:
        return True
    negative_markers = (
        "low quality",
        "blurry",
        "watermark",
        "subtitles",
        "overlay",
        "still frame",
    )
    return any(marker in text for marker in negative_markers)


def find_text_nodes(workflow):
    text_nodes = nodes_by_class(workflow, "CLIPTextEncode")
    positive = None
    negative = None

    for node_id, node in text_nodes:
        if negative is None and is_negative_text_node(node):
            negative = (node_id, node)

    for node_id, node in text_nodes:
        if negative and node_id == negative[0]:
            continue
        title = (node.get("_meta", {}).get("title") or "").lower()
        if positive is None and ("positive" in title or "prompt" in title):
            positive = (node_id, node)

    if positive is None and text_nodes:
        non_negative_nodes = [
            (node_id, node)
            for node_id, node in text_nodes
            if not (negative and node_id == negative[0]) and not is_negative_text_node(node)
        ]
        positive = non_negative_nodes[0] if non_negative_nodes else text_nodes[0]
    if negative is None and len(text_nodes) > 1:
        negative_candidates = [
            (node_id, node)
            for node_id, node in text_nodes
            if not (positive and node_id == positive[0])
        ]
        negative = negative_candidates[0] if negative_candidates else text_nodes[1]
    return positive, negative


def prompt_like_score(node):
    title = (node.get("_meta", {}).get("title") or "").lower()
    class_type = (node.get("class_type") or "").lower()
    blob = f"{title} {class_type}"
    score = 0
    for marker in ("prompt", "text", "caption", "ltx"):
        if marker in blob:
            score += 1
    return score


def apply_text_prompts(workflow, positive_text, negative_text):
    text_nodes = nodes_by_class(workflow, "CLIPTextEncode")
    if text_nodes:
        positive_node, negative_node = find_text_nodes(workflow)
        negative_ids = {negative_node[0]} if negative_node else set()

        for node_id, node in text_nodes:
            inputs = node.get("inputs", {})
            if "text" not in inputs or isinstance(inputs.get("text"), list):
                continue
            if node_id in negative_ids or is_negative_text_node(node):
                inputs["text"] = negative_text
            else:
                inputs["text"] = positive_text

        if positive_node and not isinstance(positive_node[1].get("inputs", {}).get("text"), list):
            positive_node[1]["inputs"]["text"] = positive_text
        if negative_node and not isinstance(negative_node[1].get("inputs", {}).get("text"), list):
            negative_node[1]["inputs"]["text"] = negative_text

    positive_keys = ("prompt", "positive_prompt", "caption", "text")
    negative_keys = ("negative", "negative_prompt", "negative_text")
    touched_positive = False
    candidates = []

    for node_id, node in workflow.items():
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        title = (node.get("_meta", {}).get("title") or "").lower()
        class_type = (node.get("class_type") or "").lower()

        if "prompt" in title and "value" in inputs and not isinstance(inputs["value"], list):
            inputs["value"] = positive_text
            touched_positive = True

        for key in negative_keys:
            if key in inputs and not isinstance(inputs[key], list):
                inputs[key] = negative_text
        for key in positive_keys:
            if key in inputs and not isinstance(inputs[key], list):
                candidates.append((prompt_like_score(node), node_id, node, key))

    candidates.sort(reverse=True, key=lambda item: item[0])
    for score, _node_id, node, key in candidates:
        if score > 0 or not touched_positive:
            node["inputs"][key] = positive_text
            touched_positive = True

    if not touched_positive:
        raise KeyError("No prompt/text input found in workflow")


def set_noise_seeds(workflow, seed, upscale_seed):
    seed_index = 0
    for _node_id, node in workflow.items():
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for key in ("noise_seed", "seed"):
            if key in inputs and not isinstance(inputs[key], list):
                inputs[key] = seed if seed_index == 0 else upscale_seed
                seed_index += 1

def read_primitive_int(workflow, title_marker, default):
    for _node_id, node in workflow.items():
        if node.get("class_type") != "PrimitiveInt":
            continue
        title = (node.get("_meta", {}).get("title") or "").lower()
        value = node.get("inputs", {}).get("value")
        if title_marker in title and isinstance(value, int):
            return value
    return default


def disable_prompt_enhance(workflow):
    for _node_id, node in workflow.items():
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        title = (node.get("_meta", {}).get("title") or "").lower()
        class_type = (node.get("class_type") or "").lower()
        if "prompt enhance" in title and "value" in inputs:
            inputs["value"] = False
        if class_type == "textgenerateltx2prompt":
            if "sampling_mode" in inputs:
                inputs["sampling_mode"] = "off"
            if "use_default_template" in inputs:
                inputs["use_default_template"] = False


def apply_frame_count(workflow, frame_count):
    if frame_count is None:
        return
    fps = read_primitive_int(workflow, "frame rate", 24)
    duration_seconds = max(1, round((frame_count - 1) / max(1, fps)))
    for _node_id, node in workflow.items():
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        title = (node.get("_meta", {}).get("title") or "").lower()
        class_type = node.get("class_type")
        for key in ("length", "frame_count", "num_frames", "frames"):
            if key in inputs and not isinstance(inputs[key], list):
                inputs[key] = frame_count
        if class_type == "PrimitiveInt" and "duration" in title and "value" in inputs:
            inputs["value"] = duration_seconds
        elif class_type == "PrimitiveInt" and any(marker in title for marker in ("length", "frame count", "num frames")) and "value" in inputs:
            inputs["value"] = frame_count
        if "duration" in inputs and not isinstance(inputs["duration"], list):
            inputs["duration"] = duration_seconds


def apply_duration_seconds(workflow, duration):
    """Duree en secondes, pour les workflows qui la pilotent directement au lieu
    d'un nombre de frames — cas de MiniMax H3 T2V, ou la longueur sort d'une
    ComfyMathExpression branchee sur un PrimitiveFloat 'duration'."""
    if duration is None:
        return
    for _node_id, node in workflow.items():
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        title = (node.get("_meta", {}).get("title") or "").lower()
        class_type = node.get("class_type")
        if class_type in ("PrimitiveFloat", "PrimitiveInt") and "duration" in title:
            if "value" in inputs and not isinstance(inputs["value"], list):
                inputs["value"] = duration if class_type == "PrimitiveFloat" else max(1, int(round(duration)))
        for key in ("duration", "duration_seconds"):
            if key in inputs and not isinstance(inputs[key], list):
                inputs[key] = duration


def apply_resolution_selector(workflow, aspect_ratio, megapixels):
    """ResolutionSelector (MiniMax H3) definit la taille par ratio + megapixels,
    pas par width/height : apply_size ne peut rien pour lui."""
    if aspect_ratio is None and megapixels is None:
        return
    for _node_id, node in workflow.items():
        if node.get("class_type") != "ResolutionSelector":
            continue
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        if aspect_ratio and not isinstance(inputs.get("aspect_ratio"), list):
            inputs["aspect_ratio"] = aspect_ratio
        if megapixels is not None and not isinstance(inputs.get("megapixels"), list):
            inputs["megapixels"] = megapixels


def parse_size(value):
    if not value:
        return None
    normalized = value.lower().replace("*", "x")
    if "x" not in normalized:
        raise SystemExit("Size must be formatted like 1280x720")
    width_text, height_text = normalized.split("x", 1)
    try:
        width = int(width_text.strip())
        height = int(height_text.strip())
    except ValueError as exc:
        raise SystemExit("Size must be formatted like 1280x720") from exc
    if width <= 0 or height <= 0:
        raise SystemExit("Size width and height must be positive")
    return width, height


def apply_size(workflow, size):
    if size is None:
        return
    width, height = size
    for _node_id, node in workflow.items():
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        title = (node.get("_meta", {}).get("title") or "").lower()
        class_type = node.get("class_type")
        if class_type == "PrimitiveInt" and "width" in title and "value" in inputs:
            inputs["value"] = width
        if class_type == "PrimitiveInt" and "height" in title and "value" in inputs:
            inputs["value"] = height
        for key, value in (("width", width), ("height", height), ("resize_type.width", width), ("resize_type.height", height)):
            if key in inputs and not isinstance(inputs[key], list):
                inputs[key] = value
        if "resolution" in inputs and not isinstance(inputs["resolution"], list):
            inputs["resolution"] = f"{width}x{height}"


def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_image_data(server, image_data, filename):
    if "," in image_data:
        header, encoded = image_data.split(",", 1)
        mime_type = header.split(";")[0].replace("data:", "") or mimetypes.guess_type(filename)[0] or "image/png"
    else:
        encoded = image_data
        mime_type = mimetypes.guess_type(filename)[0] or "image/png"

    image_bytes = base64.b64decode(encoded)
    boundary = f"----codex-ltx-{uuid.uuid4().hex}"

    def part(name, value, content_type=None, part_filename=None):
        headers = [f"--{boundary}"]
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if part_filename:
            disposition += f'; filename="{part_filename}"'
        headers.append(disposition)
        if content_type:
            headers.append(f"Content-Type: {content_type}")
        headers.append("")
        head = "\r\n".join(headers).encode("utf-8") + b"\r\n"
        body = value if isinstance(value, bytes) else str(value).encode("utf-8")
        return head + body + b"\r\n"

    body = b"".join([
        part("image", image_bytes, mime_type, filename),
        part("type", "input"),
        part("subfolder", "ltx_queue"),
        part("overwrite", "false"),
        f"--{boundary}--\r\n".encode("utf-8"),
    ])

    request = urllib.request.Request(
        f"{server.rstrip('/')}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))
    return f"{result['subfolder']}/{result['name']}" if result.get("subfolder") else result["name"]


def upload_image_path(server, image_path):
    path = Path(image_path)
    image_bytes = path.read_bytes()
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    boundary = f"----codex-ltx-{uuid.uuid4().hex}"

    def part(name, value, content_type=None, part_filename=None):
        headers = [f"--{boundary}"]
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if part_filename:
            disposition += f'; filename="{part_filename}"'
        headers.append(disposition)
        if content_type:
            headers.append(f"Content-Type: {content_type}")
        headers.append("")
        head = "\r\n".join(headers).encode("utf-8") + b"\r\n"
        body = value if isinstance(value, bytes) else str(value).encode("utf-8")
        return head + body + b"\r\n"

    body = b"".join([
        part("image", image_bytes, mime_type, path.name),
        part("type", "input"),
        part("subfolder", "ltx_queue"),
        part("overwrite", "false"),
        f"--{boundary}--\r\n".encode("utf-8"),
    ])

    request = urllib.request.Request(
        f"{server.rstrip('/')}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))
    return f"{result['subfolder']}/{result['name']}" if result.get("subfolder") else result["name"]


def queue_prompt(server, workflow, client_id):
    return post_json(f"{server.rstrip('/')}/prompt", {
        "client_id": client_id,
        "prompt": workflow,
    })


def resolve_path(root, raw_path):
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else root / path


def looks_like_api_workflow(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return any(
        isinstance(node, dict) and "class_type" in node
        for node in data.values()
    )


def resolve_workflow_path(root, raw_path):
    path = resolve_path(root, raw_path)
    if path.is_dir():
        candidates = sorted(
            [item for item in path.glob("*.json") if looks_like_api_workflow(item)],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise SystemExit(f"No ComfyUI API workflow .json found in folder: {path}")
        selected = candidates[0]
        print(f"Using latest workflow in folder: {selected}")
        return selected
    if not path.exists():
        raise SystemExit(f"Workflow not found: {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="Queue a list of LTX ComfyUI prompts.")
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--workflow", default="ltx19b_i2v_quality_upscale_api.json")
    parser.add_argument("--prompts", default="ltx_custom_queue.json")
    parser.add_argument("--frame-count", type=int, default=None)
    parser.add_argument("--size", default=None, help="Optional video size override, for example 1280x720")
    parser.add_argument("--noise-seed", type=int, default=None, help="Optional base noise seed. Each shot increments it by one.")
    parser.add_argument("--duration-seconds", type=float, default=None,
                        help="Optional duration override in seconds, for workflows driven by a duration widget (MiniMax H3 T2V).")
    parser.add_argument("--aspect-ratio", default=None,
                        help="Optional ResolutionSelector aspect ratio, for example '16:9 (Widescreen)'.")
    parser.add_argument("--megapixels", type=float, default=None,
                        help="Optional ResolutionSelector megapixels, for example 0.9.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    workflow_path = resolve_workflow_path(root, args.workflow)
    prompts_path = resolve_path(root, args.prompts)
    if not prompts_path.exists():
        raise SystemExit(f"Prompts file not found: {prompts_path}")

    base_workflow = json.loads(workflow_path.read_text(encoding="utf-8-sig"))
    ensure_api_workflow(base_workflow, workflow_path)
    prompts = json.loads(prompts_path.read_text(encoding="utf-8-sig"))
    size_override = parse_size(args.size)
    client_id = str(uuid.uuid4())

    queued = []
    for index, shot in enumerate(prompts, start=1):
        workflow = copy.deepcopy(base_workflow)
        name = shot["name"]
        image_node_id, image_node = find_load_image_node(workflow)
        save_node_id, save_node = find_save_node(workflow)

        apply_text_prompts(
            workflow,
            shot["positive"],
            shot.get("negative", DEFAULT_NEGATIVE),
        )
        disable_prompt_enhance(workflow)
        image_name = shot.get("image")
        if shot.get("image_path"):
            image_name = upload_image_path(args.server, shot["image_path"])
        if shot.get("image_data"):
            image_name = upload_image_data(
                args.server,
                shot["image_data"],
                shot.get("image_filename") or f"{name}.png",
            )
        if image_name and image_node:
            image_node["inputs"]["image"] = image_name
        apply_frame_count(workflow, args.frame_count)
        apply_size(workflow, size_override)
        apply_duration_seconds(workflow, args.duration_seconds)
        apply_resolution_selector(workflow, args.aspect_ratio, args.megapixels)
        seed = args.noise_seed + index - 1 if args.noise_seed is not None else shot.get("seed", int(time.time() * 1000) + index)
        upscale_seed = args.noise_seed + index if args.noise_seed is not None else shot.get("upscale_seed", index - 1)
        set_noise_seeds(
            workflow,
            seed,
            upscale_seed,
        )
        save_node["inputs"]["filename_prefix"] = f"video/ltx_queue/{index:02d}_{name}"

        if args.dry_run:
            print(f"DRY RUN: {index:02d} {name}")
            continue

        try:
            result = queue_prompt(args.server, workflow, client_id)
        except urllib.error.HTTPError as exc:
            # ComfyUI a repondu : il refuse le workflow et dit pourquoi dans le
            # corps de la reponse. L'afficher evite un diagnostic a l'aveugle.
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1200]
            except Exception:
                pass
            raise SystemExit(
                f"ComfyUI a refuse le shot {index:02d} {name} (HTTP {exc.code}):\n{detail}\n"
                "Verifie que ComfyUI a bien les noeuds et les modeles de ce workflow."
            ) from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"Could not reach ComfyUI at {args.server}: {exc}") from exc

        prompt_id = result.get("prompt_id", "unknown")
        queued.append({"shot": name, "prompt_id": prompt_id})
        print(f"Queued {index:02d} {name}: {prompt_id}")
        time.sleep(0.2)

    if queued:
        print("\nQueued shots:")
        for item in queued:
            print(f"- {item['shot']}: {item['prompt_id']}")


if __name__ == "__main__":
    main()




