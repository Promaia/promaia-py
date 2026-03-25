# ID-Based Property System

## Overview

This document describes the ID-based property system implemented for Promaia, which makes property filtering resilient to property and option name changes in Notion.

## Problem

Previously, Promaia filtered Notion pages using property and option names (e.g., `"Team": "Consumer Product"`). When you renamed a property or option in Notion:
- The filter would break
- You'd need to update the config manually
- Old synced data would have mismatched metadata

## Solution

The new system tracks Notion properties and options by their stable IDs instead of names. This mirrors how Notion works internally.

### Key Components

#### 1. Database Schema (`promaia/storage/hybrid_storage.py`)

Three new/updated tables:

**`notion_property_schema`** (updated)
- Added `property_id` column to store Notion's stable property ID
- Properties are now tracked by both name and ID

**`notion_select_options`** (new)
- Tracks option IDs, names, and colors for select/multi-select/status properties
- Maintains current and historical option names via timestamps

**`notion_relations`** (new)
- Tracks relation property metadata including target databases
- Stores both property IDs and relation configuration

#### 2. Property Resolver (`promaia/storage/property_resolver.py`)

Central service for ID ↔ name resolution:
- `get_property_id(database_id, property_name)` → property ID
- `get_property_name(database_id, property_id)` → current property name
- `get_option_id(database_id, property_id, option_name)` → option ID
- `get_option_name(database_id, property_id, option_id)` → current option name
- `resolve_filter_value()` - Resolve IDs to names for filtering
- `resolve_property_name_to_id()` - Resolve names to IDs for migration

Uses LRU caching for performance.

#### 3. Schema Sync (`promaia/connectors/notion_connector.py`)

New `sync_property_metadata()` method:
- Extracts property IDs from Notion's database schema
- Stores option IDs for select/multi-select/status properties
- Tracks relation property metadata
- Updates existing properties when names change
- Marks inactive properties/options

#### 4. Filter Building (`promaia/cli/database_commands.py`)

Updated `build_filters()` function:
- Detects ID-based vs name-based filters (heuristic: no spaces + length > 10)
- Resolves property IDs and option IDs to current names before filtering
- Maintains backward compatibility with name-based configs

#### 5. Config Format (`promaia/config/databases.py`)

Supports both formats:

**Name-based (legacy)**:
```json
"property_filters": {
  "Team": "Consumer Product"
}
```

**ID-based (new)**:
```json
"property_filters": {
  "%5CSwL": "VfT:"
}
```

Multi-value filters also supported:
```json
"property_filters": {
  "%5CSwL": ["VfT:", "TXb~"]
}
```

## Migration

### Automatic Migration Script

Use `migrate_config_to_ids.py` to convert existing configs:

```bash
# Dry run (preview changes)
python migrate_config_to_ids.py --dry-run

# Actual migration (creates backup)
python migrate_config_to_ids.py
```

The script:
1. Syncs property metadata from Notion
2. Resolves all property names and option names to IDs
3. Creates timestamped backup of old config
4. Writes new ID-based config

### Manual Migration

1. Run schema sync for your database:
   ```python
   from promaia.connectors.notion_connector import NotionConnector
   connector = NotionConnector(db_config)
   await connector.sync_property_metadata()
   ```

2. Get property and option IDs:
   ```python
   from promaia.storage.property_resolver import PropertyResolver
   resolver = PropertyResolver()

   prop_id = resolver.get_property_id(database_id, "Team")
   option_id = resolver.get_option_id(database_id, prop_id, "Consumer Product")
   ```

3. Update config with IDs:
   ```json
   "property_filters": {
     "%5CSwL": "VfT:"
   }
   ```

## Usage

### After Renaming a Property in Notion

**Before (would break)**:
1. Notion: Rename "Team" → "Product Team"
2. Config: Still has `"Team": "Consumer Product"`
3. Result: ❌ Filter breaks, nothing syncs

**Now (just works)**:
1. Notion: Rename "Team" → "Product Team"
2. Config: Has `"%5CSwL": "VfT:"` (IDs don't change)
3. System: Resolves IDs to new names automatically
4. Result: ✅ Filter continues working

### After Renaming an Option in Notion

**Before (would break)**:
1. Notion: Rename "Consumer Product" → "Product"
2. Config: Still has `"Team": "Consumer Product"`
3. Result: ❌ Filter breaks

**Now (just works)**:
1. Notion: Rename "Consumer Product" → "Product"
2. Config: Has `"%5CSwL": "VfT:"` (IDs don't change)
3. System: Resolves option ID "VfT:" → "Product"
4. Result: ✅ Filter continues working with new name

### Multi-Value Filters

Filter by multiple options:

```json
"property_filters": {
  "%5CSwL": ["VfT:", "TXb~", "=C;_"]
}
```

This would match pages where Team is "Consumer Product" OR "Backend" OR "Game Dev".

## Implementation Details

### ID Detection Heuristic

The system uses a simple heuristic to detect ID-based vs name-based filters:
- **ID**: No spaces AND length > 10 characters
- **Name**: Contains spaces OR shorter

This works because:
- Notion property IDs are typically 4-10 characters (URL-encoded)
- Property names usually contain spaces or are descriptive
- Option IDs are short random strings

### Resolution Flow

1. **Config Load**: `property_filters` loaded as-is (IDs or names)
2. **Filter Building**: Detects format using heuristic
3. **ID Resolution**: For IDs, resolves to current names via PropertyResolver
4. **Query Building**: Uses current names to build Notion API queries
5. **Filtering**: Notion API filters by current names

### Backward Compatibility

- Old name-based configs continue to work
- No breaking changes to existing systems
- Migration is optional but recommended
- Both formats can coexist during transition

## Testing

### Test Script

Run `test_property_id_system.py` to verify:
- Schema extraction and storage
- Property/option ID resolution
- Filter resolution (IDs → names)
- Multi-value filter support
- Name-to-ID conversion

### Example Output

```bash
$ python test_property_id_system.py

============================================================
Test 1: Schema Extraction and Storage
============================================================
✓ Property metadata synced successfully

============================================================
Test 2: Property Resolution
============================================================
✓ Team property ID: %5CSwL
✓ Property name: Team
✓ Found 6 options:
  - Game Dev (ID: =C;_, Color: orange)
  - Backend (ID: TXb~, Color: green)
  - Consumer Product (ID: VfT:, Color: pink)
  ...

============================================================
✓ All tests passed!
============================================================
```

## Benefits

1. **Resilient to Renames**: Property and option renames don't break filters
2. **Automatic Resolution**: System automatically uses current names
3. **Multi-Value Support**: Filter by multiple options easily
4. **Backward Compatible**: Existing configs continue working
5. **Performance**: LRU caching minimizes database lookups
6. **Mirrors Notion**: Aligns with how Notion tracks properties internally

## Future Enhancements

Potential improvements:
- Automatic schema sync during regular database syncs
- UI for viewing property/option mappings
- Config validator to detect orphaned IDs
- Historical name tracking for audit trails
- Support for more property types (formulas, rollups, etc.)
