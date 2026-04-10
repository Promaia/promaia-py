"""
Main OCR processor for Promaia.

Orchestrates the full OCR pipeline:
- Image preprocessing
- Text extraction
- Text postprocessing
- Markdown generation
- File organization
"""
import logging
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from promaia.config.ocr import get_ocr_config
from promaia.ocr.engines.base import BaseOCREngine, OCRResult
from promaia.ocr.engines.mock import MockOCREngine
from promaia.ocr.image_preprocessor import ImagePreprocessor
from promaia.ocr.text_postprocessor import TextPostprocessor, create_ocr_markdown
from promaia.ocr.llm_postprocessor import LLMPostprocessor
from promaia.ocr.verification import OCRVerifier
from promaia.storage.ocr_storage import OCRStorage

# Try to import OpenAI Vision engine
try:
    from promaia.ocr.engines.openai_vision import OpenAIVisionEngine
    openai_vision_available = True
except ImportError:
    openai_vision_available = False
    OpenAIVisionEngine = None

# Try to import Gemini Vision engine
try:
    from promaia.ocr.engines.gemini_vision import GeminiVisionEngine
    gemini_vision_available = True
except ImportError:
    gemini_vision_available = False
    GeminiVisionEngine = None

# Try to import Google Vision engine
try:
    from promaia.ocr.engines.google_vision import GoogleVisionEngine
    google_vision_available = True
except ImportError:
    google_vision_available = False
    GoogleVisionEngine = None

# Try to import Notion sync
try:
    from promaia.ocr.notion_sync import create_notion_page_from_ocr
    notion_sync_available = True
except ImportError as e:
    print(f"WARNING: Notion sync not available: {e}")
    notion_sync_available = False
    create_notion_page_from_ocr = None

logger = logging.getLogger(__name__)


@dataclass
class ProcessedDocument:
    """Result from processing an image through OCR pipeline."""

    image_path: Path
    ocr_result: OCRResult
    markdown_path: Optional[Path] = None
    processed_image_path: Optional[Path] = None
    status: str = "pending"  # pending, completed, failed, review_needed
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "image_path": str(self.image_path),
            "markdown_path": str(self.markdown_path) if self.markdown_path else None,
            "processed_image_path": str(self.processed_image_path) if self.processed_image_path else None,
            "status": self.status,
            "confidence": self.ocr_result.confidence if self.ocr_result else 0.0,
            "language": self.ocr_result.language if self.ocr_result else "unknown",
            "text_length": len(self.ocr_result.text) if self.ocr_result and self.ocr_result.text else 0,
            "error": self.error
        }


class OCRProcessor:
    """Main OCR processor orchestrating the full pipeline."""

    def __init__(self, config_file: str = "promaia.config.json", workspace: str = "default"):
        """
        Initialize OCR processor.

        Args:
            config_file: Path to configuration file
            workspace: Workspace name for storing results
        """
        self.config = get_ocr_config()
        self.workspace = workspace
        self.engine = self._initialize_engine()
        self.preprocessor = ImagePreprocessor(
            resize_max=self.config.preprocessing.get("resize_max", 4096),
            enhance_contrast=self.config.preprocessing.get("enhance_contrast", True),
            denoise=self.config.preprocessing.get("denoise", False)
        )
        self.postprocessor = TextPostprocessor()
        # Initialize LLM post-processor with its own config
        self.llm_postprocessor = LLMPostprocessor(config=self.config.llm_cleanup)
        self.verifier = OCRVerifier(config=self.config.verification)
        self.storage = OCRStorage()

    def _get_notion_database_id(self) -> Optional[str]:
        """Get Notion database ID from config if available."""
        try:
            import json
            from promaia.utils.env_writer import get_config_path
            with open(str(get_config_path()), 'r') as f:
                config = json.load(f)
                db_config = config.get("databases", {}).get("ocr_uploads", {})
                return db_config.get("database_id")
        except Exception as e:
            logger.debug(f"No Notion database configured: {e}")
            return None

    def _initialize_engine(self) -> BaseOCREngine:
        """
        Initialize the OCR engine based on configuration.

        Returns:
            Configured OCR engine
        """
        engine_name = self.config.engine
        engine_config = self.config.get_engine_config()

        if engine_name == "gemini_vision":
            if not gemini_vision_available:
                logger.error(
                    "Gemini Vision not available. "
                    "Install with: pip install google-generativeai"
                )
                logger.warning("Falling back to mock engine")
                return MockOCREngine(engine_config)
            return GeminiVisionEngine(engine_config)
        elif engine_name == "openai_vision":
            if not openai_vision_available:
                logger.error(
                    "OpenAI Vision not available. "
                    "Install with: pip install openai"
                )
                logger.warning("Falling back to mock engine")
                return MockOCREngine(engine_config)
            return OpenAIVisionEngine(engine_config)
        elif engine_name == "google_cloud_vision":
            if not google_vision_available:
                logger.error(
                    "Google Cloud Vision not available. "
                    "Install with: pip install google-cloud-vision"
                )
                logger.warning("Falling back to mock engine")
                return MockOCREngine(engine_config)
            return GoogleVisionEngine(engine_config)
        elif engine_name == "mock":
            return MockOCREngine(engine_config)
        elif engine_name == "tesseract":
            # TODO: Implement TesseractEngine
            logger.warning("Tesseract engine not yet implemented, using mock")
            return MockOCREngine(engine_config)
        else:
            logger.error(f"Unknown OCR engine: {engine_name}, using mock")
            return MockOCREngine(engine_config)

    async def process_image(
        self,
        image_path: Path,
        annotation: Optional[str] = None,
        save_markdown: bool = True,
        move_to_processed: bool = True,
        sync_to_notion: bool = True,
        markdown_dir: Optional[Path] = None,
    ) -> ProcessedDocument:
        """
        Process a single image through the OCR pipeline.

        Args:
            image_path: Path to image file
            annotation: Optional context hint (e.g. from Discord message text)
                        that is prepended to the engine prompt to help transcription
            save_markdown: Whether to save markdown file
            move_to_processed: Whether to move image to processed folder

        Returns:
            ProcessedDocument with results
        """
        logger.info(f"Processing image: {image_path}")
        if annotation:
            logger.info(f"Using annotation: {annotation[:80]}...")

        try:
            # Validate image exists
            if not image_path.exists():
                raise FileNotFoundError(f"Image not found: {image_path}")

            # Preprocess image
            preprocessed_path = await self.preprocessor.preprocess_image(image_path)

            # Inject OCR notes and annotation into engine prompt
            original_prompt = self.engine.prompt
            ocr_notes = self._load_ocr_notes()
            if ocr_notes or annotation:
                parts = []
                if ocr_notes:
                    parts.append(f"Background context about this person's writing:\n{ocr_notes}")
                if annotation:
                    parts.append(f"The person who took this photo provided this context:\n{annotation}")
                parts.append(original_prompt)
                self.engine.prompt = "\n\n".join(parts)

            # Extract text using OCR engine
            try:
                ocr_result = await self.engine.extract_text(preprocessed_path)
            finally:
                # Always restore original prompt
                self.engine.prompt = original_prompt

            if not ocr_result.success:
                # OCR failed
                doc = ProcessedDocument(
                    image_path=image_path,
                    ocr_result=ocr_result,
                    status="failed",
                    error=ocr_result.error
                )

                # Move to failed directory
                if move_to_processed:
                    failed_path = self._move_to_failed(image_path)
                    doc.processed_image_path = failed_path

                return doc

            # Postprocess text
            cleaned_text = self.postprocessor.postprocess_text(ocr_result.text)
            ocr_result.text = cleaned_text

            # LLM cleanup for better formatting (if enabled)
            if self.llm_postprocessor.enabled:
                logger.info("Running LLM post-processing to clean up OCR text")
                formatted_text = await self.llm_postprocessor.cleanup_text(ocr_result.text)
                if formatted_text:
                    ocr_result.text = formatted_text

            # Cross-model verification (if enabled)
            if self.verifier.enabled:
                logger.info("Running cross-model verification with Opus")
                verified_text = await self.verifier.verify(image_path, ocr_result.text)
                if verified_text:
                    ocr_result.text = verified_text

            # Determine status based on confidence
            status = "completed"
            if ocr_result.confidence < self.config.confidence_threshold:
                status = "review_needed"

            # Create processed document
            doc = ProcessedDocument(
                image_path=image_path,
                ocr_result=ocr_result,
                status=status
            )

            # Save markdown file
            if save_markdown:
                markdown_path = self._save_markdown(image_path, ocr_result, annotation=annotation, output_dir=markdown_dir)
                doc.markdown_path = markdown_path

            # Store in database BEFORE moving file (so we can get file stats)
            self.storage.store_processed_document(
                doc,
                workspace=self.workspace,
                database_name="uploads"
            )

            # Auto-sync to Notion if configured and requested
            if sync_to_notion and notion_sync_available:
                notion_database_id = self._get_notion_database_id()
                if notion_database_id:
                    logger.info(f"Attempting to sync to Notion for workspace: {self.workspace}")
                    try:
                        page_id = await create_notion_page_from_ocr(
                            notion_database_id,
                            doc,
                            self.workspace
                        )
                        if page_id:
                            # Update storage with page ID (use original path as stored in DB)
                            self.storage.update_page_id(str(image_path), page_id)
                            logger.info(f"✓ Created Notion page: {page_id}")
                        else:
                            logger.error("Notion page creation returned no page_id")
                    except Exception as e:
                        logger.error(f"Failed to sync to Notion: {e}")
                        import traceback
                        logger.error(traceback.format_exc())

            # Move image to processed directory (after all processing is complete)
            if move_to_processed:
                if status == "completed" or status == "review_needed":
                    processed_path = self._move_to_processed(image_path)
                    doc.processed_image_path = processed_path
                else:
                    failed_path = self._move_to_failed(image_path)
                    doc.processed_image_path = failed_path

            # Clean up temporary preprocessed image
            if preprocessed_path != image_path and preprocessed_path.exists():
                preprocessed_path.unlink()

            logger.info(
                f"Successfully processed {image_path.name} "
                f"(confidence: {ocr_result.confidence:.2f}, status: {status})"
            )

            return doc

        except Exception as e:
            logger.error(f"Error processing image {image_path}: {e}")

            doc = ProcessedDocument(
                image_path=image_path,
                ocr_result=OCRResult(text="", confidence=0.0, success=False, error=str(e)),
                status="failed",
                error=str(e)
            )

            # Move to failed directory
            if move_to_processed:
                try:
                    failed_path = self._move_to_failed(image_path)
                    doc.processed_image_path = failed_path
                except Exception as move_error:
                    logger.error(f"Failed to move image to failed directory: {move_error}")

            return doc

    async def process_directory(
        self,
        directory: Optional[Path] = None,
        batch_size: int = None,
        recursive: bool = False
    ) -> List[ProcessedDocument]:
        """
        Process all images in a directory.

        Args:
            directory: Directory to process (default: config uploads_directory)
            batch_size: Number of images to process in parallel (default: config batch_size)
            recursive: Whether to process subdirectories

        Returns:
            List of processed documents
        """
        if directory is None:
            directory = self.config.resolve_path(self.config.uploads_directory)

        if batch_size is None:
            batch_size = self.config.batch_size

        # Find all image files
        image_files = self._find_image_files(directory, recursive)

        if not image_files:
            logger.info(f"No images found in {directory}")
            return []

        logger.info(f"Found {len(image_files)} images to process")

        # Process images in batches
        results = []
        for i in range(0, len(image_files), batch_size):
            batch = image_files[i:i + batch_size]
            logger.info(
                f"Processing batch {i // batch_size + 1} "
                f"({len(batch)} images)"
            )

            # Process batch sequentially for now
            # Could be parallelized with asyncio.gather in the future
            for image_path in batch:
                result = await self.process_image(image_path)
                results.append(result)

        # Log summary
        self._log_summary(results)

        return results

    def _find_image_files(
        self,
        directory: Path,
        recursive: bool = False
    ) -> List[Path]:
        """
        Find all image files in directory.

        Args:
            directory: Directory to search
            recursive: Whether to search subdirectories

        Returns:
            List of image file paths
        """
        supported_formats = self.engine.get_supported_formats()
        image_files = []

        pattern = "**/*" if recursive else "*"

        for file_path in directory.glob(pattern):
            if file_path.is_file() and file_path.suffix.lower() in supported_formats:
                image_files.append(file_path)

        # Sort by modification time (oldest first)
        image_files.sort(key=lambda p: p.stat().st_mtime)

        return image_files

    def _load_ocr_notes(self) -> Optional[str]:
        """Load OCR notes from the prompts directory if it exists."""
        try:
            from promaia.utils.env_writer import get_prompts_dir
            notes_path = get_prompts_dir() / "ocr" / "notes.md"
            if notes_path.exists():
                text = notes_path.read_text().strip()
                if text:
                    logger.debug(f"Loaded OCR notes ({len(text)} chars)")
                    return text
        except Exception as e:
            logger.debug(f"Could not load OCR notes: {e}")
        return None

    def _save_markdown(self, image_path: Path, ocr_result: OCRResult, annotation: Optional[str] = None, output_dir: Optional[Path] = None) -> Path:
        """
        Save OCR result as markdown file.

        Args:
            image_path: Original image path
            ocr_result: OCR result
            output_dir: Custom output directory (defaults to data/md/ocr/)

        Returns:
            Path to saved markdown file
        """
        # Create markdown directory if needed
        if output_dir:
            md_dir = Path(output_dir)
        else:
            from promaia.utils.env_writer import get_data_subdir
            md_dir = get_data_subdir() / "md" / "ocr"
        md_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename: YYYY-MM-DD filename.md
        date_str = datetime.now().strftime("%Y-%m-%d")
        md_filename = f"{date_str} {image_path.stem}.md"
        md_path = md_dir / md_filename

        # Create metadata
        metadata = {
            "title": image_path.stem,
            "source_image": str(image_path),
            "confidence": f"{ocr_result.confidence:.2f}",
            "language": ocr_result.language,
            "processed_date": datetime.now().isoformat(),
            "text_length": len(ocr_result.text)
        }
        if annotation:
            metadata["annotation"] = annotation

        # Create markdown content
        markdown_content = create_ocr_markdown(
            text=ocr_result.text,
            title=image_path.stem,
            metadata=metadata
        )

        # Write to file
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        logger.debug(f"Saved markdown to {md_path}")

        return md_path

    def _move_to_processed(self, image_path: Path) -> Path:
        """
        Move image to processed directory.

        Args:
            image_path: Image to move

        Returns:
            New path
        """
        processed_dir = self.config.resolve_path(self.config.processed_directory)
        processed_dir.mkdir(parents=True, exist_ok=True)

        dest_path = processed_dir / image_path.name

        # Handle name conflicts
        counter = 1
        while dest_path.exists():
            dest_path = processed_dir / f"{image_path.stem}_{counter}{image_path.suffix}"
            counter += 1

        shutil.move(str(image_path), str(dest_path))
        logger.debug(f"Moved image to {dest_path}")

        return dest_path

    def _move_to_failed(self, image_path: Path) -> Path:
        """
        Move image to failed directory.

        Args:
            image_path: Image to move

        Returns:
            New path
        """
        failed_dir = self.config.resolve_path(self.config.failed_directory)
        failed_dir.mkdir(parents=True, exist_ok=True)

        dest_path = failed_dir / image_path.name

        # Handle name conflicts
        counter = 1
        while dest_path.exists():
            dest_path = failed_dir / f"{image_path.stem}_{counter}{image_path.suffix}"
            counter += 1

        shutil.move(str(image_path), str(dest_path))
        logger.debug(f"Moved image to failed directory: {dest_path}")

        return dest_path

    def _log_summary(self, results: List[ProcessedDocument]):
        """
        Log summary of processing results.

        Args:
            results: List of processed documents
        """
        total = len(results)
        completed = sum(1 for r in results if r.status == "completed")
        review_needed = sum(1 for r in results if r.status == "review_needed")
        failed = sum(1 for r in results if r.status == "failed")

        avg_confidence = sum(
            r.ocr_result.confidence for r in results if r.ocr_result
        ) / total if total > 0 else 0.0

        logger.info("=" * 60)
        logger.info("OCR Processing Summary")
        logger.info("=" * 60)
        logger.info(f"Total images:        {total}")
        logger.info(f"Completed:           {completed}")
        logger.info(f"Review needed:       {review_needed}")
        logger.info(f"Failed:              {failed}")
        logger.info(f"Average confidence:  {avg_confidence:.2f}")
        logger.info("=" * 60)
