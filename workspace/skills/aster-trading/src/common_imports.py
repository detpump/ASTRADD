#!/usr/bin/env python3
"""
Common imports setup for aster-trading scripts.
Add this import at the top of any script: from common_imports import *
"""
import os
import sys

# Add src directory to path for imports
_src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
