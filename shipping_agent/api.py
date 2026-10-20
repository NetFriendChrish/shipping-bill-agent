from pathlib import Path
from uuid import uuid4
import json
import re
import threading
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .config import Settings, ROOT
from .ledger import load_existing
from .parsers import SUPPORTED
from .workflow import ShippingBillAgent, save_report

app = FastAPI(title='航运智能账单助手', version='1.0.0')
app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')
RUNS = ROOT / 'output' / 'runs'
processing_lock = threading.Lock()


@app.get('/')
def index(): return FileResponse(ROOT / 'web' / 'index.html')


@app.get('/api/status')
def status():
    s = Settings()
    return {'version': '1.0.0', 'llm_configured': bool(s.llm_api_key and s.llm_model), 'ocr_url_map_configured': bool(s.ocr_url_map), 'default_extractor': s.extractor, 'default_ocr': s.ocr_mode}


def run_job(paths, entity, existing, folder, extractor, ocr, aliases):
    settings = Settings()
    extractor = extractor or settings.extractor
    ocr = ocr or settings.ocr_mode
    if extractor not in ('rules', 'llm') or ocr not in ('local', 'remote'):
        raise HTTPException(400, '提取模式或OCR模式无效')
    settings.extractor, settings.ocr_mode = extractor, ocr
    try:
        previous = load_existing(existing)
        report = ShippingBillAgent(settings).run(paths, entity.strip() or None, previous, folder / 'work', [v.strip() for v in aliases.split(';') if v.strip()])
        save_report(report, folder, previous)
        return {'run_id': folder.name, **report.model_dump()}
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/process')
def process(files: list[UploadFile] = File(...), entity: str = Form(''), aliases: str = Form(''), extractor: str | None = Form(None), ocr: str | None = Form(None), existing: UploadFile | None = File(None)):
    if not processing_lock.acquire(blocking=False): raise HTTPException(409, '已有任务正在处理，请稍后再试')
    try:
        if not 1 <= len(files) <= 30: raise HTTPException(400, '每批选择1至30个文件')
        folder = RUNS / uuid4().hex
        inputs = folder / 'inputs'; inputs.mkdir(parents=True)
        paths, total_size = [], 0
        for idx, file in enumerate([*files, *([existing] if existing else [])]):
            name = re.split(r'[/\\]', file.filename or 'unnamed')[-1]
            name = re.sub(r'[<>:"|?*\x00-\x1f]', '_', name)
            is_existing = file is existing
            if is_existing and Path(name).suffix.lower() != '.json': raise HTTPException(400, '已有账单应为JSON文件')
            if not is_existing and Path(name).suffix.lower() not in SUPPORTED: raise HTTPException(400, f'不支持的文件类型：{name}')
            sub = inputs / str(idx); sub.mkdir()
            target = sub / name
            size = 0
            with target.open('wb') as stream:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk); total_size += len(chunk)
                    if size > Settings().max_file_bytes or total_size > 100 * 1024 * 1024: raise HTTPException(413, '每文件最多25MB，每批最多100MB')
                    stream.write(chunk)
            if not is_existing: paths.append(target)
            else: previous_path = target
        return run_job(paths, entity, previous_path if existing else None, folder, extractor, ocr, aliases)
    finally:
        for file in files: file.file.close()
        if existing: existing.file.close()
        processing_lock.release()


@app.post('/api/demo')
def demo():
    if not processing_lock.acquire(blocking=False): raise HTTPException(409, '已有任务正在处理')
    try:
        paths = sorted((ROOT / 'samples' / 'original').iterdir())
        return run_job(paths, 'NEPTUNE SHIPPING LIMITED', None, RUNS / uuid4().hex, 'rules', 'local', 'NEPTUNE SHIPPING LIMITED (LAX)')
    finally: processing_lock.release()


@app.get('/api/results/{run_id}/{filename}')
def result_file(run_id: str, filename: str):
    if not re.fullmatch(r'[a-f0-9]{32}', run_id) or filename not in {'records.json', 'report.json', 'proposed_ledger.json'}:
        raise HTTPException(404)
    path = RUNS / run_id / filename
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path, media_type='application/json', filename=filename)
