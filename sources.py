"""Where the bakes keep downloads (CACHE) and write results (OUT). HKT_DATA moves both (default: ./data next to this file)."""
import os
DATA = os.environ.get('HKT_DATA', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
CACHE, OUT = os.path.join(DATA, 'cache'), os.path.join(DATA, 'out')
