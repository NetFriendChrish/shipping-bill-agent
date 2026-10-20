from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')


@dataclass
class Settings:
    extractor: str = os.getenv('EXTRACTOR', 'rules')
    ocr_mode: str = os.getenv('OCR_MODE', 'local')
    ocr_endpoint: str = os.getenv('OCR_ENDPOINT', 'http://36.103.199.11:5002/process')
    ocr_url_map: str = os.getenv('OCR_URL_MAP', '')
    connect_timeout: int = int(os.getenv('OCR_CONNECT_TIMEOUT', '10'))
    read_timeout: int = int(os.getenv('OCR_READ_TIMEOUT', '300'))
    llm_model: str = os.getenv('LLM_MODEL', '')
    llm_api_key: str = os.getenv('LLM_API_KEY', '')
    llm_base_url: str = os.getenv('LLM_BASE_URL', '')
    llm_method: str = os.getenv('LLM_STRUCTURED_METHOD', 'function_calling')
    llm_timeout: int = int(os.getenv('LLM_TIMEOUT', '90'))
    min_confidence: float = float(os.getenv('MIN_CONFIDENCE', '0.85'))
    max_file_bytes: int = 25 * 1024 * 1024
    max_units: int = 150
    max_depth: int = 4
    max_text_chars: int = 100_000
    max_pages: int = 100
