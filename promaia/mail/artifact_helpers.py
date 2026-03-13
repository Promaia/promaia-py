"""
Helper functions for working with email artifacts in draft mode.

Provides utilities to:
- Extract email metadata from JSON artifacts
- Update draft database with artifact metadata
- Get email body for sending
"""
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def extract_email_metadata_from_artifact(artifact_manager, artifact_id: int) -> Tuple[Optional[str], Dict[str, str]]:
    """
    Extract email metadata from an artifact.

    Args:
        artifact_manager: ArtifactManager instance
        artifact_id: Artifact ID to extract from

    Returns:
        Tuple of (email_body, metadata_dict)
        metadata_dict contains: {'subject': ..., 'to': ..., 'cc': ...}
        Returns (None, {}) if artifact not found or not email type
    """
    artifact = artifact_manager.get_artifact(artifact_id)
    if not artifact:
        logger.warning(f"Artifact #{artifact_id} not found")
        return None, {}

    # Check if this is a JSON email artifact
    artifact_data = artifact.get('data')
    if not artifact_data or artifact_data.get('type') != 'email':
        logger.info(f"Artifact #{artifact_id} is not a JSON email artifact, skipping metadata extraction")
        return artifact.get('content'), {}

    # Extract email body and metadata
    metadata = {}
    body = artifact_data.get('body', '')

    if 'subject' in artifact_data and artifact_data['subject']:
        metadata['subject'] = artifact_data['subject']

    if 'to' in artifact_data and artifact_data['to']:
        metadata['to'] = artifact_data['to']

    if 'cc' in artifact_data and artifact_data['cc']:
        metadata['cc'] = artifact_data['cc']

    if 'thread_id' in artifact_data and artifact_data['thread_id']:
        metadata['thread_id'] = artifact_data['thread_id']

    if 'message_id' in artifact_data and artifact_data['message_id']:
        metadata['message_id'] = artifact_data['message_id']

    logger.info(f"📧 Extracted metadata from artifact #{artifact_id}: {list(metadata.keys())}")
    return body, metadata


def update_draft_with_artifact_metadata(draft_manager, draft_id: str, metadata: Dict[str, str]) -> None:
    """
    Update draft in database with metadata extracted from artifact.

    Args:
        draft_manager: DraftManager instance
        draft_id: Draft ID to update
        metadata: Dict with 'subject', 'to', 'cc' keys
    """
    if not metadata:
        logger.debug("No metadata to update")
        return

    try:
        import sqlite3

        # Build update query based on what metadata we have
        updates = []
        params = []

        if 'subject' in metadata and metadata['subject']:
            updates.append('draft_subject = ?')
            params.append(metadata['subject'])

        if 'to' in metadata and metadata['to']:
            updates.append('inbound_to = ?')
            params.append(metadata['to'])

        if 'cc' in metadata and metadata['cc']:
            updates.append('inbound_cc = ?')
            params.append(metadata['cc'])

        if not updates:
            logger.debug("No valid metadata fields to update")
            return

        # Add draft_id to params
        params.append(draft_id)

        # Execute update
        with sqlite3.connect(draft_manager.db_path) as conn:
            cursor = conn.cursor()
            query = f"UPDATE email_drafts SET {', '.join(updates)} WHERE draft_id = ?"
            cursor.execute(query, params)
            conn.commit()

        logger.info(f"✅ Updated draft {draft_id} with artifact metadata: {list(metadata.keys())}")

    except Exception as e:
        logger.error(f"❌ Failed to update draft with metadata: {e}", exc_info=True)


def get_email_body_from_artifact(artifact_manager, artifact_id: int) -> Optional[str]:
    """
    Get the email body text from an artifact.

    For JSON email artifacts, returns the 'body' field.
    For plain text artifacts, returns the full content.

    Args:
        artifact_manager: ArtifactManager instance
        artifact_id: Artifact ID

    Returns:
        Email body text, or None if artifact not found
    """
    artifact = artifact_manager.get_artifact(artifact_id)
    if not artifact:
        return None

    # Check if this is a JSON email artifact
    artifact_data = artifact.get('data')
    if artifact_data and artifact_data.get('type') == 'email':
        return artifact_data.get('body', '')

    # Plain text artifact - return full content
    return artifact.get('content')
