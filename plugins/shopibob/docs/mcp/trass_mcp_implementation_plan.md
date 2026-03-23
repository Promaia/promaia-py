# Trass MCP Server Implementation Plan
## Strategic Approach: Leverage Existing Assets

Based on analysis of your comprehensive build guide and existing mature codebase, this plan focuses on **wrapping proven tools in MCP interface** rather than rebuilding from scratch.

## Phase 1: MCP Server Foundation (Week 1-2)

### 1.1 Core MCP Structure
```python
# src/trass_mcp_server.py
from mcp.server import Server
from mcp.server.models import InitializationOptions
import asyncio
import logging

# Import existing modules
from archive.shopify_shipbob_sync import ShopifyShipBobSync
from wro.wro_creator import WROCreator  # To be extracted
from reverse_sync.batch_sync import BatchSync

class TrassMCPServer:
    def __init__(self):
        self.server = Server("trass-operations")
        self.shopify_sync = None
        self.wro_creator = None
        self.batch_sync = None
        
    async def initialize(self):
        """Initialize all existing services"""
        # Load existing configs and authenticate
        self.shopify_sync = ShopifyShipBobSync()
        self.wro_creator = WROCreator()
        self.batch_sync = BatchSync()
        
        # Register MCP tools
        self._register_tools()
        
    def _register_tools(self):
        """Register MCP tools that wrap existing functionality"""
        
        @self.server.create_tool()
        async def create_wro(
            po_number: str,
            products: dict,  # {"SKU": quantity}
            expected_arrival: str,
            fulfillment_center: str = "Ontario"
        ):
            """Create Warehouse Receiving Order using existing WRO logic"""
            return await self.wro_creator.create_complete_wro(
                po_number, products, expected_arrival, fulfillment_center
            )
            
        @self.server.create_tool()
        async def sync_fulfillments(
            shipment_ids: list = None,
            batch_size: int = 50,
            dry_run: bool = True
        ):
            """Sync ShipBob fulfillments to Shopify using existing batch sync"""
            return await self.batch_sync.process_batch(
                shipment_ids, batch_size, dry_run
            )
```

### 1.2 Extract and Modularize Existing Code
Create clean modules from your existing scripts:

```bash
# Directory structure
src/
├── tools/
│   ├── shipbob/
│   │   ├── wro_creator.py      # Extracted from WRO guide + existing logic
│   │   ├── fulfillment_sync.py # Extracted from batch_sync.py
│   │   └── client.py           # Unified ShipBob API client
│   ├── shopify/
│   │   ├── order_sync.py       # Extracted from existing sync tools
│   │   └── client.py           # GraphQL client wrapper
│   └── documents/
│       └── processor.py        # For analyzing uploaded PO documents
└── shared/
    ├── config.py               # Unified configuration
    ├── auth.py                # API authentication management
    └── utils.py               # Common utilities
```

### 1.3 Configuration Unification
Merge your existing .env and configs into unified structure:

```python
# src/shared/config.py
import os
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class TrassConfig:
    # ShipBob settings (from your existing WRO guide)
    shipbob_api_key: str
    fulfillment_centers: Dict[str, int] = None
    pallet_limits: Dict[str, int] = None
    
    # Shopify settings (from your existing sync)
    shopify_shop_domain: str = None
    shopify_access_token: str = None
    
    def __post_init__(self):
        # Set defaults from your existing successful configurations
        self.fulfillment_centers = self.fulfillment_centers or {
            "Ontario": 156,
            "Moreno Valley": 100,
            "Commerce": 111
        }
        self.pallet_limits = self.pallet_limits or {
            "plush": 432,
            "small_parts": 4320
        }

def load_config() -> TrassConfig:
    return TrassConfig(
        shipbob_api_key=os.getenv("SHIPBOB_API_KEY"),
        shopify_shop_domain=os.getenv("SHOPIFY_SHOP_DOMAIN"),
        shopify_access_token=os.getenv("SHOPIFY_ACCESS_TOKEN")
    )
```

## Phase 2: Core Business Tools (Week 3-4)

### 2.1 WRO Creator Tool (Highest Priority)
Extract and enhance your WRO automation:

```python
# src/tools/shipbob/wro_creator.py
from typing import Dict, List, Any
import asyncio
from ...shared.config import load_config
from .client import ShipBobClient

class WROCreator:
    def __init__(self):
        self.config = load_config()
        self.client = ShipBobClient(self.config.shipbob_api_key)
        
    async def create_from_document(self, document_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create WRO from analyzed document data
        Uses your existing pallet calculation logic
        """
        try:
            # Extract data using your proven patterns
            po_number = document_data.get("po_number")
            products = document_data.get("products", {})
            expected_arrival = document_data.get("expected_arrival")
            
            # Use your existing pallet distribution algorithm
            pallet_distribution = self._calculate_pallet_distribution(products)
            
            # Create WRO using your existing API patterns
            result = await self._create_wro_in_shipbob(
                po_number, pallet_distribution, expected_arrival
            )
            
            return {
                "success": True,
                "wro_id": result["id"],
                "total_pallets": len(pallet_distribution),
                "tracking_numbers": result["tracking_numbers"],
                "user_message": f"WRO #{result['id']} created successfully with {len(pallet_distribution)} pallets"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "user_message": f"Failed to create WRO: {str(e)}"
            }
    
    def _calculate_pallet_distribution(self, products: Dict[str, int]) -> List[Dict]:
        """Your existing pallet calculation logic from WRO guide"""
        # Implementation from your WRO_Automation_Guide.md
        pass
```

### 2.2 Document Analysis Integration
Leverage Claude's document analysis with your business logic:

```python
# src/tools/documents/processor.py
class DocumentProcessor:
    async def analyze_po_document(self, file_content: bytes, filename: str) -> Dict[str, Any]:
        """
        Analyze PO document and extract WRO-relevant data
        Uses Claude for analysis, applies your business validation
        """
        # Let Claude analyze the document
        analysis_prompt = """
        Analyze this Purchase Order document and extract:
        1. PO Number (e.g., "0004")
        2. Product SKUs and quantities (e.g., {"YP3-C1": 4284, "YP4": 17064})
        3. Expected arrival date
        4. Destination information
        
        Apply Trass business rules:
        - Pallet limits: 432 for plush items, 4320 for small parts
        - SKU patterns: YP3-* are plush, YP4 is small parts
        """
        
        # Return structured data for WRO creation
        return {
            "po_number": "extracted_po_number",
            "products": {"SKU": quantity},
            "expected_arrival": "2025-07-07T12:00:00Z",
            "validation_status": "valid"
        }
```

## Phase 3: Enhanced Workflows (Week 5-6)

### 3.1 Complete WRO Workflow
Chain your existing tools into complete workflows:

```python
@server.create_tool()
async def complete_wro_workflow(
    document_content: str,
    filename: str,
    auto_notify: bool = True
):
    """
    Complete WRO workflow:
    1. Analyze document (Claude)
    2. Create WRO (existing logic)
    3. Update tracking (existing systems)
    4. Notify team (Slack/email)
    """
    
    # Step 1: Document analysis
    doc_processor = DocumentProcessor()
    analysis = await doc_processor.analyze_po_document(document_content, filename)
    
    # Step 2: WRO creation using your existing logic
    wro_creator = WROCreator()
    wro_result = await wro_creator.create_from_document(analysis)
    
    # Step 3: Notifications (if enabled)
    if auto_notify and wro_result["success"]:
        await notify_wro_created(wro_result)
    
    return {
        "workflow_complete": True,
        "steps_completed": ["document_analysis", "wro_creation", "notifications"],
        "wro_result": wro_result
    }
```

### 3.2 Fulfillment Sync Enhancement
Wrap your existing batch sync with MCP interface:

```python
@server.create_tool()
async def intelligent_fulfillment_sync(
    auto_detect: bool = True,
    batch_size: int = 50,
    priority_orders: List[str] = None
):
    """
    Enhanced fulfillment sync using your existing batch_sync.py logic
    """
    
    # Use your existing BatchSync class
    batch_sync = BatchSync()
    
    if auto_detect:
        # Auto-detect pending shipments
        pending_shipments = await batch_sync.get_pending_shipments()
    else:
        pending_shipments = priority_orders
    
    # Use your existing processing logic
    results = await batch_sync.process_batch(pending_shipments, batch_size)
    
    return {
        "processed_count": results["processed"],
        "success_count": results["successful"],
        "failed_count": results["failed"],
        "user_message": f"Sync complete: {results['successful']}/{results['processed']} orders updated"
    }
```

## Implementation Timeline

### Week 1-2: Foundation
- [ ] Extract core modules from existing scripts
- [ ] Create unified configuration system
- [ ] Basic MCP server setup with 2-3 core tools
- [ ] Test with existing ShipBob/Shopify credentials

### Week 3-4: Core Tools
- [ ] WRO Creator tool (primary focus)
- [ ] Document processor integration
- [ ] Fulfillment sync tool
- [ ] Error handling and validation

### Week 5-6: Workflows
- [ ] Complete WRO workflow
- [ ] Notification system
- [ ] Reporting and analytics
- [ ] Testing and refinement

### Week 7-8: Polish & Expansion
- [ ] Additional business tools
- [ ] Enhanced error handling
- [ ] Documentation and guides
- [ ] Production deployment

## Key Benefits of This Approach

1. **Leverage Proven Code**: Your existing tools handle 20K+ orders successfully
2. **Faster Implementation**: Wrapping vs rebuilding = weeks not months  
3. **Lower Risk**: Known working patterns vs new untested code
4. **Incremental Value**: Each tool adds immediate business value
5. **Familiar Patterns**: Team already knows the underlying systems

## Next Steps

1. **Start Small**: Begin with WRO Creator as the first MCP tool
2. **Extract Gradually**: Move one module at a time from archive to src/
3. **Test Continuously**: Use your existing test data and processes
4. **Add Value Incrementally**: Each week should deliver working tools

Would you like me to begin implementing Phase 1 by extracting the WRO Creator from your existing code and creating the initial MCP server structure? 