import configparser
import os

def load_query_mappings():
    config = configparser.ConfigParser()
    
    # Get the directory where config.py is located
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Construct path to query_mapping.ini
    ini_path = os.path.join(current_dir, 'query_mapping.ini')
    
    if not os.path.exists(ini_path):
        raise FileNotFoundError(f"Query mapping file not found at {ini_path}")
        
    config.read(ini_path)
    return config['DEFAULT'] if 'DEFAULT' in config else {}