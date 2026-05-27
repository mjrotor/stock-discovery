"""
Vercel serverless entry point.
Vercel's @vercel/python runtime expects a WSGI 'app' object.
"""

import sys
import os

# Add project root to path so stock_discovery package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_discovery.server import app
