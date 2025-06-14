import configparser

def load_query_mappings():
    config = configparser.ConfigParser()
    config.read('query_mapping.ini')
    return config['DEFAULT'] if 'DEFAULT' in config else {}