# Syncript - Refactored Structure

## Overview

The syncript project has been refactored from a single 1415-line monolithic file into a well-organized, modular Python package with clear separation of concerns.

## New Project Structure

```
syncript/
├── __init__.py                 # Package entry point
├── __main__.py                 # Module entry point (python -m syncript)
├── cli.py                      # Command-line interface
├── config.py                   # Configuration constants
│
├── core/                       # Core functionality
│   ├── __init__.py
│   ├── ssh_manager.py          # SSH connection management
│   └── sync_engine.py          # Main sync orchestration logic
│
├── operations/                 # File operations
│   ├── __init__.py
│   ├── scanner.py              # Local and remote file scanning
│   ├── transfer.py             # Push/pull batch operations
│   ├── conflict.py             # Conflict detection and handling
│   └── delete.py               # Delete operations
│
├── state/                      # State management
│   ├── __init__.py
│   ├── state_manager.py        # Persistent state file management
│   └── progress_manager.py     # Progress/checkpoint management
│
└── utils/                      # Utilities
    ├── __init__.py
    ├── logging.py              # Logging helpers (log, vlog, warn)
    ├── retry.py                # Retry decorator
    ├── ignore_patterns.py      # .stignore parsing and matching
    └── file_utils.py           # File comparison helpers
```

## Running Syncript

The refactored version maintains backward compatibility. You can run it in multiple ways:

### 1. Original way (backward compatible)
```bash
python syncript.py --dry-run
python syncript.py --verbose
```

### 2. As a Python module
```bash
python -m syncript --dry-run
python -m syncript --verbose
```

### 3. By importing
```python
from syncript import main
main()
```

## Benefits of the Refactoring

1. **Improved Maintainability**: Each module has a single, clear responsibility
2. **Better Testability**: Smaller, focused modules are easier to unit test
3. **Clearer Dependencies**: Import statements clearly show module relationships
4. **Easier Navigation**: Developers can quickly find specific functionality
5. **Code Reusability**: Modules can be imported and used independently
6. **Reduced Complexity**: Each file is now 50-400 lines instead of 1400+

## Module Responsibilities

### config.py
- All configuration constants (SSH settings, paths, timeouts)
- Single source of truth for configuration

### core/ssh_manager.py
- SSH/SFTP connection management
- Auto-reconnect functionality
- Keep-alive handling
- Retry-wrapped remote operations

### core/sync_engine.py
- Main sync orchestration (`run_sync`)
- Decision logic (`decide`)
- Conflict resolution workflow
- Progress tracking and recovery

### operations/scanner.py
- Remote async scanning (find command)
- Local file scanning
- Scan result parsing

### operations/transfer.py
- Batch push operations (tar+gzip)
- Batch pull operations
- File list generation

### operations/delete.py
- Remote deletion with confirmation
- Local deletion with confirmation
- User interaction for deletions

### operations/conflict.py
- Conflict detection
- Conflict file generation
- MD5 comparison for conflicts

### state/state_manager.py
- Load/save persistent state
- CSV format handling
- Legacy JSON compatibility

### state/progress_manager.py
- Load/save progress checkpoints
- Enable resume functionality

### utils/logging.py
- Timestamped logging
- Verbose mode support
- Warning messages

### utils/retry.py
- Retry decorator with exponential backoff
- Network operation resilience

### utils/ignore_patterns.py
- .stignore file parsing
- Pattern matching
- Find command generation for remote pruning

### utils/file_utils.py
- File change detection (mtime/size)
- MD5 hash calculation (local and remote)

## Original File Preserved

The original `syncript.py` has been preserved as `syncript.py.old` for reference.

## Migration Notes

- All functionality has been preserved
- No changes to command-line interface
- No changes to file formats or protocols
- Configuration is still in `syncript/config.py` (edit this file for your settings)
- The `.sync_state.csv` and `.sync_progress.json` files remain compatible
