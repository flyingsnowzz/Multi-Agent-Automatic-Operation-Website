import yaml
import os

def load_brand_guidelines():
    config_path = os.path.join(os.path.dirname(__file__), 'brand_guidelines.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

BRAND_CONFIG = load_brand_guidelines()
