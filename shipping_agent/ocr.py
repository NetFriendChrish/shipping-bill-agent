"""指定 OCR 协议适配；本地模式用于离线演示，绝不冒充远程接口结果。"""
from pathlib import Path
from functools import lru_cache
import hashlib
import json
import re
import requests
from PIL import Image, ImageSequence
from .config import Settings


def remote_text(payload) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        return '\n'.join(filter(None, (remote_text(v) for v in payload)))
    if isinstance(payload, dict):
        # Prefer a complete text representation to avoid duplicate extraction.
        for key in ('markdown', 'full_text', 'text', 'content', 'html'):
            if isinstance(payload.get(key), str) and payload[key].strip():
                return payload[key]
        for key in ('data', 'result', 'results', 'pages', 'lines', 'blocks', 'ocr_result'):
            if key in payload:
                text = remote_text(payload[key])
                if text.strip():
                    return text
    return ''


@lru_cache(maxsize=1)
def local_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)


def local_image_text(path: Path) -> tuple[str, dict]:
    import numpy as np
    pages, scores = [], []
    with Image.open(path) as img:
        if img.width * img.height > 40_000_000:
            raise ValueError('图片超过4000万像素限制')
        for i, frame in enumerate(ImageSequence.Iterator(img)):
            if i >= 100:
                raise ValueError('TIFF超过100帧限制')
            rgb = frame.convert('RGB')
            if max(rgb.size) < 1600:
                scale = 1600 / max(rgb.size)
                rgb = rgb.resize((round(rgb.width * scale), round(rgb.height * scale)))
            result, _ = local_engine()(np.array(rgb))
            # Recover table rows using bounding-box Y coordinates.
            rows: list[list] = []
            for box, text, score in sorted(result or [], key=lambda r: min(p[1] for p in r[0])):
                cy = sum(p[1] for p in box) / 4
                h = max(p[1] for p in box) - min(p[1] for p in box)
                row = next((r for r in reversed(rows[-3:]) if abs(r[0] - cy) < max(5, h * .55)), None)
                if row is None:
                    row = [cy, []]; rows.append(row)
                row[1].append((min(p[0] for p in box), text))
                scores.append(float(score))
            pages.append('\n'.join(' | '.join(t for _, t in sorted(r[1])) for r in rows))
    return '\n\n'.join(pages), {'engine': 'RapidOCR-local', 'mean_ocr_confidence': sum(scores) / len(scores) if scores else 0}


def remote_ocr(path: Path, source: str, settings: Settings, cache: Path) -> tuple[str, dict]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    mapping = json.loads(Path(settings.ocr_url_map).read_text(encoding='utf-8-sig')) if settings.ocr_url_map else {}
    url = mapping.get(digest) or mapping.get(source) or mapping.get(path.name)
    if not url or not re.match(r'^https?://', url):
        raise ValueError('指定OCR需要可下载URL：请在OCR_URL_MAP中按SHA256/来源名映射，不能传本地路径')
    key = hashlib.sha256((settings.ocr_endpoint + digest + url).encode()).hexdigest()
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f'{key}.json'
    if target.exists():
        raw = json.loads(target.read_text(encoding='utf-8'))
    else:
        # Exactly the protocol supplied in the exam. Do not retry a POST blindly.
        response = requests.post(settings.ocr_endpoint, json={
            'file': url, 'task': 'orderChange', 'callBack': '', 'taskType': ''
        }, timeout=(settings.connect_timeout, settings.read_timeout))
        response.raise_for_status()
        raw = response.json()
        if len(response.content) > 20 * 1024 * 1024:
            raise ValueError('OCR响应过大')
        target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
    text = remote_text(raw)
    if not text.strip():
        raise ValueError(f'OCR响应无受支持文本字段，请检查原始响应 {target.name}')
    return text, {'engine': 'specified-remote-OCR', 'ocr_response': str(target), 'file_url': url}
