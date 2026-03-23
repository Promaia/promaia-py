# Trass Unified MCP Server Implementation Guide

## Overview

This guide walks you through building a comprehensive Trass MCP (Model Context Protocol) server that starts with ShipBob WRO creation and expands into a unified platform for all company operations. The architecture leverages Claude's document analysis capabilities while keeping tools focused on business workflows.

## Architecture Design

### Core Principle
- **Claude**: Analyzes documents, manages conversation, coordinates workflows
- **Trass MCP Tools**: Handle business operations across all company systems
- **User**: Uploads files, provides context, confirms actions

### Unified Platform Benefits
```
Single conversation can:
User uploads shipment docs → Claude creates WRO → Updates internal tracker → 
Notifies logistics team → Schedules follow-ups → Generates reports
```

## Project Structure

```
trass-mcp-server/
├── src/
│   ├── __init__.py
│   ├── server.py              # Main Trass MCP server
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── shipbob/           # ShipBob operations
│   │   │   ├── __init__.py
│   │   │   ├── wro_creator.py
│   │   │   ├── validator.py
│   │   │   └── client.py
│   │   ├── inventory/         # Internal inventory tracking
│   │   │   ├── __init__.py
│   │   │   ├── tracker.py
│   │   │   └── reports.py
│   │   ├── communications/    # Notifications & messaging
│   │   │   ├── __init__.py
│   │   │   ├── slack.py
│   │   │   ├── email.py
│   │   │   └── notifications.py
│   │   ├── documents/         # Document processing
│   │   │   ├── __init__.py
│   │   │   ├── processor.py
│   │   │   └── generators.py
│   │   └── analytics/         # Reporting & insights
│   │       ├── __init__.py
│   │       ├── reports.py
│   │       └── dashboards.py
│   ├── shared/
│   │   ├── __init__.py
│   │   ├── config.py          # Unified configuration
│   │   ├── auth.py            # Authentication management
│   │   ├── database.py        # Database connections
│   │   └── utils.py           # Common utilities
│   └── core/
│       ├── __init__.py
│       ├── workflows.py       # Business workflow orchestration
│       ├── error_handler.py   # Unified error handling
│       └── logging.py         # Structured logging
├── config/
│   ├── trass_config.json      # Main configuration
│   ├── development.json       # Dev environment
│   └── production.json        # Production environment
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/
│   ├── api_reference.md
│   ├── workflows.md
│   └── deployment.md
├── requirements.txt
├── setup.py
└── README.md
```

## Step 1: Environment Setup

### 1.1 Create Project Structure
```bash
mkdir trass-mcp-server
cd trass-mcp-server

# Create all directories
mkdir -p src/{tools/{shipbob,inventory,communications,documents,analytics},shared,core}
mkdir -p config tests/{unit,integration,fixtures} docs

# Create __init__.py files
find src -type d -exec touch {}/__init__.py \;
touch tests/__init__.py
```

### 1.2 Install Dependencies
```bash
# Create requirements.txt
cat > requirements.txt << EOF
# MCP and core
mcp>=1.0.0
httpx>=0.25.0
pydantic>=2.0.0
python-dotenv>=1.0.0

# Logging and monitoring
structlog>=23.1.0
tenacity>=8.2.0

# Database and storage
sqlalchemy>=2.0.0
alembic>=1.12.0
redis>=5.0.0

# Communication integrations
slack-sdk>=3.21.0
emails>=0.6.0
twilio>=8.5.0

# Document processing
PyPDF2>=3.0.0
openpyxl>=3.1.0
pandas>=2.0.0
python-docx>=0.8.11

# Analytics and reporting
plotly>=5.15.0
jinja2>=3.1.0

# Development and testing
pytest>=7.4.0
pytest-asyncio>=0.21.0
black>=23.7.0
isort>=5.12.0
mypy>=1.5.0
EOF

# Install dependencies
pip install -r requirements.txt
```

### 1.3 Unified Configuration
```json
// config/trass_config.json
{
  "company": {
    "name": "Trass",
    "timezone": "America/Los_Angeles",
    "default_notifications": ["logistics@trass.com"],
    "business_hours": {
      "start": "08:00",
      "end": "17:00",
      "timezone": "America/Los_Angeles"
    }
  },
  "integrations": {
    "shipbob": {
      "base_url": "https://api.shipbob.com",
      "fulfillment_centers": {
        "Ontario": 156,
        "Moreno Valley": 100,
        "Commerce": 111
      },
      "default_fc": "Ontario",
      "package_type": "Pallet",
      "box_packaging_type": "MultipleSkuPerBox",
      "tracking_pattern": "{PO}-PALLET-{counter:03d}",
      "pallet_limits": {
        "plush": 432,
        "small_parts": 4320
      }
    },
    "slack": {
      "channels": {
        "logistics": "#logistics",
        "inventory": "#inventory",
        "alerts": "#operations-alerts"
      }
    },
    "email": {
      "smtp_server": "smtp.gmail.com",
      "smtp_port": 587,
      "templates_dir": "templates/email"
    },
    "database": {
      "type": "postgresql",
      "host": "localhost",
      "port": 5432,
      "name": "trass_operations"
    },
    "redis": {
      "host": "localhost",
      "port": 6379,
      "db": 0
    }
  },
  "validation": {
    "max_quantity_per_sku": 50000,
    "min_quantity_per_sku": 1,
    "max_pallets_per_wro": 200,
    "max_file_size_mb": 50
  },
  "workflows": {
    "receiving": {
      "auto_notify": true,
      "auto_update_tracker": true,
      "require_confirmation": true
    },
    "reporting": {
      "auto_schedule": true,
      "default_recipients": ["management@trass.com"]
    }
  }
}
```

## Step 2: Shared Infrastructure

### 2.1 Configuration Management
```python
# src/shared/config.py
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class CompanyConfig:
    name: str
    timezone: str
    default_notifications: list
    business_hours: dict

@dataclass
class IntegrationConfig:
    shipbob: dict
    slack: dict
    email: dict
    database: dict
    redis: dict

class TrassConfig:
    def __init__(self, config_path: str = None, environment: str = None):
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config"
        
        # Load main config
        main_config_path = Path(config_path) / "trass_config.json"
        with open(main_config_path, 'r') as f:
            self._config = json.load(f)
        
        # Load environment-specific overrides
        env = environment or os.getenv("TRASS_ENVIRONMENT", "development")
        env_config_path = Path(config_path) / f"{env}.json"
        if env_config_path.exists():
            with open(env_config_path, 'r') as f:
                env_config = json.load(f)
                self._merge_configs(self._config, env_config)
        
        # Load secrets from environment
        self._load_secrets()
    
    def _load_secrets(self):
        """Load sensitive configuration from environment variables"""
        secrets_map = {
            "SHIPBOB_API_KEY": ["integrations", "shipbob", "api_key"],
            "SLACK_BOT_TOKEN": ["integrations", "slack", "bot_token"],
            "SLACK_WEBHOOK_URL": ["integrations", "slack", "webhook_url"],
            "EMAIL_PASSWORD": ["integrations", "email", "password"],
            "EMAIL_USERNAME": ["integrations", "email", "username"],
            "DATABASE_URL": ["integrations", "database", "url"],
            "REDIS_URL": ["integrations", "redis", "url"],
        }
        
        for env_var, config_path in secrets_map.items():
            value = os.getenv(env_var)
            if value:
                self._set_nested_value(self._config, config_path, value)
    
    def _merge_configs(self, base: dict, override: dict):
        """Recursively merge configuration dictionaries"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_configs(base[key], value)
            else:
                base[key] = value
    
    def _set_nested_value(self, config: dict, path: list, value: Any):
        """Set a nested configuration value"""
        current = config
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[path[-1]] = value
    
    @property
    def company(self) -> CompanyConfig:
        return CompanyConfig(**self._config["company"])
    
    @property
    def integrations(self) -> IntegrationConfig:
        return IntegrationConfig(**self._config["integrations"])
    
    @property
    def validation(self) -> Dict[str, Any]:
        return self._config["validation"]
    
    @property
    def workflows(self) -> Dict[str, Any]:
        return self._config["workflows"]
    
    def get_shipbob_config(self) -> Dict[str, Any]:
        return self._config["integrations"]["shipbob"]
    
    def get_fc_id(self, fc_name: str = None) -> int:
        shipbob_config = self.get_shipbob_config()
        fc_name = fc_name or shipbob_config["default_fc"]
        return shipbob_config["fulfillment_centers"][fc_name]

# Global config instance
config = TrassConfig()
```

### 2.2 Unified Authentication
```python
# src/shared/auth.py
import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass
import httpx
from slack_sdk.web.async_client import AsyncWebClient
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

@dataclass
class AuthCredentials:
    service: str
    credentials: Dict[str, Any]
    is_valid: bool = False
    last_validated: Optional[str] = None

class TrassAuthManager:
    def __init__(self, config):
        self.config = config
        self._credentials = {}
        self._clients = {}
    
    async def get_shipbob_client(self):
        """Get authenticated ShipBob HTTP client"""
        if "shipbob" not in self._clients:
            api_key = self.config.get_shipbob_config().get("api_key")
            if not api_key:
                raise ValueError("ShipBob API key not configured")
            
            self._clients["shipbob"] = httpx.AsyncClient(
                base_url=self.config.get_shipbob_config()["base_url"],
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )
        
        return self._clients["shipbob"]
    
    async def get_slack_client(self):
        """Get authenticated Slack client"""
        if "slack" not in self._clients:
            token = self.config.integrations.slack.get("bot_token")
            if not token:
                raise ValueError("Slack bot token not configured")
            
            self._clients["slack"] = AsyncWebClient(token=token)
        
        return self._clients["slack"]
    
    async def get_email_client(self):
        """Get authenticated email client"""
        if "email" not in self._clients:
            email_config = self.config.integrations.email
            username = email_config.get("username")
            password = email_config.get("password")
            
            if not username or not password:
                raise ValueError("Email credentials not configured")
            
            # Return config for email client creation
            self._clients["email"] = {
                "username": username,
                "password": password,
                "smtp_server": email_config["smtp_server"],
                "smtp_port": email_config["smtp_port"]
            }
        
        return self._clients["email"]
    
    async def validate_all_connections(self) -> Dict[str, bool]:
        """Validate all configured integrations"""
        results = {}
        
        # Test ShipBob
        try:
            client = await self.get_shipbob_client()
            response = await client.get("/1.0/fulfillmentCenter")
            results["shipbob"] = response.status_code == 200
        except Exception:
            results["shipbob"] = False
        
        # Test Slack
        try:
            client = await self.get_slack_client()
            response = await client.auth_test()
            results["slack"] = response["ok"]
        except Exception:
            results["slack"] = False
        
        # Test Email
        try:
            email_config = await self.get_email_client()
            with smtplib.SMTP(email_config["smtp_server"], email_config["smtp_port"]) as server:
                server.starttls()
                server.login(email_config["username"], email_config["password"])
            results["email"] = True
        except Exception:
            results["email"] = False
        
        return results
    
    async def close_all_clients(self):
        """Clean up all client connections"""
        for client_name, client in self._clients.items():
            if hasattr(client, 'aclose'):
                await client.aclose()
```

### 2.3 Unified Error Handling
```python
# src/core/error_handler.py
import structlog
from typing import Dict, List, Any, Optional
from enum import Enum

logger = structlog.get_logger()

class ErrorSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ErrorCategory(Enum):
    API_ERROR = "api_error"
    VALIDATION_ERROR = "validation_error"
    CONFIGURATION_ERROR = "configuration_error"
    BUSINESS_LOGIC_ERROR = "business_logic_error"
    SYSTEM_ERROR = "system_error"

class TrassError(Exception):
    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        details: Dict[str, Any] = None,
        suggestions: List[str] = None,
        retry_possible: bool = True
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.details = details or {}
        self.suggestions = suggestions or []
        self.retry_possible = retry_possible

class TrassErrorHandler:
    def __init__(self):
        self.error_translations = {
            # ShipBob specific errors
            "permission": {
                "message": "Account doesn't have permission for this operation",
                "suggestions": [
                    "Try a different fulfillment center",
                    "Contact ShipBob support to enable access"
                ],
                "category": ErrorCategory.API_ERROR,
                "retry_possible": True
            },
            "po reference already exists": {
                "message": "This purchase order number already exists",
                "suggestions": [
                    "Add a suffix like '-B' to make it unique",
                    "Check if this shipment was already created"
                ],
                "category": ErrorCategory.VALIDATION_ERROR,
                "retry_possible": True
            },
            "tracking": {
                "message": "Issue with tracking number generation",
                "suggestions": ["This shouldn't happen - contact support"],
                "category": ErrorCategory.SYSTEM_ERROR,
                "retry_possible": False
            },
            "inventory": {
                "message": "SKU not found in inventory",
                "suggestions": [
                    "Check SKU spelling and formatting",
                    "Verify products exist in ShipBob catalog"
                ],
                "category": ErrorCategory.VALIDATION_ERROR,
                "retry_possible": True
            }
        }
    
    def translate_error(self, error: Exception, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Translate any error into user-friendly format"""
        error_text = str(error).lower()
        context = context or {}
        
        # Check for known error patterns
        for pattern, translation in self.error_translations.items():
            if pattern in error_text:
                return {
                    "success": False,
                    "error_type": translation["category"].value,
                    "severity": ErrorSeverity.MEDIUM.value,
                    "user_message": translation["message"],
                    "suggestions": translation["suggestions"],
                    "retry_possible": translation["retry_possible"],
                    "technical_error": str(error),
                    "context": context
                }
        
        # Handle HTTP errors
        if hasattr(error, 'status_code'):
            if error.status_code == 401:
                return {
                    "success": False,
                    "error_type": ErrorCategory.API_ERROR.value,
                    "user_message": "Authentication failed",
                    "suggestions": ["Check API credentials"],
                    "retry_possible": False
                }
            elif error.status_code == 429:
                return {
                    "success": False,
                    "error_type": ErrorCategory.API_ERROR.value,
                    "user_message": "Too many requests - please wait",
                    "suggestions": ["Try again in a few minutes"],
                    "retry_possible": True
                }
        
        # Generic error
        return {
            "success": False,
            "error_type": ErrorCategory.SYSTEM_ERROR.value,
            "severity": ErrorSeverity.MEDIUM.value,
            "user_message": f"An error occurred: {str(error)}",
            "suggestions": ["Please try again or contact support"],
            "retry_possible": True,
            "technical_error": str(error)
        }
    
    async def handle_and_log_error(
        self,
        error: Exception,
        operation: str,
        context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Handle error with logging and return user-friendly response"""
        
        logger.error(
            "Operation failed",
            operation=operation,
            error=str(error),
            error_type=type(error).__name__,
            context=context
        )
        
        return self.translate_error(error, context)
```

## Step 3: Core ShipBob Tools (Phase 1)

### 3.1 ShipBob Client
```python
# src/tools/shipbob/client.py
import httpx
from typing import Dict, List, Any
from tenacity import retry, stop_after_attempt, wait_exponential
import structlog

from ...core.error_handler import TrassError, ErrorCategory, ErrorSeverity

logger = structlog.get_logger()

class ShipBobClient:
    def __init__(self, auth_manager):
        self.auth_manager = auth_manager
        self._client = None
    
    async def _get_client(self):
        if self._client is None:
            self._client = await self.auth_manager.get_shipbob_client()
        return self._client
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        client = await self._get_client()
        
        try:
            response = await client.request(method, endpoint, **kwargs)
            
            if response.status_code >= 400:
                error_data = {}
                try:
                    error_data = response.json()
                except:
                    pass
                
                raise TrassError(
                    f"ShipBob API request failed: {response.status_code}",
                    category=ErrorCategory.API_ERROR,
                    details={
                        "status_code": response.status_code,
                        "endpoint": endpoint,
                        "response_data": error_data
                    }
                )
            
            return response.json()
            
        except httpx.RequestError as e:
            raise TrassError(
                f"Network error connecting to ShipBob: {str(e)}",
                category=ErrorCategory.API_ERROR,
                severity=ErrorSeverity.HIGH
            )
    
    async def get_inventory(self) -> List[Dict[str, Any]]:
        """Get all inventory items"""
        response = await self._make_request("GET", "/2.0/inventory")
        return response.get("data", [])
    
    async def get_fulfillment_centers(self) -> List[Dict[str, Any]]:
        """Get available fulfillment centers"""
        return await self._make_request("GET", "/1.0/fulfillmentCenter")
    
    async def create_wro(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new Warehouse Receiving Order"""
        logger.info("Creating WRO", 
                   po_number=payload.get("purchase_order_number"),
                   box_count=len(payload.get("boxes", [])))
        
        return await self._make_request("POST", "/2.0/receiving", json=payload)
    
    async def get_sku_to_inventory_mapping(self) -> Dict[str, int]:
        """Get mapping of SKU -> inventory_id for active products"""
        inventory = await self.get_inventory()
        mapping = {}
        
        for item in inventory:
            if item.get("is_active", False):
                sku = item.get("sku")
                inventory_id = item.get("id")
                if sku and inventory_id:
                    mapping[sku] = inventory_id
        
        logger.info("Retrieved SKU mapping", sku_count=len(mapping))
        return mapping
```

### 3.2 WRO Creator with Business Logic
```python
# src/tools/shipbob/wro_creator.py
from typing import Dict, List, Any
import structlog
from datetime import datetime

from ...shared.config import config
from ...core.error_handler import TrassErrorHandler
from .client import ShipBobClient

logger = structlog.get_logger()

class WROCreator:
    def __init__(self, shipbob_client: ShipBobClient):
        self.client = shipbob_client
        self.config = config
        self.error_handler = TrassErrorHandler()
    
    async def create_complete_wro(
        self,
        po_number: str,
        skus: Dict[str, int],
        expected_arrival: str,
        fulfillment_center: str = None,
        auto_notify: bool = None
    ) -> Dict[str, Any]:
        """
        Create a complete WRO with all Trass business logic
        """
        try:
            logger.info("Starting complete WRO creation workflow",
                       po_number=po_number,
                       sku_count=len(skus),
                       fulfillment_center=fulfillment_center)
            
            # Import here to avoid circular imports
            from ..inventory.tracker import InventoryTracker
            from ..communications.notifications import NotificationManager
            
            # Get business rule settings
            auto_notify = auto_notify if auto_notify is not None else \
                         self.config.workflows["receiving"]["auto_notify"]
            
            # Step 1: Create WRO in ShipBob
            wro_result = await self._create_shipbob_wro(
                po_number, skus, expected_arrival, fulfillment_center
            )
            
            if not wro_result["success"]:
                return wro_result
            
            # Step 2: Update internal tracking (if enabled)
            tracking_result = None
            if self.config.workflows["receiving"]["auto_update_tracker"]:
                try:
                    tracker = InventoryTracker()
                    tracking_result = await tracker.create_receiving_record(
                        po_number=po_number,
                        wro_id=wro_result["wro_id"],
                        skus=skus,
                        expected_arrival=expected_arrival,
                        pallet_count=wro_result["pallet_count"]
                    )
                except Exception as e:
                    logger.warning("Failed to update internal tracker", error=str(e))
                    tracking_result = {"success": False, "error": str(e)}
            
            # Step 3: Send notifications (if enabled)
            notification_result = None
            if auto_notify:
                try:
                    notifier = NotificationManager()
                    notification_result = await notifier.send_wro_created_notification(
                        wro_id=wro_result["wro_id"],
                        po_number=po_number,
                        pallet_count=wro_result["pallet_count"],
                        expected_arrival=expected_arrival,
                        fulfillment_center=fulfillment_center or self.config.get_shipbob_config()["default_fc"]
                    )
                except Exception as e:
                    logger.warning("Failed to send notifications", error=str(e))
                    notification_result = {"success": False, "error": str(e)}
            
            # Compile complete result
            return {
                "success": True,
                "wro_creation": wro_result,
                "internal_tracking": tracking_result,
                "notifications": notification_result,
                "workflow_summary": {
                    "wro_id": wro_result["wro_id"],
                    "po_number": po_number,
                    "total_pallets": wro_result["pallet_count"],
                    "tracking_updated": tracking_result["success"] if tracking_result else False,
                    "notifications_sent": notification_result["success"] if notification_result else False
                },
                "user_message": f"Complete workflow executed! WRO #{wro_result['wro_id']} created with {wro_result['pallet_count']} pallets. " +
                               ("Internal tracking updated. " if tracking_result and tracking_result["success"] else "") +
                               ("Team notified. " if notification_result and notification_result["success"] else "")
            }
            
        except Exception as e:
            logger.error("Complete WRO workflow failed", error=str(e))
            return await self.error_handler.handle_and_log_error(
                e, "complete_wro_creation", {"po_number": po_number}
            )
    
    async def _create_shipbob_wro(
        self,
        po_number: str,
        skus: Dict[str, int],
        expected_arrival: str,
        fulfillment_center: str = None
    ) -> Dict[str, Any]:
        """Create WRO in ShipBob only"""
        # Import pallet calculator
        from .pallet_calculator import PalletCalculator
        
        try:
            # Get SKU mapping
            sku_mapping = await self.client.get_sku_to_inventory_mapping()
            
            # Calculate pallet distribution
            calculator = PalletCalculator(self.config.get_shipbob_config()["pallet_limits"])
            pallet_distribution = calculator.calculate_distribution(skus)
            
            # Build payload
            payload = await self._build_wro_payload(
                po_number, pallet_distribution, sku_mapping,
                expected_arrival, fulfillment_center
            )
            
            # Create WRO
            response = await self.client.create_wro(payload)
            
            return self._process_success_response(response, pallet_distribution)
            
        except Exception as e:
            return await self.error_handler.handle_and_log_error(
                e, "shipbob_wro_creation", {"po_number": po_number}
            )
    
    async def _build_wro_payload(self, po_number, pallet_distribution, sku_mapping, expected_arrival, fulfillment_center):
        """Build ShipBob API payload"""
        shipbob_config = self.config.get_shipbob_config()
        fc_name = fulfillment_center or shipbob_config["default_fc"]
        fc_id = self.config.get_fc_id(fc_name)
        
        # Generate tracking numbers
        total_pallets = len(pallet_distribution)
        tracking_numbers = []
        for i in range(1, total_pallets + 1):
            tracking_number = shipbob_config["tracking_pattern"].replace(
                "{PO}", po_number
            ).replace("{counter:03d}", f"{i:03d}")
            tracking_numbers.append(tracking_number)
        
        # Build boxes
        boxes = []
        for i, pallet_items in enumerate(pallet_distribution):
            box_items = []
            for item in pallet_items:
                if item.sku not in sku_mapping:
                    raise ValueError(f"SKU '{item.sku}' not found in ShipBob inventory")
                box_items.append({
                    "inventory_id": sku_mapping[item.sku],
                    "quantity": item.quantity
                })
            
            boxes.append({
                "tracking_number": tracking_numbers[i],
                "box_items": box_items
            })
        
        return {
            "box_packaging_type": shipbob_config["box_packaging_type"],
            "package_type": shipbob_config["package_type"],
            "boxes": boxes,
            "expected_arrival_date": expected_arrival,
            "fulfillment_center": {"id": fc_id},
            "purchase_order_number": po_number
        }
    
    def _process_success_response(self, response, pallet_distribution):
        """Process successful WRO creation"""
        wro_id = response.get("id")
        tracking_numbers = [box.get("tracking_number") for box in response.get("boxes", [])]
        
        # Calculate summary
        total_units = sum(sum(item.quantity for item in pallet) for pallet in pallet_distribution)
        
        return {
            "success": True,
            "wro