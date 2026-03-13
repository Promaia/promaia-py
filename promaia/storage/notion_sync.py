"""
Notion Sync System

This module handles syncing local JSON changes back to Notion API,
including property updates, content modifications, and conflict resolution.
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass
import asyncio

from promaia.notion.client import notion_client
from promaia.storage.json_editor import NotionJSONEditor, EditChange
from promaia.config.databases import get_database_manager

@dataclass
class SyncResult:
    """Result of a sync operation"""
    success: bool
    page_id: str
    changes_applied: int
    errors: List[str]
    conflicts: List[str]

class NotionSyncer:
    """Handles syncing local changes back to Notion"""
    
    def __init__(self):
        self.config = get_database_manager()
        self.client = notion_client()
        self.editor = NotionJSONEditor()
        
    def _get_remote_page(self, page_id: str) -> Dict[str, Any]:
        """Fetch the current state of a page from Notion"""
        try:
            # Get the page properties
            page = self.client.pages.retrieve(page_id=page_id)
            
            # Get the page content blocks
            blocks = self.client.blocks.children.list(block_id=page_id)
            
            return {
                'properties': page['properties'],
                'content': blocks['results'],
                'last_edited_time': page['last_edited_time']
            }
        except Exception as e:
            raise Exception(f"Failed to fetch page {page_id}: {str(e)}")
    
    def _detect_conflicts(self, local_data: Dict[str, Any], 
                         remote_data: Dict[str, Any]) -> List[str]:
        """Detect conflicts between local and remote versions"""
        conflicts = []
        
        # Check if remote was edited after our local version
        local_saved = datetime.fromisoformat(local_data['saved_at'].replace('Z', '+00:00'))
        remote_edited = datetime.fromisoformat(remote_data['last_edited_time'].replace('Z', '+00:00'))
        
        if remote_edited > local_saved:
            conflicts.append(f"Remote page was edited after local version (remote: {remote_edited}, local: {local_saved})")
        
        return conflicts
    
    def _sync_properties(self, page_id: str, local_props: Dict[str, Any]) -> List[str]:
        """Sync property changes to Notion"""
        errors = []
        
        try:
            # Filter out read-only properties
            readonly_props = {'Last edited time', 'Created time', 'Created by', 'Last edited by'}
            syncable_props = {k: v for k, v in local_props.items() if k not in readonly_props}
            
            # Update the page properties
            self.client.pages.update(
                page_id=page_id,
                properties=syncable_props
            )
        except Exception as e:
            errors.append(f"Failed to sync properties: {str(e)}")
        
        return errors
    
    def _sync_content_blocks(self, page_id: str, local_content: List[Dict[str, Any]]) -> List[str]:
        """Sync content block changes to Notion"""
        errors = []
        
        try:
            # Get current remote blocks
            remote_blocks = self.client.blocks.children.list(block_id=page_id)
            remote_block_ids = {block['id'] for block in remote_blocks['results']}
            local_block_ids = {block['id'] for block in local_content}
            
            # Find blocks to delete (exist remotely but not locally)
            blocks_to_delete = remote_block_ids - local_block_ids
            for block_id in blocks_to_delete:
                try:
                    self.client.blocks.delete(block_id=block_id)
                except Exception as e:
                    errors.append(f"Failed to delete block {block_id}: {str(e)}")
            
            # Update/create blocks
            for block in local_content:
                try:
                    if block['id'] in remote_block_ids:
                        # Update existing block
                        block_type = block['type']
                        if block_type in block:
                            self.client.blocks.update(
                                block_id=block['id'],
                                **{block_type: block[block_type]}
                            )
                    else:
                        # Create new block
                        block_data = {k: v for k, v in block.items() 
                                    if k not in ['id', 'object', 'created_time', 'last_edited_time', 'created_by', 'last_edited_by']}
                        self.client.blocks.children.append(
                            block_id=page_id,
                            children=[block_data]
                        )
                except Exception as e:
                    errors.append(f"Failed to sync block {block.get('id', 'unknown')}: {str(e)}")
        
        except Exception as e:
            errors.append(f"Failed to sync content blocks: {str(e)}")
        
        return errors
    
    def sync_page(self, content_type: str, page_id: str, 
                  force: bool = False, backup: bool = True) -> SyncResult:
        """Sync a single page to Notion"""
        try:
            # Load local data
            local_data = self.editor.load_page(content_type, page_id)
            
            # Get remote data for conflict detection
            if not force:
                remote_data = self._get_remote_page(page_id)
                conflicts = self._detect_conflicts(local_data, remote_data)
                if conflicts:
                    return SyncResult(
                        success=False,
                        page_id=page_id,
                        changes_applied=0,
                        errors=[],
                        conflicts=conflicts
                    )
            
            # Backup local file before sync
            if backup:
                backup_path = f"{self.editor._get_content_type_dir(content_type)}/{local_data['title']} {page_id}.json.sync_backup.{int(datetime.now().timestamp())}"
                with open(backup_path, 'w', encoding='utf-8') as f:
                    json.dump(local_data, f, indent=2, ensure_ascii=False)
            
            errors = []
            changes_applied = 0
            
            # Sync properties
            prop_errors = self._sync_properties(page_id, local_data['notion_data']['properties'])
            errors.extend(prop_errors)
            if not prop_errors:
                changes_applied += 1
            
            # Sync content blocks
            content_errors = self._sync_content_blocks(page_id, local_data['notion_data']['content'])
            errors.extend(content_errors)
            if not content_errors:
                changes_applied += 1
            
            # Update local file with sync timestamp
            local_data['last_synced'] = datetime.now().isoformat()
            self.editor.save_page(local_data, backup=False)
            
            return SyncResult(
                success=len(errors) == 0,
                page_id=page_id,
                changes_applied=changes_applied,
                errors=errors,
                conflicts=[]
            )
            
        except Exception as e:
            return SyncResult(
                success=False,
                page_id=page_id,
                changes_applied=0,
                errors=[f"Sync failed: {str(e)}"],
                conflicts=[]
            )
    
    def sync_database(self, content_type: str, force: bool = False) -> List[SyncResult]:
        """Sync all pages in a database"""
        results = []
        content_dir = self.editor._get_content_type_dir(content_type)
        
        if not os.path.exists(content_dir):
            return results
        
        for filename in os.listdir(content_dir):
            if filename.endswith('.json') and not 'backup' in filename:
                try:
                    with open(os.path.join(content_dir, filename), 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        page_id = data.get('page_id')
                        if page_id:
                            result = self.sync_page(content_type, page_id, force=force)
                            results.append(result)
                except Exception as e:
                    results.append(SyncResult(
                        success=False,
                        page_id=filename,
                        changes_applied=0,
                        errors=[f"Failed to process file {filename}: {str(e)}"],
                        conflicts=[]
                    ))
        
        return results
    
    def get_modified_pages(self, content_type: str, since: Optional[datetime] = None) -> List[str]:
        """Get list of pages that have been modified locally"""
        modified_pages = []
        content_dir = self.editor._get_content_type_dir(content_type)
        
        if not os.path.exists(content_dir):
            return modified_pages
        
        for filename in os.listdir(content_dir):
            if filename.endswith('.json') and not 'backup' in filename:
                filepath = os.path.join(content_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        
                    # Check if page was modified
                    saved_at = datetime.fromisoformat(data['saved_at'])
                    last_synced = data.get('last_synced')
                    
                    if not last_synced or datetime.fromisoformat(last_synced) < saved_at:
                        if not since or saved_at > since:
                            modified_pages.append(data['page_id'])
                            
                except Exception:
                    continue
        
        return modified_pages
    
    def create_sync_plan(self, content_type: str = None) -> Dict[str, List[str]]:
        """Create a plan showing what needs to be synced"""
        plan = {}
        
        if content_type:
            content_types = [content_type]
        else:
            # Get all configured databases
            content_types = list(self.config.keys())
        
        for ct in content_types:
            modified = self.get_modified_pages(ct)
            if modified:
                plan[ct] = modified
        
        return plan 