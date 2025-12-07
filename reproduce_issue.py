
import sys
from unittest.mock import MagicMock
from pathlib import Path
from PySide6.QtCore import QObject

# Mock dependencies before importing modules that depend on them
sys.modules['src.iPhoto.gui.facade'] = MagicMock()
sys.modules['src.iPhoto.gui.ui.models.asset_cache_manager'] = MagicMock()
sys.modules['src.iPhoto.gui.ui.models.asset_data_loader'] = MagicMock()
sys.modules['src.iPhoto.gui.ui.models.asset_state_manager'] = MagicMock()
sys.modules['src.iPhoto.gui.ui.models.live_map'] = MagicMock()
sys.modules['src.iPhoto.gui.ui.models.roles'] = MagicMock()
sys.modules['src.iPhoto.gui.ui.tasks.thumbnail_loader'] = MagicMock()

# Import the module under test
# We need to make sure the imports inside asset_list_model work
# We can mock the imports by setting sys.modules
from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel

# Mock facade and other components
facade = MagicMock()
facade.current_album.manifest = {}
facade.asset_list_model = None

# Instantiate model
model = AssetListModel(facade)

# Mock cache manager methods
model._cache_manager.live_map_snapshot.return_value = {}

# Call _on_scan_chunk_ready
root = Path("/tmp")
chunk = [{"rel": "img.jpg", "mime": "image/jpeg"}]
model._album_root = root

print("Calling _on_scan_chunk_ready...")
try:
    model._on_scan_chunk_ready(root, chunk)
    print("Success")
except Exception as e:
    print(f"Caught exception: {e}")
    import traceback
    traceback.print_exc()
