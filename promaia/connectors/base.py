"""
Base connector interface for database connectors.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class QueryFilter:
    """Represents a query filter for database operations."""
    
    def __init__(self, property_name: str, operator: str, value: Any):
        self.property_name = property_name
        self.operator = operator  # eq, ne, gt, lt, gte, lte, in, not_in, contains, etc.
        self.value = value
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "property": self.property_name,
            "operator": self.operator,
            "value": self.value
        }

class DateRangeFilter:
    """Represents a date range filter."""
    
    def __init__(self, property_name: str, start_date: Optional[datetime] = None, 
                 end_date: Optional[datetime] = None, days_back: Optional[int] = None):
        self.property_name = property_name
        self.start_date = start_date
        self.end_date = end_date
        self.days_back = days_back
        
        # Calculate dates if days_back is provided
        if days_back and not start_date:
            self.start_date = datetime.now() - timedelta(days=days_back)
        if days_back and not end_date:
            self.end_date = datetime.now()

class SyncResult:
    """Result of a sync operation."""
    
    def __init__(self):
        self.pages_fetched = 0
        self.pages_saved = 0
        self.pages_skipped = 0
        self.pages_failed = 0
        self.pages_deleted = 0
        self.files_created: List[str] = []
        self.errors: List[str] = []
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        # MONITORING: Add performance metrics
        self.api_calls_count = 0
        self.api_rate_limit_hits = 0
        self.api_errors_count = 0
        self.database_name: Optional[str] = None
    
    def add_success(self, file_path: str):
        """Add a successful sync."""
        self.pages_saved += 1
        self.files_created.append(file_path)
    
    def add_skip(self):
        """Add a skipped page."""
        self.pages_skipped += 1
    
    def add_error(self, error: str):
        """Add an error."""
        self.pages_failed += 1
        self.errors.append(error)
    
    def add_api_call(self):
        """Track API call count."""
        self.api_calls_count += 1
    
    def add_rate_limit_hit(self):
        """Track rate limit hits."""
        self.api_rate_limit_hits += 1
    
    def add_api_error(self):
        """Track API errors."""
        self.api_errors_count += 1
    
    @property
    def duration_seconds(self) -> Optional[float]:
        """Get sync duration in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "database_name": self.database_name,
            "pages_fetched": self.pages_fetched,
            "pages_saved": self.pages_saved,
            "pages_skipped": self.pages_skipped,
            "pages_failed": self.pages_failed,
            "pages_deleted": self.pages_deleted,
            "files_created": self.files_created,
            "errors": self.errors,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.duration_seconds,
            "api_calls_count": self.api_calls_count,
            "api_rate_limit_hits": self.api_rate_limit_hits,
            "api_errors_count": self.api_errors_count
        }

class BaseConnector(ABC):
    """Base class for all database connectors."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.database_id = config.get("database_id")
        self.auth_config = config.get("auth", {})
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the database."""
        pass
    
    @abstractmethod
    async def test_connection(self) -> bool:
        """Test if the connection is working."""
        pass
    
    @abstractmethod
    async def get_database_schema(self) -> Dict[str, Any]:
        """Get the schema/properties of the database."""
        pass
    
    @abstractmethod
    async def query_pages(self, 
                         filters: Optional[List[QueryFilter]] = None,
                         date_filter: Optional[DateRangeFilter] = None,
                         sort_by: Optional[str] = None,
                         sort_direction: str = "desc",
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Query pages from the database."""
        pass
    
    @abstractmethod
    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        """Get full content of a specific page."""
        pass
    
    @abstractmethod
    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        """Get properties of a specific page."""
        pass
    
    @abstractmethod
    async def sync_to_local(self, 
                           output_directory: str,
                           filters: Optional[List[QueryFilter]] = None,
                           date_filter: Optional[DateRangeFilter] = None,
                           include_properties: bool = True,
                           force_update: bool = False,
                           excluded_properties: List[str] = None) -> SyncResult:
        """Sync database content to local storage."""
        pass
    
    def apply_property_filters(self, pages: List[Dict[str, Any]], 
                              property_filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply property-based filters to a list of pages."""
        if not property_filters:
            return pages
        
        filtered_pages = []
        for page in pages:
            properties = page.get("properties", {})
            include_page = True
            
            for prop_name, expected_values in property_filters.items():
                if prop_name not in properties:
                    include_page = False
                    break
                
                prop_value = self._extract_property_value(properties[prop_name])
                
                # Handle different filter types
                if isinstance(expected_values, list):
                    if prop_value not in expected_values:
                        include_page = False
                        break
                elif prop_value != expected_values:
                    include_page = False
                    break
            
            if include_page:
                filtered_pages.append(page)
        
        return filtered_pages
    
    def _extract_property_value(self, property_data: Dict[str, Any]) -> Any:
        """Extract the actual value from a property data structure."""
        # This is a generic implementation - connectors should override if needed
        prop_type = property_data.get("type")
        
        if prop_type == "title" and property_data.get("title"):
            return "".join([t.get("plain_text", "") for t in property_data["title"]])
        elif prop_type == "rich_text" and property_data.get("rich_text"):
            return "".join([t.get("plain_text", "") for t in property_data["rich_text"]])
        elif prop_type == "select" and property_data.get("select"):
            return property_data["select"].get("name")
        elif prop_type == "multi_select" and property_data.get("multi_select"):
            return [item.get("name") for item in property_data["multi_select"]]
        elif prop_type == "date" and property_data.get("date"):
            return property_data["date"].get("start")
        elif prop_type == "checkbox":
            return property_data.get("checkbox", False)
        elif prop_type == "number":
            return property_data.get("number")
        
        return None

class ConnectorRegistry:
    """Registry for database connectors."""
    
    _connectors: Dict[str, type] = {}
    
    @classmethod
    def register(cls, source_type: str, connector_class: type):
        """Register a connector for a source type."""
        cls._connectors[source_type] = connector_class
        logger.info(f"Registered connector for source type: {source_type}")
    
    @classmethod
    def get_connector(cls, source_type: str, config: Dict[str, Any]) -> Optional[BaseConnector]:
        """Get a connector instance for a source type."""
        connector_class = cls._connectors.get(source_type)
        if connector_class:
            return connector_class(config)
        return None
    
    @classmethod
    def list_connectors(cls) -> List[str]:
        """List all registered connector types."""
        return list(cls._connectors.keys()) 