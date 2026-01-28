# -----------------------------------------------------------------------------
# Copyright (c) 2026 Chris Wuestefeld
# Licensed under the MIT License. See LICENSE in the project root for details.
# -----------------------------------------------------------------------------

import json
import os
import sys
import math
import logging
import sqlite3
import time
from plexapi.server import PlexServer
from tqdm import tqdm


# --- Config & State loading ---
APP_VERSION = "0.1.1"
CONFIG_FILE = 'config.json'

class TqdmLoggingHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)

    def emit(self, record):
        try:
            msg = self.format(record)
            # This is the magic line that keeps the bar at the bottom
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)

def setup_logging():
    # 1. Get the Root Logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 2. Clear any existing handlers (important to prevent duplicates)
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # 3. Create the File Handler
    file_handler = logging.FileHandler("descr-clean-enh.log")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    root_logger.addHandler(file_handler)

    # 4. Create the TQDM Handler
    tqdm_handler = TqdmLoggingHandler()
    tqdm_handler.setFormatter(logging.Formatter('%(levelname)-8s %(message)s'))
    root_logger.addHandler(tqdm_handler)

setup_logging()
logger = logging.getLogger(__name__)

from library_interface import LibraryInterface
from country_manager import CountryManager
from ai_interface import AIInterface

def load_json(path, default):
    if not os.path.exists(path): 
        logger.warning(f"Config file '{path}' not found, using default.")
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

def get_config():
    config_version = "1.1.1"
    if not os.path.exists(CONFIG_FILE):
        print(f"Configuration file '{CONFIG_FILE}' not found.")
        create = input("Would you like to create a default config file? (y/n): ").strip().lower()
        if create == 'y':
            default_config = {
                "version": config_version,
                "PLEX_URL": "http://your-server-ip:32400",
                "PLEX_TOKEN": "ENTER_TOKEN_HERE",
                "LIBRARY_NAME": "Music",
                "CONFIDENCE_C": 3.0,
                "BIAS_CRITIC": 1.5,
                "WEIGHT_CRITIC": 3.0,
                "WEIGHT_GLOBAL": 1.0,
                "DRY_RUN": True,
                "INFERRED_TAG": "Rating_Inferred",
                "DYNAMIC_PRECISION": True,
                "COOLDOWN_BATCH": 25,
                "COOLDOWN_SLEEP": 5,
                "DB_FILENAME": "plex_metadata.sqlite"
            }
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=2)
                print(f"\nSuccessfully created {CONFIG_FILE}.")
                print("Please open the file and update 'PLEX_URL', 'PLEX_TOKEN', and 'LIBRARY_NAME'.")
                print("\nTo find your PLEX_TOKEN:")
                print("1. Sign in to Plex in a browser.")
                print("2. Go to any media item and View XML.")
                print("3. Look for 'X-Plex-Token' in the URL or the XML content.")
                print("   (See https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)\n")
                logger.info("Configuration file created successfully.")
                sys.exit(0)
            except Exception as e:
                logger.critical(f"Error creating config file: {e}")
                sys.exit(0)
        else:
            logger.critical("Configuration required. Exiting.")
            sys.exit(1)
    
    cfg = load_json(CONFIG_FILE, {})
    if cfg.get('version') != config_version:
        logger.warning(f"Config file version mismatch: ({cfg.get('version')}) does not match script version ({config_version}).")
    return cfg

config = get_config()

def get_database():
    # If the database file specified in the config doesn't exist, 
    # the first order of business is to ask the user if they want to create a new one. 
    # If so, we run the schema creation script (schema found in file descr-clean-enh-schema.sql). 
    # Then stamp it with its version tag (which is a constant defined in the program).
    # Finally, return to the caller the DB connection object.
    # If any of this can't be completed successfully, return a null object.

    DB_SCHEMA_FILE = 'descr-clean-enh-schema.sql'
    DB_VERSION_KEY = 'db_schema_version'
    DB_VERSION = "1.0"

    db_filename = config.get("DB_FILENAME")
    if not db_filename:
        logger.error("DB_FILENAME not specified in config.json.")
        return None

    db_exists = os.path.exists(db_filename)
    conn = None

    if not db_exists:
        logger.warning(f"Database file '{db_filename}' not found.")
        create = input("Would you like to create and initialize a new database file? (y/n): ").strip().lower()
        if create != 'y':
            logger.critical("Database is required to proceed. Exiting.")
            return None
        
        try:
            logger.info(f"Creating new database: {db_filename}")
            conn = sqlite3.connect(db_filename)
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON;")

            with open(DB_SCHEMA_FILE, 'r', encoding='utf-8') as f:
                schema_sql = f.read()
            cursor.executescript(schema_sql)
            
            cursor.execute(
                "INSERT INTO system_metadata (key, value) VALUES (?, ?)",
                (DB_VERSION_KEY, DB_VERSION)
            )
            
            conn.commit()
            logger.info("Database created and initialized successfully.")
            return conn

        except FileNotFoundError:
            logger.critical(f"Error: Schema file '{DB_SCHEMA_FILE}' not found. Cannot initialize database.")
            if conn: conn.close()
            if os.path.exists(db_filename): os.remove(db_filename)
            return None
        except sqlite3.Error as e:
            logger.critical(f"Database error during initialization: {e}")
            if conn: conn.close()
            if os.path.exists(db_filename): os.remove(db_filename)
            return None

    try:
        logger.debug(f"Connecting to existing database: {db_filename}")
        conn = sqlite3.connect(db_filename)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn
    except sqlite3.Error as e:
        logger.critical(f"Error connecting to database: {e}")
        if conn: conn.close()
        return None

def maintenance_vacuum(dbconn):
    logger.info("Defragmenting database and reclaiming space...")
    dbconn.execute("VACUUM")
    logger.debug("Database optimized.")

def get_library(dbconn):
    # Using the data in the configs, we then try to open the plex library that's specified. If that's successful, then we can proceed. 
    # Look for the specified library_name in the libraries table. If not found, add it to the table. 
    # Check that the library in the database has the same UUID as that of the library we're connected to in Plex. 
    # If so, good. If not, ask the user if they really want to update the existing database record and proceed.
    # return to the caller the object that was returned from plex.library.section(), or a null object if any of this didn't go according to plan.
    try:
        plex = PlexServer(config['PLEX_URL'], config['PLEX_TOKEN'])
        music = plex.library.section(config['LIBRARY_NAME'])
        print(f"Successfully connected to Plex library: '{music.title}'")
    except Exception as e:
        print(f"Plex Connection Error: {e}")
        return None

    cursor = dbconn.cursor()
    library_name = config['LIBRARY_NAME']
    
    cursor.execute("SELECT library_id, library_uuid FROM libraries WHERE library_name = ?", (library_name,))
    row = cursor.fetchone()

    if row is None:
        logger.info(f"Library '{library_name}' not found in the database. Adding it now.")
        try:
            cursor.execute(
                "INSERT INTO libraries (library_name, library_uuid) VALUES (?, ?)",
                (music.title, music.uuid)
            )
            dbconn.commit()
            logger.debug("Library added to database successfully.")
        except sqlite3.Error as e:
            logger.critical(f"Database error while adding library: {e}")
            return None
    else:
        db_uuid = row[1]
        if db_uuid != music.uuid:
            logger.error(f"\nCRITICAL WARNING: Library UUID mismatch! Expected {db_uuid}, but found {music.uuid} at the Plex server")
            print("This can happen if you are running this tool against a different Plex server or library than before.")
            
            confirm = input("Do you want to update the database to use this new library UUID? (y/n): ").strip().lower()
            if confirm != 'y':
                print("Aborting due to UUID mismatch.")
                return None
            
            try:
                logger.warning("Updating library UUID in the database...")
                cursor.execute("UPDATE libraries SET library_uuid = ? WHERE library_name = ?", (music.uuid, library_name))
                dbconn.commit()
                logger.info("Database updated successfully.")
            except sqlite3.Error as e:
                logger.critical(f"Database error while updating library UUID: {e}")
                return None

    return music

def handle_menu():
    while True:
        print("\n 1: Extract Plex data")
        print(" 2: Analyze Country data")
        print(" 3: Normalize Country data")
        print(" 4: Aggregate Album Twins data")
        print(" 9: Push data back to Plex")
        print(" X: eXit")
        
        choice = input("\nSelect Option [1-4,X]: ").strip().upper()

        if choice == 'X':
            return choice
        if choice in ('1', '2', '3', '4', '9'):
            return int(choice)
        print("\nInvalid option. Please try again.")


def print_welcome():
    print(f"======= Descriptive data clean & enhance (v{APP_VERSION}) =======")
    print( "-------     Copyright (c) 2026 Chris Wuestefeld     -------\n")


def main():

    print_welcome()

    dbconn = get_database()
    if not dbconn:
        sys.exit(1)

    try:
        mylibrary = get_library(dbconn)
        if not mylibrary:
            dbconn.close()
            sys.exit(1)

        lib_interface = LibraryInterface.initialize_interface(config, dbconn)
        cmgr = CountryManager(dbconn, lib_interface.library_id)
        ai_interface = AIInterface()

        while True:
            choice = handle_menu()
            if choice == 'X':
                break
            if choice == 1:
                logger.info("extracting plex data")
                lib_interface.extract_mirror()
            elif choice == 2:
                logger.info("analyzing country data")
                cmgr.resolve_countries( ai_interface )
            elif choice == 3:
                logger.info("applying country cleanup")
                cmgr.apply_normalization()
            elif choice == 4:
                logger.info("aggregating album twins data")
                lib_interface.propagate_album_metadata()
            elif choice == 9:
                logger.info("pushing data to Plex")
                lib_interface.sync_to_plex(True)

        maintenance_vacuum(dbconn)

    except Exception as e:
        logger.error(f"uncaught error: {e}")

    finally:
        # Cleanly close the database connection when the program finishes.
        dbconn.close()

    print("Process finished.")

if __name__ == "__main__":
    main()