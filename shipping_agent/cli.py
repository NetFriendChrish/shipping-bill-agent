import argparse
from pathlib import Path
import json
from .config import Settings
from .ledger import load_existing
from .parsers import SUPPORTED
from .workflow import ShippingBillAgent, save_report


def main():
    parser = argparse.ArgumentParser(description='航运智能账单助手')
    parser.add_argument('inputs', nargs='+', type=Path, help='输入文件或文件夹（文件夹仅读取直接文件）')
    parser.add_argument('--entity', default=None, help='当前记账主体名称，不能使用模糊简称')
    parser.add_argument('--alias', action='append', default=[], help='当前主体明确别名，可重复')
    parser.add_argument('--existing', type=Path, help='已有账单JSON数组')
    parser.add_argument('--output', type=Path, default=Path('output/latest'))
    parser.add_argument('--extractor', choices=['rules', 'llm'])
    parser.add_argument('--ocr', choices=['local', 'remote'])
    args = parser.parse_args()
    paths = []
    for path in args.inputs:
        if path.is_dir(): paths.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED))
        else: paths.append(path)
    settings = Settings()
    if args.extractor: settings.extractor = args.extractor
    if args.ocr: settings.ocr_mode = args.ocr
    try:
        existing = load_existing(args.existing)
        report = ShippingBillAgent(settings).run(paths, args.entity, existing, args.output / 'work', args.alias)
        save_report(report, args.output, existing)
    except Exception as exc:
        parser.exit(1, f'处理失败：{exc}\n')
    print(json.dumps(report.summary, ensure_ascii=False, indent=2))
    print(f'结果：{args.output.resolve() / "records.json"}')
    print(f'审计：{args.output.resolve() / "report.json"}')
    print(f'账单快照建议：{args.output.resolve() / "proposed_ledger.json"}')


if __name__ == '__main__': main()
