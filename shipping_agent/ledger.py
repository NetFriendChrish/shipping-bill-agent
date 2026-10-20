"""确定性的财务校验和保守增量匹配；从不直接写入财务系统。"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from datetime import date
import hashlib
import json
import re
import pycountry
from .models import (SourceUnit, ExtractedDocument, ExtractedLine, BillRecord, AuditTrail,
                     FinancialAttributes, DocumentInfo, TransactionParty, Charge)

ALIASES = {
    'handlingfee': 'Handling Charge', 'hdfee': 'Handling Charge', 'hc': 'Handling Charge', 'handlingcharge': 'Handling Charge',
    'docfee': 'Documentation Fee', 'documentationfee': 'Documentation Fee',
    'thc': 'Terminal Handling Charge', 'terminalhandlingcharge': 'Terminal Handling Charge',
    'truckingfee': 'Trucking Charge', 'truckingcharge': 'Trucking Charge',
    'customsfee': 'Customs Clearance Fee', 'customsclearancefee': 'Customs Clearance Fee',
    'storagefee': 'Storage Charge', 'storagecharge': 'Storage Charge',
    'wharfageadministrationfee': 'Wharfage Administration Fee', 'wharfage': 'Wharfage',
}
CURRENCY_ALIASES = {'RMB': 'CNY', 'US$': 'USD', '美元': 'USD', '人民币': 'CNY', '欧元': 'EUR', '英镑': 'GBP'}
TERMS = {'P': 'PP', 'PP': 'PP', 'PREPAID': 'PP', 'FREIGHTPREPAID': 'PP', '预付': 'PP', 'C': 'CC', 'CC': 'CC', 'COLLECT': 'CC', 'FREIGHTCOLLECT': 'CC', '到付': 'CC', 'COD': 'COD', 'CASHONDELIVERY': 'COD'}


def canonical(value):
    return re.sub(r'[^\w]', '', str(value or '').casefold())


def normalize_charge(name):
    return ALIASES.get(canonical(name), name.strip() if name else None)


def normalize_currency(value):
    if value is None: return None
    v = value.strip().upper()
    return CURRENCY_ALIASES.get(v, v)


def money(value):
    return Decimal(str(value))


def contained(quote, text):
    return bool(quote) and re.sub(r'\s+', '', quote).casefold() in re.sub(r'\s+', '', text).casefold()


def currency_in_text(currency, text):
    tokens = re.findall(r'(?<![A-Za-z])[A-Za-z]{3}(?![A-Za-z])|US\$|美元|人民币|欧元|英镑', text)
    return any(normalize_currency(token) == currency for token in tokens)


def numbers(text):
    values = []
    for v in re.findall(r'(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])', text):
        try: values.append(Decimal(v.replace(',', '')))
        except InvalidOperation: pass
    return values


def validate_document(doc: ExtractedDocument, unit: SourceUnit):
    warnings = list(doc.issues) + unit.issues
    for obj in (doc.document_info, doc.transaction_party):
        for key, value in obj.model_dump().items():
            if value is None: continue
            quote = doc.evidence.get(key)
            if not contained(quote, unit.text):
                warnings.append(f'{key}缺少可核验的原文证据')
            elif key not in ('document_date', 'document_type') and canonical(value) not in canonical(quote):
                warnings.append(f'{key}的值与引用不符')
            elif key == 'document_type':
                labels = {'debitnote': ('debitnote', '借项通知'), 'creditnote': ('creditnote', '贷项通知'), 'invoice': ('invoice', '发票'), 'generalnotice': ('generalnotice',), 'email': ('subject', '邮件')}
                if not any(label in canonical(quote) for label in labels.get(canonical(value), (canonical(value),))):
                    warnings.append('单据类型与原文不符')
    if doc.document_info.document_date:
        try: date.fromisoformat(doc.document_info.document_date)
        except ValueError: warnings.append('单据日期不是YYYY-MM-DD有效日期')
        from .extractors import to_date
        dates = re.findall(r'\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b', doc.evidence.get('document_date', ''))
        if doc.document_info.document_date not in {to_date(v) for v in dates}:
            warnings.append('单据日期与日期原文不一致')
    if doc.total is not None:
        quote = doc.evidence.get('total', '')
        if not contained(quote, unit.text) or money(doc.total) not in numbers(quote):
            warnings.append('合计金额缺少有效证据')
        currencies = {normalize_currency(line.charge.currency) for line in doc.lines}
        if len(currencies) == 1 and doc.lines and all(line.charge.amount is not None for line in doc.lines):
            s = sum((money(line.charge.amount) for line in doc.lines), Decimal(0))
            if doc.tax is not None:
                tax_quote = doc.evidence.get('tax', '')
                if not contained(tax_quote, unit.text) or money(doc.tax) not in numbers(tax_quote): warnings.append('税额缺少证据')
                s += money(doc.tax)
            if abs(s - money(doc.total)) > Decimal('0.01'):
                warnings.append(f'明细合计{s}与单据合计{doc.total}不一致')
            if doc.total_currency and normalize_currency(doc.total_currency) not in currencies:
                warnings.append('合计币种与明细币种不一致')
        elif len(currencies) > 1:
            warnings.append('多币种单据不能直接相加，需要按币种核验合计')
    if doc.subtotal is not None:
        quote = doc.evidence.get('subtotal', '')
        if not contained(quote, unit.text) or money(doc.subtotal) not in numbers(quote): warnings.append('小计缺少有效证据')
        if len({normalize_currency(line.charge.currency) for line in doc.lines}) == 1 and all(line.charge.amount is not None for line in doc.lines):
            if abs(sum((money(line.charge.amount) for line in doc.lines), Decimal(0)) - money(doc.subtotal)) > Decimal('0.01'): warnings.append('费用明细与小计不一致')
    return warnings


def financial(doc: ExtractedDocument, entity: str | None, entity_aliases: list[str]):
    p = doc.transaction_party
    identities = {canonical(v) for v in [entity, *entity_aliases] if v}
    payer, payee = p.payer, p.payee
    typed = canonical(doc.document_info.document_type) in {'debitnote', 'creditnote', 'invoice', 'generalnotice'}
    if typed:
        payer = payer or p.recipient
        payee = payee or p.issuer
    is_payer = bool(payer and canonical(payer) in identities)
    is_payee = bool(payee and canonical(payee) in identities)
    posting = 'AP' if is_payer and not is_payee else 'AR' if is_payee and not is_payer else None
    reason = f'当前主体与明确付款方{payer}匹配，判为AP' if posting == 'AP' else f'当前主体与明确收款方{payee}匹配，判为AR' if posting else '当前主体与收付方关系不明确，不能确定AR/AP'
    effect = 'DECREASE' if canonical(doc.document_info.document_type) == 'creditnote' else 'INCREASE'
    return FinancialAttributes(accounting_entity=entity or None, posting_type=posting, settlement_effect=effect if posting else None), reason


def make_record(doc: ExtractedDocument, line: ExtractedLine, unit: SourceUnit, entity: str | None, aliases: list[str], min_confidence: float):
    charge = line.charge.model_copy(deep=True)
    charge.original_charge_item = charge.original_charge_item or charge.charge_item
    charge.charge_item = normalize_charge(charge.original_charge_item)
    charge.currency = normalize_currency(charge.currency)
    if charge.payment_term:
        charge.payment_term = TERMS.get(canonical(charge.payment_term).upper(), TERMS.get(charge.payment_term.strip().upper()))
    warnings = validate_document(doc, unit)
    if not charge.charge_item: warnings.append('缺少费用名称')
    if charge.amount is None: warnings.append('缺少有效金额')
    if not charge.currency or not pycountry.currencies.get(alpha_3=charge.currency): warnings.append('币种缺失或不符合ISO 4217')
    if line.charge.payment_term and not charge.payment_term: warnings.append('未知付款方式')
    if charge.payment_term:
        quote = doc.evidence.get('payment_term', '')
        proof = line.evidence + '\n' + (quote if contained(quote, unit.text) else '')
        valid_term = any(TERMS.get(canonical(token).upper()) == charge.payment_term for token in re.findall(r'\b(?:FREIGHT\s+PREPAID|FREIGHT\s+COLLECT|CASH\s+ON\s+DELIVERY|PP|CC|PREPAID|COLLECT|COD|P|C)\b|预付|到付', proof, re.I))
        if not valid_term: warnings.append('付款方式缺少费用行或明确付款条件证据')
    if not contained(line.evidence, unit.text): warnings.append('费用原文引用不在来源文本中')
    if charge.original_charge_item and canonical(charge.original_charge_item) not in canonical(line.evidence): warnings.append('费用名称与原文不符')
    if charge.amount is not None and money(charge.amount) not in numbers(line.evidence): warnings.append('金额或正负号与费用原文不符')
    for field in ('quantity', 'unit_price'):
        value = getattr(charge, field)
        if value is not None and money(value) not in numbers(line.evidence): warnings.append(f'{field}与费用原文不符')
    if charge.currency:
        quote = doc.evidence.get('total_currency', '')
        proof = line.evidence + '\n' + (quote if contained(quote, unit.text) else '')
        if not currency_in_text(charge.currency, proof):
            warnings.append('币种缺少费用行或合计原文证据')
    if re.search(r'\b\d[OoIl]\d|\b[OoIl]\.\d', line.evidence): warnings.append('存在0/O或1/l疑似OCR混淆，需要原图核对')
    if line.confidence < min_confidence: warnings.append('提取置信度低于阈值')
    if unit.metadata.get('mean_ocr_confidence', 1) < min_confidence: warnings.append('OCR平均置信度低于阈值')
    if charge.quantity is not None and charge.unit_price is not None and charge.amount is not None:
        if abs(abs(money(charge.quantity) * money(charge.unit_price)) - abs(money(charge.amount))) > Decimal('0.01'): warnings.append('数量乘单价与金额不一致')
    fa, reason = financial(doc, entity, aliases)
    if charge.amount is not None and charge.amount < 0 and fa.posting_type: fa.settlement_effect = 'DECREASE'
    if not entity: warnings.append('未指定记账主体')
    elif not fa.posting_type: warnings.append('无法从当前记账主体确定AR/AP')
    if not (doc.document_info.reference_no or doc.document_info.bill_of_lading_no or doc.document_info.house_bill_no or doc.document_info.extension_no or doc.document_info.container_no): warnings.append('缺少用于识别单据的编号/提单号/箱号')
    warnings = list(dict.fromkeys(warnings))
    seed = '|'.join([unit.sha256, str(unit.page), str(unit.metadata.get('sheet')), str(charge.line_id), line.evidence, entity or ''])
    record_id = 'bill-' + hashlib.sha256(seed.encode()).hexdigest()[:20]
    return BillRecord(record_id=record_id, operation='PENDING_REVIEW' if warnings else 'CREATE', document_info=doc.document_info.model_copy(deep=True), transaction_party=doc.transaction_party.model_copy(deep=True), charge_details=charge, financial_attributes=fa, audit_trail=AuditTrail(source_file=unit.source, source_sha256=unit.sha256, page=unit.page, raw_text=unit.text, evidence={**doc.evidence, 'charge_line': line.evidence}, document_totals={'subtotal': doc.subtotal, 'tax': doc.tax, 'total': doc.total, 'currency': doc.total_currency}, warnings=warnings, confidence=line.confidence, ai_reasoning=[f'保留原费用名称并标准化为{charge.charge_item}', reason, '运行金额、币种及明细合计校验；校验异常列于warnings']))


def get_path(record, path):
    value = record
    for part in path.split('.'):
        if not isinstance(value, dict): return None
        value = value.get(part)
    return value


def set_path(record, path, value):
    group, key = path.split('.')
    record.setdefault(group, {})[key] = value


ALLOWED_UPDATES = {f'{group}.{key}' for group, model in [('document_info', DocumentInfo), ('transaction_party', TransactionParty), ('charge_details', Charge)] for key in model.model_fields} - {'charge_details.line_id', 'charge_details.original_charge_item'}
UPDATE_LABELS = {
    'amount': r'金额|amount', 'currency': r'币种|currency', 'payment_term': r'付款方式|支付方式|payment\s*term',
    'quantity': r'数量|quantity|qty', 'unit_price': r'单价|unit\s*price|rate',
    'charge_item': r'费用名称|charge\s*item|description', 'document_type': r'单据类型|document\s*type',
    'reference_no': r'单据编号|发票号|reference\s*no|invoice\s*no', 'document_date': r'单据日期|document\s*date',
    'bill_of_lading_no': r'提单号|MBL|bill\s*of\s*lading', 'house_bill_no': r'分提单|HBL|house\s*bill',
    'extension_no': r'扩展号|extension', 'container_no': r'箱号|container',
    'issuer': r'签发方|issuer', 'recipient': r'接收方|recipient', 'payer': r'付款方|payer', 'payee': r'收款方|payee',
}


def match_candidates(record: dict, existing: list[dict], entity_aliases=None):
    d, c = record['document_info'], record['charge_details']
    entity = record['financial_attributes']['accounting_entity']
    identities = {canonical(v) for v in [entity, *(entity_aliases or [])] if v}
    pool = [r for r in existing if canonical(get_path(r, 'charge_details.charge_item')) == canonical(c['charge_item']) and canonical(get_path(r, 'financial_attributes.accounting_entity')) in identities]
    strategies = [
        ('单据编号+费用名称', lambda r: d.get('reference_no') and canonical(get_path(r, 'document_info.reference_no')) == canonical(d['reference_no'])),
        ('提单号+费用名称+币种', lambda r: any(d.get(key) and canonical(get_path(r, f'document_info.{key}')) == canonical(d[key]) for key in ('bill_of_lading_no', 'house_bill_no')) and (c['currency'] is None or get_path(r, 'charge_details.currency') == c['currency'])),
        ('扩展号+费用名称+币种', lambda r: d.get('extension_no') and canonical(get_path(r, 'document_info.extension_no')) == canonical(d['extension_no']) and (c['currency'] is None or get_path(r, 'charge_details.currency') == c['currency'])),
        ('箱号+交易双方+日期+金额', lambda r: all([d.get('container_no'), d.get('document_date'), record['transaction_party'].get('issuer'), record['transaction_party'].get('recipient'), c['amount'] is not None]) and all(get_path(r, p) == get_path(record, p) for p in ('document_info.container_no', 'document_info.document_date', 'transaction_party.issuer', 'transaction_party.recipient', 'charge_details.amount', 'charge_details.currency'))),
    ]
    for label, predicate in strategies:
        found = [r for r in pool if predicate(r)]
        if found:
            if len(found) > 1:
                same_line = [r for r in found if c.get('line_id') and get_path(r, 'charge_details.line_id') == c['line_id'] and (not c['line_id'].startswith('row-') or get_path(r, 'audit_trail.source_sha256') == get_path(record, 'audit_trail.source_sha256'))]
                if len(same_line) == 1: found = same_line
                else:
                    same_value = [r for r in found if all(get_path(r, p) == get_path(record, p) for p in ('charge_details.amount', 'charge_details.currency', 'charge_details.payment_term'))]
                    if len(same_value) == 1: found = same_value
            return found, label
    return [], None


def reconcile(record: BillRecord, extracted: ExtractedLine, existing: list[dict], entity_aliases=None):
    incoming = record.model_dump()
    matches, strategy = match_candidates(incoming, existing, entity_aliases)
    if not matches: return record
    audit = record.audit_trail
    if len(matches) != 1:
        record.operation = 'PENDING_REVIEW'; audit.warnings.append('匹配到多条已有账单，不能自动选取'); return record
    old = deepcopy(matches[0])
    audit.matched_record_id = old['record_id']; audit.ai_reasoning.append(f'通过{strategy}匹配到已有账单')
    # Missing fields in an update inherit the matched ledger. Invalid supplied
    # values and source/evidence failures are never removed by inheritance.
    inherited = []
    for group in ('document_info', 'transaction_party', 'charge_details'):
        for key, value in old.get(group, {}).items():
            if incoming[group].get(key) is None and value is not None:
                incoming[group][key] = value; inherited.append(f'{group}.{key}')
    removable = set()
    if 'charge_details.amount' in inherited: removable.add('缺少有效金额')
    if 'charge_details.currency' in inherited: removable.add('币种缺失或不符合ISO 4217')
    merged_doc = ExtractedDocument(document_info=DocumentInfo.model_validate(incoming['document_info']), transaction_party=TransactionParty.model_validate(incoming['transaction_party']))
    merged_fa, _ = financial(merged_doc, record.financial_attributes.accounting_entity, entity_aliases or [])
    if merged_fa.posting_type and merged_fa.posting_type == get_path(old, 'financial_attributes.posting_type'):
        removable.add('无法从当前记账主体确定AR/AP')
    audit.warnings = [w for w in audit.warnings if w not in removable]
    if inherited: audit.ai_reasoning.append('未提供字段继承匹配旧账：' + ', '.join(inherited))
    if record.operation == 'PENDING_REVIEW' and not audit.warnings: record.operation = 'CREATE'
    if record.operation == 'PENDING_REVIEW': return record
    # An entity change is never inferred or patched. It changes the accounting perspective.
    differences = [p for p in ALLOWED_UPDATES if get_path(incoming, p) is not None and get_path(old, p) != get_path(incoming, p)]
    differences = [p for p in differences if p != 'charge_details.charge_item' or canonical(get_path(old, p)) != canonical(get_path(incoming, p))]
    if not differences:
        result = BillRecord.model_validate({**old, 'operation': 'NO_CHANGE', 'audit_trail': audit.model_dump()})
        result.audit_trail.ai_reasoning.append('同一费用数据一致，重复输入无需修改'); return result
    explicit = set(extracted.explicit_updates)
    if not explicit or not contained(extracted.update_evidence, audit.raw_text) or not re.search(r'修改|更正|改为|调整|update|change|correct|revise', extracted.update_evidence or '', re.I):
        record.operation = 'PENDING_REVIEW'; audit.warnings.append('与已有账单存在差异，但材料没有明确修改指令'); return record
    if not explicit <= ALLOWED_UPDATES:
        record.operation = 'PENDING_REVIEW'; audit.warnings.append('修改指令包含不允许更新的字段'); return record
    unsupported = set(differences) - explicit
    # Unrequested differences remain the original values, rather than overwrite.
    patched = deepcopy(old)
    for path in sorted(explicit):
        value = get_path(incoming, path)
        if value is None:
            record.operation = 'PENDING_REVIEW'; audit.warnings.append('不能用空值覆盖已有字段'); return record
        field = path.split('.')[1]
        quote = extracted.update_evidence or ''
        value_proven = money(value) in numbers(quote) if field in ('amount', 'quantity', 'unit_price') else canonical(value) in canonical(quote)
        if not re.search(UPDATE_LABELS[field], quote, re.I) or not value_proven:
            record.operation = 'PENDING_REVIEW'; audit.warnings.append(f'修改指令未明确指定{path}及其新值'); return record
        if get_path(old, path) != value:
            set_path(patched, path, value)
            audit.changed_fields[path] = {'before': get_path(old, path), 'after': value}
    if not audit.changed_fields:
        patched['operation'] = 'NO_CHANGE'
    else: patched['operation'] = 'UPDATE'
    audit.evidence['update_instruction'] = extracted.update_evidence
    audit.ai_reasoning.append('仅更新明确要求修改的字段；其他字段继承原账单值')
    if unsupported: audit.ai_reasoning.append('未被明确修改的差异字段保留旧值：' + ', '.join(sorted(unsupported)))
    patched['audit_trail'] = audit.model_dump()
    if patched.get('financial_attributes', {}).get('posting_type'):
        decreased = canonical(get_path(patched, 'document_info.document_type')) == 'creditnote' or (get_path(patched, 'charge_details.amount') or 0) < 0
        patched['financial_attributes']['settlement_effect'] = 'DECREASE' if decreased else 'INCREASE'
    # Recompute AR/AP after party modifications; avoid stale financial attributes.
    if any(p.startswith('transaction_party.') for p in audit.changed_fields):
        record.operation = 'PENDING_REVIEW'; audit.warnings.append('交易方修改会影响AR/AP，需要人工复核'); return record
    return BillRecord.model_validate(patched)


def load_existing(path):
    if not path: return []
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(raw, list): raise ValueError('已有账单JSON必须是数组')
    result, ids = [], set()
    for item in raw:
        # Accept the exam's minimal shape and initialise optional audit/id fields.
        value = deepcopy(item)
        value.setdefault('record_id', 'existing-' + hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20])
        value.setdefault('operation', 'NO_CHANGE')
        value.setdefault('audit_trail', {'source_file': 'existing.json', 'source_sha256': '', 'raw_text': ''})
        record = BillRecord.model_validate(value)
        if not record.financial_attributes.accounting_entity or not record.financial_attributes.posting_type:
            raise ValueError('已有账单必须有明确记账主体和AR/AP')
        if record.charge_details.amount is None or not record.charge_details.charge_item or not pycountry.currencies.get(alpha_3=record.charge_details.currency or ''):
            raise ValueError('已有账单必须有有效费用名称、金额和ISO币种')
        if record.record_id in ids: raise ValueError('已有账单包含重复record_id')
        ids.add(record.record_id)
        if record.operation == 'PENDING_REVIEW': raise ValueError('已有账单不能含未复核记录')
        result.append(record.model_dump())
    return result
