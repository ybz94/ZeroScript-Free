"""Copy existing site adapters into the standalone extension; no downloads."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent
shutil.copytree(root.parent / 'zeroscript-extension' / 'providers',
                root / 'extension' / 'providers', dirs_exist_ok=True)
print(f'Load unpacked extension: {root / "extension"}')
