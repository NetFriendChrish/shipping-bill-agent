from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter
import hashlib
import json
from langchain_core.runnables import RunnableLambda
from .config import Settings
from .models import (SourceUnit, BillRecord, AuditTrail, DocumentInfo, TransactionParty,
                     FinancialAttributes, Charge, RunReport)
from .parsers import parse_file
from .extractors import Extractor
from .ledger import make_record, reconcile, canonical, normalize_charge


def failure_record(unit: SourceUnit, entity, message):
    return BillRecord(record_id='review-' + hashlib.sha256((unit.source + unit.sha256 + message).encode()).hexdigest()[:20], operation='PENDING_REVIEW', document_info=DocumentInfo(), transaction_party=TransactionParty(), charge_details=Charge(), financial_attributes=FinancialAttributes(accounting_entity=entity or None), audit_trail=AuditTrail(source_file=unit.source, source_sha256=unit.sha256, page=unit.page, raw_text=unit.text, warnings=[message], ai_reasoning=['来源未成功解析或提取，未生成可入账费用']))


class ShippingBillAgent:
    """Bounded domain agent: each stage is observable; no autonomous finance writes."""
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.extractor = Extractor(self.settings)
        self.chain = (
            RunnableLambda(self._parse, name='parse_documents')
            | RunnableLambda(self._extract_validate_match, name='extract_validate_reconcile')
            | RunnableLambda(self._batch_checks, name='detect_batch_conflicts')
            | RunnableLambda(self._report, name='build_audited_json')
        )

    def run(self, paths: list[Path], entity: str | None, existing: list[dict] | None = None, work: Path | None = None, entity_aliases: list[str] | None = None):
        if not paths: raise ValueError('请至少选择一个文件')
        if len(paths) > 30: raise ValueError('每批最多30个文件')
        work = work or Path('output/work')
        work.mkdir(parents=True, exist_ok=True)
        # Existing records use the same canonical charge names as new records.
        previous = json.loads(json.dumps(existing or [], allow_nan=False))
        for r in previous:
            c = r.get('charge_details', {})
            c['charge_item'] = normalize_charge(c.get('charge_item') or c.get('original_charge_item'))
        return self.chain.invoke({'paths': paths, 'entity': entity, 'aliases': entity_aliases or [], 'existing': previous, 'work': work, 'started': perf_counter(), 'diagnostics': []})

    def _parse(self, state):
        units = []
        for path in state['paths']:
            units.extend(parse_file(path, self.settings, state['work']))
            if len(units) > self.settings.max_units: raise ValueError('解析单元超过150个，请拆分批次')
        state['units'] = units
        return state

    def _extract_validate_match(self, state):
        records = []
        for unit in state['units']:
            if unit.metadata.get('skipped'):
                state['diagnostics'].append({'source': unit.source, 'status': 'SKIPPED', 'reason': unit.metadata['skipped']}); continue
            if unit.issues:
                records.append(failure_record(unit, state['entity'], '; '.join(unit.issues))); continue
            try:
                batch = self.extractor.invoke(unit)
                count = 0
                for doc in batch.documents:
                    for line in doc.lines:
                        record = make_record(doc, line, unit, state['entity'], state['aliases'], self.settings.min_confidence)
                        records.append(reconcile(record, line, state['existing'], state['aliases'])); count += 1
                    if not doc.lines and doc.issues:
                        records.append(failure_record(unit, state['entity'], '; '.join(doc.issues)))
                state['diagnostics'].append({'source': unit.source, 'page': unit.page, 'status': 'EXTRACTED' if count else 'NO_CHARGES', 'charge_count': count, 'engine': unit.metadata.get('engine'), 'extractor': self.settings.extractor})
            except Exception as exc:
                records.append(failure_record(unit, state['entity'], f'提取失败：{type(exc).__name__}: {exc}'))
        state['records'] = records
        return state

    def _batch_checks(self, state):
        records = state['records']
        fingerprints = {}
        groups = defaultdict(list)
        for r in records:
            d, c, a = r.document_info, r.charge_details, r.audit_trail
            if c.amount is None: continue
            # Preserve physical same-name rows; do not use charge name alone.
            identity = canonical(d.reference_no or d.bill_of_lading_no or d.house_bill_no or d.extension_no)
            if not identity: identity = a.source_sha256
            parties = tuple(canonical(v) for v in r.transaction_party.model_dump().values())
            document = tuple(canonical(v) for v in d.model_dump().values())
            key = (identity, canonical(c.charge_item), c.amount, c.currency, c.quantity, c.unit_price, c.payment_term, canonical(r.financial_attributes.accounting_entity), parties, document)
            prior = fingerprints.get(key)
            if prior and prior.audit_trail.source_sha256 == a.source_sha256 and prior.audit_trail.page == a.page and prior.charge_details.line_id != c.line_id:
                # Identical physical row text is ambiguous; keep both for review.
                if prior.audit_trail.evidence.get('charge_line') == a.evidence.get('charge_line'):
                    for v in (prior, r):
                        v.operation = 'PENDING_REVIEW'; v.audit_trail.warnings.append('同一单据存在完全相同费用行，需确认是重复提取还是两笔实际费用')
            elif prior:
                if r.operation != 'PENDING_REVIEW' and prior.operation != 'PENDING_REVIEW':
                    r.operation = 'NO_CHANGE'; a.duplicate_of = prior.record_id
                    a.ai_reasoning.append('与本批次已提取的相同单据费用重复，无需再次新增')
            else: fingerprints[key] = r
            target = a.matched_record_id
            if target: groups[('target', target)].append(r)
            else: groups[('create', identity, canonical(c.charge_item))].append(r)
        for key, group in groups.items():
            if len(group) < 2: continue
            # Different amounts in ONE physical invoice can be independent fees.
            by_source = defaultdict(set)
            for r in group:
                c = r.charge_details
                signature = (c.amount, c.currency, c.payment_term, c.quantity, c.unit_price, *(canonical(v) for v in r.transaction_party.model_dump().values()), *(canonical(v) for v in r.document_info.model_dump().values()))
                by_source[r.audit_trail.source_sha256].add(signature)
            values = {frozenset(v) for v in by_source.values()}
            if len(by_source) > 1 and len(values) > 1:
                for r in group:
                    r.operation = 'PENDING_REVIEW'; r.audit_trail.warnings.append('本批多个来源对同一账单费用提供冲突数据')
        return state

    def _report(self, state):
        counts = Counter(r.operation for r in state['records'])
        return RunReport(records=state['records'], units=state['units'], diagnostics=state['diagnostics'], summary={
            'files': len(state['paths']), 'source_units': len(state['units']), 'records': len(state['records']),
            'operations': {k: counts[k] for k in ('CREATE', 'UPDATE', 'NO_CHANGE', 'PENDING_REVIEW')},
            'extractor': self.settings.extractor, 'ocr_mode': self.settings.ocr_mode,
            'seconds': round(perf_counter() - state['started'], 2),
            'accounting_entity': state['entity'], 'financial_system_written': False,
        })


def proposed_ledger(report: RunReport, existing: list[dict]):
    """A reviewable snapshot, never applies PENDING_REVIEW or NO_CHANGE."""
    ledger = {r['record_id']: r for r in existing}
    for record in report.records:
        if record.operation in ('CREATE', 'UPDATE'):
            ledger[record.record_id] = record.model_dump()
    return list(ledger.values())


def save_report(report: RunReport, output: Path, existing: list[dict] | None = None):
    output.mkdir(parents=True, exist_ok=True)
    values = {
        'records.json': [r.model_dump() for r in report.records],
        'report.json': report.model_dump(),
        'proposed_ledger.json': proposed_ledger(report, existing or []),
    }
    for name, value in values.items():
        target = output / name
        temp = target.with_suffix('.json.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temp.replace(target)
