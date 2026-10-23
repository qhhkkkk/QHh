# -*- coding: utf-8 -*-
"""
QHh - 自定义 API 出图节点
Banana：Gemini 原生协议（Nano Banana Pro 系列），图生图/文生图通用
GPTImage：OpenAI images 协议（GPT Image 系列），传原图走编辑，不传走生成
使用你自己的 API Key，按供应商计费，无需 Comfy 账户。
"""

import base64
import io
import json

import numpy as np
import torch
import requests


MAX_UPLOAD_SIDE = 1536      # 上传前把长边压到这个值，减少传输和上游处理时间
JPEG_QUALITY = 88           # JPEG 压缩质量
REQUEST_TIMEOUT = 600       # API 请求超时（秒），4K 出图较慢


def tensor_to_png_base64(image_tensor):
    """ComfyUI IMAGE tensor (B,H,C,3 float 0-1) -> list of compressed base64 strings.

    发送前把长边缩到 MAX_UPLOAD_SIDE、以 JPEG 压缩（带透明通道的图保留 PNG），
    显著降低上传体积和上游处理时间。出图质量不受影响（返回的图片由 API 生成）。
    """
    results = []
    for i in range(image_tensor.shape[0]):
        frame = image_tensor[i]
        arr = (frame.cpu().numpy().clip(0, 1) * 255.0).astype(np.uint8)
        from PIL import Image
        img = Image.fromarray(arr)
        w, h = img.size
        if max(w, h) > MAX_UPLOAD_SIDE:
            scale = MAX_UPLOAD_SIDE / float(max(w, h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        if "A" in img.getbands():
            img.save(buf, format="PNG")
            mime = "image/png"
        else:
            img.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
            mime = "image/jpeg"
        results.append((base64.b64encode(buf.getvalue()).decode("utf-8"), mime))
    return results


def dedupe_images(items):
    """去掉内容完全相同的重复图（Batch 接了多个相同 LoadImage 时不重复上传）。"""
    seen = set()
    out = []
    for item in items:
        if item[0] not in seen:
            seen.add(item[0])
            out.append(item)
    return out


def base64_to_tensor(b64_str):
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(b64_str))).convert("RGB")
    arr = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


class Banana:
    """Nano Banana Pro 图生图/文生图（Gemini 原生协议，API 直连）"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "把背景换成黄昏的城市天际线，光线自然融合，保持主体不变。",
                    "tooltip": "编辑指令或生成描述",
                }),
                "model": ("STRING", {"default": "gemini-3-pro-image", "tooltip": "供应商模型列表里的 Gemini 图像模型 ID"}),
                "api_url": ("STRING", {"default": "https://api.lk888.ai/v1beta", "tooltip": "填到 /v1beta 为止，节点自动拼接 /models/{模型}:generateContent"}),
                "api_key": ("STRING", {"default": "sk-请替换为你的API密钥", "tooltip": "你的 API Key"}),
                "aspect_ratio": (["auto", "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"], {"tooltip": "输出画面比例"}),
                "resolution": (["1K", "2K", "4K"], {"tooltip": "输出分辨率，越高越慢越贵"}),
            },
            "optional": {
                "images": ("IMAGE", {"tooltip": "可选：原图（图生图）。多张图会一起作为参考图发送，最多14张。"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING",)
    RETURN_NAMES = ("IMAGE", "text",)
    FUNCTION = "generate"
    CATEGORY = "QHh"
    OUTPUT_NODE = False
    DESCRIPTION = "通过 Gemini 原生接口直连出图，支持图生图、多图合成（最多14张参考图）、1K-4K 分辨率。按次计费。"

    def generate(self, prompt, model, api_url, api_key, aspect_ratio, resolution, images=None):
        if not api_key or api_key.startswith("sk-请替换"):
            raise ValueError("请先在节点上填写你的 API Key")

        url = "{}/models/{}:generateContent".format(api_url.rstrip("/"), model)
        headers = {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        }

        parts = [{"text": prompt}]

        if images is not None:
            image_b64_list = dedupe_images(tensor_to_png_base64(images)[:14])
            for b64, mime in image_b64_list:
                parts.append({
                    "inlineData": {
                        "mimeType": mime,
                        "data": b64,
                    }
                })

        image_config = {"imageSize": resolution}
        if aspect_ratio != "auto":
            image_config["aspectRatio"] = aspect_ratio

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": image_config,
            },
        }

        response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            raise RuntimeError(
                "API 错误 HTTP {}: {}".format(response.status_code, response.text[:500])
            )

        data = response.json()

        images_out = []
        texts_out = []
        candidates = data.get("candidates") or []
        if candidates:
            content_parts = (candidates[0].get("content") or {}).get("parts") or []
            for part in content_parts:
                if part.get("thought"):
                    continue
                inline = part.get("inlineData")
                if inline and inline.get("data"):
                    images_out.append(base64_to_tensor(inline["data"]))
                elif part.get("text"):
                    texts_out.append(part["text"])

        if not images_out:
            detail = json.dumps(data, ensure_ascii=False)[:500]
            raise RuntimeError("API 返回中没有图片（HTTP 200 也会扣费一次）。返回内容：{}".format(detail))

        final_image = torch.cat(images_out, dim=0)
        final_text = "\n".join(texts_out)
        return (final_image, final_text,)


class GPTImage:
    """GPT Image 图生图/文生图（OpenAI images 协议，API 直连）"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "一只戴墨镜的橘猫在海边冲浪，扁平插画风格",
                    "tooltip": "生成或编辑指令",
                }),
                "model": ("STRING", {"default": "gpt-image-2.5-sunburst-vip", "tooltip": "供应商模型列表里的 GPT Image 模型 ID"}),
                "api_url": ("STRING", {"default": "https://api.lk888.ai/v1", "tooltip": "OpenAI 兼容端点，填到 /v1 为止"}),
                "api_key": ("STRING", {"default": "sk-请替换为你的API密钥", "tooltip": "你的 API Key"}),
                "size": (["1024x1024", "1024x1536", "1536x1024", "auto"], {"tooltip": "输出尺寸，只有固定三档"}),
                "quality": (["auto", "low", "medium", "high"], {"tooltip": "生成质量"}),
            },
            "optional": {
                "images": ("IMAGE", {"tooltip": "可选：原图（图生图编辑）。提供后走 /images/edits 接口。"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING",)
    RETURN_NAMES = ("IMAGE", "text",)
    FUNCTION = "generate"
    CATEGORY = "QHh"
    OUTPUT_NODE = False
    DESCRIPTION = "通过 OpenAI images 协议直连出图。传原图走 /images/edits 编辑，不传走 /images/generations 生成。text 输出为 revised_prompt（如有）。"

    def generate(self, prompt, model, api_url, api_key, size, quality, images=None):
        if not api_key or api_key.startswith("sk-请替换"):
            raise ValueError("请先在节点上填写你的 API Key")

        base = api_url.rstrip("/")
        headers = {"Authorization": "Bearer " + api_key}

        # 图生图：/images/edits（multipart 表单）
        if images is not None:
            url = base + "/images/edits"
            files = []
            b64_list = dedupe_images(tensor_to_png_base64(images))
            if len(b64_list) == 1:
                b64, mime = b64_list[0]
                ext = "png" if mime == "image/png" else "jpg"
                files.append(("image", ("image." + ext, base64.b64decode(b64), mime)))
            else:
                for i, (b64, mime) in enumerate(b64_list):
                    ext = "png" if mime == "image/png" else "jpg"
                    files.append(("image[]", ("image_{}.{}".format(i, ext), base64.b64decode(b64), mime)))
            form = {
                "model": (None, model),
                "prompt": (None, prompt),
                "size": (None, size),
                "quality": (None, quality),
            }
            response = requests.post(url, headers=headers, data=form, files=files, timeout=REQUEST_TIMEOUT)
        # 文生图：/images/generations（JSON）
        else:
            url = base + "/images/generations"
            payload = {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": size,
                "quality": quality,
            }
            response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)

        if response.status_code != 200:
            raise RuntimeError(
                "API 错误 HTTP {}: {}".format(response.status_code, response.text[:500])
            )

        data = response.json()
        items = data.get("data") or []
        images_out = []
        texts_out = []
        for item in items:
            b64 = item.get("b64_json")
            if b64:
                images_out.append(base64_to_tensor(b64))
            elif item.get("url"):
                img_resp = requests.get(item["url"], timeout=120)
                img_resp.raise_for_status()
                images_out.append(base64_to_tensor(base64.b64encode(img_resp.content).decode("utf-8")))
            if item.get("revised_prompt"):
                texts_out.append(item["revised_prompt"])

        if not images_out:
            raise RuntimeError("API 返回中没有图片。返回内容：{}".format(json.dumps(data, ensure_ascii=False)[:500]))

        final_image = torch.cat(images_out, dim=0)
        return (final_image, "\n".join(texts_out),)


NODE_CLASS_MAPPINGS = {
    "Banana": Banana,
    "GPTImage": GPTImage,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Banana": "🍌 Nano Banana Pro (API直连)",
    "GPTImage": "🖼 GPT Image 2.5 (API直连)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
