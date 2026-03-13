"""
OCR configuration management for Promaia.

This module provides configuration support for the OCR pipeline,
including directory management, engine settings, and processing options.
"""
import os
import json
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass, field

from promaia.utils.env_resolver import resolve_env_variables, load_env_file

logger = logging.getLogger(__name__)

@dataclass
class OCRConfig:
    """Configuration for OCR processing."""

    # Core settings
    enabled: bool = True
    engine: str = "openai_vision"  # openai_vision, google_cloud_vision, tesseract, mock

    # Directories
    uploads_directory: str = "data/uploads/pending"
    processed_directory: str = "data/uploads/processed"
    failed_directory: str = "data/uploads/failed"

    # Processing settings
    auto_process: bool = False
    batch_size: int = 10
    confidence_threshold: float = 0.7

    # Image preprocessing options
    preprocessing: Dict[str, Any] = field(default_factory=lambda: {
        "resize_max": 4096,
        "enhance_contrast": True,
        "denoise": False
    })

    # Multi-page document handling
    multi_page: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "group_by": "upload_time",
        "max_gap_seconds": 60
    })

    # API settings
    api_settings: Dict[str, Any] = field(default_factory=dict)

    # LLM cleanup settings
    llm_cleanup: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "model": "claude-haiku-4-5-20251001"
    })

    # Cross-model verification settings
    verification: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "model": "claude-opus-4-6"
    })

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "enabled": self.enabled,
            "engine": self.engine,
            "uploads_directory": self.uploads_directory,
            "processed_directory": self.processed_directory,
            "failed_directory": self.failed_directory,
            "auto_process": self.auto_process,
            "batch_size": self.batch_size,
            "confidence_threshold": self.confidence_threshold,
            "preprocessing": self.preprocessing,
            "multi_page": self.multi_page,
            "api_settings": self.api_settings,
            "llm_cleanup": self.llm_cleanup,
            "verification": self.verification
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OCRConfig":
        """Create config from dictionary."""
        config = cls()

        # Update with provided values
        config.enabled = data.get("enabled", config.enabled)
        config.engine = data.get("engine", config.engine)
        config.uploads_directory = data.get("uploads_directory", config.uploads_directory)
        config.processed_directory = data.get("processed_directory", config.processed_directory)
        config.failed_directory = data.get("failed_directory", config.failed_directory)
        config.auto_process = data.get("auto_process", config.auto_process)
        config.batch_size = data.get("batch_size", config.batch_size)
        config.confidence_threshold = data.get("confidence_threshold", config.confidence_threshold)

        # Update nested dicts
        if "preprocessing" in data:
            config.preprocessing.update(data["preprocessing"])
        if "multi_page" in data:
            config.multi_page.update(data["multi_page"])
        if "api_settings" in data:
            config.api_settings = data["api_settings"]
        if "llm_cleanup" in data:
            config.llm_cleanup.update(data["llm_cleanup"])
        if "verification" in data:
            config.verification.update(data["verification"])

        return config

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a config path relative to the data directory."""
        p = Path(relative_path)
        if p.is_absolute():
            return p
        from promaia.utils.env_writer import get_data_dir
        return get_data_dir() / p

    def validate_directories(self, create_if_missing: bool = True) -> bool:
        """
        Validate that required directories exist.

        Args:
            create_if_missing: If True, create missing directories

        Returns:
            True if all directories are valid
        """
        directories = [
            self.uploads_directory,
            self.processed_directory,
            self.failed_directory
        ]

        all_valid = True
        for directory in directories:
            path = self.resolve_path(directory)

            if not path.exists():
                if create_if_missing:
                    try:
                        path.mkdir(parents=True, exist_ok=True)
                        logger.info(f"Created OCR directory: {path}")
                    except Exception as e:
                        logger.error(f"Failed to create directory {path}: {e}")
                        all_valid = False
                else:
                    logger.error(f"OCR directory does not exist: {path}")
                    all_valid = False
            elif not path.is_dir():
                logger.error(f"OCR path exists but is not a directory: {path}")
                all_valid = False

        return all_valid

    def get_engine_config(self) -> Dict[str, Any]:
        """Get configuration specific to the selected OCR engine."""
        if self.engine == "gemini_vision":
            return {
                "api_key": os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
                **self.api_settings
            }
        elif self.engine == "openai_vision":
            return {
                "api_key": os.getenv("OPENAI_API_KEY"),
                **self.api_settings
            }
        elif self.engine == "google_cloud_vision":
            return {
                "api_key": os.getenv("GOOGLE_CLOUD_VISION_API_KEY"),
                **self.api_settings
            }
        elif self.engine == "tesseract":
            return {
                "tesseract_cmd": os.getenv("TESSERACT_CMD", "tesseract"),
                **self.api_settings
            }
        elif self.engine == "mock":
            return self.api_settings
        else:
            logger.warning(f"Unknown OCR engine: {self.engine}")
            return self.api_settings


class OCRConfigManager:
    """Manages OCR configuration from promaia.config.json."""

    def __init__(self, config_file: str = None):
        if config_file is None:
            from promaia.utils.env_writer import get_config_path
            config_file = str(get_config_path())
        self.config_file = config_file
        self.config: Optional[OCRConfig] = None
        self.load_config()

    def load_config(self) -> OCRConfig:
        """Load OCR configuration from file."""
        if os.path.exists(self.config_file):
            try:
                # Load environment variables first
                load_env_file()

                with open(self.config_file, 'r') as f:
                    config_data = json.load(f)

                # Resolve environment variables
                config_data = resolve_env_variables(config_data)

                # Load OCR config from global section
                ocr_data = config_data.get("global", {}).get("ocr", {})

                if ocr_data:
                    self.config = OCRConfig.from_dict(ocr_data)
                    logger.debug("Loaded OCR configuration from file")
                else:
                    # No OCR config, use defaults
                    self.config = OCRConfig()
                    logger.debug("Using default OCR configuration")

            except Exception as e:
                logger.error(f"Error loading OCR config: {e}")
                self.config = OCRConfig()
        else:
            logger.debug(f"Config file {self.config_file} not found, using defaults")
            self.config = OCRConfig()

        return self.config

    def save_config(self):
        """Save OCR configuration to file."""
        config_data = {}

        # Load existing config to preserve other sections
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config_data = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load existing config for merging: {e}")

        # Ensure global section exists
        if "global" not in config_data:
            config_data["global"] = {}

        # Update OCR section
        config_data["global"]["ocr"] = self.config.to_dict()

        # Write config file
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config_data, f, indent=2)
            logger.debug(f"Saved OCR configuration to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving OCR config: {e}")

    def update_config(self, **kwargs):
        """Update OCR configuration with new values."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.save_config()

    def validate(self, create_directories: bool = True) -> bool:
        """
        Validate OCR configuration.

        Args:
            create_directories: If True, create missing directories

        Returns:
            True if configuration is valid
        """
        if not self.config.enabled:
            logger.info("OCR is disabled in configuration")
            return False

        # Validate directories
        if not self.config.validate_directories(create_if_missing=create_directories):
            return False

        # Validate engine-specific configuration
        if self.config.engine == "gemini_vision":
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.error("GOOGLE_API_KEY or GEMINI_API_KEY not found in environment")
                return False
            logger.debug("Using Gemini Vision for OCR")
        elif self.config.engine == "openai_vision":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.error("OPENAI_API_KEY not found in environment")
                return False
            logger.debug("Using OpenAI Vision for OCR")
        elif self.config.engine == "google_cloud_vision":
            api_key = os.getenv("GOOGLE_CLOUD_VISION_API_KEY")
            if not api_key:
                logger.error("GOOGLE_CLOUD_VISION_API_KEY not found in environment")
                return False
        elif self.config.engine == "tesseract":
            # Check if tesseract is available
            tesseract_cmd = os.getenv("TESSERACT_CMD", "tesseract")
            # Could add actual command check here, but for now just log
            logger.debug(f"Using Tesseract command: {tesseract_cmd}")
        elif self.config.engine == "mock":
            logger.debug("Using mock OCR engine (for testing)")
        else:
            logger.error(f"Unknown OCR engine: {self.config.engine}")
            return False

        return True

    def get_config(self) -> OCRConfig:
        """Get current OCR configuration."""
        if self.config is None:
            self.load_config()
        return self.config


# Global OCR config manager instance
_ocr_config_manager = None

def get_ocr_config_manager(config_file: str = None) -> OCRConfigManager:
    """Get the global OCR config manager instance."""
    global _ocr_config_manager
    if _ocr_config_manager is None:
        _ocr_config_manager = OCRConfigManager(config_file)
    return _ocr_config_manager

def get_ocr_config() -> OCRConfig:
    """Get current OCR configuration."""
    manager = get_ocr_config_manager()
    return manager.get_config()

def validate_ocr_config(create_directories: bool = True) -> bool:
    """Validate OCR configuration."""
    manager = get_ocr_config_manager()
    return manager.validate(create_directories=create_directories)
