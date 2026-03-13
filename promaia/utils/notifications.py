"""
System notification utilities for Maia.

Provides cross-platform notifications for important events like sync completion.
"""
import platform
import subprocess
import logging

logger = logging.getLogger(__name__)


def send_notification(title: str, message: str, subtitle: str = None, sound: str = None):
    """
    Send a system notification.
    
    Args:
        title: Notification title
        message: Notification message body
        subtitle: Optional subtitle (macOS only)
        sound: Optional sound name (macOS only, e.g., "Glass", "Basso", "Ping")
    
    Returns:
        bool: True if notification was sent successfully, False otherwise
    """
    system = platform.system()
    
    try:
        if system == "Darwin":  # macOS
            return _send_macos_notification(title, message, subtitle, sound)
        elif system == "Linux":
            return _send_linux_notification(title, message)
        elif system == "Windows":
            return _send_windows_notification(title, message)
        else:
            logger.warning(f"System notifications not supported on {system}")
            return False
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        return False


def _send_macos_notification(title: str, message: str, subtitle: str = None, sound: str = None) -> bool:
    """Send notification on macOS using osascript."""
    try:
        # Build AppleScript command
        script_parts = [
            'display notification',
            f'"{message}"',
            f'with title "{title}"'
        ]
        
        if subtitle:
            script_parts.append(f'subtitle "{subtitle}"')
        
        if sound:
            script_parts.append(f'sound name "{sound}"')
        
        script = ' '.join(script_parts)
        
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logger.debug(f"macOS notification sent: {title}")
            return True
        else:
            logger.warning(f"Failed to send macOS notification: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending macOS notification: {e}")
        return False


def _send_linux_notification(title: str, message: str) -> bool:
    """Send notification on Linux using notify-send."""
    try:
        result = subprocess.run(
            ['notify-send', title, message],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logger.debug(f"Linux notification sent: {title}")
            return True
        else:
            logger.warning(f"Failed to send Linux notification: {result.stderr}")
            return False
            
    except FileNotFoundError:
        logger.warning("notify-send not found. Install libnotify to enable notifications.")
        return False
    except Exception as e:
        logger.error(f"Error sending Linux notification: {e}")
        return False


def _send_windows_notification(title: str, message: str) -> bool:
    """Send notification on Windows using PowerShell."""
    try:
        # Use PowerShell to send a toast notification
        ps_script = f'''
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
        
        $template = @"
        <toast>
            <visual>
                <binding template="ToastText02">
                    <text id="1">{title}</text>
                    <text id="2">{message}</text>
                </binding>
            </visual>
        </toast>
"@
        
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($template)
        $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Maia").Show($toast)
        '''
        
        result = subprocess.run(
            ['powershell', '-Command', ps_script],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logger.debug(f"Windows notification sent: {title}")
            return True
        else:
            logger.warning(f"Failed to send Windows notification: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending Windows notification: {e}")
        return False


def send_sync_complete_notification(success_count: int, failed_count: int, duration: float):
    """
    Send a notification for sync completion.
    
    Args:
        success_count: Number of successfully synced databases
        failed_count: Number of failed syncs
        duration: Total sync duration in seconds
    """
    total = success_count + failed_count
    
    if failed_count == 0:
        # All successful
        title = "✅ Sync Complete"
        message = f"Successfully synced {success_count} database{'s' if success_count != 1 else ''} in {duration:.1f}s"
        sound = "Glass"  # Pleasant success sound on macOS
    else:
        # Some failures
        title = "⚠️ Sync Complete (with errors)"
        message = f"{success_count}/{total} databases synced. {failed_count} failed."
        sound = "Basso"  # Alert sound on macOS
    
    return send_notification(
        title=title,
        message=message,
        sound=sound
    )
