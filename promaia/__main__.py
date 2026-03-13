"""
Main entry point for the promaia package.
"""
# Import main from cli.py file (not the cli/ package directory)
# NOTE: There's both promaia/cli.py and promaia/cli/ which causes import confusion
# Use importlib to explicitly load the cli.py file without breaking sys.path
import importlib.util
import os

# Get path to cli.py file (sibling to __main__.py)
cli_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cli.py')

# Load cli.py as a module without breaking sys.path or relative imports
spec = importlib.util.spec_from_file_location("promaia_cli_main", cli_file_path)
cli_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli_module)

main = cli_module.main

if __name__ == "__main__":
    main() 