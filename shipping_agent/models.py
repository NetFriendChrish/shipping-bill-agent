from typing import Literal, Any
from pydantic import BaseModel, Field, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SourceUnit(StrictModel):
    source: str
    sha256: str
    kind: str
    text: str = ''
    tables: list[list[list[Any]]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    page: int | None = None


class DocumentInfo(StrictModel):
    document_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None
    bill_of_lading_no: str | None = None
    house_bill_no: str | None = None
    extension_no: str | None = None
    container_no: str | None = None


class TransactionParty(StrictModel):
    issuer: str | None = None
    recipient: str | None = None
    payer: str | None = None
    payee: str | None = None


class Charge(StrictModel):
    charge_item: str | None = None
    original_charge_item: str | None = None
    amount: float | None = None
    currency: str | None = None
    payment_term: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    line_id: str | None = None


class ExtractedLine(StrictModel):
    charge: Charge = Field(default_factory=Charge)
    evidence: str = Field(description='逐字复制的费用原始行。禁止改写或补充。')
    confidence: float = Field(default=0.9, ge=0, le=1)
    # Only explicit corrections, never inferred from differing values.
    explicit_updates: list[str] = Field(default_factory=list, description='明确修改的点分字段，如 charge_details.amount')
    update_evidence: str | None = Field(default=None, description='明确修改指令的原文引用')


class ExtractedDocument(StrictModel):
    document_info: DocumentInfo = Field(default_factory=DocumentInfo)
    transaction_party: TransactionParty = Field(default_factory=TransactionParty)
    lines: list[ExtractedLine] = Field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    total_currency: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict, description='单据及交易方的非空字段到逐字原文引用的映射')
    issues: list[str] = Field(default_factory=list)


class ExtractionBatch(StrictModel):
    documents: list[ExtractedDocument] = Field(default_factory=list)


class FinancialAttributes(StrictModel):
    accounting_entity: str | None = None
    posting_type: Literal['AR', 'AP'] | None = None
    settlement_effect: Literal['INCREASE', 'DECREASE'] | None = None


class AuditTrail(StrictModel):
    ai_reasoning: list[str] = Field(default_factory=list, description='简短、可验证的业务决策依据')
    source_file: str
    source_sha256: str
    page: int | None = None
    raw_text: str
    evidence: dict[str, str] = Field(default_factory=dict)
    document_totals: dict[str, float | str | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    confidence: float = 0
    matched_record_id: str | None = None
    changed_fields: dict[str, Any] = Field(default_factory=dict)
    duplicate_of: str | None = None


class BillRecord(StrictModel):
    record_id: str
    operation: Literal['CREATE', 'UPDATE', 'NO_CHANGE', 'PENDING_REVIEW']
    document_info: DocumentInfo
    transaction_party: TransactionParty
    charge_details: Charge
    financial_attributes: FinancialAttributes
    audit_trail: AuditTrail


class RunReport(StrictModel):
    records: list[BillRecord]
    units: list[SourceUnit]
    diagnostics: list[dict[str, Any]]
    summary: dict[str, Any]
