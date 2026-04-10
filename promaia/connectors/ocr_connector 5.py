"""
OCR Connector for Promaia.

Integrates OCR processing with Promaia's connector system,
allowing OCR results to be treated like any other data source.
"""
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from promaia.connectors.base import BaseConnector, SyncResult, QueryFilter, DateRangeFilter
from promaia.ocr.processor import OCRProcessor, ProcessedDocument
from promaia.storage.ocr_storage import OCRStorage

logger = logging.getLogger(__name__)


class OCRConnector(BaseConnector):
    """
    Connector for OCR-processed documents.

    This connector doesn't connect to an external service but rather
    processes local images through OCR and stores results.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize OCR connector.

        Args:
            config: Configuration dict with:
                - workspace: Workspace name
                - database_name: Database name (default: "uploads")
                - uploads_directory: Directory to process (optional)
        """
        super().__init__(config)
        self.workspace = config.get("workspace", "default")
        self.database_name = config.get("database_name", "uploads")
        self.uploads_directory = config.get("uploads_directory")

        self.processor = OCRProcessor()
        self.storage = OCRStorage()

    async def connect(self) -> bool:
        """
        Connect to OCR service (validate configuration).

        Returns:
            True if configuration is valid
        """
        try:
            # Validate OCR configuration
            from promaia.config.ocr import validate_ocr_config
            return validate_ocr_config(create_directories=True)
        except Exception as e:
            logger.error(f"Error connecting OCR: {e}")
            return False

    async def test_connection(self) -> bool:
        """
        Test OCR connection.

        Returns:
            True if OCR is configured and working
        """
        return await self.connect()

    async def get_database_schema(self) -> Dict[str, Any]:
        """
        Get OCR database schema.

        Returns:
            Schema dict describing OCR upload properties
        """
        return {
            "name": self.database_name,
            "source_type": "ocr",
            "properties": {
                "Title": {"type": "title"},
                "Upload Date": {"type": "date"},
                "Processing Date": {"type": "date"},
                "OCR Confidence": {"type": "number"},
                "Status": {"type": "select", "options": [
                    "pending", "processing", "completed", "failed", "review_needed"
                ]},
                "Source Image": {"type": "files"},
                "Language": {"type": "select"},
                "Text Length": {"type": "number"}
            }
        }

    async def query_pages(
        self,
        filters: Optional[List[QueryFilter]] = None,
        date_filter: Optional[DateRangeFilter] = None,
        sort_by: Optional[str] = None,
        sort_direction: str = "desc",
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Query OCR processed pages.

        Args:
            filters: Query filters
            date_filter: Date range filter
            sort_by: Property to sort by
            sort_direction: Sort direction
            limit: Max results

        Returns:
            List of page summaries
        """
        try:
            # Get all uploads for workspace
            uploads = self.storage.get_by_workspace(self.workspace)

            # Apply filters
            if filters:
                uploads = self._apply_filters(uploads, filters)

            if date_filter:
                uploads = self._apply_date_filter(uploads, date_filter)

            # Sort
            if sort_by:
                reverse = (sort_direction.lower() == "desc")
                uploads.sort(key=lambda x: x.get(sort_by, ""), reverse=reverse)

            # Limit
            if limit:
                uploads = uploads[:limit]

            # Convert to page summary format
            pages = []
            for upload in uploads:
                pages.append({
                    "page_id": upload.get("page_id", ""),
                    "title": upload.get("title", ""),
                    "created_time": upload.get("created_time", ""),
                    "last_edited_time": upload.get("last_edited_time", ""),
                    "properties": {
                        "Title": upload.get("title", ""),
                        "Status": upload.get("status", ""),
                        "OCR Confidence": upload.get("ocr_confidence", 0.0),
                        "Language": upload.get("language", ""),
                        "Upload Date": upload.get("upload_date", ""),
                        "Processing Date": upload.get("processing_date", ""),
                    }
                })

            return pages

        except Exception as e:
            logger.error(f"Error querying OCR pages: {e}")
            return []

    def _apply_filters(
        self,
        uploads: List[Dict[str, Any]],
        filters: List[QueryFilter]
    ) -> List[Dict[str, Any]]:
        """Apply query filters to uploads."""
        filtered = []
        for upload in uploads:
            include = True
            for f in filters:
                value = upload.get(f.property_name)

                if f.operator == "eq" and value != f.value:
                    include = False
                    break
                elif f.operator == "ne" and value == f.value:
                    include = False
                    break
                elif f.operator == "gt" and not (value and value > f.value):
                    include = False
                    break
                elif f.operator == "lt" and not (value and value < f.value):
                    include = False
                    break
                elif f.operator == "contains" and f.value not in str(value):
                    include = False
                    break

            if include:
                filtered.append(upload)

        return filtered

    def _apply_date_filter(
        self,
        uploads: List[Dict[str, Any]],
        date_filter: DateRangeFilter
    ) -> List[Dict[str, Any]]:
        """Apply date range filter to uploads."""
        filtered = []
        for upload in uploads:
            date_str = upload.get(date_filter.property_name, "")
            if not date_str:
                continue

            try:
                date = datetime.fromisoformat(date_str)

                if date_filter.start_date and date < date_filter.start_date:
                    continue
                if date_filter.end_date and date > date_filter.end_date:
                    continue

                filtered.append(upload)

            except Exception as e:
                logger.warning(f"Error parsing date {date_str}: {e}")
                continue

        return filtered

    async def get_page_content(
        self,
        page_id: str,
        include_properties: bool = True
    ) -> Dict[str, Any]:
        """
        Get full content of an OCR page.

        Args:
            page_id: Page ID or image path
            include_properties: Whether to include properties

        Returns:
            Page content dict
        """
        try:
            # Try to find by page_id or image path
            upload = None
            uploads = self.storage.get_by_workspace(self.workspace)

            for u in uploads:
                if u.get("page_id") == page_id or u.get("source_image_path") == page_id:
                    upload = u
                    break

            if not upload:
                return {}

            # Read markdown content
            markdown_path = upload.get("file_path", "")
            content = ""
            if markdown_path and Path(markdown_path).exists():
                with open(markdown_path, 'r', encoding='utf-8') as f:
                    content = f.read()

            result = {
                "page_id": upload.get("page_id", ""),
                "title": upload.get("title", ""),
                "content": content,
                "created_time": upload.get("created_time", ""),
                "last_edited_time": upload.get("last_edited_time", ""),
            }

            if include_properties:
                result["properties"] = {
                    "Title": upload.get("title", ""),
                    "Status": upload.get("status", ""),
                    "OCR Confidence": upload.get("ocr_confidence", 0.0),
                    "Language": upload.get("language", ""),
                    "Upload Date": upload.get("upload_date", ""),
                    "Processing Date": upload.get("processing_date", ""),
                    "Source Image": upload.get("source_image_path", ""),
                }

            return result

        except Exception as e:
            logger.error(f"Error getting OCR page content: {e}")
            return {}

    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        """
        Get properties of an OCR page.

        Args:
            page_id: Page ID

        Returns:
            Properties dict
        """
        content = await self.get_page_content(page_id, include_properties=True)
        return content.get("properties", {})

    async def sync_to_local(
        self,
        output_directory: str,
        filters: Optional[List[QueryFilter]] = None,
        date_filter: Optional[DateRangeFilter] = None,
        include_properties: bool = True,
        force_update: bool = False,
        excluded_properties: List[str] = None
    ) -> SyncResult:
        """
        Process OCR images and sync to local storage.

        Args:
            output_directory: Output directory for markdown files
            filters: Query filters
            date_filter: Date range filter
            include_properties: Include properties in output
            force_update: Force reprocessing
            excluded_properties: Properties to exclude

        Returns:
            Sync result
        """
        result = SyncResult()
        result.start_time = datetime.now()
        result.database_name = self.database_name

        try:
            # Determine directory to process
            process_dir = Path(self.uploads_directory) if self.uploads_directory else None

            if not process_dir or not process_dir.exists():
                from promaia.config.ocr import get_ocr_config
                config = get_ocr_config()
                process_dir = Path(config.uploads_directory)

            # Process images
            logger.info(f"Processing OCR images from {process_dir}")
            documents = await self.processor.process_directory(process_dir)

            # Store results
            for doc in documents:
                result.pages_fetched += 1

                if doc.status == "failed":
                    result.add_error(f"Failed to process {doc.image_path.name}: {doc.error}")
                    continue

                # Store in database
                success = self.storage.store_processed_document(
                    doc,
                    workspace=self.workspace,
                    database_name=self.database_name
                )

                if success:
                    if doc.markdown_path:
                        result.add_success(str(doc.markdown_path))
                    else:
                        result.add_skip()
                else:
                    result.add_error(f"Failed to store {doc.image_path.name}")

            result.end_time = datetime.now()
            logger.info(
                f"OCR sync completed: {result.pages_saved} saved, "
                f"{result.pages_failed} failed"
            )

        except Exception as e:
            logger.error(f"Error in OCR sync: {e}")
            result.add_error(str(e))
            result.end_time = datetime.now()

        return result
