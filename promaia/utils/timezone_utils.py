"""
Timezone utilities for automatically handling local timezone detection and conversion.

This module provides timezone-aware datetime operations that automatically detect
the user's local timezone and handle conversions to/from UTC appropriately.
"""

import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

def get_local_timezone() -> timezone:
    """
    Get the local timezone using Python's built-in capabilities.
    
    Returns:
        timezone: The local timezone object
    """
    # Get local timezone offset in seconds
    if time.daylight:
        # Daylight saving time is in effect, use the DST offset
        offset_seconds = -time.altzone
    else:
        # Standard time offset
        offset_seconds = -time.timezone
    
    # Create timezone object with the offset
    local_tz = timezone(timedelta(seconds=offset_seconds))
    
    logger.debug(f"Detected local timezone offset: {offset_seconds/3600:.1f} hours from UTC")
    return local_tz

def get_local_timezone_name() -> str:
    """
    Get a human-readable name for the local timezone.
    
    Returns:
        str: The timezone name (e.g., "PDT", "PST", "EST", etc.)
    """
    return time.tzname[time.daylight]

def now_local() -> datetime:
    """
    Get the current datetime in the local timezone.
    
    Returns:
        datetime: Current time with local timezone info
    """
    return datetime.now(get_local_timezone())

def now_utc() -> datetime:
    """
    Get the current datetime in UTC.
    
    Returns:
        datetime: Current time in UTC
    """
    return datetime.now(timezone.utc)

def days_ago_local(days: int) -> datetime:
    """
    Get a datetime that was N days ago in the local timezone.
    
    Args:
        days: Number of days ago
        
    Returns:
        datetime: Datetime N days ago in local timezone
    """
    return now_local() - timedelta(days=days)

def days_ago_utc(days: int) -> datetime:
    """
    Get a datetime that was N days ago, converted to UTC.
    
    This calculates "N days ago" relative to the local timezone,
    then converts the result to UTC for storage/API usage.
    
    Args:
        days: Number of days ago
        
    Returns:
        datetime: Datetime N days ago in local time, converted to UTC
    """
    local_past_time = days_ago_local(days)
    return local_past_time.astimezone(timezone.utc)

def to_utc(dt: datetime) -> datetime:
    """
    Convert a datetime to UTC, handling timezone-naive datetimes appropriately.
    
    Args:
        dt: The datetime to convert
        
    Returns:
        datetime: The datetime converted to UTC
    """
    if dt.tzinfo is None:
        # Assume naive datetime is in local timezone
        dt = dt.replace(tzinfo=get_local_timezone())
    
    return dt.astimezone(timezone.utc)

def to_local(dt: datetime) -> datetime:
    """
    Convert a datetime to local timezone.
    
    Args:
        dt: The datetime to convert (assumed UTC if timezone-naive)
        
    Returns:
        datetime: The datetime converted to local timezone
    """
    if dt.tzinfo is None:
        # Assume naive datetime is UTC
        dt = dt.replace(tzinfo=timezone.utc)
    
    return dt.astimezone(get_local_timezone())

def log_timezone_info():
    """Log current timezone information for debugging."""
    local_tz = get_local_timezone()
    tz_name = get_local_timezone_name()
    local_now = now_local()
    utc_now = now_utc()
    
    logger.info(f"Local timezone: {tz_name} (UTC{local_tz.utcoffset(None)})")
    logger.info(f"Current local time: {local_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"Current UTC time: {utc_now.strftime('%Y-%m-%d %H:%M:%S %Z')}") 