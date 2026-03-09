"""
Centralized path management for the Promaia application.
"""
import os

def get_project_root() -> str:
    """
    Get the absolute path to the project's root directory.
    
    Assumes this file is located at promaia/config/paths.py,
    so the project root is three levels up.
    
    Returns:
        Absolute path to the project root.
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) 