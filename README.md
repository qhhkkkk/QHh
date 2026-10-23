# QHh - ComfyUI API 出图节点

自用 ComfyUI 自定义节点包，通过 OpenAI 兼容 API 直连出图，无需 Comfy 账户。

## 包含节点

| 节点 | 显示名 | 协议 | 说明 |
|---|---|---|---|
| `Banana` | 🍌 Nano Banana Pro (API直连) | Gemini 原生 | 图生图/文生图，支持最多 14 张参考图、1K/2K/4K、10 种画面比例 |
| `GPTImage` | 🖼 GPT Image 2.5 (API直连) | OpenAI images | 传原图走 `/images/edits` 编辑，不传走 `/images/generations` 生成 |

两个节点均输出 `IMAGE` + `text`（模型返回的文字，如 revised_prompt）。

两个节点的 `aspect_ratio` / `resolution` 参数完全一致，可在画布上直接互换。GPTImage 内部会把 比例+分辨率 换算成像素 size（如 16:9 + 2K → 2048x1152）；若供应商只支持固定三档尺寸，超出档位可能报错，届时用 1K 即可。

## 安装

**方式一（推荐）**：ComfyUI Manager → Install via Git URL → 粘贴本仓库地址

**方式二（手动）**：
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/qhhkkkk/QHh.git
```
重启 ComfyUI 即可。零第三方依赖（torch/PIL/numpy/requests 均为 ComfyUI 自带）。

## 配置

| 字段 | 说明 |
|---|---|
| `api_url` | Banana 填到 `/v1beta`（节点自动拼 `/models/{模型}:generateContent`）；GPTImage 填到 `/v1` |
| `api_key` | 你的 API Key（`sk-` 开头） |
| `model` | 自由输入，填你的 API 供应商模型列表里的图像模型 ID |

注意：两个节点协议不同，模型名不能互换——Banana 只认 Gemini 系图像模型，GPTImage 只认 gpt-image 系。

## 特性

- 上传参考图自动压缩（长边 1536px、JPEG 质量 88），大幅减少传输时间
- 内容完全相同的重复参考图自动去重，不重复上传/计费
- 请求超时 600 秒（4K 出图够用）
