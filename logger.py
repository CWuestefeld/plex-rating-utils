# -----------------------------------------------------------------------------
# Copyright (c) 2026 Chris Wuestefeld
# Licensed under the MIT License. See LICENSE in the project root for details.
# -----------------------------------------------------------------------------

import logging
import json
import os
import sys

CONFIG_FILE = 'config.json'

def _load_log_config():
    """Loads logging configuration from config.json, with error handling."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config.get('LOGGING', {})
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Could not read or parse logging config from {CONFIG_FILE}. Error: {e}", file=sys.stderr)
        return {}

def setup_logging():
    """
    Configures and returns the main logger for the application.
    Reads configuration from config.json for LEVEL and FILE.
    """
    log_config = _load_log_config()
    log_level_str = log_config.get('LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    log_file = log_config.get('FILE', 'rating_inference.log')

    # Get a specific logger for this application
    logger = logging.getLogger('PlexRatingUtils')
    logger.setLevel(log_level)

    # Prevent adding handlers multiple times if the script reloads
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create a file handler to log events to a file
    try:
        # Use 'a' for append mode
        fh = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        # Format: Timestamp | Severity | Function/Sub-function | Message
        formatter = logging.Formatter('%(asctime)s | %(levelname)-8s | %(message)s')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except (IOError, PermissionError) as e:
        # If file logging fails, fall back to console-only
        print(f"CRITICAL: Could not create log file at '{log_file}'. Logging to console only. Error: {e}", file=sys.stderr)

    # # Also add a handler to print to the console
    # # This allows seeing logs in real-time without tailing the file
    # ch = logging.StreamHandler(sys.stdout)
    # ch.setLevel(log_level) # Respect the same log level
    # ch_formatter = logging.Formatter('%(levelname)-8s | %(message)s')
    # ch.setFormatter(ch_formatter)
    # logger.addHandler(ch)
    
    return logger

# Create the logger instance that will be imported by other modules
log = setup_logging()

def log_event(subsystem: str, message: str, severity: str = "info"):
    """
    A helper function to log events in a standardized format.
    
    Args:
        subsystem (str): A string identifying the calling function/process, 
                         e.g., "CALC|Album-Up|BEGIN".
        message (str): The descriptive log message.
        severity (str): The log level ('debug', 'info', 'warning', 'error', 'critical').
                        Defaults to 'info'.
    """
    # Get the appropriate logging method (e.g., log.info, log.warning)
    log_function = getattr(log, severity.lower(), log.info)
    
    # Format the message with the subsystem and pass it to the logger
    log_function(f"{subsystem:<30} ~ {message}")
