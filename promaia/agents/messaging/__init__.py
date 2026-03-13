"""
Messaging platform abstraction for multi-platform conversational AI.

This module provides a platform-agnostic interface for sending and receiving
messages across different platforms (Slack, Discord, etc.).
"""

from .base import BaseMessagingPlatform, MessageMetadata

__all__ = ['BaseMessagingPlatform', 'MessageMetadata']
