import yaml
import requests

def check_cookies():
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    cookies = config.get('cookies', [])
    if not cookies:
        print("No cookies found in config.yaml")
        return

    print("\n" + "="*50)
    print("Cookie Validity Diagnostic")
    print("="*50)

    url = "https://www.zhihu.com/api/v4/me" # Simple endpoint to check auth
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    }

    for i, cookie in enumerate(cookies, 1):
        test_headers = headers.copy()
        test_headers["Cookie"] = cookie
        
        try:
            response = requests.get(url, headers=test_headers, timeout=10)
            if response.status_code == 200:
                user_data = response.json()
                name = user_data.get('name', 'Unknown')
                print(f"[Cookie {i}] VALID (User: {name})")
            elif response.status_code == 401:
                print(f"[Cookie {i}] EXPIRED (401 Unauthorized)")
            else:
                print(f"[Cookie {i}] UNKNOWN (Status: {response.status_code})")
        except Exception as e:
            print(f"[Cookie {i}] ERROR: {str(e)}")

    print("="*50 + "\n")

if __name__ == "__main__":
    check_cookies()
