import os
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

# Configuración de la bd
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT', '3306')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = urllib.parse.quote_plus(os.getenv('DB_PASSWORD'))
DB_NAME = os.getenv('DB_NAME')

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"