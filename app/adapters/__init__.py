"""Source adapters for the evidence contract."""

from .excel_adapter import ExcelIngestionResult, SheetHeader, ingest_excel

__all__ = ["ExcelIngestionResult", "SheetHeader", "ingest_excel"]
